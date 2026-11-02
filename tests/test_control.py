"""The control decisions, on the host, with no board attached.

    python tests/test_control.py

main.py's hardware is four lines of its constructor; the state machine under
it is ordinary arithmetic over a clock. So `machine` and `dht` are stubbed,
`time.ticks_*` is replaced with a clock this file moves by hand, and
`Controller.decide()` is called directly. Nothing here spins a fan.

This exists because of a bug that only a test like this catches. Switching
MODE from "Force on" back to "Auto" left the fan running in a dark, dry room
with nothing calling for it -- measured at 190 s on real hardware. The state
machine was never wrong about the room; it had simply inherited a RUN that no
automatic rule had asked for, and RUN's own exit refuses to leave before
MIN_RUN_S. Every subsystem in this repo had a host-side test except the one
that decides whether the fan turns.

Hardware is still test_device.py's job. What cannot be decided here is
whether the radio behaves or the tach counts.
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
    print("  %-52s %s %s" % (name, "PASS" if ok else "FAIL", detail))


# -- the stubs ------------------------------------------------------------
#
# A fake clock rather than real time. The things being tested are all
# "has MIN_RUN_S elapsed" questions, and a test that answers them by
# sleeping is a test nobody runs.

CLOCK = [10_000_000]          # well away from zero, so wrap bugs would show


class FakePin:
    OUT, IN, PULL_UP, IRQ_FALLING, IRQ_RISING = 1, 0, 2, 4, 8

    def __init__(self, no, mode=None, pull=None, value=0):
        self._no = no
        self._v = value

    def value(self, v=None):
        if v is None:
            return self._v
        self._v = 1 if v else 0
        return None

    def irq(self, trigger=None, handler=None):
        pass


# What the LDR is reading, in raw counts. A list so a test can move it
# between samples: the light decision is now made from the SHAPE of a
# sequence, so a constant-valued stub cannot exercise it at all.
LIGHT = [3000]                # a dark bathroom at night


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
    """An empty bus: nothing answers, every transfer raises.

    Deliberately empty rather than a working SGP41 stub. The odor path is
    an enhancement, and the behaviour that matters most is that a missing
    sensor leaves this controller identical to the one that predates it --
    so every test in this file runs with the path absent, and any state
    machine change that quietly depends on it fails here.
    """

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
machine.reset_cause = lambda: 1
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
# read_light blanks the LEDs and waits for the ADC to settle. On the host the
# wait is the only part with no meaning, and sleeping through it would make a
# test nobody runs -- the fake clock is what time means here.
_time.sleep_ms = lambda ms: None

# Settings writes settings.json beside the cwd, and the real one on the
# device holds runtime overrides that must never be confused with a test's.
# So the whole run happens somewhere disposable.
TMP = tempfile.mkdtemp(prefix="fan-control-")
os.chdir(TMP)

import config as cfg                                 # noqa: E402
import settings                                      # noqa: E402
import main                                          # noqa: E402


def advance(seconds):
    CLOCK[0] += int(seconds * 1000)


def build(boot="watchdog or reset", mode=None, selftest_s=0):
    """A controller in a dark, dry, settled room, fan off, in IDLE.

    The self-test arms itself in the constructor, from the boot cause and
    from MODE as it stands on flash -- so both are set by patching what the
    constructor will read, not by poking at the object afterwards. It is off
    by default here: every test that is not about it wants a controller that
    does not grab the fan on the way up.
    """
    cfg.SELFTEST_S = selftest_s
    FAN_RPM[0] = 4200
    # main.py does `from machine import reset_cause`, so the name it calls was
    # bound at import. Patching the stub module alone changes nothing -- a
    # mistake this harness made, and one that made the test pass for the
    # wrong reason.
    cause = 1 if boot == "power-on" else 3
    machine.reset_cause = lambda: cause
    main.reset_cause = lambda: cause
    pre = settings.Settings(log=lambda *a: None)
    pre.reset()                               # a known file, not the last test's
    if mode:
        pre.set("MODE", mode)
    c = main.Controller()
    c.state = main.IDLE
    c.entered_at = CLOCK[0]
    c.rh = 53.7
    c.temp = 21.0
    c.baseline = 53.7
    c.rpm = 0
    c.light_on = False
    c.dark_since = CLOCK[0] - 3600_000       # dark for an hour
    # Seed the light detector as a settled dark room, the way an hour of
    # samples would have left it. Without this the first sample of every
    # test reads as a step away from an unlearned ambient.
    LIGHT[0] = 3000
    c.lightdet.amb = 3000.0
    c.lightdet.prev = 3000
    return c


# What the physical fan does when it is energised. 0 is a fan that has died
# in the ceiling, which is the whole point of the self-test.
FAN_RPM = [4200]


def step(c, passes=1):
    """One pass of the loop's sampling half, in the loop's own order.

    The order is not incidental and the bug this file was written for lived
    in it: the tach is read from the pin as the previous pass left it, the
    self-test gets first refusal on the fan, and only then do the health
    check and decide() run. drive_outputs also blinks LEDs and polls the
    button, neither of which this file is about, so its one line that
    matters stands in for it.
    """
    for _ in range(passes):
        driven = bool(c.fan.value())
        c.rpm = FAN_RPM[0] if driven else 0
        c.rpm_idle = 0
        c.read_light()
        # In the loop's order: after the climate read, which supplies the
        # odor sensor's compensation. A no-op while c.voc is None, which is
        # every test above the odor section.
        c.read_voc()
        if not c.step_selftest():
            c.check_fan_health()
            c.decide()
        c.set_fan(c.selftest == "running"
                  or c.state in (main.RUN, main.BOOST))


def mode(c, value):
    ok, result = c.st.set("MODE", value)
    assert ok, result


print("\nmode overrides, and what happens when they are released\n")

# The reported bug. Force on, then back to Auto in a room that wants
# nothing: the fan must stop, not serve out the rest of MIN_RUN_S.
c = build()
mode(c, "Force on")
step(c)
check("force on runs the fan",
      c.state == main.RUN and c.fan.value() == 1, c.state)

advance(20)
step(c)
mode(c, "Auto")
step(c)
check("releasing force on in a settled room stops the fan",
      c.state == main.IDLE and c.fan.value() == 0,
      "%s fan=%d" % (c.state, c.fan.value()))

# ...and it stays stopped, rather than being re-started by the automatic
# logic a pass later.
advance(10)
step(c, 3)
check("and it stays stopped", c.state == main.IDLE and c.fan.value() == 0,
      c.state)

# The room's own verdict still wins. Released into a humid room, the fan
# keeps running -- on the automatic logic's authority this time, and
# without the fan ever seeing a stop.
c = build()
mode(c, "Force on")
step(c)
advance(20)
c.rh = c.baseline + cfg.RH_RISE_ON + 1       # someone is showering
mode(c, "Auto")
step(c)
check("releasing into a humid room keeps the fan on",
      c.state == main.RUN and c.fan.value() == 1,
      "%s exc=%+.1f" % (c.state, c.excess_rh))
check("and the fan is not cycled to get there", c.fan_off_at is None)

# The workaround that used to be necessary must not regress.
c = build()
mode(c, "Force on")
step(c)
advance(20)
mode(c, "Force off")
step(c)
check("force off stops the fan at once",
      c.state == main.IDLE and c.fan.value() == 0, c.state)
mode(c, "Auto")
step(c)
check("releasing force off leaves a settled room alone",
      c.state == main.IDLE and c.fan.value() == 0, c.state)

print("\nMIN_RUN_S still protects the runs it is there for\n")

# The guard itself. An automatic run that the room stops wanting after ten
# seconds must still see out MIN_RUN_S -- that is the short-cycling this
# exists to prevent, and the fix must not have traded it away.
c = build()
c.rh = c.baseline + cfg.RH_RISE_ON + 1
step(c)
check("humidity starts an automatic run",
      c.state == main.RUN and c.fan.value() == 1, c.state)
c.rh = c.baseline                            # dried out, implausibly fast
advance(10)
step(c)
check("an automatic run is not short-cycled",
      c.state == main.RUN and c.fan.value() == 1,
      "ran %.0fs of %ds" % (c.fan_seconds(), c.st.MIN_RUN_S))
advance(cfg.MIN_RUN_S)
step(c)
check("and it ends once MIN_RUN_S has passed",
      c.state == main.IDLE and c.fan.value() == 0,
      "ran %.0fs" % c.fan_seconds())

# A forced run must not spend the automatic cap. Airing the room out for
# longer than MAX_RUN_S and then returning to Auto with the room still
# humid used to hand the automatic logic a run that was already over its
# limit, which latched it on the spot.
c = build()
mode(c, "Force on")
step(c)
advance(cfg.MAX_RUN_S + 300)
step(c)
check("a forced run ignores the cap, as an override should",
      c.state == main.RUN and not c.maxed,
      "%s maxed=%s after %.0f min" % (c.state, c.maxed, c.fan_seconds() / 60))
c.rh = c.baseline + cfg.RH_RISE_ON + 1
mode(c, "Auto")
step(c, 2)
check("and does not spend the automatic cap on the way out",
      c.state == main.RUN and not c.maxed,
      "%s maxed=%s ran=%.0fs" % (c.state, c.maxed, c.fan_seconds()))

print("\nthe power-on self-test\n")

# A healthy fan. The test drives it, the tach answers, and it gets out of
# the way -- the fan must not be left running and the state must not be
# touched.
c = build(boot="power-on", selftest_s=6)
check("a power-on arms the self-test", c.selftest == "running")
step(c)
check("it drives the fan before any state wants to",
      c.fan.value() == 1 and c.state == main.IDLE, c.state)
advance(cfg.SPINUP_S + 1)
step(c)
check("a fan that turns passes",
      c.selftest == "passed" and c.state == main.IDLE, c.selftest)
check("and is switched back off", c.fan.value() == 0)

# A dead fan: driven, and the tach reports nothing.
c = build(boot="power-on", selftest_s=6)
FAN_RPM[0] = 0
step(c)
advance(cfg.SPINUP_S + 1)
step(c)
check("a fan that does not turn is still being given time",
      c.state == main.IDLE and c.fan.value() == 1,
      "%.0fs of %ds" % (c.fan_seconds(), cfg.SELFTEST_S))
advance(cfg.SELFTEST_S)
step(c)
check("a fan that does not turn faults",
      c.state == main.FAULT and c.fault_kind == "selftest", c.fault_kind)
check("and the fan is not left energised", c.fan.value() == 0)
check("and the fault says what to check",
      "did not turn at power-on" in c.fault_reason and "tach" in c.fault_reason,
      c.fault_reason[:58])

# It must not expire on its own. A mid-run stall backs off and retries; a
# fan that never turned has nothing to retry, and a fault that clears
# itself unseen is the same as no fault at all.
advance(cfg.FAULT_RETRY_S * 3)
step(c, 3)
check("a self-test fault does not clear on a timer",
      c.state == main.FAULT and c.fan.value() == 0,
      "%s after %d min" % (c.state, cfg.FAULT_RETRY_S * 3 // 60))

# And Force on must not drive a fan that would not turn. FAULT already
# outranks the mode overrides; this is the case that reasoning exists for.
mode(c, "Force on")
step(c, 2)
check("Force on cannot run the fan through a self-test fault",
      c.state == main.FAULT and c.fan.value() == 0,
      "%s fan=%d" % (c.state, c.fan.value()))

# But a human can clear it.
mode(c, "Auto")
c.handle_button("short")
step(c)
check("the button clears it", c.state == main.IDLE and c.fault_kind is None,
      c.state)

# The three ways it declines to run.
c = build(boot="watchdog or reset", selftest_s=6)
check("an OTA or watchdog restart does not spin the fan",
      c.selftest == "skipped", str(c.selftest))
c = build(boot="power-on", mode="Force off", selftest_s=6)
check("Force off is not overruled by a diagnostic",
      c.selftest == "skipped", str(c.selftest))
c = build(boot="power-on", selftest_s=0)
check("SELFTEST_S = 0 disables it", c.selftest == "disabled",
      str(c.selftest))
check("and the result is reported either way",
      "selftest" in c.snap.payload(c, 5000), "missing from the payload")

print("\nthe light detector, against readings this bathroom actually took\n")

# Both traces below are real, read off the device's own stored history for
# 2026-10-27 to 10-29 and converted from the percentages it records back to
# raw counts. They are the two cases the detector has to tell apart, and the
# whole point is that an absolute threshold cannot: the sunlight one peaks
# HIGHER than the fixture one.


def ramp(c, counts, seconds):
    """Move the light to `counts` over `seconds`, sampling every SAMPLE_S.

    Linear between samples, which is how the sky behaves at this timescale
    and is also the shape the 5 min history buckets record.
    """
    start = float(LIGHT[0])
    steps = max(1, int(seconds / cfg.SAMPLE_S))
    for i in range(1, steps + 1):
        LIGHT[0] = int(start + (counts - start) * i / steps)
        advance(cfg.SAMPLE_S)
        step(c)


def switch(c, on):
    """Hit the wall switch, and take the sample that sees it.

    53000 counts is what this bathroom's fixture actually reads, and 3000 is
    the room without it. Going through read_light rather than assigning
    light_on means these tests exercise the detector and the state machine
    together, which is how they fail in a ceiling.
    """
    LIGHT[0] = 53000 if on else 3000
    advance(cfg.SAMPLE_S)
    step(c)


# --- the false trigger: 10-28 14:05, 2 h 55 min of fan in a dry room ------
#
# Sunlight climbed 27.2 % -> 55.4 % over about two hours and came back down.
# The room stayed dry throughout (exc never left -0.6..+2.1). Under the old
# absolute LIGHT_ON = 22000 this crossed at 14:05 and the fan ran until
# 17:00. Nothing here may start it.
c = build()
ramp(c, 17800, 3600)                 # 08:40 -> 13:30, 4.4 % -> 27.2 %
check("a morning of rising daylight does not read as a light",
      not c.light_on, "rise %+d" % c.light_rise())
check("and does not start the fan", c.state == main.IDLE and c.fan.value() == 0,
      c.state)

ramp(c, 36300, 7200)                 # 13:30 -> 15:20, up to the 55.4 % peak
check("nor does the brightest sun this room has seen in the afternoon",
      not c.light_on, "light %d rise %+d" % (LIGHT[0], c.light_rise()))
check("the fan stayed off through all of it",
      c.state == main.IDLE and c.fan.value() == 0, c.state)
check("and the ambient tracked the sun up instead of being outrun by it",
      abs(LIGHT[0] - c.light_amb) < cfg.LIGHT_RISE_ON,
      "light %d amb %d" % (LIGHT[0], c.light_amb))

ramp(c, 3500, 2400)                  # 16:45 -> 17:30, sunset back to dark
check("and the sun going back down is not an event either",
      not c.light_on and c.state == main.IDLE, c.state)

# --- the real thing: 10-29 06:40, a pre-dawn shower -----------------------
#
# 2.5 % -> 81.1 % between two samples. 51511 counts in one step. This must
# be caught, and caught on the first sample -- a fan that starts a minute
# into a shower is a fan with a wet ceiling.
c = build()
LIGHT[0] = 53000                     # somebody hit the switch
advance(cfg.SAMPLE_S)
step(c)
check("the fixture switching on is caught in a single sample",
      c.light_on, "rise %+d" % c.light_rise())
check("and starts the fan at once",
      c.state == main.RUN and c.fan.value() == 1, c.state)

# It must also hold while the bulb is burning -- the ambient is frozen, so
# it cannot climb to meet the fixture and declare the room dark.
for _ in range(60):
    advance(cfg.SAMPLE_S)
    step(c)
check("and holds while the bulb is lit, rather than being learned away",
      c.light_on, "rise %+d after 2 min" % c.light_rise())

# And the off-edge, into a room the sun has brightened since. This is the
# trap a frozen ambient sets: pre-dawn ambient was 3000, the light has been
# on through sunrise, and the room it reveals reads 25000. A detector that
# measured against the stale 3000 would call that a second light-on and
# never let go.
LIGHT[0] = 25000
advance(cfg.SAMPLE_S)
step(c)
check("the off-edge is caught even though the room is now brighter than "
      "before it", not c.light_on, "rise %+d" % c.light_rise())
check("and the ambient re-anchored to the daylight it revealed",
      abs(c.light_amb - 25000) < 1000, "amb %d" % c.light_amb)

print("\nthe MAX_RUN_S latch, against a light that will not go out\n")

# The 10-28 bug itself, reduced. A light signal that stays asserted used to
# clear the latch on the pass after it was set, because IDLE tested the
# level and called it a new occupancy event. 175 minutes against a 60 minute
# cap. Here the light is forced on directly: this test is about the latch,
# not the detector, and it has to hold even if the detector is ever wrong.
_reanchor = cfg.LIGHT_MAX_ON_S
cfg.LIGHT_MAX_ON_S = 10 ** 9         # stand the detector's own bound down

c = build()
switch(c, True)
check("a light-on event starts a run", c.state == main.RUN, c.state)
advance(cfg.MAX_RUN_S + 1)
step(c)
check("the cap latches the run off", c.state == main.IDLE and c.maxed,
      "%s maxed=%s" % (c.state, c.maxed))
# The regression. The light is still on and the room is dry, which is
# exactly the state that used to re-RUN immediately.
advance(cfg.SAMPLE_S)
step(c, 5)
check("and a light still on does not clear it a pass later",
      c.state == main.IDLE and c.fan.value() == 0 and c.maxed,
      "%s maxed=%s light_on=%s" % (c.state, c.maxed, c.light_on))
check("the room being dry does not clear it either, while the light is on",
      c.maxed, "maxed=%s exc=%+.1f" % (c.maxed, c.excess_rh))

# A genuinely new event still gets its run. The latch rejects a level, not
# a person coming back into the room.
switch(c, False)
check("the light going off releases the latch in a dry room",
      not c.maxed and not c.light_on, "maxed=%s" % c.maxed)
switch(c, True)
check("and the next real light-on runs the fan again",
      c.state == main.RUN and c.fan.value() == 1, c.state)

cfg.LIGHT_MAX_ON_S = _reanchor

print("\nlight alone is occupancy, not an instruction to ventilate\n")

# The belt to the braces. However well the detector works, it reads one LDR
# through a window; this is what bounds a bad reading to ten minutes.
c = build()
switch(c, True)
check("the fan starts on the light", c.state == main.RUN, c.state)
advance(cfg.LIGHT_ONLY_MAX_S - cfg.MIN_RUN_S)
step(c)
check("and a light-only run is not cut short of the cap",
      c.state == main.RUN and c.fan.value() == 1,
      "ran %.0fs of %ds" % (c.fan_seconds(), cfg.LIGHT_ONLY_MAX_S))
advance(cfg.MIN_RUN_S + cfg.SAMPLE_S)
step(c)
check("past the cap, a dry room stops the fan with the light still on",
      c.state == main.IDLE and c.fan.value() == 0,
      "%s ran %.0f min" % (c.state, c.fan_seconds() / 60))
check("and it latches, so IDLE does not start the whole run again",
      c.maxed, "maxed=%s" % c.maxed)
advance(cfg.SAMPLE_S)
step(c, 5)
check("it stays off for as long as the light stays on",
      c.state == main.IDLE and c.fan.value() == 0, c.state)
check("well short of the old MAX_RUN_S", cfg.LIGHT_ONLY_MAX_S < cfg.MAX_RUN_S,
      "%ds vs %ds" % (cfg.LIGHT_ONLY_MAX_S, cfg.MAX_RUN_S))

# ...but a wet room is a real reason, and the cap must not touch it. This is
# the line between "someone left the light on" and "someone is showering".
c = build()
switch(c, True)
c.rh = c.baseline + cfg.RH_RISE_ON + 1       # the shower starts
advance(cfg.LIGHT_ONLY_MAX_S + cfg.MIN_RUN_S)
step(c)
check("a humid room runs straight through the light-only cap",
      c.state == main.RUN and c.fan.value() == 1,
      "%s exc=%+.1f ran %.0f min" % (c.state, c.excess_rh,
                                     c.fan_seconds() / 60))
check("and is not latched by it", not c.maxed, "maxed=%s" % c.maxed)
# And it still ends the ordinary way, once the room is actually dry.
c.rh = c.baseline
switch(c, False)
advance(cfg.POST_LIGHT_S + cfg.SAMPLE_S)
step(c, 2)
check("then ends on the humidity, the way it always did",
      c.state == main.IDLE and c.fan.value() == 0,
      "%s exc=%+.1f" % (c.state, c.excess_rh))

print("\n\nodor sensing: the driver, the detector, and the gates\n")

import sgp41                                         # noqa: E402
import voc as vocmod                                 # noqa: E402


class EchoBus:
    """Answers the way the real part does, with real CRCs over the wire.

    Built so the CRC check is a round trip rather than an assertion about a
    constant: the reply is framed by the same crc8 the driver verifies with,
    so a wrong polynomial would have to be wrong identically in both
    directions to pass.
    """

    def __init__(self, voc=29000, nox=18000, corrupt=False, dead=False):
        self.voc, self.nox = voc, nox
        self.corrupt, self.dead = corrupt, dead
        self.last = None
        self.writes = 0

    def writeto(self, addr, buf):
        if self.dead:
            raise OSError(5)
        self.writes += 1
        self.last = (buf[0] << 8) | buf[1]

    def readfrom(self, addr, n):
        if self.dead:
            raise OSError(5)
        words = {0x3682: [0x0000, 0x068B, 0x380D],
                 0x280E: [0xD400],
                 0x2612: [self.voc],
                 0x2619: [self.voc, self.nox]}.get(self.last)
        if words is None:
            raise OSError(5)
        out = bytearray()
        for w in words[:n // 3]:
            wb = w.to_bytes(2, "big")
            out += wb + bytes((sgp41.crc8(wb),))
        if self.corrupt:
            out[2] ^= 0xFF
        return bytes(out)


class FakeSGP41:
    """The driver's contract, without the bus: a reading, or None."""

    def __init__(self, voc=29000):
        self.voc = voc
        self.ok = True
        self.fails = 0
        self.reads = 0
        self.raises = False

    def read(self, rh=None, t=None):
        self.reads += 1
        if self.raises:
            raise OSError(5)
        return None if self.voc is None else (self.voc, 18000)

    def start(self):
        pass

    def serial(self):
        return "0000068B380D"

    def heater_off(self):
        pass


