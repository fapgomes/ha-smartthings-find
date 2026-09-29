"""Diagnostics for SmartThings Find.

The raw responses are included on purpose: the shapes of
getOperationResult.do and getTagLocation.do are not documented, and a
diagnostics download is the safe way to inspect them without reusing the
session elsewhere.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .const import CONF_COOKIE
from .coordinator import StfConfigEntry

TO_REDACT = {
    CONF_COOKIE,
    "usrId",
    "userId",
    "user_id",
    "latitude",
    "longitude",
    "encLocation",
    "mqttUrl",
    "mqttClientId",
    "mqttWillTopic",
    "mquserName",
    "mquserCode",
    "topic",
    "imei",
    "serialNumber",
    "phoneNumber",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: StfConfigEntry
) -> dict[str, Any]:
    return async_redact_data(
        {
            "entry": {"data": dict(entry.data), "options": dict(entry.options)},
            "coordinator": entry.runtime_data.diagnostics(),
        },
        TO_REDACT,
    )
