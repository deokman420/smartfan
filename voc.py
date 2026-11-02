"""Is the air worse than this room's normal?

The odor signal, and the second sensed reason this controller has to run.
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
in level but differ a thousandfold in rate. Odor has the opposite
character: slow, diffuse, no edge. An edge detector would never fire on it.

WHY THE BASELINE IS SLOWER THAN THE HUMIDITY ONE. cfg.VOC_BASELINE_A is
0.0005 against BASELINE_A's 0.002 -- about 67 minutes to settle at 2 s
samples, against 15. An odor event is shorter than a shower, and a baseline
quick enough to follow one would learn the smell as normal and then report
clean air as the deviation.

TWO PIXELS, ONE ARITHMETIC. An SGP41 carries a VOC pixel and a NOx pixel,
and this class serves both: handed a raw tick count it learns the quiet-air
level and reports the deviation, which is the same job either way. The
second instance exists because the two pixels answer different questions.
The VOC pixel responds to reducing gases, which is what human-origin
bathroom odor is made of -- H2S, mercaptans, ammonia, skatole. The NOx
pixel responds to oxidizing gases, principally NO2, which is what a gas
flame produces and what bathroom use does not. VOC moving with NOx flat and
VOC moving with NOx is the discriminator this controller had no way to make
with one channel: it ran for an hour on 2026-11-02 against odor carried in
from a gas hob next door, correctly, but with no way to say so.

THE NOx SIGN IS NOT CONFIRMED. For the VOC pixel the direction was measured
on this part (see SIGN above) and `base - raw` is known to mean worse air.
The NOx pixel has had no equivalent bench check, so nothing here or above
this may act on its deviation yet. It is computed, recorded and published
with the same `base - raw` convention purely so the sign can be read off a
week of this room's own data -- a gas hob next door is a better NO2 source
than anything on the bench -- exactly as the VOC thresholds were. Until
then there is deliberately no NOX_RISE_ON to set: the fan cannot be armed
on a signal whose direction is a guess.

NOx WARMS UP MORE SLOWLY. sgp41.py records the VOC pixel flat after ~60 s
from cold while sraw_nox was still falling ~80/s at 30 s. The `settled`
gate it provides is the VOC figure, so an early NOx baseline is the weaker
of the two. That costs nothing while nothing acts on it, and it is the
first thing to re-measure before anything does.

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

    def __init__(self, st, log=print, alpha=None, name="voc"):
        self.st = st
        self._log = log
        # Which pixel this instance is reading, for the seed log only. Two
        # detectors seeding one line apart with no way to tell which is
        # which is the sort of log that gets read wrong at 2 a.m.
        self.name = name
        # The EWMA weight, or None for cfg.VOC_BASELINE_A. Both pixels use
        # that figure today and the argument for it -- an odor event is
        # shorter than a shower, so a baseline quick enough to follow one
        # learns the smell as normal -- holds for either. It is a parameter
        # rather than a second config key because a knob nobody has a
        # measurement for is a knob that gets turned on a hunch.
        self.alpha = alpha
        self.base = None     # learned baseline in raw ticks; None until seeded
        self.raw = None      # last raw reading, for the log and telemetry

    def excess(self, raw=None):
        """How far the air sits the wrong way from baseline, in ticks.

        Positive means worse than normal. **None means no opinion** -- no
        reading yet, or no baseline to judge one against.

        This returned 0 for "no opinion", which collided with the 0 that
        means "exactly as clean as normal". Those are different facts and
        the difference matters at every layer above: Home Assistant drew a
        flat 0 for an absent sensor where it should have drawn nothing, the
        recorder stored a real-looking zero, and the status strip could not
        tell "clean" from "not fitted" without going behind this method's
        back to check the raw reading. None costs every caller an explicit
        test, which is the point -- a caller that forgets it now raises
        instead of quietly treating absence as good air.

        Callers that compare this against a threshold must check for None
        first. In MicroPython `None >= 0` raises TypeError, so a missed
        check fails loudly in the control loop rather than arming the fan.
        """
        if raw is None:
            raw = self.raw
        if self.base is None or raw is None:
            return None
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
                self._log("%s: baseline seeded at %d ticks" % (self.name, raw))
            else:
                a = cfg.VOC_BASELINE_A if self.alpha is None else self.alpha
                self.base = (1 - a) * self.base + a * raw
        return self.excess(raw)

    def relearn(self, raw=None):
        """Drop the learned baseline. Pairs with the humidity Relearn."""
        self.base = float(raw) if raw is not None else None