# -- the driver -----------------------------------------------------------

check("crc8 matches the datasheet vector", sgp41.crc8(b"\xbe\xef") == 0x92,
      "0x%02X" % sgp41.crc8(b"\xbe\xef"))

bus = EchoBus()
s = sgp41.SGP41(bus)
check("serial round-trips through CRC", s.serial() == "0000068B380D",
      s.serial())
check("self test reads 0xD400 as a pass", s.self_test() is True)

bad = sgp41.SGP41(EchoBus(corrupt=True))
check("a corrupted word is rejected, not returned", bad.serial() is None)

# Conditioning must not block: the WDT is armed at 8 s and a 10 s wait
# anywhere in the loop is a reset loop. So the window is a deadline, and
# every call inside it returns at once with no reading.
s = sgp41.SGP41(EchoBus())
s.start()
check("conditioning reports itself as such", s.conditioning is True)
nones = sum(1 for _ in range(4) if (advance(2), s.read(45.0, 22.0))[1] is None)
check("read() returns None throughout the window, never blocking", nones == 4,
      "%d of 4" % nones)
advance(3)
check("the window closes on its own deadline", s.conditioning is False)
got = s.read(45.0, 22.0)
check("and a real reading follows", got == (29000, 18000), "%s" % (got,))

# Compensation ticks, and the clamp that stops a nonsense reading biasing
# every sample after it.
check("ticks default to 50 %RH / 25 C when climate is absent",
      sgp41.ticks(None, None) == (0x8000, 0x6666))
