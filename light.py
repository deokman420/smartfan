"""Is the room's own light on?

The occupancy signal, and the one sensor in this build that cannot be read at
face value.

An LDR in a bathroom with a window sees two sources and is asked to report
only one of them. Measured in this room over the four days to 2026-10-29:

    truly dark, night            1600-3300   ( 2.5-5.0 %)
    fixture on, room dark            53000   (81 %)
    direct sun, no fixture       up to 36300 (55 %) and still climbing
    brightest ambient seen           55800   (85 %)

Daylight is *brighter than the bulb*. So there is no level that separates
them, and the absolute LIGHT_ON / LIGHT_OFF pair this replaced could not have
worked at any setting: the fan ran 75 minutes on 10-27 and 175 minutes on
10-28, in a bone-dry room, on sunlight.

What does separate them is the rate, and it is not close. Per 2 s sample,
over those same four days:

    the fixture switching on     +51511 counts in ONE sample
    the fastest ambient drift       +51 counts per sample

Three orders of magnitude. So this tracks the ambient and calls "fixture"
only what the ambient cannot account for -- the same move the humidity logic
makes with its learned baseline, for the same reason.

Replaying all four days of the device's own stored history through this rule
gives 30 minutes of asserted occupancy against the old rule's 275, every
minute of it inside a window the history independently recorded as lit, and
zero false triggers. Worst the sky ever came to the two conditions below:
a 51-count jump against 10000, and a 5958-count rise against 8000.

It lives in its own module because main.py is pushed to the device over the
air, and ota.py pulls each file into RAM whole: a main.py much past 44 kB
fails to install, with the error going only to a USB serial console nobody is
holding. Keeping the detector and its reasoning here is what lets both stay
small enough to ship.
"""

import time

import config as cfg


class Detector:
    """Tracks the ambient light, and reports the fixture as a step above it.

    No hardware: it is handed a raw ADC reading and returns a verdict, so the
    tests drive it directly with four days of real readings.
    """

    def __init__(self, st, log=print):
        self.st = st
        self._log = log
        self.on = False
        self.amb = None          # learned ambient; None until the first read
        self.prev = None         # previous raw sample, for the one-sample jump
        self.on_at = time.ticks_ms()

    def rise(self, raw):
        """How far `raw` sits above the learned ambient.

        The number that explains a verdict. A log line or an app tile showing
        only the raw reading cannot tell a lit room from a sunny one, which is
        exactly the confusion this module exists to resolve.
        """
        if self.amb is None:
            return 0
        return int(raw - self.amb)

    def update(self, raw):
        """Take one sample. Returns True if the fixture is believed on."""
        if self.amb is None:                 # the first reading owns the ambient
            self.amb = raw
        prev = self.prev
        self.prev = raw
        jump = raw - prev if prev is not None else 0
        rise = raw - self.amb

        if not self.on:
            # Both conditions, and they catch different lies. The jump rejects
            # the sky, which cannot move this fast. The rise rejects a jump
            # that merely climbs out of a shadow back to where the room
            # already was -- fast, but not a light.
            if jump >= self.st.LIGHT_STEP_ON and rise >= self.st.LIGHT_RISE_ON:
                self.on = True
                self.on_at = time.ticks_ms()
        else:
            # The off-edge, taken the same way round, and it re-anchors the
            # ambient as it goes. That matters: the sun may have climbed while
            # the fan ran, and an ambient still frozen at a pre-dawn 3000
            # would read the new daylight as a second light-on and never let
            # go of it.
            if jump <= -self.st.LIGHT_STEP_ON:
                self.on = False
                self.amb = raw
            elif rise <= self.st.LIGHT_RISE_OFF:
                self.on = False
            elif time.ticks_diff(time.ticks_ms(),
                                 self.on_at) / 1000.0 >= cfg.LIGHT_MAX_ON_S:
                # Neither edge arrived and the offset never closed, so the
                # frozen ambient is stale and nothing can be trusted against
                # it. Re-anchor rather than sit here being wrong -- this is
                # what bounds every light misreading, whatever its cause.
                self._log("light: on for %d min with no off-edge; re-anchoring "
                          "ambient at %d" % (cfg.LIGHT_MAX_ON_S // 60, raw))
                self.on = False
                self.amb = raw

        # The ambient tracks only while the fixture is off. Letting it run
        # while the bulb is lit would climb to meet it, and the light would
        # read off with the room still blazing.
        if not self.on:
            if raw < self.amb:
                # Falling: cannot be a fixture switching on, so believe it.
                self.amb += cfg.LIGHT_AMB_DOWN_A * (raw - self.amb)
            else:
                # Rising: absorb it, but only as fast as weather can explain.
                # A hard ceiling in counts, not an EMA weight, and that is the
                # point -- anything slower is absorbed COMPLETELY and can
                # never accumulate into a false trigger. An EMA cannot promise
                # that: a cloud edge crossing the sun shifts indoor light by
                # tens of percent in twenty seconds, which an EMA lags and a
                # slew limit eats.
                self.amb += min(cfg.LIGHT_AMB_UP, raw - self.amb)

        return self.on
