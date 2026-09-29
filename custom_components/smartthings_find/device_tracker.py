"""Device trackers for SmartThings Find."""

from __future__ import annotations

from typing import Any

from homeassistant.components.device_tracker import SourceType, TrackerEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import BASE_URL
from .coordinator import StfConfigEntry, StfCoordinator
from .entity import StfEntity, async_setup_device_entities

_ICON_BASE = f"{str(BASE_URL).rstrip('/')}/img/device_icon"

_TYPE_ICONS = {
    "PHONE": "phone",
    "PHONE DEVICE": "phone",
    "TAB": "tablet",
    "TAB DEVICE": "tablet",
    "PC": "laptop",
    "PC DEVICE": "laptop",
    "SPEN": "spen_pro",
    "VR": "vr",
    "AR": "ar",
}
_SUBTYPE_ICONS = {
    "BUDS": {
        "CANAL": "buds_pair",
        "CANAL2": "attic_pair",
        "CANAL3": "buds3_pair",
        "CANAL4": "buds4_pair",
        "OPEN": "bean_pair",
        "TWS_3RD_PARTY": "tws_pair",
    },
    "WATCH": {"WATCH": "watch", "FIT": "band", "RING": "ring"},
    "WEARABLE": {"WATCH": "watch", "FIT": "band", "RING": "ring"},
}
_SUBTYPE_DEFAULTS = {"BUDS": "buds_pair", "WATCH": "watch", "WEARABLE": "watch"}


def device_icon_url(raw: dict[str, Any]) -> str | None:
    """The picture the SmartThings Find website shows for a device."""
    type_code = str(raw.get("deviceTypeCode") or "").upper()
    if type_code == "TAG":
        icon = (raw.get("icons") or {}).get("coloredIcon")
        if isinstance(icon, str) and icon.startswith("http"):
            return icon
        if isinstance(icon, str) and icon.startswith("/"):
            return f"{str(BASE_URL).rstrip('/')}{icon}"
        return None
    if type_code in _SUBTYPE_ICONS:
        sub_type = str(raw.get("subType") or "").upper()
        name = _SUBTYPE_ICONS[type_code].get(sub_type, _SUBTYPE_DEFAULTS[type_code])
        return f"{_ICON_BASE}/{name}.svg"
    if type_code in _TYPE_ICONS:
        return f"{_ICON_BASE}/{_TYPE_ICONS[type_code]}.svg"
    return None


async def async_setup_entry(
    hass: HomeAssistant,
    entry: StfConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_setup_device_entities(
        hass,
        entry,
        async_add_entities,
        lambda coordinator, device_id: [StfTracker(coordinator, device_id)],
    )


class StfTracker(StfEntity, TrackerEntity):
    """Position of a device as last reported to SmartThings Find."""

    _attr_name = None
    _attr_source_type = SourceType.GPS

    def __init__(self, coordinator: StfCoordinator, device_id: str) -> None:
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_tracker"

    @property
    def entity_picture(self) -> str | None:
        state = self.state_data
        return device_icon_url(state.device.raw) if state else None

    @property
    def latitude(self) -> float | None:
        state = self.state_data
        return state.location.latitude if state and state.location else None

    @property
    def longitude(self) -> float | None:
        state = self.state_data
        return state.location.longitude if state and state.location else None

    @property
    def location_accuracy(self) -> float:
        state = self.state_data
        if state and state.location and state.location.accuracy is not None:
            return state.location.accuracy
        return 0

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        state = self.state_data
        if not state or not state.location:
            return {}
        return {
            "location_time": state.location.reported_at.isoformat(),
            "location_source": state.location.operation,
        }