check("ticks clamp rather than wrap", sgp41.ticks(150, 999) == (0xFFFF, 0xFFFF),
      "%s" % (sgp41.ticks(150, 999),))

# Stand-down, so a dead part stops costing a bus transaction and a 55 ms
# block on every pass.
dead = sgp41.SGP41(EchoBus(dead=True), max_fails=3, log=lambda *a: None)
dead._until = None
for _ in range(3):
    dead.read(45.0, 22.0)
check("stands down after max_fails", dead.ok is False and dead.fails == 3,
      "ok=%s fails=%d" % (dead.ok, dead.fails))
before = dead.i2c.writes
dead.read(45.0, 22.0)
check("and then stops touching the bus", dead.i2c.writes == before)

# -- the detector ---------------------------------------------------------

det = vocmod.Detector(settings.Settings(log=lambda *a: None),
                      log=lambda *a: None)
# None, not 0. An unseeded detector has no opinion, and 0 is a real
# reading -- air exactly as clean as normal.
check("unseeded detector reports None, not 0", det.excess(28000) is None)
det.update(29000, True)
# The discriminating case, and the reason the distinction exists: a seeded
# detector reading exactly at baseline must say 0 and not None. Returning
# None for both, or 0 for both, passes every other assertion here.
check("a reading exactly at baseline is 0, not None",
      det.excess(29000) == 0 and det.excess(29000) is not None)
