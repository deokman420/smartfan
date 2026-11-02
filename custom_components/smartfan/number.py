"""Every tunable threshold the firmware will accept over the wire.

The table mirrors settings.py's SPECS, and tests/test_integration.py asserts
it still does. Bounds are repeated here because Home Assistant needs min, max
and step to render a box at all -- but they are not the authority. The device
clamps, and it may move a paired setting out of the way: writing RH_RISE_ON
below RH_FALL_OFF pushes the other one down rather than refusing. So nothing
here pre-validates, and the coordinator takes the `cfg` that comes back as
the truth.
"""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.number import (
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import SmartfanConfigEntry
from .entity import SmartfanEntity

TICKS = "ticks"


@dataclass(frozen=True, kw_only=True)
class SmartfanNumberDescription(NumberEntityDescription):
    setting: str


NUMBERS: tuple[SmartfanNumberDescription, ...] = (
    SmartfanNumberDescription(
        key="humidity_rise_to_start",
        setting="RH_RISE_ON",
        native_min_value=3.0,
        native_max_value=20.0,
        native_step=0.5,
        native_unit_of_measurement=PERCENTAGE,
        mode=NumberMode.BOX,
    ),
    SmartfanNumberDescription(
        key="humidity_fall_to_stop",
        setting="RH_FALL_OFF",
        native_min_value=1.0,
        native_max_value=10.0,
        native_step=0.5,
        native_unit_of_measurement=PERCENTAGE,
        mode=NumberMode.BOX,
    ),
    # 0 means "never start the fan on odor", which is what the path shipped
    # at for its observe-only week. The ceiling is 1000 rather than 300
    # because the first measured event peaked at +3972 against a noise floor
    # near 30 -- a cap you are sitting on is not a cap.
    SmartfanNumberDescription(
        key="odor_rise_to_start",
        setting="VOC_RISE_ON",
        native_min_value=0,
        native_max_value=1000,
        native_step=5,
        native_unit_of_measurement=TICKS,
        mode=NumberMode.BOX,
    ),
    SmartfanNumberDescription(
        key="odor_fall_to_stop",
        setting="VOC_FALL_OFF",
        native_min_value=0,
        native_max_value=1000,
        native_step=5,
        native_unit_of_measurement=TICKS,
        mode=NumberMode.BOX,
    ),
    SmartfanNumberDescription(
        key="light_switch_step",
        setting="LIGHT_STEP_ON",
        native_min_value=2000,
        native_max_value=40000,
        native_step=500,
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
    ),
    SmartfanNumberDescription(
        key="light_rise_to_start",
        setting="LIGHT_RISE_ON",
        native_min_value=1000,
        native_max_value=40000,
        native_step=500,
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
    ),
    SmartfanNumberDescription(
        key="light_rise_to_stop",
        setting="LIGHT_RISE_OFF",
        native_min_value=500,
        native_max_value=39000,
        native_step=500,
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
    ),
    SmartfanNumberDescription(
        key="light_only_run",
        setting="LIGHT_ONLY_MAX_S",
        native_min_value=60,
        native_max_value=3600,
        native_step=60,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        mode=NumberMode.BOX,
    ),
    SmartfanNumberDescription(
        key="minimum_run",
        setting="MIN_RUN_S",
        native_min_value=30,
        native_max_value=900,
        native_step=10,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        mode=NumberMode.BOX,
    ),
    SmartfanNumberDescription(
        key="maximum_run",
        setting="MAX_RUN_S",
        native_min_value=300,
        native_max_value=7200,
        native_step=60,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        mode=NumberMode.BOX,
    ),
    SmartfanNumberDescription(
        key="run_after_lights_out",
        setting="POST_LIGHT_S",
        native_min_value=0,
        native_max_value=900,
        native_step=10,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        mode=NumberMode.BOX,
    ),
    SmartfanNumberDescription(
        key="boost_duration",
        setting="BOOST_S",
        native_min_value=60,
        native_max_value=3600,
        native_step=60,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        mode=NumberMode.BOX,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SmartfanConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        SmartfanNumber(coordinator, description) for description in NUMBERS
    )


class SmartfanNumber(SmartfanEntity, NumberEntity):
    entity_description: SmartfanNumberDescription

    def __init__(self, coordinator, description: SmartfanNumberDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> float | None:
        raw = self.setting(self.entity_description.setting)
        return None if raw is None else float(raw)

    async def async_set_native_value(self, value: float) -> None:
        # Integers as integers. The firmware rounds anything whose config.py
        # default is an int, but sending "180.0" for a seconds field and
        # reading back 180 makes every log line and every test harder to
        # match than it needs to be.
        sent: float | int = value
        if float(value).is_integer() and self.entity_description.native_step >= 1:
            sent = int(value)
        await self.coordinator.async_set_setting(
            self.entity_description.setting, sent
        )
