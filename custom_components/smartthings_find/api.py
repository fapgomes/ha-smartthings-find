"""Client for the SmartThings Find web API.

This module deliberately has no Home Assistant imports so it can be tested
on its own. The endpoints were mapped from the public web client bundle
(see tools/extract_site_js.py).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import html
import json
import logging
import math
import re
from http.cookies import CookieError, SimpleCookie
from typing import Any

import aiohttp
from yarl import URL

_LOGGER = logging.getLogger(__name__)

BASE_URL = URL("https://smartthingsfind.samsung.com/")

PATH_CHK_LOGIN = "chkLogin.do"
PATH_DEVICE_LIST = "device/getDeviceList.do"
PATH_SET_LAST_SELECT = "device/setLastSelect.do"
PATH_ADD_OPERATION = "dm/addOperation.do"
PATH_OPERATION_RESULT = "dm/getOperationResult.do"
PATH_TAG_LOCATION = "dm/getTagLocation.do"

OP_RING = "RING"
OP_CHECK_CONNECTION = "CHECK_CONNECTION"
OP_CHECK_CONNECTION_WITH_LOCATION = "CHECK_CONNECTION_WITH_LOCATION"
OP_LOCATION = "LOCATION"
OP_LASTLOC = "LASTLOC"
OP_OFFLINE_LOC = "OFFLINE_LOC"
LOCATION_OPERATIONS = (OP_LOCATION, OP_LASTLOC, OP_OFFLINE_LOC)
# Network of a position relayed by a nearby Galaxy device (no ``netType``).
NETWORK_OFFLINE_FINDING = "offline_finding"

# oprnStsCd of a finished operation (2100 = still running). The web client
# treats oprnResultCode 1200 as success.
OPERATION_DONE = "2800"
OPERATION_RUNNING = "2100"
RESULT_SUCCESS = "1200"
CONNECTION_OPERATIONS = (OP_CHECK_CONNECTION, OP_CHECK_CONNECTION_WITH_LOCATION)

DEVICE_TYPE_TAG = "TAG"
DEVICE_TYPE_BUDS = "BUDS"
# Device types without a screen lock.
UNLOCKABLE_TYPES = frozenset({DEVICE_TYPE_TAG, DEVICE_TYPE_BUDS})

# Bodies SmartThings Find returns with HTTP 200 once the web session is gone.
SESSION_EXPIRED_BODIES = frozenset({"fail", "Logout"})

BATTERY_LEVELS: dict[str, int] = {
    "FULL": 100,
    "HIGH": 80,
    "NORMAL": 50,
    "MEDIUM": 50,
    "LOW": 15,
    "VERY_LOW": 5,
    "EMPTY": 0,
}

_COOKIE_NAME_RE = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")
_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0 Safari/537.36"
)


class StfError(Exception):
    """Base error for SmartThings Find."""


class StfAuthError(StfError):
    """The web session is invalid or expired."""


class StfConnectionError(StfError):
    """Network failure, rate limit or server-side outage."""


class StfDeviceNotFoundError(StfError):
    """The server does not know the device for this session (HTTP 404)."""


@dataclass(slots=True)
class StfDevice:
    """One entry of getDeviceList.do."""

    device_id: str
    name: str
    type_code: str
    model: str
    user_id: str | None
    raw: dict[str, Any] = field(repr=False)

    @property
    def is_tag(self) -> bool:
        return self.type_code == DEVICE_TYPE_TAG

    @property
    def has_lock(self) -> bool:
        return self.type_code not in UNLOCKABLE_TYPES


@dataclass(slots=True)
class StfLocation:
    """A position reported by an operation result."""

    latitude: float
    longitude: float
    accuracy: float | None
    reported_at: datetime
    operation: str
    # ``basic`` for a fresh fix, ``last`` for the last known position.
    location_type: str | None = None
    network_type: str | None = None
    wifi_bssid: str | None = None

    @property
    def network(self) -> str | None:
        """Network of the position, or ``offline_finding`` when relayed."""
        if self.network_type is None and self.operation == OP_OFFLINE_LOC:
            return NETWORK_OFFLINE_FINDING
        return self.network_type


@dataclass(slots=True)
class StfLockStatus:
    """Lock state reported with a connection check.

    ``locked`` is the screen lock *right now* (verified: it follows the user
    locking and unlocking the phone); ``remote_locked`` is a lock applied
    through SmartThings Find.
    """

    locked: bool
    remote_locked: bool
    checked_at: datetime


@dataclass(slots=True)
class StfOperationStatus:
    """Outcome of one operation as the server reports it."""

    operation: str
    status_code: str
    result_code: str | None
    created: datetime
    done: datetime | None
    connected: bool | None = None

    @property
    def outcome(self) -> str:
        """``pending``, ``success`` or ``failed``."""
        if self.status_code == OPERATION_RUNNING:
            return "pending"
        if self.status_code == OPERATION_DONE and self.result_code == RESULT_SUCCESS:
            return "success"
        return "failed"


@dataclass(slots=True)
class StfDeviceReport:
    """What one response says about a device's battery and position."""

    battery: int | None = None
    location: StfLocation | None = None
    operations: list[str] = field(default_factory=list)
    # Newest ``oprnCrtDate`` of each *finished* operation type, to tell
    # fresh answers from running operations and from earlier requests.
    completed: dict[str, datetime] = field(default_factory=dict)
    # Newest operation of any type, and newest connection check.
    last_operation: StfOperationStatus | None = None
    connection: StfOperationStatus | None = None
    lock: StfLockStatus | None = None

    def answered_since(self, operation: str, since: datetime) -> bool:
        created = self.completed.get(operation)
        return created is not None and created >= since


