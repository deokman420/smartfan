"""The Smartfan bathroom controller, over its own local HTTP API."""

from __future__ import annotations

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryError, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import Smartfan, SmartfanError, SmartfanUnsupported
from .const import CONF_HOST
from .coordinator import SmartfanConfigEntry, SmartfanCoordinator

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
]


async def async_setup_entry(
    hass: HomeAssistant, entry: SmartfanConfigEntry
) -> bool:
    """Set up one controller.

    The identity request comes first and is allowed to fail the setup:
    without it there is no device name, no firmware version and no API
    version to check, and entities built on guesses at those would have to
    be rebuilt the moment the real answer arrived. This is the
    `test-before-setup` rule, and the distinction it asks for is between a
    device that is merely asleep -- ConfigEntryNotReady, retried -- and one
    that will never work, which is raised separately below.
    """
    session = async_get_clientsession(hass)
    client = Smartfan(entry.data[CONF_HOST], session)

    try:
        info = await client.info()
    except SmartfanUnsupported as err:
        # Not a transient failure: retrying cannot make the firmware older.
        # Raising ConfigEntryNotReady here would retry for ever and say
        # nothing useful while doing it.
        raise ConfigEntryError(str(err)) from err
    except SmartfanError as err:
        raise ConfigEntryNotReady(
            f"{entry.data[CONF_HOST]} did not answer: {err}"
        ) from err

    coordinator = SmartfanCoordinator(hass, entry, client, info)
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: SmartfanConfigEntry
) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_reload(hass: HomeAssistant, entry: SmartfanConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
