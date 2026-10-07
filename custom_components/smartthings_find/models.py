"""Per-device state and its persistence format (no Home Assistant imports)."""

from __future__ import annotations

from dataclasses import MISSING, asdict, dataclass, field, fields
from datetime import datetime
from typing import Any

try:
    from .api import StfDevice, StfLocation, StfLockStatus, StfOperationStatus
except ImportError:  # imported as a top-level module by the tests
    from api import StfDevice, StfLocation, StfLockStatus, StfOperationStatus


@dataclass(slots=True)
class DeviceState:
    """Everything the entities show for one device."""

    device: StfDevice
    served: bool = True
    battery: int | None = None
    battery_at: datetime | None = None
    location: StfLocation | None = None
    connection: StfOperationStatus | None = None
    last_operation: StfOperationStatus | None = None
    lock: StfLockStatus | None = None
    request: str | None = None
    # Automatic location requests: None follows the default for the device type.
    auto_location: bool | None = None
    raw: dict[str, Any] = field(default_factory=dict)


def device_from_store(device_id: str, data: dict[str, Any]) -> StfDevice:
    return StfDevice(
        device_id=device_id,
        name=str(data.get("name") or device_id),
        type_code=str(data.get("type_code") or ""),
        model=str(data.get("model") or ""),
        user_id=data.get("user_id"),
        raw={},
    )


# Last known values survive restarts: Samsung's snapshot does not always
# repeat them (a watch's answer to a location request, for instance).
_SAVED_FIELDS: dict[str, type] = {
    "location": StfLocation,
    "connection": StfOperationStatus,
    "last_operation": StfOperationStatus,
    "lock": StfLockStatus,
}


def _to_json(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _to_json(item) for key, item in value.items()}
    return value


def _dataclass_from_json(cls: type, data: Any) -> Any:
    if not isinstance(data, dict):
        return None
    values = {}
    for item in fields(cls):
        value = data.get(item.name)
        if value is None:
            if item.default is MISSING:
                return None  # a required field is missing: discard
            continue
        if isinstance(value, str) and "datetime" in str(item.type):
            try:
                value = datetime.fromisoformat(value)
            except ValueError:
                return None
        values[item.name] = value
    try:
        return cls(**values)
    except TypeError:
        return None


def state_to_store(state: DeviceState) -> dict[str, Any]:
    device = state.device
    data: dict[str, Any] = {
        "name": device.name,
        "type_code": device.type_code,
        "model": device.model,
        "user_id": device.user_id,
        "battery": state.battery,
        "battery_at": _to_json(state.battery_at),
        "auto_location": state.auto_location,
    }
    for name in _SAVED_FIELDS:
        value = getattr(state, name)
        data[name] = _to_json(asdict(value)) if value is not None else None
    return data


def state_from_store(device_id: str, data: dict[str, Any]) -> DeviceState:
    state = DeviceState(device=device_from_store(device_id, data), served=False)
    battery = data.get("battery")
    state.battery = battery if isinstance(battery, int) else None
    if isinstance(data.get("battery_at"), str):
        try:
            state.battery_at = datetime.fromisoformat(data["battery_at"])
        except ValueError:
            state.battery = None
    auto_location = data.get("auto_location")
    state.auto_location = auto_location if isinstance(auto_location, bool) else None
    for name, cls in _SAVED_FIELDS.items():
        setattr(state, name, _dataclass_from_json(cls, data.get(name)))
    return state


