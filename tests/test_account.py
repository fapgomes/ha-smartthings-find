"""Tests for the Samsung Account sign-in against local fake servers.

All values are synthetic: no real account, token or callback is used.
"""

from __future__ import annotations

import asyncio
import base64
import json
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import unquote, urlencode

import pytest
from aiohttp import web
from cryptography.hazmat.primitives import padding, serialization
from cryptography.hazmat.primitives.asymmetric import padding as asym_padding, rsa
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from yarl import URL

import account
import api

GRANT = account.AccountGrant(
    auth_server_url="https://eu-auth2.samsungosp.com",
    userauth_token="token-1",
    user_id="user-1",
    login_id="someone@example.com",
    device_id="dev-1",
)


def _ecb_encrypt(value: str, key: str) -> str:
    """Inverse of ``account.decrypt_callback_value``, as Samsung encrypts."""
    padder = padding.PKCS7(128).padder()
    padded = padder.update(value.encode()) + padder.finalize()
    key_bytes = key.encode()[:16].ljust(16, b"\0")
    encryptor = Cipher(algorithms.AES(key_bytes), modes.ECB()).encryptor()
    return (encryptor.update(padded) + encryptor.finalize()).hex()


def _pending(**overrides: Any) -> account.PendingLogin:
    values = {
        "state": "pending-state-1234567",
        "code_verifier": "v" * 43,
        "device_id": "dev-1",
        "created_at": time.time(),
    }
    values.update(overrides)
    return account.PendingLogin(**values)


def _callback(pending: account.PendingLogin, server: str = "eu-auth2.samsungosp.com") -> str:
    response_key = "response-key-123"
    query = urlencode(
        {
            "state": _ecb_encrypt(response_key, pending.state),
            "auth_server_url": _ecb_encrypt(server, response_key),
            "code": _ecb_encrypt("code-1", response_key),
            "retValue": _ecb_encrypt("someone@example.com", response_key),
        }
    )
    return f"{account.REDIRECT_URI}?{query}"


# --- pure helpers ---------------------------------------------------------


def test_trusted_auth_server_accepts_bare_samsung_hosts() -> None:
    assert account.trusted_auth_server("eu-auth2.samsungosp.com") == (
        "https://eu-auth2.samsungosp.com"
    )
    assert account.trusted_auth_server("https://account.samsung.com/") == (
        "https://account.samsung.com"
    )


@pytest.mark.parametrize(
    "value",
    [
        "evil.example.com",
        "http://eu-auth2.samsungosp.com",
        "https://samsungosp.com.evil.example",
        "https://eu-auth2.samsungosp.com/path",
        "https://user@eu-auth2.samsungosp.com",
        "https://eu-auth2.samsungosp.com:8443",
        "",
    ],
)
def test_trusted_auth_server_rejects_others(value: str) -> None:
    with pytest.raises(account.AccountAuthError):
        account.trusted_auth_server(value)


def test_parse_callback_decrypts_fields() -> None:
    pending = _pending()
    assert account.parse_callback(_callback(pending), pending) == (
        "https://eu-auth2.samsungosp.com",
        "code-1",
        "someone@example.com",
    )
    # Pasted with surrounding blanks.
    assert account.parse_callback(f"  {_callback(pending)}\n", pending)[1] == "code-1"


def test_parse_callback_rejects_other_sign_ins_and_addresses() -> None:
    pending = _pending()
    with pytest.raises(account.AccountRedirectError):
        account.parse_callback(_callback(pending), _pending(state="another-state-123456"))
    with pytest.raises(account.AccountRedirectError):
        account.parse_callback("https://example.com/?code=1", pending)
    with pytest.raises(account.AccountRedirectError):
        account.parse_callback(f"{account.REDIRECT_URI}?state=00", pending)
    with pytest.raises(account.AccountAuthError):
        account.parse_callback(_callback(pending, server="evil.example.com"), pending)


def test_encrypt_svc_param_round_trip() -> None:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_der = private_key.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    payload = {"clientId": "x", "state": "s"}
    encoded = account.encrypt_svc_param(
        payload, 3, base64.b64encode(public_der).decode()
    )
    envelope = json.loads(base64.b64decode(unquote(encoded)))
    assert envelope["chkDoNum"] == "3"
    key = base64.b64decode(
        private_key.decrypt(base64.b64decode(envelope["svcEncKY"]), asym_padding.PKCS1v15())
    )
    decryptor = Cipher(
        algorithms.AES(key), modes.CBC(bytes.fromhex(envelope["svcEncIV"]))
    ).decryptor()
    padded = decryptor.update(base64.b64decode(envelope["svcEncParam"])) + decryptor.finalize()
    unpadder = padding.PKCS7(128).unpadder()
    assert json.loads(unpadder.update(padded) + unpadder.finalize()) == payload


def test_grant_from_dict() -> None:
    assert account.AccountGrant.from_dict(GRANT.as_dict()) == GRANT
    assert account.AccountGrant.from_dict({**GRANT.as_dict(), "userauth_token": ""}) is None
    assert account.AccountGrant.from_dict({"user_id": "u"}) is None
    assert account.AccountGrant.from_dict(None) is None


def test_pending_expiry() -> None:
    assert not _pending().expired
    assert _pending(created_at=time.time() - account.PENDING_MAX_AGE - 1).expired


# --- HTTP flows -----------------------------------------------------------


