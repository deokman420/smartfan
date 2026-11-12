"""Odor judged per visit, on the host, with no board attached.

    python tests/test_visit.py

The level path asked whether the air was worse than an hour-long baseline,
and the flat's own drift answered yes for most of an evening: from 7 to 11
Nov the fan ran 28.8 h, 20.6 h of it on odor alone with the light off and
the room dry. VISIT_ODOR replaces that question with a narrower one. Did the
air get worse than it was when somebody walked in, and did it get worse
fast? Outside a visit, only an extreme and sustained fall may start the fan.

These drive Controller the same way tests/test_control.py does: stubbed
hardware, a clock moved by hand, decide() called directly. Every reading
sequence below is built at the loop's real 2 s cadence, because a rate
measured on samples that are not there is not a rate.
"""

import os
import sys
import tempfile
import time as _time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

results = []


def check(name, ok, detail=""):
    results.append(ok)
    print("  %-60s %s %s" % (name, "PASS" if ok else "FAIL", detail))


# -- the stubs, as in test_control.py ---------------------------------------

CLOCK = [10_000_000]
LIGHT = [3000]
DARK, LIT = 3000, 30000


class FakePin:
    OUT, IN, PULL_UP, IRQ_FALLING, IRQ_RISING = 1, 0, 2, 4, 8

    def __init__(self, no, mode=None, pull=None, value=0):
        self._v = value

    def value(self, v=None):
        if v is None:
            return self._v
        self._v = 1 if v else 0
        return None

    def irq(self, trigger=None, handler=None):
        pass


class FakeADC:
    def __init__(self, pin):
        pass

    def read_u16(self):
        return LIGHT[0]


class FakeWDT:
    def __init__(self, timeout=0):
        pass

    def feed(self):
        pass


class FakeI2C:
    def __init__(self, bus_id, sda=None, scl=None, freq=0):
        pass

    def writeto(self, addr, buf):
        raise OSError(5)

    def readfrom(self, addr, n):
        raise OSError(5)

    def scan(self):
        return []


class FakeDHT:
    def __init__(self, pin):
        pass

    def measure(self):
        pass

    def humidity(self):
        return 50.0

    def temperature(self):
        return 21.0


machine = type(sys)("machine")
machine.Pin = FakePin
machine.ADC = FakeADC
machine.I2C = FakeI2C
machine.WDT = FakeWDT
machine.reset_cause = lambda: 3
machine.PWRON_RESET = 1
machine.WDT_RESET = 3
machine.reset = lambda: None
sys.modules["machine"] = machine

dht = type(sys)("dht")
dht.DHT22 = FakeDHT
sys.modules["dht"] = dht

_time.ticks_ms = lambda: CLOCK[0]
_time.ticks_diff = lambda a, b: a - b
_time.ticks_add = lambda t, d: t + d
_time.sleep_ms = lambda ms: None

TMP = tempfile.mkdtemp(prefix="fan-visit-")
os.chdir(TMP)

import config as cfg                                 # noqa: E402
import settings                                      # noqa: E402
import main                                          # noqa: E402
import voc as vocmod                                 # noqa: E402

cfg.SELFTEST_S = 0
main.reset_cause = lambda: 3


class FakeSGP41:
    """The driver's contract without the bus. `voc` is what the next read says."""

    def __init__(self, voc=30000, nox=18000):
        self.voc, self.nox = voc, nox
        self.ok = True
        self.fails = 0
        self.settled_voc = True
        self.settled_nox = True

    def read(self, rh=None, t=None):
        return None if self.voc is None else (self.voc, self.nox)

    def start(self):
        pass

    def serial(self):
        return "0000068B380D"

    def heater_off(self):
        pass


