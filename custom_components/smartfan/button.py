"""Boost, cancel, relearn.

`clear` is deliberately absent. It clears a fault, and a fault on this
device means the fan did not turn or the climate sensor stopped answering --
something a person should look at rather than dismiss from a dashboard. The
firmware still accepts it over MQTT and from the button on the board.
"""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import SmartfanConfigEntry
from .entity import SmartfanEntity


@dataclass(frozen=True, kw_only=True)
class SmartfanButtonDescription(ButtonEntityDescription):
    command: str


BUTTONS: tuple[SmartfanButtonDescription, ...] = (
    SmartfanButtonDescription(key="boost", command="boost"),
    SmartfanButtonDescription(key="cancel_boost", command="cancel"),
    # Drops every learned baseline -- humidity, odor and NOx -- and reseeds
    # from the next quiet sample. The thing to reach for after the unit is
    # moved to a different room, or after a baseline was seeded while the
    # sensor element was still coming to temperature.
    SmartfanButtonDescription(key="relearn", command="relearn"),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SmartfanConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        SmartfanButton(coordinator, description) for description in BUTTONS
    )


class SmartfanButton(SmartfanEntity, ButtonEntity):
    entity_description: SmartfanButtonDescription

    def __init__(self, coordinator, description: SmartfanButtonDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    async def async_press(self) -> None:
        await self.coordinator.async_command(self.entity_description.command)
