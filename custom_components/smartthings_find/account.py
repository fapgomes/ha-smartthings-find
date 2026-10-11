"""Samsung Account sign-in that can mint SmartThings Find web sessions.

The user signs in once on Samsung's own page. Samsung hands the result to a
private ``ms-app://`` callback, which the user copies back. Exchanging it
gives a long-lived account grant (``userauth_token``); with it a new
SmartThings Find ``JSESSIONID`` can be created whenever Samsung ends the
current one, without signing in again.

The protocol is the one used by Samsung's apps, as implemented by
samsung-re-find (github.com/charlesbel/samsung-re-find). Like ``api.py``,
this module has no Home Assistant imports so it can be tested on its own.
"""

from __future__ import annotations

import base64
from dataclasses import asdict, dataclass
import hashlib
import json
import os
import secrets
import time
from typing import Any
from urllib.parse import parse_qs, quote, urlsplit

import aiohttp
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.asymmetric import padding as asym_padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.serialization import load_der_public_key
from yarl import URL

try:
    from .api import BASE_URL, StfAuthError, StfConnectionError, StfError
except ImportError:  # tests import the module on its own
    from api import BASE_URL, StfAuthError, StfConnectionError, StfError  # type: ignore[no-redef]

ENTRY_POINT_URL = "https://account.samsung.com/accounts/ANDROIDSDK/getEntryPoint"
REDIRECT_URI = (
    "ms-app://s-1-15-2-4027708247-2189610-1983755848-2937435718-1578786913-"
    "2158692839-1974417358"
)
# Samsung Account app client (sign-in) and SmartThings Find web client.
AUTH_CLIENT_ID = "yfrtglt53o"
WEB_FIND_CLIENT_ID = "ntly6zvfpn"
WEB_FIND_SCOPE = "iot.client"

# A sign-in link must be completed within this time.
PENDING_MAX_AGE = 900

_SAMSUNG_ACCOUNT_DOMAINS = ("account.samsung.com", "samsungosp.com")


class AccountAuthError(StfAuthError):
    """Samsung rejected the sign-in or the stored account grant."""


class AccountRedirectError(AccountAuthError):
    """The pasted address is not a usable sign-in callback."""


class AccountLoginExpiredError(AccountAuthError):
    """The sign-in link is too old; a new one is needed."""


@dataclass(slots=True)
class PendingLogin:
    """Secrets of a sign-in link, kept until its callback arrives."""

    state: str
    code_verifier: str
    device_id: str
    created_at: float

    @property
    def expired(self) -> bool:
        age = time.time() - self.created_at
        return age < -60 or age > PENDING_MAX_AGE


@dataclass(slots=True)
class AccountGrant:
    """Long-lived Samsung Account authorization (stored in the config entry)."""

    auth_server_url: str
    userauth_token: str
    user_id: str
    login_id: str
    device_id: str

    def as_dict(self) -> dict[str, str]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Any) -> AccountGrant | None:
        if not isinstance(data, dict):
            return None
        try:
            grant = cls(**{key: str(data[key]) for key in cls.__slots__})
        except KeyError:
            return None
        return grant if all(asdict(grant).values()) else None


# --- crypto (Samsung's protocol, not a design choice) ---------------------


def _urlsafe_random(byte_count: int) -> str:
    return base64.urlsafe_b64encode(os.urandom(byte_count)).rstrip(b"=").decode()


def _code_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def encrypt_svc_param(payload: dict[str, Any], chk_do_num: int, public_key_b64: str) -> str:
    """Encrypt the sign-in parameters for ``signInGate``."""
    chk_text = str(chk_do_num)
    hashed = base64.b64encode(hashlib.sha256(chk_text.encode()).digest())
    derived = hashlib.pbkdf2_hmac("sha256", hashed, os.urandom(16), chk_do_num, dklen=16)
    public_key = load_der_public_key(base64.b64decode(public_key_b64))
    encrypted_key = public_key.encrypt(  # type: ignore[union-attr]
        base64.b64encode(derived), asym_padding.PKCS1v15()
    )
    iv = os.urandom(16)
    padder = padding.PKCS7(128).padder()
    plaintext = json.dumps(payload, separators=(",", ":")).encode()
    padded = padder.update(plaintext) + padder.finalize()
    encryptor = Cipher(algorithms.AES(derived), modes.CBC(iv)).encryptor()
    encrypted = encryptor.update(padded) + encryptor.finalize()
    envelope = {
        "chkDoNum": chk_text,
        "svcEncParam": base64.b64encode(encrypted).decode(),
        "svcEncKY": base64.b64encode(encrypted_key).decode(),
        "svcEncIV": iv.hex(),
    }
    return quote(base64.b64encode(json.dumps(envelope).encode()).decode(), safe="")