class FakeSamsung:
    """Samsung Account and SmartThings Find on one local server."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, str]]] = []
        self.authorize: dict[str, Any] = {"code": "web-code", "code_expires_in": 300}
        self.authorize_status = 200
        self.authenticate: dict[str, Any] = {"userauth_token": "token-1", "userId": "user-1"}
        self.authenticate_status = 200

    async def handle(self, request: web.Request) -> web.Response:
        path = request.path
        params = dict(request.query)
        if request.method == "POST":
            params.update(await request.post())
        self.calls.append((path, params))
        if path == "/auth/oauth2/authenticate":
            return web.json_response(self.authenticate, status=self.authenticate_status)
        if path == "/auth/oauth2/v2/authorize":
            return web.json_response(self.authorize, status=self.authorize_status)
        if path == "/getState.do":
            return web.json_response({"state": "login-state"})
        if path == "/login.do":
            response = web.Response(status=302, headers={"Location": "/"})
            response.set_cookie("JSESSIONID", "fresh-session")
            return response
        if path == "/chkLogin.do":
            ok = request.cookies.get("JSESSIONID") == "fresh-session"
            return web.Response(text="", headers={"_csrf": "csrf"} if ok else {})
        return web.Response(status=404)


def run(
    fake: FakeSamsung,
    scenario: Callable[[api.StfClient, URL], Any],
    monkeypatch: pytest.MonkeyPatch,
) -> Any:
    async def _main() -> Any:
        app = web.Application()
        app.router.add_route("*", "/{path:.*}", fake.handle)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        base = URL(f"http://127.0.0.1:{port}/")
        # The real check only allows Samsung hosts over https.
        monkeypatch.setattr(
            account, "trusted_auth_server", lambda _value: str(base).rstrip("/")
        )
        session = api.create_session({}, base)
        try:
            return await scenario(api.StfClient(session, base), base)
        finally:
            await session.close()
            await runner.cleanup()

    return asyncio.run(_main())


def test_web_login_signs_a_session_in(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeSamsung()

    async def scenario(client: api.StfClient, base: URL) -> str:
        await account.async_web_login(client.session, GRANT, base)
        return await client.check_login()

    assert run(fake, scenario, monkeypatch) == "csrf"
    authorize = dict(fake.calls)["/auth/oauth2/v2/authorize"]
    assert authorize["userauth_token"] == "token-1"
    assert authorize["client_id"] == account.WEB_FIND_CLIENT_ID
    assert authorize["physical_address_text"] == "dev-1"
    login = dict(fake.calls)["/login.do"]
    assert (login["code"], login["state"]) == ("web-code", "login-state")


def test_web_login_retries_without_login_id(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeSamsung()
    answers = [{"privacyAccepted": "N"}, {"code": "web-code"}]
    original = fake.handle

    async def handle(request: web.Request) -> web.Response:
        if request.path == "/auth/oauth2/v2/authorize":
            fake.authorize = answers.pop(0)
        return await original(request)

    fake.handle = handle  # type: ignore[method-assign]

    async def scenario(client: api.StfClient, base: URL) -> None:
        await account.async_web_login(client.session, GRANT, base)

    run(fake, scenario, monkeypatch)
    authorizes = [p for path, p in fake.calls if path == "/auth/oauth2/v2/authorize"]
    assert "login_id" in authorizes[0] and "login_id" not in authorizes[1]


@pytest.mark.parametrize("status", [400, 401])
def test_web_login_rejected_grant(monkeypatch: pytest.MonkeyPatch, status: int) -> None:
    fake = FakeSamsung()
    fake.authorize_status = status

    async def scenario(client: api.StfClient, base: URL) -> None:
        await account.async_web_login(client.session, GRANT, base)

    with pytest.raises(account.AccountAuthError):
        run(fake, scenario, monkeypatch)


def test_web_login_server_error_is_a_connection_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeSamsung()
    fake.authorize_status = 503

    async def scenario(client: api.StfClient, base: URL) -> None:
        await account.async_web_login(client.session, GRANT, base)

    with pytest.raises(api.StfConnectionError):
        run(fake, scenario, monkeypatch)


def test_complete_login_returns_grant(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeSamsung()
    pending = _pending()

    async def scenario(client: api.StfClient, base: URL) -> account.AccountGrant:
        return await account.async_complete_login(client.session, pending, _callback(pending))

    grant = run(fake, scenario, monkeypatch)
    assert grant.userauth_token == "token-1"
    assert (grant.user_id, grant.login_id, grant.device_id) == (
        "user-1",
        "someone@example.com",
        "dev-1",
    )
    sent = dict(fake.calls)["/auth/oauth2/authenticate"]
    assert (sent["code"], sent["code_verifier"]) == ("code-1", pending.code_verifier)


def test_complete_login_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeSamsung()
    fake.authenticate_status = 400
    pending = _pending()

    async def scenario(client: api.StfClient, base: URL) -> None:
        await account.async_complete_login(client.session, pending, _callback(pending))

    with pytest.raises(account.AccountAuthError):
        run(fake, scenario, monkeypatch)

    old = _pending(created_at=time.time() - account.PENDING_MAX_AGE - 1)

    async def expired(client: api.StfClient, base: URL) -> None:
        await account.async_complete_login(client.session, old, _callback(old))

    with pytest.raises(account.AccountLoginExpiredError):
        run(FakeSamsung(), expired, monkeypatch)
