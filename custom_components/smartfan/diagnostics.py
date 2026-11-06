"""What to attach to a bug report.

The whole of /api/state and /api/info, because on this device almost every
field is a diagnostic: the fault reason, the boot cause, the learned
baselines, the reconnect count and the raw sensor ticks are exactly what any
question about its behavior turns on.

Only the network identifiers are redacted. They say where the device and its
broker live, which is nothing a stranger reading a GitHub issue needs.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .api import SmartfanError
from .const import CONF_HOST
from .coordinator import SmartfanConfigEntry

REDACT = {"ip", "mqtt_host", "hostname", CONF_HOST}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: SmartfanConfigEntry
) -> dict[str, Any]:
    coordinator = entry.runtime_data

    # Unchecked, so a device running firmware too new for this integration
    # still reports what version it speaks -- which is the one thing worth
    # knowing when that is the complaint.
    try:
        info: Any = await coordinator.client.info_unchecked()
    except SmartfanError as err:
        info = {"error": str(err)}

    return {
        "entry": async_redact_data(dict(entry.data), REDACT),
        "info": async_redact_data(info, REDACT) if isinstance(info, dict) else info,
        "state": async_redact_data(coordinator.data or {}, REDACT),
        "update_success": coordinator.last_update_success,
    }