def build(raw=30000, base=30000.0, rise=80, fall=45, sensor=True):
    """A dark, dry, settled room, fan off, IDLE; the gas sensor armed."""
    pre = settings.Settings(log=lambda *a: None)
    pre.reset()
    c = main.Controller()
    c.state = main.IDLE
    c.entered_at = CLOCK[0]
    c.rh = 53.7
    c.temp = 21.0
    c.baseline = 53.7
    c.rpm = 0
    c.light_on = False
    c.dark_since = CLOCK[0] - 3600_000
    LIGHT[0] = DARK
    c.lightdet.amb = float(DARK)
    c.lightdet.prev = DARK
    if sensor:
        c.voc = FakeSGP41(raw)
        c.vocdet.base = base
        c.vocdet.raw = raw
        c.noxdet.base = 18000.0
        c.noxdet.raw = 18000
        c.voc_ready_since = _time.ticks_ms()
        c.st.set("VOC_RISE_ON", rise)
        c.st.set("VOC_FALL_OFF", fall)
    return c


def tick(c, n=1, voc=None, slope=0):
    """n loop passes, 2 s apart. `slope` is ticks per pass, applied to raw."""
    for _ in range(n):
        CLOCK[0] += 2000
        if c.voc is not None:
            if voc is not None:
                c.voc.voc = voc
                voc = None
            c.voc.voc += slope
        driven = bool(c.fan.value())
        c.rpm = 4200 if driven else 0
        c.rpm_idle = 0
        c.read_light()
        c.read_voc()
        if not c.step_selftest():
            c.check_fan_health()
            c.decide()
        c.set_fan(c.state in (main.RUN, main.BOOST))


def minutes(m):
    return int(m * 60 / 2)


def per_pass(per_min):
    """A fall of per_min ticks a minute, as the change on each 2 s pass."""
    return -int(round(per_min / 30.0))


print("\nthe ring of recent readings\n")

r = vocmod.Recent(240000)
for i in range(30):
    r.push(i * 2000, 30000)
check("a short history cannot answer a 120 s rate",
      r.fall_per_min(29 * 2000, 120000) is None)
for i in range(30, 200):
    r.push(i * 2000, 30000 - max(0, i - 100) * 10)
now = 199 * 2000
check("the ring forgets past its span", len(r) == 121, "%d" % len(r))
check("a steady fall reads as its rate",
      abs(r.fall_per_min(now, 120000) - 300.0) < 0.01,
      "%.1f" % r.fall_per_min(now, 120000))
check("...in either half of the window too",
      abs(r.fall_per_min(now, 60000, end_age_ms=60000) - 300.0) < 0.01)
check("the median takes only the window asked for",
      r.median(now, 0) == 30000 - 99 * 10, "%s" % r.median(now, 0))
up = vocmod.Recent(240000)
for i in range(61):
    up.push(i * 2000, 30000 + i * 5)
check("a rising count is a negative fall",
      abs(up.fall_per_min(120000, 120000) + 150.0) < 0.01,
      "%.1f" % up.fall_per_min(120000, 120000))

print("\nthe default, and an absent sensor\n")

check("visit odor ships on", cfg.VISIT_ODOR is True)
check("the thresholds are the measured ones",
      (cfg.VISIT_TAIL_S, cfg.VISIT_REF_S, cfg.VISIT_RISE_ON,
       cfg.VISIT_RATE_S, cfg.VISIT_RATE_ON, cfg.VISIT_SHOWER_DP,
       cfg.VISIT_BACKSTOP_RATE) == (600, 120, 150, 120, 150, 1.0, 300))

c = build(sensor=False)
LIGHT[0] = LIT
tick(c, 5)
LIGHT[0] = DARK
tick(c, minutes(cfg.POST_LIGHT_S / 60.0 + cfg.MIN_RUN_S / 60.0) + 5)
check("with no sensor a visit is just the light, as before",
      c.state == main.IDLE and c.odor_ref is None, c.state)

print("\nthe flat drifting, with nobody in the room\n")

# The failure this replaces: the air is 300 ticks worse than the hour-long
# baseline, and has been all evening. Nobody is in the room.
c = build(raw=29700, base=30000.0)
tick(c, minutes(10))
check("a drifted baseline no longer starts the fan",
      c.state == main.IDLE and c.fan.value() == 0,
      "%s exc=%s" % (c.state, c.excess_voc))
cfg.VISIT_ODOR = False
c = build(raw=29700, base=30000.0)
tick(c, 2)
check("...where the level path would have (VISIT_ODOR False)",
      c.state == main.RUN, c.state)
cfg.VISIT_ODOR = True

