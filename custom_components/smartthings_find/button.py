"""Ring and location buttons for SmartThings Find."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
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
        lambda coordinator, device_id: [
            StfRingButton(coordinator, device_id, start=True),
            StfRingButton(coordinator, device_id, start=False),
            StfUpdateLocationButton(coordinator, device_id),
        ],
    )


class StfRingButton(StfEntity, ButtonEntity):
    """Start or stop ringing the device."""

    def __init__(
        self, coordinator: StfCoordinator, device_id: str, *, start: bool
    ) -> None:
        super().__init__(coordinator, device_id)
        self._start = start
        # unique_id format inherited from upstream, kept for entity history.
        action = "start" if start else "stop"
        self._attr_unique_id = f"stf_ring_{action}_{device_id}"
        self._attr_translation_key = "ring" if start else "stop_ring"
        # SmartTags cannot be stopped remotely (menu ringStop=N).
        self._feature = "ring" if start else "ringStop"

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.supports(
            self._device_id, self._feature
        )

    async def async_press(self) -> None:
        await self.coordinator.async_ring(self._device_id, self._start)


class StfUpdateLocationButton(StfEntity, ButtonEntity):
    """Ask the device for its position and battery now."""

    _attr_translation_key = "update_location"

    def __init__(self, coordinator: StfCoordinator, device_id: str) -> None:
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"stf_update_location_{device_id}"

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.supports(
            self._device_id, "location"
        )

    async def async_press(self) -> None:
        await self.coordinator.async_request_location(self._device_id)
