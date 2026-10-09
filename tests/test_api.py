"""Tests for the SmartThings Find client against a local fake server.

All fixtures are synthetic: no real cookie, device or coordinate is used.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

import pytest
from aiohttp import web
from yarl import URL

import api

CSRF = "csrf-token-1"


class FakeStf:
    """Minimal SmartThings Find: routes are replaceable per test."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.csrf = CSRF
        self.chk_body = ""
        self.handlers: dict[str, Callable[[Any], web.Response]] = {}

    async def chk_login(self, request: web.Request) -> web.Response:
        self.calls.append(("chkLogin.do", request.cookies.get("JSESSIONID")))
        headers = {"_csrf": self.csrf} if self.csrf else {}
        return web.Response(text=self.chk_body, headers=headers)

    async def post(self, request: web.Request) -> web.Response:
        path = request.match_info["path"]
        payload = await request.json()
        self.calls.append((path, payload))
        if request.query.get("_csrf") != self.csrf:
            return web.Response(text="fail")
        handler = self.handlers.get(path)
        if handler is None:
            return web.Response(status=404)
        return handler(payload)


def run(fake: FakeStf, scenario: Callable[[api.StfClient], Any]) -> Any:
    """Start the fake server, run ``scenario`` with a client and stop."""

    async def _main() -> Any:
        app = web.Application()
        app.router.add_get("/chkLogin.do", fake.chk_login)
        app.router.add_post("/{path:.+}", fake.post)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        base = URL(f"http://127.0.0.1:{port}/")
        session = api.create_session({"JSESSIONID": "session-1"}, base)
        try:
            return await scenario(api.StfClient(session, base))
        finally:
            await session.close()
            await runner.cleanup()

    return asyncio.run(_main())


def json_response(data: Any) -> Callable[[Any], web.Response]:
    return lambda _payload: web.json_response(data)


DEVICE_LIST = {
    "deviceList": [
        {
            "dvceID": "100",
            "modelName": "Watch &amp;amp; Co",
            "deviceTypeCode": "WEARABLE",
            "modelID": "SM-X",
            "usrId": "u1",
        },
        {"dvceID": "200", "modelName": "Tag", "deviceTypeCode": "TAG", "usrId": "u1"},
        {"modelName": "no id"},
    ],
    "childrenDeviceList": [],
}


# --- pure helpers ---------------------------------------------------------


def test_parse_cookie_header_accepts_prefix_and_odd_values() -> None:
    assert api.parse_cookie_header("Cookie: a=1; JSESSIONID=X.fmm-prd-1") == {
        "a": "1",
        "JSESSIONID": "X.fmm-prd-1",
    }
    assert api.parse_cookie_header('sa_saved_account=x%40y; bad name=1; c="2') == {
        "sa_saved_account": "x%40y",
        "c": '"2',
    }
    assert api.parse_cookie_header("  ") == {}


def test_parse_battery() -> None:
    assert api.parse_battery("FULL") == 100
    assert api.parse_battery("very_low") == 5
    assert api.parse_battery("42") == 42
    assert api.parse_battery(-1) is None
    assert api.parse_battery("none") is None
    assert api.parse_battery(None) is None


def test_parse_operations_picks_newest_location_and_battery() -> None:
    ops = [
        {"oprnType": "CHECK_CONNECTION", "battery": "MEDIUM"},
        {
            "oprnType": "LOCATION",
            "latitude": "41.0",
            "longitude": "-8.0",
            "horizontalUncertainty": "3",
            "verticalUncertainty": "4",
            "extra": {"gpsUtcDt": "20260101120000"},
        },
        {
            "oprnType": "LASTLOC",
            "latitude": "41.5",
            "longitude": "-8.5",
            "extra": {"gpsUtcDt": "20260102120000"},
        },
        {"oprnType": "OFFLINE_LOC", "latitude": "1", "longitude": "1"},
    ]
    report = api.parse_operations(ops)
    assert report.battery == 50
    assert report.location is not None
    assert (report.location.latitude, report.location.longitude) == (41.5, -8.5)
    assert report.location.operation == "LASTLOC"
    assert report.location.reported_at == datetime(2026, 1, 2, 12, tzinfo=timezone.utc)
    assert report.operations == ["CHECK_CONNECTION", "LOCATION", "LASTLOC", "OFFLINE_LOC"]