def decrypt_callback_value(value: str, key: str) -> str:
    """Decrypt one field of the sign-in callback (AES-ECB, as Samsung sends it)."""
    key_bytes = key.encode()[:16].ljust(16, b"\0")
    decryptor = Cipher(algorithms.AES(key_bytes), modes.ECB()).decryptor()  # noqa: S305
    padded = decryptor.update(bytes.fromhex(value)) + decryptor.finalize()
    unpadder = padding.PKCS7(128).unpadder()
    return (unpadder.update(padded) + unpadder.finalize()).decode()


def trusted_auth_server(value: str) -> str:
    """Normalise the auth server Samsung names; refuse anything not Samsung's.

    European accounts get a bare host (``eu-auth2.samsungosp.com``).
    """
    text = (value or "").strip()
    if "://" not in text:
        text = f"https://{text}"
    parts = urlsplit(text)
    host = (parts.hostname or "").lower()
    try:
        port = parts.port
    except ValueError:
        port = -1
    trusted = any(host == d or host.endswith(f".{d}") for d in _SAMSUNG_ACCOUNT_DOMAINS)
    if (
        parts.scheme != "https"
        or not trusted
        or parts.username
        or parts.password
        or port not in (None, 443)
        or parts.path not in ("", "/")
        or parts.query
        or parts.fragment
    ):
        raise AccountAuthError(f"Untrusted Samsung authentication server: {host or '?'}")
    return f"https://{host}"


def parse_callback(redirect_uri: str, pending: PendingLogin) -> tuple[str, str, str]:
    """Return ``(auth_server_url, code, login_id)`` from the pasted callback."""
    text = (redirect_uri or "").strip()
    expected = urlsplit(REDIRECT_URI)
    try:
        actual = urlsplit(text)
    except ValueError as err:
        raise AccountRedirectError("Not a Samsung sign-in callback") from err
    if (actual.scheme, actual.netloc, actual.path) != (
        expected.scheme,
        expected.netloc,
        expected.path,
    ):
        raise AccountRedirectError("Not a Samsung sign-in callback")
    params = parse_qs(actual.query)
    if actual.fragment:
        params.update(parse_qs(actual.fragment))

    def one(name: str) -> str:
        return params.get(name, [""])[0]

    try:
        response_key = decrypt_callback_value(one("state"), pending.state)
        auth_server = decrypt_callback_value(one("auth_server_url"), response_key)
        code = decrypt_callback_value(one("code"), response_key)
        login_id = decrypt_callback_value(one("retValue"), response_key)
    except (ValueError, KeyError) as err:
        # Wrong link (another sign-in) or a truncated copy.
        raise AccountRedirectError("The callback does not match this sign-in") from err
    if not code:
        raise AccountRedirectError("The callback has no authorization code")
    return trusted_auth_server(auth_server), code, login_id


# --- HTTP -----------------------------------------------------------------


async def _get_json(
    session: aiohttp.ClientSession, url: str | URL, *, step: str, **kwargs: Any
) -> dict[str, Any]:
    try:
        async with session.get(url, allow_redirects=False, **kwargs) as resp:
            status = resp.status
            body = await resp.text()
    except (aiohttp.ClientError, TimeoutError) as err:
        raise StfConnectionError(f"{step}: {type(err).__name__}") from err
    return _json_body(status, body, step)


def _json_body(status: int, body: str, step: str) -> dict[str, Any]:
    if status == 429 or status >= 500:
        raise StfConnectionError(f"{step}: HTTP {status}")
    if status in (400, 401, 403):
        raise AccountAuthError(f"{step}: HTTP {status}")
    if status != 200:
        raise StfError(f"{step}: HTTP {status}")
    try:
        data = json.loads(body)
    except json.JSONDecodeError as err:
        raise StfError(f"{step}: invalid JSON") from err
    if not isinstance(data, dict):
        raise StfError(f"{step}: unexpected response")
    return data


