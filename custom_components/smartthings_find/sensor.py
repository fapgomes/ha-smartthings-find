"""Battery and last-update sensors for SmartThings Find."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import REQUEST_PENDING
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
        lambda coordinator, device_id: [
            StfBatterySensor(coordinator, device_id),
            StfLastUpdateSensor(coordinator, device_id),
        ],
    )


class StfBatterySensor(StfEntity, SensorEntity):
    """Battery level; SmartThings Find often reports coarse steps."""

    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: StfCoordinator, device_id: str) -> None:
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_battery"

    @property
    def native_value(self) -> int | None:
        state = self.state_data
        return state.battery if state else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        state = self.state_data
        if not state or not state.battery_at:
            return {}
        return {"battery_time": state.battery_at.isoformat()}


class StfLastUpdateSensor(StfEntity, SensorEntity):
    """When the device last reported its position to Samsung."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_translation_key = "last_update"

    def __init__(self, coordinator: StfCoordinator, device_id: str) -> None:
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_last_update"

    @property
    def native_value(self) -> datetime | None:
        state = self.state_data
        return state.location.reported_at if state and state.location else None

    @property
    def icon(self) -> str | None:
        state = self.state_data
        if state and state.request == REQUEST_PENDING:
            return "mdi:progress-clock"
        return "mdi:clock-outline"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        state = self.state_data
        return {"location_request": state.request} if state and state.request else {}
