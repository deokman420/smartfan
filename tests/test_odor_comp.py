"""The humidity correction to the VOC baseline, on the host.

    python tests/test_odor_comp.py

ODOR_RH_COMP corrects the hour-long VOC baseline for quick humidity swings
(the furnace sawtooth, 14-15 Nov). These check that it does that, that it
stands down for steam and combustion, that it never reaches the NOx veto
or the visit tests, and that with the flag off nothing has changed.
Harness copied from tests/test_visit.py.
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

TMP = tempfile.mkdtemp(prefix="fan-odorcomp-")
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


print("\n")

import math                                          # noqa: E402

CK = cfg.ODOR_COMP_K


def furnace(c, cycles=6, period_min=36, burn_min=8, drop=2.0, k=CK,
            base_rh=47.0, raw0=30000):
    """The overnight sawtooth: RH falls `drop` during each burn and recovers,
    and the raw count rises k ticks per %RH it falls. Returns excess samples."""
    out = []
    n = minutes(period_min)
    burn = minutes(burn_min)
    for _ in range(cycles):
        for i in range(n):
            if i < burn:
                d = drop * i / burn
            else:
                d = drop * math.exp(-(i - burn) / float(minutes(6)))
            c.rh = base_rh - d
            c.voc.voc = int(raw0 + k * d)
            tick(c)
            out.append(c.excess_voc)
    return out


def swing(xs):
    xs = [x for x in xs if x is not None]
    return max(xs) - min(xs)


print("\nthe default\n")

check("the correction ships off", cfg.ODOR_RH_COMP is False)
check("its constants are the fitted ones",
      (cfg.ODOR_COMP_K, cfg.ODOR_COMP_TAU_S, cfg.ODOR_COMP_MAX,
       cfg.ODOR_COMP_NOX_GATE, cfg.ODOR_COMP_UP_GATE)
      == (80, 1800, 300, 150, 50))

print("\nflag off: the arithmetic is the old one\n")

d = vocmod.Detector(None, log=lambda *a: None)
e = vocmod.Detector(None, log=lambda *a: None)
seq = [30000 + int(150 * math.sin(i / 40.0)) - i // 10 for i in range(3000)]
for i, raw in enumerate(seq):
    d.update(raw, i % 7 != 0)
    e.update(raw, i % 7 != 0, comp=0)
check("comp=0 learns exactly as no comp", d.base == e.base and
      d.excess() == e.excess(), "%s %s" % (d.base, e.base))

c = build()
c.noxdet.raw = 18000
sw_off = swing(furnace(c))
check("with the flag off the furnace still draws its sawtooth",
      sw_off >= 2 * 0.9 * CK, "swing %d" % sw_off)
check("...and no correction was applied", c.vocdet.comp == 0.0)

print("\nflag on: the furnace sawtooth goes away\n")

cfg.ODOR_RH_COMP = True
try:
    c = build()
    sw_on = swing(furnace(c, cycles=6)[minutes(36) * 2:])
    check("the corrected excess barely moves", sw_on < sw_off / 2,
          "swing %d against %d" % (sw_on, sw_off))
    check("the published baseline follows the humidity instead",
          c.vocdet.base is not None and c.excess_voc is not None
          and abs(c.vocdet.base - c.vocdet.raw - c.excess_voc) <= 1)
    check("no furnace cycle starts the fan", c.state == main.IDLE, c.state)

    print("\nthe correction is bounded and stands down\n")

    c = build()
    tick(c, minutes(2))
    c.rh = c.odor_rh_ref - 20      # a door open on a dry day, say
    tick(c)
    check("clamped to ODOR_COMP_MAX", abs(c.vocdet.comp) <= cfg.ODOR_COMP_MAX,
          "%.0f" % c.vocdet.comp)
    c = build()
    tick(c, minutes(2))
    c.rh = c.baseline + c.st.RH_FALL_OFF + 5   # steam
    tick(c)
    check("zero while the room is humid", c.vocdet.comp == 0.0,
          "%.0f" % c.vocdet.comp)
    c = build()
    tick(c, minutes(2))
    c.rh = c.odor_rh_ref - 1.5
    c.voc.nox = int(c.noxdet.base + cfg.ODOR_COMP_NOX_GATE + 200)
    c.noxdet.raw = c.voc.nox
    tick(c, 2)
    check("zero while the NOx pixel reads combustion", c.vocdet.comp == 0.0,
          "%.0f" % c.vocdet.comp)

    print("\nnothing else reads the correction\n")

    c = build()
    tick(c, minutes(2))
    c.rh = c.odor_rh_ref - 2
    tick(c, 3)
    check("the visit ring holds raw ticks",
          c.vocring.median(CLOCK[0], 0) == c.voc.voc)
    check("the NOx detector is never corrected", c.noxdet.comp == 0.0)
    check("vocdet.raw is still the raw reading", c.vocdet.raw == c.voc.voc)

    c = build()
    tick(c, minutes(3))
    LIGHT[0] = LIT
    tick(c, minutes(2) + 2, slope=per_pass(400))
    check("a smelly visit still starts the fan with it on",
          c.state == main.RUN and c.odor_ref is not None, c.state)
    LIGHT[0] = DARK
finally:
    cfg.ODOR_RH_COMP = False

c = build()
tick(c)
check("turning it off drops the reference", c.odor_rh_ref is None
      and c.vocdet.comp == 0.0)

failed = results.count(False)
print("\n%d/%d passed\n" % (len(results) - failed, len(results)))
sys.exit(1 if failed else 0)