def test_parse_operations_uses_plain_enc_location_and_skips_encrypted() -> None:
    enc = {"latitude": "10", "longitude": "20", "gpsUtcDt": "20260103000000"}
    report = api.parse_operations(
        [
            {"oprnType": "OFFLINE_LOC", "encLocation": {**enc, "encrypted": True}},
            {"oprnType": "OFFLINE_LOC", "encLocation": enc},
        ]
    )
    assert report.location is not None
    assert report.location.latitude == 10.0
    assert report.location.accuracy is None


def test_parse_response_report_top_level_location() -> None:
    report = api.parse_response_report(
        {"resultCode": "00", "latitude": 1, "longitude": 2, "gpsUtcDt": "20260104000000"}
    )
    assert report.location is not None
    assert report.location.operation == "TAG_LOCATION"
    assert api.parse_response_report({"resultCode": "01", "dvceId": "1"}).location is None


# --- client against the fake server --------------------------------------


def test_get_devices_parses_and_unescapes_names() -> None:
    fake = FakeStf()
    fake.handlers["device/getDeviceList.do"] = json_response(DEVICE_LIST)

    devices = run(fake, lambda client: client.get_devices())

    assert [d.device_id for d in devices] == ["100", "200"]
    assert devices[0].name == "Watch & Co"
    assert devices[0].model == "SM-X"
    assert devices[1].is_tag and not devices[0].is_tag
    assert fake.calls[0] == ("chkLogin.do", "session-1")


def test_session_expired_body_raises_auth_error() -> None:
    fake = FakeStf()
    fake.chk_body = "Logout"
    fake.csrf = ""

    with pytest.raises(api.StfAuthError):
        run(fake, lambda client: client.check_login())


def test_server_errors_are_connection_errors_not_auth() -> None:
    fake = FakeStf()
    fake.handlers["device/getDeviceList.do"] = lambda _p: web.Response(status=503)

    with pytest.raises(api.StfConnectionError):
        run(fake, lambda client: client.get_devices())


def test_rotated_csrf_is_refreshed_once_for_reads() -> None:
    fake = FakeStf()
    fake.handlers["device/getDeviceList.do"] = json_response(DEVICE_LIST)

    async def scenario(client: api.StfClient) -> list[api.StfDevice]:
        await client.check_login()
        fake.csrf = "csrf-token-2"  # server rotates the token
        return await client.get_devices()

    devices = run(fake, scenario)
    assert len(devices) == 2
    assert [c[0] for c in fake.calls] == [
        "chkLogin.do",
        "device/getDeviceList.do",
        "chkLogin.do",
        "device/getDeviceList.do",
    ]


def test_operations_are_never_replayed() -> None:
    fake = FakeStf()
    fake.handlers["dm/addOperation.do"] = json_response({})
    device = api.StfDevice("100", "W", "WEARABLE", "", "u1", {})

    async def scenario(client: api.StfClient) -> None:
        await client.check_login()
        fake.csrf = "csrf-token-2"
        await client.add_operation(device, api.OP_RING, status="start")

    with pytest.raises(api.StfAuthError):
        run(fake, scenario)
    assert [c[0] for c in fake.calls].count("dm/addOperation.do") == 1


def test_snapshot_404_is_device_not_found() -> None:
    fake = FakeStf()  # no handler -> 404

    with pytest.raises(api.StfDeviceNotFoundError):
        run(fake, lambda client: client.get_snapshot("200"))


def test_operation_result_and_tag_location_payloads() -> None:
    fake = FakeStf()
    fake.handlers["dm/getOperationResult.do"] = json_response({"operation": []})
    fake.handlers["dm/getTagLocation.do"] = json_response({"resultCode": "01"})
    device = api.StfDevice("200", "Tag", "TAG", "", "u1", {})

    async def scenario(client: api.StfClient) -> None:
        await client.get_operation_result(device, [api.OP_CHECK_CONNECTION_WITH_LOCATION])
        await client.get_tag_location(device)
        await client.get_tag_location(
            device, datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
        )

    run(fake, scenario)
    posted = [c for c in fake.calls if c[0] != "chkLogin.do"]
    assert posted == [
        (
            "dm/getOperationResult.do",
            {"dvceId": "200", "operation": ["CHECK_CONNECTION_WITH_LOCATION"], "userId": "u1"},
        ),
        ("dm/getTagLocation.do", {"dvceId": "200", "latestTime": "00000000"}),
        ("dm/getTagLocation.do", {"dvceId": "200", "latestTime": "20260102030405"}),
    ]


