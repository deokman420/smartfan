"""The device's local HTTP API, and nothing about Home Assistant.

Kept free of every `homeassistant` import on purpose. The core quality scale's
`dependency-transparency` rule requires an integration's dependencies to be
open source, on PyPI and built in public CI, so a core submission means this
file becoming its own package with the integration as a thin wrapper. Keeping
the boundary clean now makes that a move rather than a rewrite; publishing it
today would mean a package, a release process and a version to keep in step
before anything uses it.

Talks to the controller directly over the LAN, not through a broker. The
device publishes MQTT discovery of its own accord and that path is left alone
-- it is the zero-install route for anyone already running a broker, and the
whole stance of the firmware is that it works with no broker at all. This is
the other half of the same idea: no broker, no cloud, one HTTP call.

The endpoints, as webapi.py serves them:

    GET  /api/info            identity, firmware, API version, intervals
    GET  /api/state           the live snapshot, including `cfg`
    POST /api/set/<NAME>      one setting, body is the bare value
    POST /api/cmd             body is `boost`, `cancel`, `relearn`, `clear`

`/api/cmd` refuses `update` with a 403 -- OTA stays on the authenticated MQTT
channel -- so there is deliberately no way to reach it from here.
"""

from __future__ import annotations

import asyncio
from typing import Any

import aiohttp

# The device answers in milliseconds on a quiet LAN, but it serves HTTP
# cooperatively between control-loop passes, so a request can wait behind a
# sensor read. Ten seconds is far longer than that and still short enough
# that a dead device fails a poll rather than stacking them up.
TIMEOUT = aiohttp.ClientTimeout(total=10)

# The `api` field in /api/info. Bumped by the firmware when the shape of a
# response changes incompatibly, not when a field is added -- additions are
# how the state payload has always grown.
SUPPORTED_API = 1


class SmartfanError(Exception):
    """Any failure talking to the device."""


class SmartfanUnsupported(SmartfanError):
    """Reachable, but speaking an API version this client does not know."""


class Smartfan:
    """One controller, addressed by host name or IP.

    `fan.local` is the name to prefer: the DHCP lease moves, and mDNS is what
    the firmware advertises itself under.
    """

    def __init__(self, host: str, session: aiohttp.ClientSession) -> None:
        self.host = host
        self._session = session

    @property
    def base(self) -> str:
        return f"http://{self.host}"

    async def _get(self, path: str) -> dict[str, Any]:
        try:
            async with self._session.get(
                self.base + path, timeout=TIMEOUT
            ) as res:
                res.raise_for_status()
                return await res.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise SmartfanError(f"GET {path} failed: {err}") from err

    async def _post(self, path: str, body: str) -> dict[str, Any]:
        try:
            async with self._session.post(
                self.base + path, data=body.encode(), timeout=TIMEOUT
            ) as res:
                # A refused setting answers 400 with a JSON reason, which is
                # worth more to the caller than the status code alone.
                payload: Any = None
                try:
                    payload = await res.json(content_type=None)
                except (aiohttp.ContentTypeError, ValueError):
                    payload = None
                if res.status >= 400:
                    reason = ""
                    if isinstance(payload, dict):
                        reason = str(payload.get("reason", ""))
                    raise SmartfanError(
                        f"POST {path} refused ({res.status}): "
                        f"{reason or await res.text()}"
                    )
                return payload if isinstance(payload, dict) else {}
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise SmartfanError(f"POST {path} failed: {err}") from err

    # --- reads --------------------------------------------------------
    async def info(self) -> dict[str, Any]:
        """Identity and capabilities. Raises if the API is too new to trust.

        Checked here rather than at every call site, because this is the one
        request made before anything is set up -- so a mismatch is reported
        while the user is still looking at the dialog that caused it.
        """
        data = await self.info_unchecked()
        api = data.get("api")
        if api is not None and int(api) > SUPPORTED_API:
            raise SmartfanUnsupported(
                f"device speaks API {api}, this integration knows "
                f"{SUPPORTED_API}"
            )
        return data

    async def info_unchecked(self) -> dict[str, Any]:
        """As `info`, without the version gate. For diagnostics."""
        return await self._get("/api/info")

    async def state(self) -> dict[str, Any]:
        """The live snapshot. `cfg` inside it carries every setting."""
        return await self._get("/api/state")

    # --- writes -------------------------------------------------------
    async def set_setting(self, name: str, value: Any) -> dict[str, Any]:
        """One setting. The device validates, clamps and persists it.

        No range checking here, deliberately. settings.py is the authority on
        what the hardware can honour, and a second copy of those bounds in
        this file would be a copy that drifts -- the firmware already learned
        that lesson with its thresholds. A refused write raises with the
        device's own reason.
        """
        return await self._post(f"/api/set/{name.upper()}", str(value))

    async def command(self, cmd: str) -> dict[str, Any]:
        """`boost`, `cancel`, `relearn` or `clear`."""
        return await self._post("/api/cmd", cmd)
