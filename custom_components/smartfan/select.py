"""Auto, Force off, Force on.

The one control that overrides the control loop, so it is the one worth
understanding: Force on runs the fan and ignores every sensed reason to
stop, Force off stops it and ignores every reason to start, and Auto hands
the room back to the firmware. A forced run does not spend the MAX_RUN_S
cap, because an override is an instruction rather than an event.
"""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import SmartfanConfigEntry
from .entity import SmartfanEntity

# Mirrors settings.MODES, asserted by tests/test_integration.py. The strings
# are the firmware's own, because /api/set/MODE matches them case-insensitively
# and sends back the canonical spelling.
MODES = ("Auto", "Force off", "Force on")


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SmartfanConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([SmartfanModeSelect(entry.runtime_data)])


class SmartfanModeSelect(SmartfanEntity, SelectEntity):
    _attr_translation_key = "mode"
    _attr_options = list(MODES)

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "mode")

    @property
    def current_option(self) -> str | None:
        raw = self.setting("MODE")
        return str(raw) if raw in MODES else None

    async def async_select_option(self, option: str) -> None:
        await self.coordinator.async_set_setting("MODE", option)