def test_cookies_reflect_rotation_by_server() -> None:
    fake = FakeStf()

    def rotate(_payload: Any) -> web.Response:
        resp = web.json_response(DEVICE_LIST)
        resp.set_cookie("JSESSIONID", "session-2")
        return resp

    fake.handlers["device/getDeviceList.do"] = rotate

    async def scenario(client: api.StfClient) -> dict[str, str]:
        await client.get_devices()
        return client.cookies()

    assert run(fake, scenario) == {"JSESSIONID": "session-2"}


def test_location_request_operations_match_web_client() -> None:
    tag = api.StfDevice("1", "Tag", "TAG", "", "u", {})
    phone = api.StfDevice("2", "Phone", "PHONE DEVICE", "", "u", {})
    buds = api.StfDevice("3", "Buds", "BUDS", "", "u", {})

    assert api.location_request_operations(tag) == ["CHECK_CONNECTION_WITH_LOCATION"]
    assert api.location_request_operations(phone) == ["CHECK_CONNECTION", "LOCATION"]
    assert api.result_query_operations(buds, "LOCATION") == ["LOCATION", "LASTLOC"]
    assert api.result_query_operations(phone, "LOCATION") == ["LOCATION"]
    assert api.result_query_operations(buds, "CHECK_CONNECTION") == ["CHECK_CONNECTION"]


def test_answered_since_ignores_results_of_earlier_requests() -> None:
    done = {"oprnStsCd": "2800"}
    report = api.parse_operations(
        [
            {"oprnType": "LOCATION", "oprnCrtDate": "20260929191812", **done},
            {"oprnType": "LOCATION", "oprnCrtDate": "20260929220000", **done},
            {"oprnType": "CHECK_CONNECTION", "oprnCrtDate": "20260929060000", **done},
        ]
    )
    sent = datetime(2026, 9, 29, 21, 0, tzinfo=timezone.utc)

    assert report.answered_since("LOCATION", sent)
    assert not report.answered_since("CHECK_CONNECTION", sent)
    assert not report.answered_since("RING", sent)


def test_running_operation_is_not_an_answer() -> None:
    # Real getOperationResult.do shapes (location redacted): LOCATION still
    # running (2100) while CHECK_CONNECTION already finished with the battery.
    running = api.parse_response_report(
        {
            "resultCode": "00",
            "operation": [
                {
                    "oprnCrtDate": "20260929220916",
                    "oprnType": "LOCATION",
                    "oprnStsCd": "2100",
                    "locationType": "basic",
                }
            ],
        }
    )
    finished = api.parse_response_report(
        {
            "resultCode": "00",
            "operation": [
                {
                    "oprnCrtDate": "20260929220916",
                    "oprnDoneDate": "20260929220917",
                    "oprnType": "CHECK_CONNECTION",
                    "oprnStsCd": "2800",
                    "oprnResultCode": "1200",
                    "battery": "36",
                    "extra": {"battery": "36", "isConnected": True},
                }
            ],
        }
    )
    sent = datetime(2026, 9, 29, 22, 9, tzinfo=timezone.utc)

    assert not running.answered_since("LOCATION", sent)
    assert running.location is None
    assert finished.answered_since("CHECK_CONNECTION", sent)
    assert finished.battery == 36


def test_parse_menu_flags() -> None:
    snapshot = {
        "menu": [
            {"ring": "Y"},
            {"ringStop": "N"},
            {"lock": [{"screenLock": "Y"}]},
            "junk",
        ]
    }
    assert api.parse_menu(snapshot) == {"ring": True, "ringStop": False}
    assert api.parse_menu({}) == {}
    assert api.parse_menu(None) == {}


