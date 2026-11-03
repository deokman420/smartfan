"""Everything the controller measures or counts.

The descriptions mirror telemetry.py's `_SENSORS` table, and
tests/test_integration.py asserts that they still do -- a firmware that
grows a sensor and an integration that does not know about it is the drift
this project has been bitten by before, so it is a test rather than a
promise.

Static tuples rather than anything derived from the device at runtime,
because translations need keys that exist before the device is reachable:
`strings.json` is read by Home Assistant, not by the fan.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    REVOLUTIONS_PER_MINUTE,
    EntityCategory,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import SmartfanConfigEntry
from .entity import SmartfanEntity

TICKS = "ticks"


@dataclass(frozen=True, kw_only=True)
class SmartfanSensorDescription(SensorEntityDescription):
    """A sensor, plus where to find it in the snapshot."""

    field: str
    convert: Callable[[Any], Any] | None = None


SENSORS: tuple[SmartfanSensorDescription, ...] = (
    SmartfanSensorDescription(
        key="temperature",
        field="temp",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SmartfanSensorDescription(
        key="humidity",
        field="rh",
        device_class=SensorDeviceClass.HUMIDITY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SmartfanSensorDescription(
        key="dew_point",
        field="dp",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SmartfanSensorDescription(
        key="humidity_baseline",
        field="base",
        device_class=SensorDeviceClass.HUMIDITY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SmartfanSensorDescription(
        key="excess_humidity",
        field="exc",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SmartfanSensorDescription(
        key="light_level",
        field="light_pc",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    # --- the odor pair, and then the NOx pair ------------------------
    # Raw ticks and a learned baseline, not a concentration. There is no
    # absolute level that means bad air on a metal-oxide element, so the
    # fan acts on the gap and so should anyone reading these.
    SmartfanSensorDescription(
        key="odor_raw",
        field="voc",
        native_unit_of_measurement=TICKS,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SmartfanSensorDescription(
        key="odor_baseline",
        field="voc_base",
        native_unit_of_measurement=TICKS,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SmartfanSensorDescription(
        key="excess_odor",
        field="voc_exc",
        native_unit_of_measurement=TICKS,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    # The second pixel. Nothing in the firmware acts on it: the VOC sign was
    # measured on this part and the NOx sign has not been. It is published so
    # the two can be compared -- odor alone is this room, odor with NOx came
    # from a gas flame somewhere else.
    SmartfanSensorDescription(
        key="nox_raw",
        field="nox",
        native_unit_of_measurement=TICKS,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
    ),
    SmartfanSensorDescription(
        key="nox_baseline",
        field="nox_base",
        native_unit_of_measurement=TICKS,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
    ),
    SmartfanSensorDescription(
        key="excess_nox",
        field="nox_exc",
        native_unit_of_measurement=TICKS,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    # --- the fan and the loop ----------------------------------------
    SmartfanSensorDescription(
        key="fan_speed",
        field="rpm",
        native_unit_of_measurement=REVOLUTIONS_PER_MINUTE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SmartfanSensorDescription(
        key="controller_state",
        field="state",
        device_class=SensorDeviceClass.ENUM,
        options=["LEARN", "IDLE", "RUN", "BOOST", "FAULT"],
    ),
    SmartfanSensorDescription(
        key="runtime_today",
        field="run_min",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),
    SmartfanSensorDescription(
        key="boost_remaining",
        field="boost_left",
        native_unit_of_measurement=UnitOfTime.MINUTES,
    ),
    # --- diagnostics -------------------------------------------------
    SmartfanSensorDescription(
        key="wifi_signal",
        field="rssi",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        native_unit_of_measurement="dBm",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    SmartfanSensorDescription(
        key="uptime",
        field="up_min",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SmartfanSensorDescription(
        key="link_reconnects",
        field="reconn",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    SmartfanSensorDescription(
        key="last_outage",
        field="outage_s",
        native_unit_of_measurement=UnitOfTime.SECONDS,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    SmartfanSensorDescription(
        key="last_boot_reason",
        field="boot",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SmartfanSensorDescription(
        # "deferred" is the one worth an automation: it means the boot
        # diagnostic never ran, because the mode was Force off, and so this
        # fan has not been proven to turn since it was last powered.
        key="self_test",
        field="selftest",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SmartfanSensorDescription(
        key="build",
        field="fw",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SmartfanSensorDescription(
        key="app_build",
        field="app",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    # Why the controller is in the state it is in, in its own words. The app
    # used to guess and guessed wrong -- it said "Clearing humidity" over a
    # room 0.9% above baseline while the fan ran on odor -- so the firmware
    # publishes the reason and nothing downstream has to infer it.
    SmartfanSensorDescription(
        key="state_reason",
        field="why",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SmartfanConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        SmartfanSensor(coordinator, description) for description in SENSORS
    )


class SmartfanSensor(SmartfanEntity, SensorEntity):
    """One field of the snapshot."""

    entity_description: SmartfanSensorDescription

    def __init__(
        self,
        coordinator,
        description: SmartfanSensorDescription,
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self):
        raw = self.value(self.entity_description.field)
        if raw is None:
            return None
        if self.entity_description.convert is not None:
            return self.entity_description.convert(raw)
        return raw