def parse_cookie_header(value: str) -> dict[str, str]:
    """Parse a browser ``Cookie`` header, with or without the ``Cookie:`` prefix."""
    text = (value or "").strip()
    if text.lower().startswith("cookie:"):
        text = text.split(":", 1)[1].strip()
    if not text:
        return {}

    try:
        parsed = SimpleCookie()
        parsed.load(text)
        cookies = {
            name: morsel.value
            for name, morsel in parsed.items()
            if _COOKIE_NAME_RE.match(name)
        }
        if cookies:
            return cookies
    except CookieError:
        pass

    # SimpleCookie rejects some real-world values; fall back to plain splitting.
    cookies = {}
    for part in text.split(";"):
        name, sep, cookie_value = part.strip().partition("=")
        name = name.strip()
        if sep and _COOKIE_NAME_RE.match(name):
            cookies[name] = cookie_value.strip()
    return cookies


def format_cookie_header(cookies: dict[str, str]) -> str:
    return "; ".join(f"{name}={value}" for name, value in sorted(cookies.items()))


def parse_stf_date(value: Any) -> datetime | None:
    """Parse the ``YYYYMMDDhhmmss`` UTC timestamps used by the API."""
    try:
        return datetime.strptime(str(value), "%Y%m%d%H%M%S").replace(
            tzinfo=timezone.utc
        )
    except (TypeError, ValueError):
        return None


def parse_battery(value: Any) -> int | None:
    """Map a battery string (``FULL``, ``LOW``...) or number to a percentage."""
    if value is None:
        return None
    text = str(value).strip().upper()
    if text in BATTERY_LEVELS:
        return BATTERY_LEVELS[text]
    try:
        level = int(float(text))
    except ValueError:
        return None
    # Negative values mean "unknown" in the web client.
    return level if 0 <= level <= 100 else None


def _accuracy(op: dict[str, Any]) -> float | None:
    try:
        horizontal = float(op.get("horizontalUncertainty"))
        vertical = float(op.get("verticalUncertainty"))
    except (TypeError, ValueError):
        return None
    value = math.hypot(horizontal, vertical)
    return round(value, 1) if math.isfinite(value) else None


