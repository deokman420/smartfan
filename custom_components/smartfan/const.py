"""Names and intervals shared across the platforms."""

from __future__ import annotations

from datetime import timedelta

DOMAIN = "smartfan"

CONF_HOST = "host"

# The device samples every 2 s and pushes to MQTT every 30 s. Ten seconds
# sits between the two: fast enough that the fan's own state does not lag
# visibly behind the room, slow enough to be nothing to a board that already
# serves its own app at a 2 s poll. This is the `appropriate-polling` rule's
# "an interval that fits the data" -- the data is a room, and rooms are slow.
SCAN_INTERVAL = timedelta(seconds=10)

# Settings the device exposes but which are not worth a Home Assistant
# entity. MODE is a select rather than a number, and is handled there.
SELECT_SETTINGS = ("MODE",)
