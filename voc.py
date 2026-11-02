"""Is the air worse than this room's normal?

The odour signal, and the second sensed reason this controller has to run.
Before it, the only non-button reason was humidity above baseline, which
covers showers and nothing else -- so the other thing a bathroom fan is for
produced no signal at all and read as an empty room.

An SGP41 gives raw tick counts off two heated metal-oxide pixels. There is no
absolute level that means "bad air": the count depends on the element, its
age, the room, and how long it has been powered. Measured on this build's
part on 2026-11-01, still indoor air at 23.9 C / 43 %RH:

    sraw_voc, settled, clean room    28712-29331   (spread 619 over 20 s)
    sraw_nox, first 30 s from cold   19480->17263  (still warming)

So this does what the humidity path does: learn what normal is, and report
the deviation from it. Same arithmetic, same shape, different sensor.

SIGN. For SGP4x parts the raw signal moves DOWN as reducing-gas
concentration goes up, so the deviation meaning "worse air" is
`base - raw`, not the other way round. Getting this backwards gives a
controller that ventilates whenever the air is clean, which would take a
long time to notice. Confirm it on the bench with a marker pen before
choosing any threshold -- `calibrate.py voc` prints the direction.

WHY NOT light.py's ARITHMETIC. That detector is edge-triggered on a
one-sample jump, because its problem was that sunlight and the bulb overlap
in level but differ a thousandfold in rate. Odour has the opposite
character: slow, diffuse, no edge. An edge detector would never fire on it.

WHY THE BASELINE IS SLOWER THAN THE HUMIDITY ONE. cfg.VOC_BASELINE_A is
0.0005 against BASELINE_A's 0.002 -- about 67 minutes to settle at 2 s
samples, against 15. An odour event is shorter than a shower, and a baseline
quick enough to follow one would learn the smell as normal and then report
clean air as the deviation.

NO POLICY HERE. `update()` is told whether the room is quiet enough to learn
from; it does not work that out. The humidity gate and VOC_RECOVER_S live in
main.py, where something can see every sensor at once. A detector that
reached for excess_rh itself would be two sensors in a trenchcoat. What this
buys is that a week of recorded ticks replays straight through it with no
hardware present, which is how the thresholds get measured instead of
argued -- the same way the light rewrite was validated against four days of
the device's own history before any of it reached the fan.
"""

import config as cfg


class Detector:
    """Learns the room's clean-air tick count and reports the shortfall.

    No hardware and no clock: handed a raw reading and a verdict on whether
    the room is quiet, it returns a number. The tests drive it directly.
    """

    def __init__(self, st, log=print):
        self.st = st
        self._log = log
        self.base = None     # learned baseline in raw ticks; None until seeded
        self.raw = None      # last raw reading, for the log and telemetry

    def excess(self, raw=None):
        """How far the air sits the wrong way from baseline, in ticks.

        Positive means worse than normal. Zero while unseeded, so an
        unlearned detector cannot trigger anything.
        """
        if raw is None:
            raw = self.raw
        if self.base is None or raw is None:
            return 0
        return int(self.base - raw)

    def update(self, raw, learning):
        """One sample. Returns the excess for this reading.

        `learning` is the controller's judgement that the room is quiet
        enough to learn from. The baseline is seeded on the first such
        sample and tracked by EWMA after, which mirrors the humidity path
        exactly -- including that it does not learn while the room is busy,
        or the event teaches the baseline to ignore itself.
        """
        if raw is None:
            return self.excess()
        self.raw = raw
        if learning:
            if self.base is None:
                self.base = float(raw)
                self._log("voc: baseline seeded at %d ticks" % raw)
            else:
                a = cfg.VOC_BASELINE_A
                self.base = (1 - a) * self.base + a * raw
        return self.excess(raw)

    def relearn(self, raw=None):
        """Drop the learned baseline. Pairs with the humidity Relearn."""
        self.base = float(raw) if raw is not None else None