c = build()
tick(c, minutes(3))
tick(c, minutes(30), slope=per_pass(60))
check("a slow 60/min slide for half an hour starts nothing",
      c.state == main.IDLE and c.odor_ref is None,
      "%s fell %d" % (c.state, 30000 - c.voc.voc))

print("\na visit that smells\n")

c = build()
tick(c, minutes(3))                       # two minutes of flat air to learn
LIGHT[0] = LIT
tick(c, 1)
check("the light opens a visit", c.visit_open is True)
check("...with the air before it as the reference",
      c.visit_ref == 30000, "%s" % c.visit_ref)
tick(c, minutes(1))
tick(c, minutes(2), slope=per_pass(450))  # the 4 Nov shape: fast and deep
check("a fast, deep fall during a visit is an odor episode",
      c.odor_ref == 30000 and c.odor_kind == "visit",
      "ref=%s kind=%s" % (c.odor_ref, c.odor_kind))
check("...and the light is what runs the fan meanwhile",
      c.state == main.RUN, c.state)
LIGHT[0] = DARK
tick(c, minutes(cfg.POST_LIGHT_S / 60.0) + 30)
check("the smell holds the fan after the light goes off",
      c.state == main.RUN and not c._voc_quiet(),
      "%s %r" % (c.state, c.state_reason))
check("...and says so", c.state_reason.startswith("odor"),
      repr(c.state_reason))
c.voc.voc = 30000 - 30                    # the air clears to its reference
tick(c, 2)
check("the episode ends when the air is back within VOC_FALL_OFF",
      c.odor_ref is None)
tick(c, 2)
check("...and the fan stops", c.state == main.IDLE, c.state)

# The light comes on with the room dark and dry, the fan off, and the smell
# arrives only after the light has gone off again -- inside the tail.
c = build()
tick(c, minutes(3))
LIGHT[0] = LIT
tick(c, minutes(1))
LIGHT[0] = DARK
tick(c, minutes(cfg.POST_LIGHT_S / 60.0 + cfg.MIN_RUN_S / 60.0) + 10)
check("a visit with no smell lets the fan stop as usual",
      c.state == main.IDLE, c.state)
tick(c, minutes(2), slope=per_pass(400))
check("a smell inside the tail still starts the fan",
      c.state == main.RUN and c.odor_kind == "visit",
      "%s %r" % (c.state, c.state_reason))

# The drifted evening again, with somebody in it this time. The level path
# held every such run open on the baseline's excess until MAX_RUN_S; the
# visit asks only whether the air changed while they were there.
c = build(raw=29700, base=30000.0)
tick(c, minutes(3))
LIGHT[0] = LIT
tick(c, minutes(1))
LIGHT[0] = DARK
tick(c, minutes(cfg.POST_LIGHT_S / 60.0 + cfg.MIN_RUN_S / 60.0) + 10)
check("a drifted baseline no longer holds a visit's run open",
      c.state == main.IDLE and c.odor_ref is None,
      "%s exc=%s" % (c.state, c.excess_voc))

c = build()
tick(c, minutes(3))
LIGHT[0] = LIT
tick(c, minutes(1))
LIGHT[0] = DARK
tick(c, minutes(cfg.VISIT_TAIL_S / 60.0) + 5)
check("the visit closes VISIT_TAIL_S after the light goes off",
      c.visit_open is False and c.visit_ref is None)

print("\nvisits that must not count\n")

c = build()
tick(c, minutes(3))
LIGHT[0] = LIT
tick(c, minutes(1))
tick(c, minutes(10), slope=per_pass(30))  # 300 ticks, but at 30 a minute
check("a deep but slow fall is not a smell", c.odor_ref is None,
      "fell %d" % (30000 - c.voc.voc))

c2 = build()
tick(c2, minutes(3))
LIGHT[0] = LIT
tick(c2, minutes(1))
tick(c2, 6, slope=per_pass(500))          # fast and shallow: ~100 ticks
tick(c2, minutes(3))
check("a fast but shallow fall is not a smell", c2.odor_ref is None,
      "fell %d" % (30000 - c2.voc.voc))