check("first quiet sample seeds the baseline", det.base == 29000.0,
      "%s" % det.base)
# The sign: the raw signal falls as the air gets worse.
check("a FALLING raw count means worse air", det.excess(28800) == 200,
      "%d" % det.excess(28800))
check("a rising raw count is not an event", det.excess(29200) == -200,
      "%d" % det.excess(29200))
det.update(20000, False)
check("a busy room does not teach the baseline", det.base == 29000.0,
      "%s" % det.base)
det.relearn()
check("relearn drops the baseline", det.base is None)

# -- the gates ------------------------------------------------------------


def armed(rise=200, fall=50, base=29000.0, raw=28700):
    """A controller in a dark, dry, settled room with a working sensor."""
    c = build()
    c.voc = FakeSGP41(raw)
    c.vocdet.base = base
    # Prime the last reading too, the way a settled device holds one. Without
    # this, excess_voc is 0 until read_voc() has run, and a test that pokes
    # the gates directly reads 0 and proves nothing.
    c.vocdet.raw = raw
    # RISE first: _enforce_order pushes the other one out of the way of
    # whichever was written last.
    c.st.set("VOC_RISE_ON", rise)
    c.st.set("VOC_FALL_OFF", fall)
    return c


c = armed()
step(c)
check("odor starts the fan in a dark, dry room",
      c.state == main.RUN and c.fan.value() == 1,
      "%s exc=%s" % (c.state, c.excess_voc))
