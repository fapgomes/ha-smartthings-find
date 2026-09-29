"""Private storage for the rotated session cookie and the known devices.

The file keeps upstream's key (``smartthings_find.<entry_id>.session``) and
its ``source_hash``/``cookie`` fields, so an existing installation's rotated
session is reused. ``devices`` is new: it remembers every device ever served,
which is how a device silently dropped by Samsung is noticed after a restart.
"""

from __future__ import annotations

from hashlib import sha256
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import AUTH_METHOD_COOKIE, DOMAIN

STORAGE_VERSION = 1


def _source_hash(configured_cookie: str) -> str:
    """Identify which configured cookie a rotated snapshot descends from."""
    return sha256(f"{AUTH_METHOD_COOKIE}:{configured_cookie}".encode()).hexdigest()


class SessionStore:
    """Load and save one entry's private session state."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self._store: Store[dict[str, Any]] = Store(
            hass,
            STORAGE_VERSION,
            f"{DOMAIN}.{entry_id}.session",
            private=True,
            atomic_writes=True,
        )
        self._data: dict[str, Any] = {}

    async def async_load(self) -> None:
        data = await self._store.async_load()
        self._data = data if isinstance(data, dict) else {}

    def rotated_cookie(self, configured_cookie: str) -> str | None:
        """The last rotated cookie, if it descends from ``configured_cookie``."""
        if self._data.get("source_hash") != _source_hash(configured_cookie):
            return None
        cookie = self._data.get("cookie")
        return cookie if isinstance(cookie, str) and cookie else None

    @property
    def devices(self) -> dict[str, dict[str, Any]]:
        devices = self._data.get("devices")
        return devices if isinstance(devices, dict) else {}

    async def async_save(
        self,
        *,
        configured_cookie: str,
        cookie: str | None = None,
        devices: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        data = dict(self._data)
        if cookie is not None:
            data["source_hash"] = _source_hash(configured_cookie)
            data["cookie"] = cookie
        if devices is not None:
            data["devices"] = devices
        if data != self._data:
            self._data = data
            await self._store.async_save(data)

    async def async_remove(self) -> None:
        self._data = {}
        await self._store.async_remove()