def test_connection_network_and_last_operation() -> None:
    report = api.parse_operations(
        [
            {
                "oprnType": "LOCATION",
                "oprnCrtDate": "20260929221216",
                "oprnDoneDate": "20260929221222",
                "oprnStsCd": "2800",
                "oprnResultCode": "1200",
                "latitude": "1",
                "longitude": "2",
                "locationType": "basic",
                "extra": {
                    "gpsUtcDt": "20260929221222",
                    "seData": {"wifi": {"bssid": "aa:bb"}, "netType": "wifi"},
                },
            },
            {
                "oprnType": "CHECK_CONNECTION",
                "oprnCrtDate": "20260929221210",
                "oprnStsCd": "2800",
                "oprnResultCode": "1200",
                "extra": {"isConnected": True},
            },
        ]
    )
    assert report.location is not None
    assert report.location.location_type == "basic"
    assert report.location.network_type == "wifi"
    assert report.location.network == "wifi"
    assert report.location.wifi_bssid == "aa:bb"
    assert report.connection is not None and report.connection.connected is True
    assert report.last_operation is not None
    assert report.last_operation.operation == "LOCATION"
    assert report.last_operation.outcome == "success"


def test_offline_finding_network() -> None:
    # Seen on a phone: a relayed position newer than the last LOCATION, with
    # no ``extra.seData``.
    enc = {"latitude": "1", "longitude": "2", "gpsUtcDt": "20261009055801"}
    report = api.parse_operations(
        [
            {
                "oprnType": "LOCATION",
                "latitude": "3",
                "longitude": "4",
                "extra": {"gpsUtcDt": "20261008170624", "seData": {"netType": "wifi"}},
            },
            {"oprnType": "OFFLINE_LOC", "encLocation": enc, "encrypted": False},
        ]
    )
    assert report.location is not None
    assert report.location.operation == "OFFLINE_LOC"
    assert report.location.network_type is None
    assert report.location.network == api.NETWORK_OFFLINE_FINDING


def test_failed_and_running_outcomes() -> None:
    # A tag that did not answer (seen: oprnStsCd 1900 / 3451) and a watch
    # whose location failed (501).
    tag = api.parse_operations(
        [{"oprnType": "CHECK_CONNECTION", "oprnCrtDate": "20260929222150",
          "oprnStsCd": "1900", "oprnResultCode": "3451"}]
    )
    assert tag.connection is not None and tag.connection.connected is False
    assert tag.last_operation.outcome == "failed"

    watch = api.parse_operations(
        [{"oprnType": "LOCATION", "oprnCrtDate": "20260929193216",
          "oprnStsCd": "2800", "oprnResultCode": "501"}]
    )
    assert watch.last_operation.outcome == "failed"
    assert watch.connection is None

    running = api.parse_operations(
        [{"oprnType": "CHECK_CONNECTION", "oprnCrtDate": "20260929222150",
          "oprnStsCd": "2100"}]
    )
    assert running.connection.outcome == "pending"
    assert running.connection.connected is None


def test_lock_status_from_connection_check() -> None:
    def check(created: str, lock: dict[str, bool]) -> dict[str, Any]:
        return {
            "oprnType": "CHECK_CONNECTION",
            "oprnCrtDate": created,
            "oprnDoneDate": created,
            "oprnStsCd": "2800",
            "oprnResultCode": "1200",
            "extra": {"lockStatus": lock},
        }

    # Real sequence: unlocked at 22:28:11, locked again at 22:29:11.
    report = api.parse_operations(
        [
            check("20260929222811", {"fmmLock": False, "normalLock": False}),
            check("20260929222911", {"fmmLock": False, "normalLock": True}),
        ]
    )
    assert report.lock is not None
    assert report.lock.locked is True and report.lock.remote_locked is False
    assert report.lock.checked_at == datetime(2026, 9, 29, 22, 29, 11, tzinfo=timezone.utc)

    remote = api.parse_operations(
        [check("20260929230000", {"fmmLock": True, "normalLock": False})]
    )
    assert remote.lock.locked is True and remote.lock.remote_locked is True
    assert api.parse_operations([{"oprnType": "LOCATION"}]).lock is None

    assert not api.StfDevice("1", "T", "TAG", "", None, {}).has_lock
    assert api.StfDevice("2", "P", "PHONE DEVICE", "", None, {}).has_lock
