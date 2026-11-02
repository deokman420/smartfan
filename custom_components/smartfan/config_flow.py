"""Setting it up from the UI, by host name.

`fan.local` is the default because the DHCP lease moves -- the address in
this project's own docs went stale twice -- and the firmware sets
`network.hostname()` before the interface comes up, which is what makes that
name resolve. An IP still works for anyone whose network does not carry mDNS.

THERE IS NO ZEROCONF STEP, and that is a fact about the firmware rather than
a gap here. `network.hostname()` gives an mDNS responder for the host's own
A record -- `ping fan.local` answers -- and nothing more: no `_http._tcp`
service is advertised, so Home Assistant's zeroconf discovery would never
see this device. Declaring the service type in the manifest anyway would be
worse than useless: it would never fire for a controller, and it would run
this flow against every printer and NAS on the network to abort on each one.
Discovery is a Gold-tier rule, and earning it means teaching the firmware to
advertise a service first.

The connection is tested before the entry is created (`test-before-configure`)
and the device's own DEVICE_ID becomes the entry's unique_id, so the same
board cannot be added twice (`unique-config-entry`). That identifier comes
from the firmware rather than from the host name on purpose: one board
reached at `fan.local` and at `192.168.0.28` is one board.
"""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import TextSelector, TextSelectorConfig

from .api import Smartfan, SmartfanError, SmartfanUnsupported
from .const import CONF_HOST, DOMAIN

_LOGGER = logging.getLogger(__name__)

DEFAULT_HOST = "fan.local"

SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST, default=DEFAULT_HOST): TextSelector(
            TextSelectorConfig(autocomplete="off")
        )
    }
)


class SmartfanConfigFlow(ConfigFlow, domain=DOMAIN):
    """Ask for a host, prove it answers, remember which board it was."""

    VERSION = 1

    async def _probe(self, host: str) -> tuple[dict[str, Any] | None, str | None]:
        """(info, error_key). Never raises: the caller is drawing a form."""
        client = Smartfan(host, async_get_clientsession(self.hass))
        try:
            return await client.info(), None
        except SmartfanUnsupported as err:
            _LOGGER.warning("smartfan at %s is too new: %s", host, err)
            return None, "unsupported_api"
        except SmartfanError as err:
            _LOGGER.debug("smartfan at %s did not answer: %s", host, err)
            return None, "cannot_connect"

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            info, error = await self._probe(host)
            if info is not None:
                await self.async_set_unique_id(str(info.get("device")))
                self._abort_if_unique_id_configured(
                    updates={CONF_HOST: host}
                )
                return self.async_create_entry(
                    title=str(info.get("name") or "Smartfan"),
                    data={CONF_HOST: host},
                )
            errors["base"] = error or "cannot_connect"

        return self.async_show_form(
            step_id="user", data_schema=SCHEMA, errors=errors
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Move an existing entry to a new address.

        Worth having even though it is a Silver rule: this device's address
        is the thing most likely to change about it, and without this the
        only cure for a moved board is removing the entry and losing every
        entity's history with it.
        """
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            info, error = await self._probe(host)
            if info is not None:
                await self.async_set_unique_id(str(info.get("device")))
                self._abort_if_unique_id_mismatch(reason="wrong_device")
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_HOST: host}
                )
            errors["base"] = error or "cannot_connect"

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_HOST, default=entry.data.get(CONF_HOST, DEFAULT_HOST)
                    ): TextSelector(TextSelectorConfig(autocomplete="off"))
                }
            ),
            errors=errors,
        )