# The controller has to record WHICH test it passed, not just that it
# passed one. Without this the app has to guess, and it guessed
# "Clearing humidity" over a room 0.9 % above baseline while the fan ran
# on odor -- the status page describing a cause that was not the cause.
check("...and says odor was the reason",
      c.state_reason.startswith("odor"), repr(c.state_reason))

# The humidity path must still name itself, or the fix above just moves
# the lie to the other branch.
c2 = armed()
c2.rh = c2.baseline + c2.st.RH_RISE_ON + 1
step(c2, 2)
check("a humidity run says humidity was the reason",
      c2.state == main.RUN and c2.state_reason.startswith("humidity"),
      "%s %r" % (c2.state, c2.state_reason))

# Observe-only is the shipping default, and it has to be genuinely inert.
c = armed(rise=0, fall=0, raw=20000)
step(c)
check("VOC_RISE_ON of 0 never triggers, however bad the air",
      c.state == main.IDLE and c.fan.value() == 0,
      "%s exc=%s" % (c.state, c.excess_voc))

# A damp room suppresses odor triggering for VOC_RECOVER_S, because drying
# moves the MOX signal on its own and would hand the fan a second reason to
# run just as the first expired.
c = armed()
c.rh = c.baseline + 5.0          # above RH_FALL_OFF, below RH_RISE_ON
step(c)
check("a damp room gates the odor trigger off",
      c.state == main.IDLE, "%s" % c.state)
