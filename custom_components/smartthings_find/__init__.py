"""SmartThings Find integration."""

from __future__ import annotations

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.device_registry import DeviceEntry

from .account import AccountGrant
from .const import (
    AUTH_METHOD_ACCOUNT,
    AUTH_METHOD_COOKIE,
    CONF_ACCOUNT,
    CONF_AUTH_METHOD,
    DOMAIN,
)
from .coordinator import StfConfigEntry, StfCoordinator
from .store import SessionStore

PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.DEVICE_TRACKER,
    Platform.SENSOR,
    Platform.SWITCH,
]


async def async_setup_entry(hass: HomeAssistant, entry: StfConfigEntry) -> bool:
    method = entry.data.get(CONF_AUTH_METHOD, AUTH_METHOD_COOKIE)
    if method == AUTH_METHOD_ACCOUNT:
        # Upstream v1.4.0/1.4.1 account entries kept their grant elsewhere.
        if AccountGrant.from_dict(entry.data.get(CONF_ACCOUNT)) is None:
            raise ConfigEntryAuthFailed("Sign in again with the Samsung account")
    elif method != AUTH_METHOD_COOKIE:
        raise ConfigEntryAuthFailed(f"Unknown sign-in method {method!r}")

    coordinator = StfCoordinator(hass, entry)
    await coordinator.async_setup()
    try:
        await coordinator.async_config_entry_first_refresh()
    except Exception:
        await coordinator.async_shutdown()
        raise

    entry.runtime_data = coordinator
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _async_reload(hass: HomeAssistant, entry: StfConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: StfConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_shutdown()
    return unloaded


async def async_remove_entry(hass: HomeAssistant, entry: StfConfigEntry) -> None:
    """Delete the private session file with the entry."""
    await SessionStore(hass, entry.entry_id).async_remove()


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: StfConfigEntry, device: DeviceEntry
) -> bool:
    """Allow deleting devices Samsung no longer returns (and forget them)."""
    coordinator = entry.runtime_data
    device_ids = [i for d, i in device.identifiers if d == DOMAIN]
    if any(coordinator.is_served(device_id) for device_id in device_ids):
        return False
    for device_id in device_ids:
        await coordinator.async_forget_device(device_id)
    return True
