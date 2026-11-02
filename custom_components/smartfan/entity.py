"""The device, and what every entity on it has in common."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import SmartfanCoordinator


class SmartfanEntity(CoordinatorEntity[SmartfanCoordinator]):
    """Base for every platform.

    `_attr_has_entity_name` is True and each entity carries only its own
    short name, so Home Assistant composes "Smartfan Excess odor" from the
    device name rather than every entity repeating it -- the
    `has-entity-name` rule. The MQTT entities ended up with the room baked
    into their ids and their friendly names both, which is why one rename
    left two prefixes behind.
    """

    _attr_has_entity_name = True

    def __init__(self, coordinator: SmartfanCoordinator, key: str) -> None:
        super().__init__(coordinator)
        self._key = key
        self._attr_unique_id = f"{coordinator.device_id}_{key}"
        # Set here rather than on each of the forty-odd descriptions. Home
        # Assistant looks up a name at entity.<platform>.<translation_key>
        # in strings.json, and without one an entity falls back to no name
        # at all under has_entity_name -- so the keys and the strings file
        # have to agree, and tests/test_integration.py checks that they do.
        self._attr_translation_key = key

    @property
    def device_info(self) -> DeviceInfo:
        info = self.coordinator.info
        host = self.coordinator.client.host
        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.device_id)},
            name=info.get("name") or "Smartfan",
            manufacturer="phbeks",
            model="Pico 2 W bathroom fan controller",
            sw_version=info.get("fw"),
            # The app the device serves itself, which is the page a user
            # wants when they click through from the device card -- not a
            # project README.
            configuration_url=f"http://{host}/",
        )

    @property
    def available(self) -> bool:
        """Up only when the last poll succeeded and the device is not faulted.

        A FAULT state is deliberately NOT unavailable: the fault sensor and
        its reason are the two things worth reading at that moment, and
        marking everything unavailable would hide them.
        """
        return super().available and self.coordinator.data is not None

    def value(self, key: str | None = None):
        """One field from the shared snapshot, or None.

        None-preserving on purpose, all the way up from the firmware. The
        odor and NOx fields are None while the sensor is conditioning or
        absent, and 0 is a real reading -- air exactly as clean as normal.
        Turning the first into the second would draw a flat zero for an
        absent sensor and store a real-looking number for it.
        """
        data = self.coordinator.data or {}
        return data.get(key or self._key)

    def setting(self, name: str):
        """One setting from the snapshot's `cfg` block."""
        cfg = (self.coordinator.data or {}).get("cfg") or {}
        return cfg.get(name)
