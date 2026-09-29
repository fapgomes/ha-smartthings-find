"""Polling, session upkeep and device state for one SmartThings Find account."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import logging
from typing import Any, TypeVar

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryNotReady,
    HomeAssistantError,
)
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    BASE_URL,
    OP_RING,
    StfAuthError,
    StfClient,
    StfDevice,
    StfDeviceNotFoundError,
    StfDeviceReport,
    StfError,
    create_session,
    format_cookie_header,
    location_request_operations,
    parse_cookie_header,
    parse_menu,
    parse_response_report,
    result_query_operations,
)
from .const import (
    AUTH_FAILURES_BEFORE_REAUTH,
    CONF_ACTIVE_MODE_OTHERS,
    CONF_ACTIVE_MODE_SMARTTAGS,
    CONF_COOKIE,
    CONF_KEEPALIVE_INTERVAL,
    CONF_UPDATE_INTERVAL,
    DEFAULT_ACTIVE_MODE_OTHERS,
    DEFAULT_ACTIVE_MODE_SMARTTAGS,
    DEFAULT_KEEPALIVE_INTERVAL,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
    LOCATION_RESULT_POLL_DELAYS,
    MIN_KEEPALIVE_INTERVAL,
    MIN_UPDATE_INTERVAL,
    REQUEST_FAILED,
    REQUEST_OK,
    REQUEST_PENDING,
    REQUEST_TIMEOUT,
    RING_MESSAGE,
    SETUP_AUTH_RETRY_DELAYS,
    SIGNAL_NEW_DEVICES,
)
from .models import DeviceState, device_from_store, state_from_store, state_to_store
from .store import SessionStore

_LOGGER = logging.getLogger(__name__)

_T = TypeVar("_T")

type StfConfigEntry = ConfigEntry[StfCoordinator]


def _int_option(entry: ConfigEntry, key: str, default: int, minimum: int) -> int:
    try:
        return max(minimum, int(entry.options.get(key, default)))
    except (TypeError, ValueError):
        return default


class StfCoordinator(DataUpdateCoordinator[dict[str, DeviceState]]):
    """Own the web session, poll every device and run device commands."""

    config_entry: StfConfigEntry

    def __init__(self, hass: HomeAssistant, entry: StfConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            config_entry=entry,
            update_interval=timedelta(
                seconds=_int_option(
                    entry,
                    CONF_UPDATE_INTERVAL,
                    DEFAULT_UPDATE_INTERVAL,
                    MIN_UPDATE_INTERVAL,
                )
            ),
        )
        self._keepalive_interval = _int_option(
            entry,
            CONF_KEEPALIVE_INTERVAL,
            DEFAULT_KEEPALIVE_INTERVAL,
            MIN_KEEPALIVE_INTERVAL,
        )
        self._active_tags = bool(
            entry.options.get(CONF_ACTIVE_MODE_SMARTTAGS, DEFAULT_ACTIVE_MODE_SMARTTAGS)
        )
        self._active_others = bool(
            entry.options.get(CONF_ACTIVE_MODE_OTHERS, DEFAULT_ACTIVE_MODE_OTHERS)
        )
        self._store = SessionStore(hass, entry.entry_id)
        self._client: StfClient | None = None
        # Every request goes through this lock: the CSRF token and the cookie
        # jar are shared and rotate.
        self._lock = asyncio.Lock()
        self._states: dict[str, DeviceState] = {}
        self._auth_failures = 0
        self._last_request = datetime.min.replace(tzinfo=timezone.utc)
        self._degraded_reported = False
        self._location_tasks: dict[str, asyncio.Task[None]] = {}

    # ------------------------------------------------------------------
    # Setup and teardown

    @property
    def _configured_cookie(self) -> str:
        return str(self.config_entry.data.get(CONF_COOKIE) or "").strip()

    async def async_setup(self) -> None:
        """Restore the session and the known devices, then validate the session."""
        await self._store.async_load()
        for device_id, data in self._store.devices.items():
            self._states[device_id] = state_from_store(device_id, data)
        # Installations upgraded from upstream have no device list stored yet;
        # the device registry still remembers them.
        registry = dr.async_get(self.hass)
        for entry in dr.async_entries_for_config_entry(
            registry, self.config_entry.entry_id
        ):
            for domain, device_id in entry.identifiers:
                if domain == DOMAIN and device_id not in self._states:
                    self._states[device_id] = DeviceState(
                        device=device_from_store(
                            device_id, {"name": entry.name, "model": entry.model}
                        ),
                        served=False,
                    )

        candidates = [self._store.rotated_cookie(self._configured_cookie)]
        candidates.append(self._configured_cookie)
        last_error: StfError | None = None
        for cookie_line in dict.fromkeys(c for c in candidates if c):
            cookies = parse_cookie_header(cookie_line)
            if not cookies:
                continue
            session = create_session(cookies)
            client = StfClient(session)
            try:
                await self._check_login_with_retries(client)
            except StfError as err:
                await session.close()
                last_error = err
                if isinstance(err, StfAuthError):
                    continue
                raise ConfigEntryNotReady(str(err)) from err
            self._client = client
            return

        if last_error is None:
            raise ConfigEntryAuthFailed("No SmartThings Find cookie configured")
        raise ConfigEntryAuthFailed(str(last_error)) from last_error

    @staticmethod
    async def _check_login_with_retries(client: StfClient) -> None:
        """A single ``Logout`` answer is occasionally transient."""
        for delay in SETUP_AUTH_RETRY_DELAYS:
            try:
                await client.check_login()
                return
            except StfAuthError:
                await asyncio.sleep(delay)
        await client.check_login()

    async def async_config_entry_first_refresh(self) -> None:
        await super().async_config_entry_first_refresh()
        # Only needed when polling is slower than the session's idle timeout.
        if self._keepalive_interval < self.update_interval.total_seconds():
            self.config_entry.async_on_unload(
                async_track_time_interval(
                    self.hass,
                    self._async_keepalive,
                    timedelta(seconds=self._keepalive_interval),
                    name=f"{DOMAIN} keepalive",
                )
            )

    async def async_shutdown(self) -> None:
        for task in self._location_tasks.values():
            task.cancel()
        self._location_tasks.clear()
        await super().async_shutdown()
        if self._client is not None:
            await self._client.session.close()
            self._client = None

    async def async_remove_store(self) -> None:
        await self._store.async_remove()

    # ------------------------------------------------------------------
    # Requests

    async def _request(self, call: Callable[[StfClient], Awaitable[_T]]) -> _T:
        if self._client is None:
            raise StfAuthError("No session")
        async with self._lock:
            result = await call(self._client)
            self._last_request = datetime.now(timezone.utc)
            return result

    async def _persist(self) -> None:
        if self._client is None:
            return
        await self._store.async_save(
            configured_cookie=self._configured_cookie,
            cookie=format_cookie_header(self._client.cookies()),
            devices={
                device_id: state_to_store(state)
                for device_id, state in self._states.items()
            },
        )

    def _auth_failed(self, err: StfAuthError) -> Exception:
        """Count a rejected session; ask for a new cookie once it persists."""
        self._auth_failures += 1
        if self._auth_failures >= AUTH_FAILURES_BEFORE_REAUTH:
            return ConfigEntryAuthFailed(
                f"SmartThings Find session expired: {err}"
            )
        _LOGGER.warning(
            "SmartThings Find rejected the session (%s/%s): %s",
            self._auth_failures,
            AUTH_FAILURES_BEFORE_REAUTH,
            err,
        )
        return UpdateFailed(f"SmartThings Find rejected the session: {err}")

    async def _async_keepalive(self, _now: datetime) -> None:
        idle = datetime.now(timezone.utc) - self._last_request
        if idle.total_seconds() < self._keepalive_interval * 0.9:
            return
        try:
            await self._request(lambda client: client.check_login())
        except StfError as err:
            _LOGGER.debug("Keepalive failed: %s", err)

    # ------------------------------------------------------------------
    # Polling

    def _is_active(self, device: StfDevice) -> bool:
        return self._active_tags if device.is_tag else self._active_others

    async def _async_update_data(self) -> dict[str, DeviceState]:
        try:
            devices = await self._request(lambda client: client.get_devices())
        except StfAuthError as err:
            raise self._auth_failed(err) from err
        except StfError as err:
            raise UpdateFailed(str(err)) from err
        self._auth_failures = 0

        new_ids = self._merge_device_list(devices)
        registry = dr.async_get(self.hass)

        for device_id, state in self._states.items():
            if not state.served:
                continue
            device_entry = self._registry_device(registry, device_id)
            if device_entry is not None and device_entry.disabled:
                continue
            device = state.device
            if self._is_active(device):
                self._start_location_request(device.device_id)
            try:
                snapshot = await self._request(
                    lambda client, d=device: client.get_snapshot(d.device_id)
                )
            except StfDeviceNotFoundError:
                state.served = False
                continue
            except StfAuthError as err:
                raise self._auth_failed(err) from err
            except StfError as err:
                # Keep the last known values; the next cycle retries.
                _LOGGER.debug("Snapshot for %s failed: %s", device.name, err)
                continue
            state.raw["snapshot"] = snapshot
            self._merge_report(state, parse_response_report(snapshot))

        self._check_degraded()
        await self._persist()
        if new_ids:
            async_dispatcher_send(
                self.hass,
                SIGNAL_NEW_DEVICES.format(entry_id=self.config_entry.entry_id),
                new_ids,
            )
        return dict(self._states)

    def _registry_device(
        self, registry: dr.DeviceRegistry, device_id: str
    ) -> dr.DeviceEntry | None:
        """This entry's registry device for a SmartThings Find device id."""
        get_by_identifier = getattr(registry, "async_get_device_by_identifier", None)
        if get_by_identifier is not None:
            return get_by_identifier((DOMAIN, device_id), self.config_entry.entry_id)
        # Home Assistant releases before identifiers became per-entry.
        return registry.async_get_device(identifiers={(DOMAIN, device_id)})

    def _merge_device_list(self, devices: list[StfDevice]) -> list[str]:
        """Update served flags; return the ids of devices seen for the first time."""
        served = {device.device_id: device for device in devices}
        new_ids = []
        for device_id, device in served.items():
            state = self._states.get(device_id)
            if state is None:
                self._states[device_id] = DeviceState(device=device)
                new_ids.append(device_id)
            else:
                state.device = device
                state.served = True
        for device_id, state in self._states.items():
            if device_id not in served:
                state.served = False
        return new_ids

    def _check_degraded(self) -> None:
        """Ask for a new cookie when known devices stop being served.

        Samsung sometimes keeps accepting a session but stops returning some
        devices (seen with SmartTags). The cookie is not rejected, so only a
        missing device reveals it. Devices disabled in Home Assistant are
        ignored; a device really removed from the account can be deleted
        from Home Assistant, which also forgets it here.
        """
        registry = dr.async_get(self.hass)
        missing = []
        for device_id, state in self._states.items():
            if state.served:
                continue
            entry = self._registry_device(registry, device_id)
            if entry is not None and entry.disabled:
                continue
            missing.append(state.device.name)

        if not missing:
            self._degraded_reported = False
            return
        if self._degraded_reported:
            return
        self._degraded_reported = True
        _LOGGER.warning(
            "SmartThings Find no longer returns %s for this session; "
            "the session is probably degraded, provide a new cookie",
            ", ".join(sorted(missing)),
        )
        self.config_entry.async_start_reauth(self.hass)

    @staticmethod
    def _merge_report(
        state: DeviceState, report: StfDeviceReport, *, fresh: bool = False
    ) -> None:
        """Apply newer values from a report.

        ``fresh`` marks an answer to a request just sent, so its battery is
        current even when the value did not change. Snapshots repeat cached
        values, so for them the time only moves when the value changes.
        """
        if report.battery is not None and (fresh or report.battery != state.battery):
            state.battery = report.battery
            state.battery_at = datetime.now(timezone.utc)
        location = report.location
        if location is not None and (
            state.location is None or location.reported_at > state.location.reported_at
        ):
            state.location = location
        if report.lock is not None and (
            state.lock is None or report.lock.checked_at > state.lock.checked_at
        ):
            state.lock = report.lock
        for attr in ("connection", "last_operation"):
            new = getattr(report, attr)
            old = getattr(state, attr)
            if new is None:
                continue
            # Same creation time: the same operation, keep the finished copy.
            if (
                old is None
                or new.created > old.created
                or (new.created == old.created and new.outcome != "pending")
            ):
                setattr(state, attr, new)

    # ------------------------------------------------------------------
    # Commands

    def _state(self, device_id: str) -> DeviceState:
        state = self._states.get(device_id)
        if state is None or not state.served:
            raise HomeAssistantError(
                f"SmartThings Find is not serving device {device_id}"
            )
        return state

    async def _command(self, call: Callable[[StfClient], Awaitable[None]]) -> None:
        try:
            await self._request(call)
        except StfAuthError as err:
            raise HomeAssistantError(
                "SmartThings Find rejected the session; the command was not sent"
            ) from err
        except StfError as err:
            raise HomeAssistantError(f"SmartThings Find command failed: {err}") from err

    async def async_ring(self, device_id: str, start: bool) -> None:
        device = self._state(device_id).device
        extra: dict[str, Any] = {"status": "start" if start else "stop"}
        if start:
            extra["lockMessage"] = RING_MESSAGE
        await self._command(
            lambda client: client.add_operation(device, OP_RING, **extra)
        )

    async def async_request_location(self, device_id: str) -> None:
        """Wake the device and collect the answer in the background."""
        self._state(device_id)
        if device_id in self._location_tasks:
            return
        await self._send_location_request(device_id)

    def _start_location_request(self, device_id: str) -> None:
        """Active mode: same as the button, without surfacing errors."""
        if device_id in self._location_tasks:
            return

        async def _run() -> None:
            try:
                await self._send_location_request(device_id)
            except HomeAssistantError as err:
                _LOGGER.debug("Active location request failed: %s", err)

        self.config_entry.async_create_background_task(
            self.hass, _run(), f"{DOMAIN} active location {device_id}"
        )

    async def _send_location_request(self, device_id: str) -> None:
        state = self._state(device_id)
        device = state.device
        # Answers are recognised by their creation time; allow for clock skew.
        sent_at = datetime.now(timezone.utc) - timedelta(minutes=2)
        sent: list[str] = []
        for operation in location_request_operations(device):
            try:
                await self._command(
                    lambda client, op=operation: client.add_operation(device, op)
                )
            except HomeAssistantError:
                if not sent:
                    state.request = REQUEST_FAILED
                    self.async_update_listeners()
                    raise
                break
            sent.append(operation)
        state.request = REQUEST_PENDING
        self.async_update_listeners()
        task = self.config_entry.async_create_background_task(
            self.hass,
            self._collect_location(state, sent, sent_at),
            f"{DOMAIN} location result {device_id}",
        )
        self._location_tasks[device_id] = task
        task.add_done_callback(lambda _t: self._location_tasks.pop(device_id, None))

    async def _collect_location(
        self, state: DeviceState, operations: list[str], sent_at: datetime
    ) -> None:
        """Poll each operation's result (and, for tags, their location)."""
        device = state.device
        previous = state.location.reported_at if state.location else None
        pending = list(operations)

        for delay in LOCATION_RESULT_POLL_DELAYS:
            await asyncio.sleep(delay)
            for operation in list(pending):
                queried = result_query_operations(device, operation)
                try:
                    result = await self._request(
                        lambda client, q=queried: client.get_operation_result(
                            device, q
                        )
                    )
                except StfError as err:
                    _LOGGER.debug("Result of %s for %s failed: %s", operation, device.name, err)
                    continue
                state.raw[f"operation_result_{operation}"] = result
                report = parse_response_report(result)
                fresh = any(report.answered_since(op, sent_at) for op in queried)
                self._merge_report(state, report, fresh=fresh)
                if fresh:
                    pending.remove(operation)

            if device.is_tag and pending:
                try:
                    tag = await self._request(
                        lambda client: client.get_tag_location(device, previous)
                    )
                except StfError as err:
                    _LOGGER.debug("Tag location for %s failed: %s", device.name, err)
                else:
                    state.raw["tag_location"] = tag
                    report = parse_response_report(tag)
                    self._merge_report(state, report)
                    if report.location is not None and (
                        previous is None or report.location.reported_at > previous
                    ):
                        pending.clear()

            if not pending:
                state.request = REQUEST_OK
                self.async_set_updated_data(dict(self._states))
                return
            self.async_update_listeners()

        state.request = REQUEST_TIMEOUT
        self.async_update_listeners()
        await self.async_request_refresh()

    # ------------------------------------------------------------------
    # Helpers for entities, device removal and diagnostics

    @callback
    def device_ids(self) -> list[str]:
        return list(self._states)

    @callback
    def device_info(self, device_id: str) -> DeviceInfo:
        device = self._states[device_id].device
        return DeviceInfo(
            identifiers={(DOMAIN, device_id)},
            manufacturer="Samsung",
            name=device.name,
            model=device.model or None,
            configuration_url=str(BASE_URL),
        )

    def supports(self, device_id: str, feature: str) -> bool:
        """Whether the server offers ``feature`` (``ring``, ``ringStop``,
        ``location``...) for the device; assume yes until a snapshot says."""
        state = self._states.get(device_id)
        if state is None:
            return True
        return parse_menu(state.raw.get("snapshot")).get(feature, True)

    def is_served(self, device_id: str) -> bool:
        state = self._states.get(device_id)
        return state is not None and state.served

    async def async_forget_device(self, device_id: str) -> None:
        self._states.pop(device_id, None)
        await self._persist()

    def diagnostics(self) -> dict[str, Any]:
        return {
            "auth_failures": self._auth_failures,
            "degraded_reported": self._degraded_reported,
            "last_request": self._last_request.isoformat(),
            "devices": {
                device_id: {
                    "name": state.device.name,
                    "type_code": state.device.type_code,
                    "model": state.device.model,
                    "served": state.served,
                    "battery": state.battery,
                    "battery_at": state.battery_at,
                    "location_at": state.location.reported_at if state.location else None,
                    "location_operation": (
                        state.location.operation if state.location else None
                    ),
                    "request": state.request,
                    "connection": asdict(state.connection) if state.connection else None,
                    "lock": asdict(state.lock) if state.lock else None,
                    "last_operation": (
                        asdict(state.last_operation) if state.last_operation else None
                    ),
                    "raw": state.raw,
                }
                for device_id, state in self._states.items()
            },
        }