def _location_from(source: dict[str, Any], op_type: str) -> StfLocation | None:
    """Build a location from an operation or its ``encLocation`` block."""
    extra = source.get("extra") if isinstance(source.get("extra"), dict) else {}
    reported_at = parse_stf_date(extra.get("gpsUtcDt") or source.get("gpsUtcDt"))
    if reported_at is None:
        return None
    try:
        latitude = float(source["latitude"])
        longitude = float(source["longitude"])
    except (KeyError, TypeError, ValueError):
        return None
    if not (math.isfinite(latitude) and math.isfinite(longitude)):
        return None
    se_data = extra.get("seData") if isinstance(extra.get("seData"), dict) else {}
    wifi = se_data.get("wifi") if isinstance(se_data.get("wifi"), dict) else {}
    return StfLocation(
        latitude=latitude,
        longitude=longitude,
        accuracy=_accuracy(source),
        reported_at=reported_at,
        operation=op_type,
        location_type=source.get("locationType") or None,
        network_type=se_data.get("netType") or None,
        wifi_bssid=wifi.get("bssid") or None,
    )


def _operation_status(op: dict[str, Any], op_type: str) -> StfOperationStatus | None:
    created = parse_stf_date(op.get("oprnCrtDate"))
    status_code = str(op.get("oprnStsCd") or "")
    if created is None or not status_code:
        return None
    result_code = op.get("oprnResultCode")
    status = StfOperationStatus(
        operation=op_type,
        status_code=status_code,
        result_code=str(result_code) if result_code is not None else None,
        created=created,
        done=parse_stf_date(op.get("oprnDoneDate")),
    )
    if op_type in CONNECTION_OPERATIONS and status.outcome != "pending":
        extra = op.get("extra") if isinstance(op.get("extra"), dict) else {}
        is_connected = extra.get("isConnected")
        status.connected = (
            is_connected if isinstance(is_connected, bool) else status.outcome == "success"
        )
    return status


def _lock_status(
    op: dict[str, Any], status: StfOperationStatus | None
) -> StfLockStatus | None:
    extra = op.get("extra") if isinstance(op.get("extra"), dict) else {}
    lock = extra.get("lockStatus")
    if status is None or not isinstance(lock, dict):
        return None
    normal, remote = lock.get("normalLock"), lock.get("fmmLock")
    if not isinstance(normal, bool) and not isinstance(remote, bool):
        return None
    return StfLockStatus(
        locked=bool(normal) or bool(remote),
        remote_locked=bool(remote),
        checked_at=status.done or status.created,
    )


def parse_operations(operations: Any) -> StfDeviceReport:
    """Extract battery and the newest position from a list of operations."""
    report = StfDeviceReport()
    if not isinstance(operations, list):
        return report

    for op in operations:
        if not isinstance(op, dict):
            continue
        op_type = str(op.get("oprnType") or "")
        report.operations.append(op_type)
        created = parse_stf_date(op.get("oprnCrtDate"))
        if (
            created is not None
            and str(op.get("oprnStsCd") or "") == OPERATION_DONE
            and (op_type not in report.completed or created > report.completed[op_type])
        ):
            report.completed[op_type] = created

        if report.battery is None and "battery" in op:
            report.battery = parse_battery(op.get("battery"))

        status = _operation_status(op, op_type)
        lock = _lock_status(op, status)
        if lock is not None and (report.lock is None or lock.checked_at > report.lock.checked_at):
            report.lock = lock
        if status is not None:
            if report.last_operation is None or status.created > report.last_operation.created:
                report.last_operation = status
            if op_type in CONNECTION_OPERATIONS and (
                report.connection is None or status.created > report.connection.created
            ):
                report.connection = status

        if op_type not in LOCATION_OPERATIONS:
            continue
        location = _location_from(op, op_type)
        if location is None:
            enc = op.get("encLocation")
            # Encrypted locations need the device's key; skip them.
            if isinstance(enc, dict) and enc.get("encrypted") is not True:
                location = _location_from(enc, op_type)
        if location is None:
            continue
        if report.location is None or location.reported_at > report.location.reported_at:
            report.location = location

    return report


def parse_menu(snapshot: Any) -> dict[str, bool]:
    """Features the server offers for a device, from ``setLastSelect.do``.

    ``menu`` is a list of one-key objects such as ``{"ringStop": "N"}``;
    nested groups (``lock``, ``wipe``) are skipped.
    """
    menu = snapshot.get("menu") if isinstance(snapshot, dict) else None
    features: dict[str, bool] = {}
    if not isinstance(menu, list):
        return features
    for item in menu:
        if not isinstance(item, dict):
            continue
        for name, value in item.items():
            if isinstance(value, str):
                features[name] = value.upper() == "Y"
    return features