async def async_start_login(
    session: aiohttp.ClientSession,
    country: str = "us",
    locale: str = "en-US",
    device_id: str | None = None,
) -> tuple[str, PendingLogin]:
    """Create a sign-in link; keep the returned secrets for the callback.

    Reusing ``device_id`` (of an existing grant) keeps Samsung seeing the
    same installation on re-authentication.
    """
    entry = await _get_json(session, ENTRY_POINT_URL, step="getEntryPoint")
    try:
        chk_do_num = int(entry["chkDoNum"])
        public_key = str(entry["pkiPublicKey"])
        sign_in = str(entry["signInURI"])
    except (KeyError, TypeError, ValueError) as err:
        raise StfError("getEntryPoint: unexpected response") from err

    pending = PendingLogin(
        state=_urlsafe_random(15)[:20],
        code_verifier=_urlsafe_random(32)[:43],
        device_id=device_id or secrets.token_hex(16),
        created_at=time.time(),
    )
    payload = {
        "clientId": AUTH_CLIENT_ID,
        "code_challenge": _code_challenge(pending.code_verifier),
        "code_challenge_method": "S256",
        "competitorDeviceYNFlag": "Y",
        "countryCode": country.lower(),
        "deviceInfo": "Google|com.android.chrome",
        "deviceModelID": "Pixel 8 Pro",
        "deviceName": "Google Pixel 8 Pro",
        "deviceOSVersion": "35",
        "devicePhysicalAddressText": f"ANID:{pending.device_id}",
        "deviceType": "APP",
        "deviceUniqueID": pending.device_id,
        "redirect_uri": REDIRECT_URI,
        "replaceableClientConnectYN": "N",
        "replaceableClientId": "",
        "replaceableDevicePhysicalAddressText": "",
        "responseEncryptionType": "1",
        "responseEncryptionYNFlag": "Y",
        "scope": "",
        "state": pending.state,
        "svcIptLgnID": "",
        "iosYNFlag": "Y",
    }
    svc_param = encrypt_svc_param(payload, chk_do_num, public_key)
    url = f"{sign_in}?locale={quote(locale)}&svcParam={svc_param}&mode=C"
    return url, pending


async def async_complete_login(
    session: aiohttp.ClientSession, pending: PendingLogin, redirect_uri: str
) -> AccountGrant:
    """Exchange the pasted callback for an account grant."""
    if pending.expired:
        raise AccountLoginExpiredError("The sign-in link has expired")
    auth_server, code, login_id = parse_callback(redirect_uri, pending)
    step = "Samsung Account sign-in"
    try:
        async with session.post(
            f"{auth_server}/auth/oauth2/authenticate",
            data={
                "grant_type": "authorization_code",
                "serviceType": "M",
                "client_id": AUTH_CLIENT_ID,
                "code": code,
                "code_verifier": pending.code_verifier,
                "username": login_id,
                "physical_address_text": pending.device_id,
            },
            allow_redirects=False,
        ) as resp:
            status = resp.status
            body = await resp.text()
    except (aiohttp.ClientError, TimeoutError) as err:
        raise StfConnectionError(f"{step}: {type(err).__name__}") from err
    data = _json_body(status, body, step)
    token = data.get("userauth_token") or data.get("userAuthToken")
    user_id = data.get("userId") or data.get("user_id")
    if not token or not user_id:
        raise AccountAuthError(f"{step}: no account token in the response")
    return AccountGrant(
        auth_server_url=auth_server,
        userauth_token=str(token),
        user_id=str(user_id),
        login_id=login_id,
        device_id=pending.device_id,
    )


async def async_web_login(
    session: aiohttp.ClientSession, grant: AccountGrant, base_url: URL = BASE_URL
) -> None:
    """Sign ``session`` in to SmartThings Find with the account grant.

    The new ``JSESSIONID`` lands in the session's cookie jar; validate it with
    ``StfClient.check_login``. Raises :class:`AccountAuthError` when Samsung
    no longer accepts the grant (the user must sign in again).
    """
    params = {
        "response_type": "code",
        "serviceType": "M",
        "client_id": WEB_FIND_CLIENT_ID,
        "childAccountSupported": "Y",
        "userauth_token": grant.userauth_token,
        "physical_address_text": grant.device_id,
        "scope": WEB_FIND_SCOPE,
        "login_id": grant.login_id,
    }
    authorize = f"{trusted_auth_server(grant.auth_server_url)}/auth/oauth2/v2/authorize"
    step = "SmartThings Find authorization"
    data = await _get_json(session, authorize, step=step, params=params)
    if not data.get("code") and data.get("privacyAccepted") == "N":
        params.pop("login_id")
        data = await _get_json(session, authorize, step=step, params=params)
    code = data.get("code")
    if not code:
        raise AccountAuthError(f"{step}: Samsung did not issue a code")

    state = await _get_json(
        session, base_url / "getState.do", step="getState.do", params={"payload": "hound"}
    )
    login_state = state.get("state")
    if not login_state:
        raise StfError("getState.do: no login state")

    auth_host = urlsplit(trusted_auth_server(grant.auth_server_url)).hostname or ""
    try:
        async with session.get(
            base_url / "login.do",
            params={
                "auth_server_url": auth_host,
                "api_server_url": auth_host,
                "code": str(code),
                "code_expires_in": str(data.get("code_expires_in", 300)),
                "state": str(login_state),
            },
            allow_redirects=False,
        ) as resp:
            status = resp.status
    except (aiohttp.ClientError, TimeoutError) as err:
        raise StfConnectionError(f"login.do: {type(err).__name__}") from err
    if status == 429 or status >= 500:
        raise StfConnectionError(f"login.do: HTTP {status}")
    if status not in (200, 302):
        raise AccountAuthError(f"login.do: HTTP {status}")
