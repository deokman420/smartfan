"""Fan running, light detected, fault, and whether the odor part answers."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import SmartfanConfigEntry
from .entity import SmartfanEntity


@dataclass(frozen=True, kw_only=True)
class SmartfanBinaryDescription(BinarySensorEntityDescription):
    field: str


BINARY_SENSORS: tuple[SmartfanBinaryDescription, ...] = (
    SmartfanBinaryDescription(
        key="fan_running",
        field="fan_on",
        device_class=BinarySensorDeviceClass.RUNNING,
    ),
    SmartfanBinaryDescription(
        key="light_detected",
        field="light_on",
        device_class=BinarySensorDeviceClass.LIGHT,
    ),
    SmartfanBinaryDescription(
        key="fault",
        field="fault",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    # So a sensor that has stood down after VOC_MAX_FAILS consecutive I2C
    # failures is visible, rather than silently inert with its six sensors
    # frozen on whatever it last saw. None when no part answered at boot,
    # which is a third state and reads as unavailable.
    SmartfanBinaryDescription(
        key="odor_sensor",
        field="voc_ok",
        device_class=BinarySensorDeviceClass.PROBLEM,
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
        SmartfanBinarySensor(coordinator, description)
        for description in BINARY_SENSORS
    )


class SmartfanBinarySensor(SmartfanEntity, BinarySensorEntity):
    entity_description: SmartfanBinaryDescription

    def __init__(self, coordinator, description: SmartfanBinaryDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        raw = self.value(self.entity_description.field)
        if raw is None:
            return None
        # voc_ok is a health flag the right way up: True means working, and
        # the PROBLEM device class wants True to mean trouble.
        if self.entity_description.key == "odor_sensor":
            return not bool(raw)
        return bool(raw)
