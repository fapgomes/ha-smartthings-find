"""Per-device automatic location requests for SmartThings Find."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
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
        lambda coordinator, device_id: [StfAutoLocationSwitch(coordinator, device_id)],
    )


class StfAutoLocationSwitch(StfEntity, SwitchEntity):
    """Ask the device for its position automatically (active mode)."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_translation_key = "auto_location"

    def __init__(self, coordinator: StfCoordinator, device_id: str) -> None:
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_auto_location"

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.supports(
            self._device_id, "location"
        )

    @property
    def is_on(self) -> bool:
        return self.coordinator.auto_location(self._device_id)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_auto_location(self._device_id, True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_auto_location(self._device_id, False)
