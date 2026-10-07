"""Round trip of the persisted device state."""

from __future__ import annotations

from datetime import datetime, timezone

import api
import models

T1 = datetime(2026, 9, 29, 22, 14, 42, tzinfo=timezone.utc)
T2 = datetime(2026, 9, 29, 22, 29, 11, tzinfo=timezone.utc)


def test_state_round_trip() -> None:
    device = api.StfDevice("738", "Watch", "WEARABLE", "SM-L310", "u1", {"x": 1})
    state = models.DeviceState(device=device, battery=45, battery_at=T1)
    state.location = api.StfLocation(
        41.5, -8.4, 11.7, T1, "LOCATION", "basic", "wifi", "aa:bb"
    )
    state.connection = api.StfOperationStatus(
        "CHECK_CONNECTION", "2800", "1200", T1, T1, True
    )
    state.last_operation = api.StfOperationStatus("LOCATION", "2800", "1200", T1, T1)
    state.lock = api.StfLockStatus(locked=True, remote_locked=False, checked_at=T2)
    state.request = "ok"
    state.auto_location = True

    data = models.state_to_store(state)
    restored = models.state_from_store("738", data)

    assert restored.served is False  # until Samsung returns it again
    assert restored.device.name == "Watch" and restored.device.type_code == "WEARABLE"
    assert restored.battery == 45 and restored.battery_at == T1
    assert restored.location == state.location
    assert restored.connection == state.connection
    assert restored.last_operation == state.last_operation
    assert restored.lock == state.lock
    assert restored.request is None
    assert restored.auto_location is True


def test_old_store_entries_without_state() -> None:
    # Store written by 2.0.x (device fields only) or damaged values.
    restored = models.state_from_store(
        "1", {"name": "Tag", "type_code": "TAG", "location": {"latitude": 1}}
    )
    assert restored.device.is_tag
    assert restored.battery is None and restored.location is None
    assert restored.auto_location is None  # follows the mode in the options
