"""Base entity and platform helper for SmartThings Find."""

from __future__ import annotations

from collections.abc import Callable, Iterable

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import SIGNAL_NEW_DEVICES
from .coordinator import DeviceState, StfConfigEntry, StfCoordinator


class StfEntity(CoordinatorEntity[StfCoordinator]):
    """An entity bound to one SmartThings Find device."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: StfCoordinator, device_id: str) -> None:
        super().__init__(coordinator)
        self._device_id = device_id
        self._attr_device_info = coordinator.device_info(device_id)

    @property
    def state_data(self) -> DeviceState | None:
        return (self.coordinator.data or {}).get(self._device_id)

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.is_served(self._device_id)


def async_setup_device_entities(
    hass: HomeAssistant,
    entry: StfConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
    factory: Callable[[StfCoordinator, str], Iterable[Entity]],
) -> None:
    """Add entities for every known device, and for devices that appear later."""
    coordinator = entry.runtime_data
    added: set[str] = set()

    @callback
    def _add(device_ids: Iterable[str]) -> None:
        entities: list[Entity] = []
        for device_id in device_ids:
            if device_id in added:
                continue
            added.add(device_id)
            entities.extend(factory(coordinator, device_id))
        if entities:
            async_add_entities(entities)

    _add(coordinator.device_ids())
    entry.async_on_unload(
        async_dispatcher_connect(
            hass, SIGNAL_NEW_DEVICES.format(entry_id=entry.entry_id), _add
        )
    )