def location_request_operations(device: StfDevice) -> list[str]:
    """Operations the web client sends to refresh a device.

    Tags get one combined operation; everything else gets a connection check
    (battery) and a separate location request.
    """
    if device.is_tag:
        return [OP_CHECK_CONNECTION_WITH_LOCATION]
    return [OP_CHECK_CONNECTION, OP_LOCATION]


def result_query_operations(device: StfDevice, operation: str) -> list[str]:
    """What to ask getOperationResult.do for, as the web client does."""
    if operation == OP_LOCATION and device.type_code == DEVICE_TYPE_BUDS:
        return [OP_LOCATION, OP_LASTLOC]
    return [operation]


def parse_response_report(payload: Any) -> StfDeviceReport:
    """Parse any response that carries operations or a bare location.

    ``setLastSelect.do`` and (as far as the web bundle shows)
    ``getOperationResult.do`` return ``{"operation": [...]}``.
    ``getTagLocation.do`` may return the location fields at the top level.
    """
    if not isinstance(payload, dict):
        return StfDeviceReport()
    report = parse_operations(payload.get("operation"))
    if report.location is None and "latitude" in payload:
        report.location = _location_from(payload, "TAG_LOCATION")
    if report.battery is None and "battery" in payload:
        report.battery = parse_battery(payload.get("battery"))
    return report


def _parse_device(raw: dict[str, Any]) -> StfDevice | None:
    device_id = str(raw.get("dvceID") or "").strip()
    if not device_id:
        return None
    # Names arrive HTML-escaped, sometimes twice.
    name = html.unescape(html.unescape(str(raw.get("modelName") or ""))).strip()
    user_id = raw.get("usrId")
    return StfDevice(
        device_id=device_id,
        name=name or device_id,
        type_code=str(raw.get("deviceTypeCode") or "").upper(),
        model=str(raw.get("modelID") or ""),
        user_id=str(user_id) if user_id is not None else None,
        raw=raw,
    )


def create_session(
    cookies: dict[str, str],
    base_url: URL = BASE_URL,
) -> aiohttp.ClientSession:
    """Create a dedicated session whose jar holds only this account's cookies."""
    session = aiohttp.ClientSession(
        # unsafe=True only matters for IP hosts (the test server).
        cookie_jar=aiohttp.CookieJar(unsafe=True),
        timeout=aiohttp.ClientTimeout(total=30),
        headers={"User-Agent": _USER_AGENT},
    )
    session.cookie_jar.update_cookies(cookies, response_url=base_url)
    return session