c.rh = c.baseline
step(c)
check("and stays gated through VOC_RECOVER_S",
      c.state == main.IDLE, "%s" % c.state)
advance(cfg.VOC_RECOVER_S + 1)
step(c)
check("then arms again once the recovery window passes",
      c.state == main.RUN, "%s" % c.state)

# The latch. Every independent reason to run must be quiet before it lets
# go, or a stuck-high odor reading releases it on humidity alone.
c = armed()
c.maxed = True
c.rh = c.baseline                # humidity half satisfied
step(c)
check("the maxed latch holds while the odor signal is still high",
      c.maxed is True)
c.voc.voc = 29000                # air clears
step(c, 2)
check("and releases once both signals are quiet", c.maxed is False)

# A run ends on the room, not a timer.
c = armed()
step(c)
advance(cfg.MIN_RUN_S + cfg.SAMPLE_S)
step(c)
check("a run is held open by odor alone", c.state == main.RUN,
      "%s ran=%ds" % (c.state, c.fan_seconds()))
c.voc.voc = 29000
step(c, 2)
check("and ends when the air clears", c.state == main.IDLE,
      "%s exc=%s" % (c.state, c.excess_voc))

# -- absence, and failure -------------------------------------------------
#
# The one that matters most. This path is an enhancement; none of it gets a
# vote on whether the humidity path works.

