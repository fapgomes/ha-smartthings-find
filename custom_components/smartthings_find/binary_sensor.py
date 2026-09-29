"""Connectivity binary sensor for SmartThings Find."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import StfConfigEntry, StfCoordinator
from .entity import StfEntity, async_setup_device_entities


async def async_setup_entry(
    hass: HomeAssistant,
    entry: StfConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_setup_device_entities(
        hass,
        entry,
        async_add_entities,
        lambda coordinator, device_id: [StfConnectedSensor(coordinator, device_id)],
    )


class StfConnectedSensor(StfEntity, BinarySensorEntity):
    """Whether the device answered its last connection check."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_translation_key = "connected"

    def __init__(self, coordinator: StfCoordinator, device_id: str) -> None:
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_connected"

    @property
    def is_on(self) -> bool | None:
        state = self.state_data
        return state.connection.connected if state and state.connection else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        state = self.state_data
        if not state or not state.connection:
            return {}
        checked = state.connection.done or state.connection.created
        return {"checked_at": checked.isoformat()}