class StfClient:
    """Stateless wrapper around one authenticated web session.

    The CSRF token is cached and refreshed once when a request is rejected.
    Retries and back-off beyond that belong to the caller.
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        base_url: URL = BASE_URL,
    ) -> None:
        self._session = session
        self._base_url = base_url
        self._csrf: str | None = None

    @property
    def session(self) -> aiohttp.ClientSession:
        return self._session

    def cookies(self) -> dict[str, str]:
        """Current cookies in the jar, as Samsung has rotated them."""
        return {
            name: morsel.value
            for name, morsel in self._session.cookie_jar.filter_cookies(
                self._base_url
            ).items()
            if _COOKIE_NAME_RE.match(name)
        }

    async def check_login(self) -> str:
        """Validate the session and return a fresh CSRF token."""
        self._csrf = None
        try:
            async with self._session.get(self._base_url / PATH_CHK_LOGIN) as resp:
                body = (await resp.text()).strip()
                csrf = resp.headers.get("_csrf")
                status = resp.status
        except (aiohttp.ClientError, TimeoutError) as err:
            raise StfConnectionError(f"chkLogin.do: {type(err).__name__}") from err

        if status == 429 or status >= 500:
            raise StfConnectionError(f"chkLogin.do: HTTP {status}")
        if status in (401, 403) or body in SESSION_EXPIRED_BODIES or not csrf:
            raise StfAuthError(f"chkLogin.do rejected the session (HTTP {status})")
        if status != 200:
            raise StfError(f"chkLogin.do: HTTP {status}")
        self._csrf = csrf
        return csrf

    async def _post(self, path: str, payload: dict[str, Any], *, retry: bool) -> Any:
        """POST JSON with the CSRF token; refresh the token once on rejection."""
        if self._csrf is None:
            await self.check_login()
        try:
            return await self._post_once(path, payload)
        except StfAuthError:
            if not retry:
                raise
        # The CSRF token may simply have rotated; a failing chkLogin.do
        # here means the session itself is gone.
        await self.check_login()
        return await self._post_once(path, payload)

    async def _post_once(self, path: str, payload: dict[str, Any]) -> Any:
        url = (self._base_url / path).update_query({"_csrf": self._csrf or ""})
        try:
            async with self._session.post(
                url, json=payload, headers={"Accept": "application/json"}
            ) as resp:
                body = await resp.text()
                status = resp.status
        except (aiohttp.ClientError, TimeoutError) as err:
            raise StfConnectionError(f"{path}: {type(err).__name__}") from err

        stripped = body.strip()
        if stripped in SESSION_EXPIRED_BODIES or status in (401, 403):
            self._csrf = None
            raise StfAuthError(f"{path} rejected the session (HTTP {status})")
        if status == 404:
            raise StfDeviceNotFoundError(f"{path}: HTTP 404")
        if status == 429 or status >= 500:
            raise StfConnectionError(f"{path}: HTTP {status}")
        if status != 200:
            raise StfError(f"{path}: HTTP {status}")
        if not stripped:
            return {}
        try:
            return json.loads(stripped)
        except json.JSONDecodeError as err:
            raise StfError(f"{path}: invalid JSON") from err

    async def get_devices(self) -> list[StfDevice]:
        """Return the account's own devices (``deviceList``).

        ``childrenDeviceList`` (family members) is ignored for now.
        """
        payload = await self._post(PATH_DEVICE_LIST, {}, retry=True)
        raw_devices = payload.get("deviceList") if isinstance(payload, dict) else None
        if not isinstance(raw_devices, list):
            raise StfError("getDeviceList.do: unexpected response shape")
        devices = []
        for raw in raw_devices:
            if isinstance(raw, dict) and (device := _parse_device(raw)):
                devices.append(device)
        return devices

    async def get_snapshot(self, device_id: str) -> dict[str, Any]:
        """Last known operations for a device (``setLastSelect.do``)."""
        payload = await self._post(
            PATH_SET_LAST_SELECT,
            {"dvceId": device_id, "removeDevice": []},
            retry=True,
        )
        return payload if isinstance(payload, dict) else {}

    async def add_operation(
        self,
        device: StfDevice,
        operation: str,
        **extra: Any,
    ) -> None:
        """Send a command to a device.

        Never retried automatically: the server may have accepted it already,
        and a duplicate ring or wake-up is worse than a reported failure.
        """
        payload = {
            "dvceId": device.device_id,
            "operation": operation,
            "usrId": device.user_id,
            **extra,
        }
        await self._post(PATH_ADD_OPERATION, payload, retry=False)

    async def get_operation_result(
        self,
        device: StfDevice,
        operations: list[str],
    ) -> dict[str, Any]:
        """Poll the result of operations sent with :meth:`add_operation`."""
        payload = await self._post(
            PATH_OPERATION_RESULT,
            {
                "dvceId": device.device_id,
                "operation": operations,
                "userId": device.user_id,
            },
            retry=True,
        )
        return payload if isinstance(payload, dict) else {}

    async def get_tag_location(
        self,
        device: StfDevice,
        latest_time: datetime | None = None,
    ) -> dict[str, Any]:
        """Current location of a SmartTag, newer than ``latest_time``."""
        latest = latest_time.strftime("%Y%m%d%H%M%S") if latest_time else "00000000"
        payload = await self._post(
            PATH_TAG_LOCATION,
            {"dvceId": device.device_id, "latestTime": latest},
            retry=True,
        )
        return payload if isinstance(payload, dict) else {}
