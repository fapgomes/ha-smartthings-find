"""SmartThings Find integration."""

from __future__ import annotations

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.device_registry import DeviceEntry

from .const import AUTH_METHOD_COOKIE, CONF_AUTH_METHOD, DOMAIN
from .coordinator import StfConfigEntry, StfCoordinator
from .store import SessionStore

PLATFORMS = [Platform.BUTTON, Platform.DEVICE_TRACKER, Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: StfConfigEntry) -> bool:
    if entry.data.get(CONF_AUTH_METHOD, AUTH_METHOD_COOKIE) != AUTH_METHOD_COOKIE:
        # Samsung Account entries from upstream v1.4.0/1.4.1 must move to a cookie.
        raise ConfigEntryAuthFailed("Only the Cookie header method is supported")

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