# A shower: the dew point climbs with the visit. Steam moves the VOC pixel
# hard, and humidity already owns the run, so odor is not judged at all.
c = build()
tick(c, minutes(3))
LIGHT[0] = LIT
tick(c, 1)
c.rh = 62.0                               # +8.3 %RH, dew point up ~2.5 C
tick(c, 1)
check("a dew point rise marks the visit as a shower", c.visit_shower is True)
tick(c, minutes(2), slope=per_pass(450))
check("...and a shower's VOC fall starts no odor episode",
      c.odor_ref is None)

# The hob's NOx, still climbing, vetoes the visit as it vetoes the level path.
c = build()
tick(c, minutes(3))
c.voc.nox = 18000 + 600                   # a 600-tick NOx rise
LIGHT[0] = LIT
tick(c, minutes(1))
tick(c, minutes(2), slope=per_pass(450))
check("a combustion veto keeps a visit from smelling", c.odor_ref is None,
      "veto=%s" % c._combustion())

print("\nthe backstop, outside a visit\n")

c = build()
tick(c, minutes(3))
tick(c, minutes(2) + 2, slope=per_pass(400))
check("a sustained 400/min fall in the dark starts the fan",
      c.state == main.RUN and c.odor_kind == "backstop",
      "%s %r" % (c.state, c.state_reason))
check("...against the air as it was before the fall",
      c.odor_ref == 30000, "%s" % c.odor_ref)

c = build()
tick(c, minutes(3))
tick(c, minutes(4), slope=per_pass(200))
check("200/min in the dark is not enough", c.state == main.IDLE,
      "%s %r" % (c.state, c.state_reason))

c = build()
tick(c, minutes(3))
tick(c, 1, voc=30000 - 900)               # one step, then flat
tick(c, minutes(3))
check("a single step is not a sustained fall", c.state == main.IDLE,
      "%s" % c.state)

c = build()
tick(c, minutes(3))
c.voc_recover_until = _time.ticks_add(_time.ticks_ms(), 900_000)
tick(c, minutes(2) + 2, slope=per_pass(400))
check("the post-steam window still holds the backstop off",
      c.state == main.IDLE and c.odor_ref is None, c.state)

print("\nbounds\n")

# The air never returns to its reference. The episode must not hold the fan
# for ever, nor keep the latch shut once the cap has fired.
c = build()
tick(c, minutes(3))
tick(c, minutes(2) + 2, slope=per_pass(400))
check("backstop run started", c.state == main.RUN, c.state)
tick(c, minutes(c.st.MAX_RUN_S / 60.0) + 2)
check("the cap ends the run and the episode with it",
      c.state == main.IDLE and c.odor_ref is None,
      "%s ref=%s" % (c.state, c.odor_ref))
tick(c, 3)
check("...and the latch can release in a dark, dry room",
      c.maxed is False and c.state == main.IDLE,
      "maxed=%s %s" % (c.maxed, c.state))

# Ten dry minutes on the seat latch a light-only run off. A smell after that
# is a new event and must be able to run the fan again.
c = build()
tick(c, minutes(3))
LIGHT[0] = LIT
tick(c, minutes(c.st.LIGHT_ONLY_MAX_S / 60.0) + 5)
check("a long dry visit hits the light-only cap",
      c.maxed is True and c.state == main.IDLE, c.state)
tick(c, minutes(2), slope=per_pass(450))
check("...and a smell after it runs the fan again",
      c.state == main.RUN and c.maxed is False,
      "%s maxed=%s" % (c.state, c.maxed))

# Force off still tracks the visit, so a smell is known about when the mode
# goes back to Auto, but the fan stays off while forced.
c = build()
c.st.set("MODE", "Force off")
tick(c, minutes(3))
tick(c, minutes(2) + 2, slope=per_pass(400))
check("Force off keeps the fan off through an episode",
      c.state == main.IDLE and c.fan.value() == 0 and c.odor_ref is not None,
      "%s ref=%s" % (c.state, c.odor_ref))
c.st.set("MODE", "Auto")
tick(c, 2)
check("...and Auto acts on it", c.state == main.RUN, c.state)

failed = results.count(False)
print("\n%d/%d passed\n" % (len(results) - failed, len(results)))
sys.exit(1 if failed else 0)
