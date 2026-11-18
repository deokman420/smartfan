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

    def __init__(self, st, log=print, alpha=None, name="voc",
                 clean_is_high=None):
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
        # WHICH WAY IS CLEAN AIR, in raw ticks. The two pixels disagree and
        # the asymmetric baseline below is the first thing here that cares.
        # The VOC pixel answers reducing gases and its count FALLS as the air
        # gets worse, so clean is the high side. The NOx pixel answers
        # oxidising gases and its count RISES, so clean is the low side.
        # Getting this backwards gives a baseline that sprints toward an
        # event and crawls back, which is the exact failure the asymmetry
        # exists to prevent, inverted.
        #
        # Defaulted FROM THE NAME rather than left to the caller, because
        # the name is the pixel's identity and a Detector(name="nox") built
        # with the VOC direction is a silent, plausible-looking wrong
        # answer. Pass it explicitly to override.
        self.clean_is_high = (name != "nox") if clean_is_high is None             else clean_is_high
        # The baseline is held in COMPENSATED ticks: raw + comp, where comp
        # is whatever main.py says this sample's climate is worth (0 unless
        # cfg.ODOR_RH_COMP is on). `base` below is the same number seen from
        # the raw side -- what clean air would read at this moment's
        # humidity -- so every caller that reads or writes `base` and `raw`
        # keeps meaning exactly what it meant, and excess() is still
        # `base - raw`. With comp at 0 nothing here differs from before.
        self._cbase = None   # learned baseline in compensated ticks
        self.comp = 0.0      # ticks this sample's climate is worth
        self.raw = None      # last raw reading, for the log and telemetry

    @property
    def base(self):
        """The learned baseline in raw ticks at the current climate, or None."""
        if self._cbase is None:
            return None
        return self._cbase - self.comp

    @base.setter
    def base(self, v):
        self._cbase = None if v is None else float(v) + self.comp

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

    def update(self, raw, learning, comp=None):
        """One sample. Returns the excess for this reading.

        `learning` is the controller's judgement that the room is quiet
        enough to learn from. The baseline is seeded on the first such
        sample and tracked by EWMA after, which mirrors the humidity path
        exactly -- including that it does not learn while the room is busy,
        or the event teaches the baseline to ignore itself.

        `comp`, when given, is this sample's climate correction in ticks
        (see main.Controller._odor_comp). It is added to the raw reading
        before the baseline learns from it, so a reading that moved only
        because the humidity did is not learned as a change in the air.
        None leaves the last correction in place; 0 is no correction.
        """
        if comp is not None:
            self.comp = float(comp)
        if raw is None:
            return self.excess()
        self.raw = raw
        if learning:
            if self._cbase is None:
                self.base = float(raw)
                self._log("%s: baseline seeded at %d ticks" % (self.name, raw))
            else:
                a = cfg.VOC_BASELINE_A if self.alpha is None else self.alpha
                # ASYMMETRIC. Moving toward clean air is not a risk -- the
                # baseline is SUPPOSED to sit on the clean level, so there is
                # nothing to be careful about in that direction. Moving
                # toward worse air is how a baseline learns an event as
                # normal, and that is what the slow weight is for.
                #
                # One symmetric weight had to serve both, and being set slow
                # for the dangerous direction left it far too slow for the
                # safe one. The cost was measured on 2026-11-06: after a
                # restart the element's clean level climbed 249 counts in
                # four hours, the baseline never caught up, and a real event
                # read as cleaner than normal. See VOC_BASELINE_UP.
                rc = raw + self.comp
                toward_clean = (rc > self._cbase) if self.clean_is_high \
                    else (rc < self._cbase)
                # ...but not quickly on a move the correction itself made.
                # The correction is a model and is sometimes wrong -- steam
                # from a pan moves the humidity without moving the pixel the
                # way the furnace does -- and a fast step toward "clean" on a
                # wrong correction leaves a baseline the slow weight then
                # takes an hour to walk back. Measured in the 7-15 Nov
                # replay: see ODOR_COMP_UP_GATE in config.py.
                gate = getattr(cfg, "ODOR_COMP_UP_GATE", None)
                if toward_clean and (gate is None or abs(self.comp) <= gate):
                    a = min(1.0, a * getattr(cfg, "VOC_BASELINE_UP", 1))
                self._cbase = (1 - a) * self._cbase + a * rc
        return self.excess(raw)

    def relearn(self, raw=None):
        """Drop the learned baseline. Pairs with the humidity Relearn."""
        self.base = float(raw) if raw is not None else None


class Recent:
    """The last few minutes of raw readings, for the visit tests in main.py.

    The Detector above answers "is the air worse than this room's normal",
    against a baseline that takes an hour to move. A visit asks something
    narrower: is the air worse than it was a moment before somebody walked
    in, and is it getting worse quickly. Both need the recent past, which
    the EWMA has averaged away, so it is kept here as it was read.

    No clock of its own, for the same reason as Detector: the caller hands
    in the time with every reading and every question, so the tests can
    drive it with plain integers. `diff` is time.ticks_diff on the device,
    because ticks_ms wraps and a plain subtraction across the wrap is a
    reading from the far future.
    """

    def __init__(self, span_ms, diff=None):
        self.span_ms = span_ms
        self._diff = diff or (lambda a, b: a - b)
        self._t = []
        self._v = []

    def __len__(self):
        return len(self._t)

    def push(self, t, v):
        """Add one reading, and forget the ones older than span_ms."""
        self._t.append(t)
        self._v.append(v)
        while len(self._t) > 1 and self._diff(t, self._t[0]) > self.span_ms:
            self._t.pop(0)
            self._v.pop(0)

    def clear(self):
        self._t = []
        self._v = []

    def median(self, now, window_ms):
        """Median of the readings no older than window_ms, or None."""
        vals = [v for t, v in zip(self._t, self._v)
                if self._diff(now, t) <= window_ms]
        if not vals:
            return None
        vals.sort()
        n = len(vals)
        mid = n // 2
        if n % 2:
            return vals[mid]
        # Whole ticks, as the sensor reports them: a float reference would
        # reach "%d" formatting downstream, which MicroPython is stricter
        # about than CPython.
        return (vals[mid - 1] + vals[mid]) // 2

    def at(self, now, age_ms):
        """(t, v) of the newest reading at least age_ms old, or None.

        None when the history does not reach that far back -- just after a
        boot, say. A rate measured across less time than it claims is a
        rate that is too high, so the question goes unanswered instead.
        """
        found = None
        for t, v in zip(self._t, self._v):
            if self._diff(now, t) >= age_ms:
                found = (t, v)
            else:
                break
        return found

    def fall_per_min(self, now, window_ms, end_age_ms=0):
        """How fast the raw count FELL across a window, in ticks a minute.

        The window ends end_age_ms ago and is window_ms long. Positive means
        the count fell, which for the VOC pixel means the air got worse.
        None if the history does not cover the window.
        """
        end = self.at(now, end_age_ms)
        start = self.at(now, end_age_ms + window_ms)
        if end is None or start is None:
            return None
        span = self._diff(end[0], start[0])
        if span <= 0:
            return None
        return (start[1] - end[1]) * 60000.0 / span