c = build()
check("an empty bus leaves the odor path disabled", c.voc is None)
check("and reports no excess at all, rather than zero excess",
      c.excess_voc is None)
step(c)
check("controller still idles normally without it",
      c.state == main.IDLE and c.fan.value() == 0, c.state)

c = armed()
c.voc.raises = True
step(c, 3)
check("a sensor that raises never reaches FAULT",
      c.state != main.FAULT, c.state)

c = armed(raw=None)              # driver returning None, as while conditioning
step(c)
check("a sensor with no reading yet cannot trigger",
      c.state == main.IDLE, c.state)

# A stood-down sensor must not hold a run open on its last good reading.
c = armed()
step(c)
check("odor run started", c.state == main.RUN)
# Stand it down with the air still reading BAD, and leave the baseline in
# place. This is the shape that mattered: read() returns None for ever
# after, so the detector keeps its last reading, and an _voc_active() that
# did not test `ok` would compare that frozen value against VOC_FALL_OFF on
# every pass -- holding the fan to MAX_RUN_S, latching, and never releasing.
# Written without the relearn() that used to be here, which made this pass
# by testing a fix that had not been written.
c.voc.ok = False
c.rh = c.baseline
advance(cfg.MIN_RUN_S + cfg.POST_LIGHT_S + cfg.SAMPLE_S)
step(c, 2)
check("a stood-down sensor cannot hold the fan on",
      c.state == main.IDLE and c.fan.value() == 0,
      "%s exc=%s" % (c.state, c.excess_voc))
check("...and reports absence, not its last reading",
      c.vocdet.raw is None, "raw=%s" % c.vocdet.raw)

# The gate itself, with the stale reading deliberately still in place. There
# is exactly one pass between the sensor standing down inside read() and
# read_voc() noticing and clearing the reading, and on that pass decide()
# runs against a frozen value. Routing this through read_voc() instead would
# test the clearing and leave the gate unproven -- which is how the first
# version of this test passed with the `ok` term deleted.
c = armed()
c.voc.ok = False                 # raw left exactly as it was
check("a stood-down sensor is inert while its last reading is still held",
      c._voc_active() is False and c._voc_quiet() is True,
      "active=%s quiet=%s exc=%s"
      % (c._voc_active(), c._voc_quiet(), c.excess_voc))

# The same thing one level up: a latch that a dead sensor could not release.
c = armed()
c.maxed = True
c.rh = c.baseline
c.voc.ok = False
step(c, 2)
check("a stood-down sensor cannot hold the maxed latch closed",
      c.maxed is False)

failed = results.count(False)
print("\n%d/%d passed\n" % (len(results) - failed, len(results)))
sys.exit(1 if failed else 0)
