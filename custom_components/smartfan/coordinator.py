"""One poll, shared by every entity."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import Smartfan, SmartfanError
from .const import DOMAIN, SCAN_INTERVAL

_LOGGER = logging.getLogger(__name__)

type SmartfanConfigEntry = ConfigEntry["SmartfanCoordinator"]


class SmartfanCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Polls /api/state and hands the same dict to forty-odd entities.

    One request per interval for the whole device, rather than one per
    entity. That matters more here than on a server-class target: the
    controller serves HTTP in slices between control-loop passes, and forty
    independent pollers would be forty chances to sit behind a sensor read.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        entry: SmartfanConfigEntry,
        client: Smartfan,
        info: dict[str, Any],
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=SCAN_INTERVAL,
            config_entry=entry,
        )
        self.client = client
        self.info = info

    @property
    def device_id(self) -> str:
        """The firmware's own DEVICE_ID, which is unique per board.

        Every entity's unique_id is built from this. The MQTT path cannot
        offer that: Home Assistant freezes an entity_id at the name it first
        saw and never rewrites it, so renaming the device left this one
        board with entities under two different prefixes --
        bathroom_fan_controller_* and bathroom_smartfan_* -- and a suite
        that knew only the older one silently stopped checking the newer
        ones. Owning the identifiers is how that stops happening.
        """
        return str(self.info.get("device") or "smartfan")

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            return await self.client.state()
        except SmartfanError as err:
            raise UpdateFailed(str(err)) from err

    async def async_set_setting(self, name: str, value: Any) -> None:
        """Write a setting, then refresh so the UI shows what landed.

        The device clamps and may move a paired setting out of the way --
        writing RH_RISE_ON below RH_FALL_OFF pushes the other one down -- so
        the value that comes back is not always the value sent, and a local
        optimistic update would show a number the device does not hold. The
        response carries the whole `cfg`, so this refreshes from that rather
        than making a second request.
        """
        result = await self.client.set_setting(name, value)
        cfg = result.get("cfg")
        if isinstance(cfg, dict) and self.data is not None:
            merged = dict(self.data)
            merged["cfg"] = cfg
            self.async_set_updated_data(merged)
        else:
            await self.async_request_refresh()

    async def async_command(self, cmd: str) -> None:
        """Send a command and refresh: a button press changes state at once."""
        await self.client.command(cmd)
        await self.async_request_refresh()
