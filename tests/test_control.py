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
    an enhancement, and the behavior that matters most is that a missing
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
    # Writes are spaced SAVE_GAP_MS apart now (audit NV-6), and this hands
    # the file to a second Settings -- the Controller's -- which the device
    # never does. Flush so the handoff sees what was set.
    pre.flush(force=True)
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
# Force off DEFERS the test rather than skipping it, and the difference is
# the whole point. Force off lives in settings.json and survives power
# cuts, so skipping meant a fan that was never proven again -- silently,
# and indistinguishably from one that passed.
c = build(boot="power-on", mode="Force off", selftest_s=6)
check("Force off is not overruled by a diagnostic",
      c.selftest == "deferred" and c.fan.value() == 0, str(c.selftest))
step(c, 3)
check("...and a deferred test does not creep into spinning the fan",
      c.fan.value() == 0 and c.selftest == "deferred",
      "%s fan=%s" % (c.selftest, c.fan.value()))

# Returning to Auto is both the moment the objection expires and the moment
# anyone wants to know the fan works, so that is where it gets its turn.
mode(c, "Auto")
step(c)
check("leaving Force off arms the deferred test", c.selftest == "running",
      str(c.selftest))
advance(cfg.SPINUP_S + 1)
step(c)
check("...and the fan is proved to turn", c.selftest == "passed",
      "%s rpm=%s" % (c.selftest, c.rpm))

# Once proven, toggling the mode must not keep restarting it: a six second
# burst of fan every time somebody touches a dropdown is its own fault.
mode(c, "Force off")
step(c)
mode(c, "Auto")
step(c)
check("a passed test is not re-run by toggling the mode",
      c.selftest == "passed", str(c.selftest))

# A FAILED test must not be re-rolled by toggling a mode. A fan that did
# not turn is a fault to go and look at, not a dice throw to repeat until
# it comes up passed -- and "leave Force off" would be an easy accidental
# way to clear the one piece of evidence.
c2 = build(boot="power-on", mode="Force off", selftest_s=6)
c2.selftest = "failed"
mode(c2, "Auto")
step(c2)
check("a failed test is not re-rolled by leaving Force off",
      c2.selftest == "failed", str(c2.selftest))

# And FAULT is not a state to start spinning a fan in.
c3 = build(boot="power-on", mode="Force off", selftest_s=6)
c3.set_state(main.FAULT, "bench")
mode(c3, "Auto")
step(c3)
check("a faulted controller does not arm the deferred test",
      c3.selftest == "deferred", str(c3.selftest))
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

# Everything from here down is the LEVEL path: odor judged against the
# hour-long baseline, wherever the room's occupant is. VISIT_ODOR = False
# restores it exactly, and these sections pin that it still does. The visit
# path that replaced it as the default has its own file, tests/test_visit.py.
cfg.VISIT_ODOR = False

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

    def __init__(self, voc=29000, nox=18000):
        self.voc = voc
        self.nox = nox
        self.ok = True
        self.fails = 0
        self.reads = 0
        self.raises = False
        # Warm, by default: every test here but the warm-up one is about a
        # device that has been running a while, which is the normal case.
        # Per pixel, because the driver is -- the NOx pixel takes about two
        # hours against the VOC pixel's minutes.
        self.settled_voc = True
        self.settled_nox = True

    def set_settled(self, on, nox=None):
        """Both pixels, or each on its own."""
        self.settled_voc = on
        self.settled_nox = on if nox is None else nox

    def read(self, rh=None, t=None):
        self.reads += 1
        if self.raises:
            raise OSError(5)
        return None if self.voc is None else (self.voc, self.nox)

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

# Readable is not settled, and settling is not a timer.
#
# This was a 60 s deadline, from a measurement that watched the ramp for a
# few minutes and called it finished. Both pixels were still climbing long
# after: the VOC pixel at ~4 ticks/min at half an hour, the NOx pixel for
# about two hours. So the gate now asks the element whether it has stopped
# moving, and these tests replay REAL traces through it -- the invented
# ramps all look like ramps, which is the one shape that was never the
# problem.

check("a readable sensor is not yet a settled one",
      s.conditioning is False and s.settled_voc is False)
fresh = sgp41.SGP41(EchoBus())
check("unstarted reports unsettled, not settled",
      fresh.settled_voc is False and fresh.settled_nox is False)

# The NOx warm-up as actually recorded, 2026-11-02, every 20 s from 2.5 to
# 19.7 minutes of uptime. This is the exact trace the OLD two-minute
# flatness test pronounced settled at its last sample -- 15 ticks of drift
# across 120 s -- and on the strength of that the baselines were relearned
# 1,400 ticks away from where the pixel eventually came to rest.
NOX_RAMP = [17442, 17363, 17295, 17226, 17167, 17126, 17078, 17048, 17021,
            17009, 16976, 16962, 16967, 16987, 17011, 17006, 17015, 17008,
            17001, 16991, 16984, 16977, 16957, 16956, 16953, 16941, 16927,
            16922, 16913, 16900, 16896, 16880, 16886, 16878, 16874, 16873,
            16865, 16866, 16851, 16858, 16852, 16843, 16831, 16821, 16839,
            16823, 16811, 16812, 16811, 16808, 16811, 16811]

f = sgp41._Flat(sgp41._FLAT_TICKS_NOX)
for v in NOX_RAMP:
    advance(20)
    f.feed(v, CLOCK[0])
check("a real warm-up ramp is never flat, however calm its last minutes",
      f.flat is False, "span %s ticks" % f.span)

# And the specific failure, stated as itself: the tail of that ramp looks
# quiet over two minutes and is nothing of the kind over fifteen.
tail = NOX_RAMP[-6:]
check("...and the quiet-looking tail is what the old test measured",
      max(tail) - min(tail) <= 20 and f.span > sgp41._FLAT_TICKS_NOX,
      "tail spread %d, window span %s" % (max(tail) - min(tail), f.span))

# Twenty-five real quiet minutes from the overnight series, every 60 s,
# taken four hours in when both pixels had genuinely settled.
NOX_QUIET = [16254, 16257, 16250, 16248, 16258, 16250, 16249, 16258, 16267,
             16260, 16269, 16254, 16262, 16260, 16260, 16262, 16262, 16272,
             16265, 16265, 16269, 16270, 16270, 16274]

f = sgp41._Flat(sgp41._FLAT_TICKS_NOX)
for v in NOX_QUIET:
    advance(60)
    f.feed(v, CLOCK[0])
check("a real settled stretch is flat", f.flat is True,
      "span %s ticks" % f.span)

# The 4 Nov bathroom boot. VOC's flattest 15-minute window was 51 ticks,
# which is why a shared 45 never fired for it, and 71 is past the 70 that
# replaced it. Same samples, two limits: loosening NOx to match would be a
# different change, and one nobody asked for.
def _driven(limit, spread):
    gate = sgp41._Flat(limit)
    for v in (10000, 10000 + spread, 10000, 10000):
        advance(300)
        gate.feed(v, CLOCK[0])
    return gate

voc51 = _driven(sgp41._FLAT_TICKS_VOC, 51)
nox51 = _driven(sgp41._FLAT_TICKS_NOX, 51)
check("VOC settles on its 51-tick bathroom window",
      voc51.flat is True and voc51.span == 51, "span %s" % voc51.span)
check("NOx does not settle on that same window",
      nox51.flat is False and nox51.span == 51, "span %s" % nox51.span)
voc71 = _driven(sgp41._FLAT_TICKS_VOC, 71)
check("71 ticks is still a ramp for VOC",
      voc71.flat is False and voc71.span == 71, "span %s" % voc71.span)

# No verdict at all until there is enough evidence for one. An empty
# tracker reporting "flat" would settle every pixel on its first sample.
f = sgp41._Flat(sgp41._FLAT_TICKS_NOX)
for _ in range(4):
    advance(60)
    f.feed(16260, CLOCK[0])
check("a tracker with less than a full window has no opinion",
      f.span is None and f.flat is False)

# Per pixel, which is the point of the rewrite: one gate for both would
# hold the VOC baseline -- the one the fan acts on -- for the two hours the
# NOx pixel takes.
bus = EchoBus(voc=29000, nox=18000)
s2 = sgp41.SGP41(bus, log=lambda *a: None)
s2.start()
advance(11)
for i in range(40):
    bus.nox = 18000 + (i % 2) * 400       # a pixel still thrashing
    advance(30)
    s2.read(45.0, 22.0)
check("a steady pixel settles while a moving one does not",
      s2.settled_voc is True and s2.settled_nox is False,
      "voc=%s nox=%s" % (s2.settled_voc, s2.settled_nox))

# Latched. Flatness DETECTS the end of warm-up; it is not a standing
# condition, and an element does not un-warm. Unlatched it would go False
# during any real event -- exactly when _voc_learning()'s hold-cap escape
# needs learning permitted -- and the odor path would deadlock on a stale
# baseline, which is the bug VOC_HOLD_MAX_S exists to prevent.
for i in range(20):
    bus.voc = 29000 + (i % 2) * 2000      # a large, real-looking event
    advance(30)
    s2.read(45.0, 22.0)
check("a settled pixel stays settled through an event",
      s2.settled_voc is True, "voc=%s" % s2.settled_voc)

# The ceiling, because a gate that never opens is a sensor that never
# works. A pixel that refuses to settle is seeded anyway, late and loudly.
notes = []
bus3 = EchoBus()
s3 = sgp41.SGP41(bus3, log=lambda *a: notes.append(a[0]),
                 settle_ceiling_s=600)
s3.start()
advance(11)
for i in range(30):
    bus3.voc = 29000 + (i % 2) * 3000
    bus3.nox = 18000 + (i % 2) * 3000
    advance(30)
    s3.read(45.0, 22.0)
check("a pixel that never goes flat settles on the ceiling",
      s3.settled_voc is True and s3.settled_nox is True)
check("...and says so, rather than passing for a measured settle",
      any("ceiling" in n for n in notes), "%s" % notes[:1])

# start() re-arms. The heater has been off, so flatness established before
# it describes a different element temperature.
s3.start()
check("a restart drops the latch and the evidence with it",
      s3.settled_voc is False and s3.settled_nox is False
      and s3._flat[0].span is None)

# The page's countdown. ready_s is when the gate is allowed to look, and
# left_s is when the ceiling seeds a baseline anyway. Only the second is a
# promise: the first just says the window is not full yet.
clock = sgp41.SGP41(EchoBus(), log=lambda *a: None)
check("an unstarted sensor has no settle clock", clock.settle_left() is None)
clock.start()
ready, left = clock.settle_left()
check("conditioning is part of the earliest settle, not free",
      15 * 60 < ready <= 15 * 60 + 10 and left >= 119 * 60,
      "ready %s left %s" % (ready, left))
advance(11)
clock.read(45.0, 22.0)
ready, left = clock.settle_left()
check("the fifteen minutes start at the first reading",
      ready == 15 * 60, "ready %s left %s" % (ready, left))
advance(5 * 60)
clock.read(45.0, 22.0)
ready, left = clock.settle_left()
check("one finished block leaves ten minutes",
      ready == 10 * 60, "ready %s" % ready)
advance(5 * 60)
clock.read(45.0, 22.0)
ready, left = clock.settle_left()
check("two finished blocks leave five minutes",
      ready == 5 * 60 and clock.settled_voc is False, "ready %s" % ready)
# A gap does not close more than the one block the next reading finishes.
# The loop samples every couple of seconds, so this is the shape it sees.
advance(5 * 60)
clock.read(45.0, 22.0)
check("a full quiet window settles, and the countdown stops",
      clock.settled_voc is True and clock.settle_left() is None,
      "settled %s" % clock.settled_voc)

# And the snapshot actually carries it. The page cannot invent this from
# "baseline is null": that is also a settled pixel waiting on a noisy room.
shown = build()
shown.voc = sgp41.SGP41(EchoBus(), log=lambda *a: None)
shown.voc.start()
got = shown.snap.payload(shown, 0)
check("the settle clock is published while the pixel is waiting",
      got.get("voc_ready_s") is not None and got.get("voc_left_s") is not None,
      "ready %s left %s" % (got.get("voc_ready_s"), got.get("voc_left_s")))
shown.voc._settled[0] = True
got = shown.snap.payload(shown, 0)
check("a settled pixel publishes no countdown",
      got.get("voc_left_s") is None and got.get("voc_ready_s") is None,
      "%s / %s" % (got.get("voc_left_s"), got.get("voc_ready_s")))

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

# The same class serves the NOx pixel, so the one thing that must hold is
# that an instance's own alpha beats the config default. Two detectors
# sharing cfg.VOC_BASELINE_A is a choice; two detectors unable to differ
# would be a design that cannot be changed when the NOx measurement lands.
seen = []
fast = vocmod.Detector(settings.Settings(log=lambda *a: None),
                       log=lambda *a: seen.append(a[0]), alpha=0.5,
                       name="nox")
fast.update(1000, True)
fast.update(2000, True)
# THE ASYMMETRIC BASELINE, and the direction trap underneath it.
#
# Toward clean air the baseline may move fast -- sitting on the clean level
# is its job. Toward worse air it must crawl, or an event teaches it to
# ignore itself. One symmetric weight served both and was tuned for the
# dangerous direction, which left it far too slow for the safe one: after
# the 2026-11-06 restart the element's clean level climbed 249 counts in
# four hours, the baseline never arrived, and a real 54-count event read -32.
def det_named(name, alpha=0.1, **kw):
    return vocmod.Detector(settings.Settings(log=lambda *a: None),
                           log=lambda *a: None, alpha=alpha, name=name, **kw)

UP = getattr(cfg, "VOC_BASELINE_UP", 1)
check("the config carries an upward multiplier at all", UP > 1, "UP=%s" % UP)

# VOC: clean air is the HIGH side, because the count falls as air worsens.
d = det_named("voc")
d.update(30000, True)
d.update(30100, True)                       # air cleaning -- chase it
fastmove = d.base - 30000.0
d2 = det_named("voc")
d2.update(30000, True)
d2.update(29900, True)                      # air worsening -- hold back
slowmove = 30000.0 - d2.base
check("the VOC baseline moves faster toward clean air than toward bad",
      fastmove > slowmove * 2,
      "up %.1f vs down %.1f" % (fastmove, slowmove))
check("...by the configured multiplier",
      abs(fastmove / slowmove - UP) < 0.5,
      "ratio %.2f, UP=%s" % (fastmove / slowmove, UP))

# NOx: the SAME class, the OPPOSITE direction. Its count RISES as the air
# gets worse, so clean is the low side. If this inherits the VOC direction,
# the baseline sprints at a cooking event and crawls back from it -- it
# would calibrate itself deaf to exactly the thing it is fitted to catch,
# and nothing else in the suite would notice.
n = det_named("nox")
n.update(16000, True)
n.update(15900, True)                       # falling = cleaning, for NOx
nfast = 16000.0 - n.base
n2 = det_named("nox")
n2.update(16000, True)
n2.update(16100, True)                      # rising = combustion, for NOx
nslow = n2.base - 16000.0
check("the NOx baseline treats a FALLING count as the clean direction",
      nfast > nslow * 2, "down %.1f vs up %.1f" % (nfast, nslow))
check("...so a rising NOx count is the one it refuses to chase",
      nslow < fastmove, "nox up %.1f vs voc up %.1f" % (nslow, fastmove))

# The direction must come from the pixel, not from whoever remembered to
# pass a flag: Detector(name="nox") with the VOC direction is a silent and
# entirely plausible-looking wrong answer.
check("a detector named nox gets the NOx direction without being told",
      det_named("nox").clean_is_high is False)
check("...and the default pixel gets the VOC one",
      det_named("voc").clean_is_high is True)
check("...with an explicit flag still winning",
      det_named("nox", clean_is_high=True).clean_is_high is True)

check("an instance alpha overrides the config default", fast.base == 1500.0,
      "%s" % fast.base)
check("...and the seed log says which pixel seeded",
      seen and seen[0].startswith("nox:"), repr(seen[:1]))

# -- the gates ------------------------------------------------------------


def armed(rise=200, fall=50, base=29000.0, raw=28700, nox_base=18000.0):
    """A controller in a dark, dry, settled room with a working sensor."""
    c = build()
    c.voc = FakeSGP41(raw)
    c.vocdet.base = base
    # Prime the last reading too, the way a settled device holds one. Without
    # this, excess_voc is 0 until read_voc() has run, and a test that pokes
    # the gates directly reads 0 and proves nothing.
    c.vocdet.raw = raw
    # The NOx pixel is seeded too, because since _nox_ready() the odor path
    # does not act until it is -- a fully warmed device has both, and that
    # is what "armed" is meant to describe. Quiet by default: base equals
    # raw, so excess_nox is 0 and no veto is in play. Pass nox_base=None for
    # the tests that are specifically about the unseeded case.
    if nox_base is not None:
        c.noxdet.base = nox_base
        c.noxdet.raw = c.voc.nox
        c.voc_ready_since = _time.ticks_ms()
    # RISE first: _enforce_order pushes the other one out of the way of
    # whichever was written last.
    c.st.set("VOC_RISE_ON", rise)
    c.st.set("VOC_FALL_OFF", fall)
    return c


# The baseline lives in RAM, so it is reseeded after every power cut -- and
# the sample it would be seeded from comes from an element still warming.
# Observed on the device as `base 29593 / raw 30094 / odr -500`, which left
# the odor path deaf for the hour the EWMA took to recover.
cold = build()
cold.voc = FakeSGP41(30094)
cold.voc.set_settled(False)
# The gate moved: _voc_learning() answers for the ROOM, and the element is
# asked per pixel at the call site, so the room predicate is now True here
# and the refusal happens one level down. Asserting the outcome rather than
# the predicate is the point -- the old check would still pass if the
# per-pixel gate were dropped from read_voc() tomorrow.
check("a cold sensor leaves the room predicate alone",
      cold._voc_learning() is True and cold.voc.settled_voc is False)
cold.read_voc()
check("...so a reboot leaves the baseline unseeded, not seeded low",
      cold.vocdet.base is None, "%s" % cold.vocdet.base)
# The reading itself is still taken and still published: it is the sensor's
# honest output, and only the baseline is withheld from it.
check("...while the raw reading is still reported",
      cold.vocdet.raw == 30094 and cold.excess_voc is None,
      "raw=%s exc=%s" % (cold.vocdet.raw, cold.excess_voc))
cold.voc.set_settled(True)
cold.read_voc()
check("and the first warm sample seeds it", cold.vocdet.base == 30094.0,
      "%s" % cold.vocdet.base)

# The two pixels seed independently, which is the whole reason the gate is
# per pixel: one shared gate would mean either seeding NOx inside its own
# ramp or holding the VOC baseline back behind it.
#
# Seeding and ACTING are now different things, and this block is about
# seeding only. The VOC baseline still learns on its own schedule and does
# not wait for NOx -- that part is unchanged and is what these checks pin.
# Whether the fan may act on it is _nox_ready()'s question, tested with the
# gates further down.
split = build()
split.voc = FakeSGP41(30094)
split.voc.set_settled(True, nox=False)
split.read_voc()
check("the VOC baseline seeds while the NOx pixel is still warming",
      split.vocdet.base == 30094.0 and split.noxdet.base is None,
      "voc=%s nox=%s" % (split.vocdet.base, split.noxdet.base))
check("...reporting a VOC excess meanwhile, with NOx still silent",
      split.excess_voc == 0 and split.excess_nox is None,
      "voc=%s nox=%s" % (split.excess_voc, split.excess_nox))
# The reading is live; acting on it is not. This is the gate that changed.
check("...but the fan may not act on it until the veto is available",
      split._voc_active() is False and split._nox_ready() is False)
split.voc.set_settled(True)
split.read_voc()
check("...then NOx seeds on its own schedule",
      split.noxdet.base == 18000.0, "%s" % split.noxdet.base)

# A latched run returns to IDLE with the air still bad: state IDLE, light
# off, room dry, sensor settled -- every term the gate used to have. So the
# baseline walked onto the smell at VOC_BASELINE_A and the deviation decayed
# with the air unchanged. Measured on the device on 2026-11-02: raw flat at
# 30394 -> 30399 over twenty minutes while the baseline slid 30633 -> 30571,
# turning a real +240 into a reported +172 by forgetting.
latched = armed(rise=80, fall=30, base=30642.0, raw=30413)
latched.maxed = True
latched.rh = latched.baseline
check("a latched room with the air still bad is not learned from",
      latched._voc_learning() is False,
      "exc=%s" % latched.excess_voc)
was = latched.vocdet.base
latched.read_voc()
check("...so the baseline does not walk onto the event",
      latched.vocdet.base == was, "%s -> %s" % (was, latched.vocdet.base))
# And the other half: once the air really has cleared, learning resumes.
# A gate that failed this would freeze the baseline for good and the odor
# path would never track the room again.
#
# Twice, and the first call is the point. read_voc() decides whether to
# learn BEFORE folding in the sample it just took, so the verdict is made
# against the previous reading -- one sample of lag, by construction. Any
# other order would ask the gate about a value the baseline had already
# absorbed, which is the question it exists to get in front of.
latched.voc.voc = 30640
latched.read_voc()
check("...and the clearing sample itself is still judged on the last one",
      latched.vocdet.base == was, "%s" % latched.vocdet.base)
latched.read_voc()
check("...and resumes on the sample after it",
      latched.vocdet.base != was, "%s" % latched.vocdet.base)

# The gate is bounded, and unbounded it deadlocked. The latch releases on
# the same _voc_quiet() the gate refuses to learn through, so a baseline that
# is wrong rather than an air that is bad could neither correct itself nor let
# the latch go -- the odor path stuck until a reboot or a Relearn. Watched on
# the device on 2026-11-02: a baseline seeded 60 s after boot, while the
# element was still warming, read +166 on air with nothing wrong with it.
stale = armed(rise=80, fall=30, base=30842.0, raw=30676)
stale.rh = stale.baseline
stale.read_voc()
check("a wrong baseline starts a hold rather than correcting at once",
      stale.voc_hold_since is not None and stale._voc_learning() is False,
      "hold=%s" % (stale.voc_hold_since,))
held = stale.vocdet.base
advance(cfg.VOC_HOLD_MAX_S - 60)
check("...which still holds just under the cap",
      stale._voc_learning() is False and stale.vocdet.base == held,
      "%s" % stale.vocdet.base)
advance(120)
check("...and expires past it, so the baseline is the one that gives",
      stale._voc_learning() is True)
stale.read_voc()
check("...and the baseline then moves toward the reading",
      stale.vocdet.base != held, "%s -> %s" % (held, stale.vocdet.base))

# The deadlock itself, end to end: latched, dry, dark, and the only thing
# keeping the latch closed is an odor reading that is never going to fall.
# Before the bound this stayed latched for ever.
stuck = armed(rise=80, fall=30, base=30842.0, raw=30676)
stuck.rh = stuck.baseline
stuck.maxed = True
step(stuck, 3)
check("a latched fan with a stale baseline stays latched inside the cap",
      stuck.maxed is True)
advance(cfg.VOC_HOLD_MAX_S + 60)
# Enough samples for the EWMA to drag the baseline the 166 ticks home. The
# cap decides WHEN learning resumes, not how fast -- VOC_BASELINE_A still
# sets the pace, so this is deliberately not one sample, and a test that
# asserted it was would be asserting the wrong thing about the fix.
for _ in range(20000):
    stuck.read_voc()
    if stuck._voc_quiet():
        break
step(stuck)
check("...and the baseline recovering is what releases it",
      stuck.maxed is False,
      "exc=%s base=%s" % (stuck.excess_voc, stuck.vocdet.base))

# -- the odor latch rests rather than giving up ---------------------------
#
# MAX_RUN_S plus the latch is right for humidity and wrong for odor: the
# latch releases only when the deviation falls under VOC_FALL_OFF, which is
# exactly what cannot happen while the source is still going. Observed on
# the device on 2026-11-02 -- a run started on gas-hob odor hit the cap an
# hour later at +229, seven times the stop point, and then sat out the rest
# of the event. These assert the rest, and assert that only odor gets one.
rest = armed(rise=80, fall=30, base=30842.0, raw=30600)   # +242, dry, dark
rest.rh = rest.baseline
rest.maxed = True
rest.maxed_at = CLOCK[0]
step(rest, 2)
check("a maxed odor latch does not release at once",
      rest.maxed is True and rest.state == main.IDLE,
      "%s maxed=%s" % (rest.state, rest.maxed))
advance(cfg.VOC_REST_S - 60)
step(rest)
check("...nor a minute short of the rest", rest.maxed is True)
advance(120)
step(rest)
check("...but does once it has rested", rest.maxed is False)
# And the whole point: it then goes back to work on air that is still bad.
step(rest)
check("...and goes back to work on the event it was sitting out",
      rest.state == main.RUN and rest.fan.value() == 1,
      "%s %s" % (rest.state, rest.state_reason))

# The humidity half must NOT rest, or a room the fan cannot dry cycles for
# ever -- the exact failure MAX_RUN_S was written for, measured once as
# RUN 23:17:26 -> IDLE -> RUN 23:17:28.
wet = armed(rise=80, fall=30, base=29000.0, raw=29000)    # odor quiet
wet.rh = wet.baseline + cfg.RH_RISE_ON + 2.0              # and still wet
wet.maxed = True
wet.maxed_at = CLOCK[0]
advance(cfg.VOC_REST_S * 3)
step(wet, 2)
check("a wet room's latch never rests, however long it waits",
      wet.maxed is True and wet.state == main.IDLE,
      "%s maxed=%s exc=%+.1f" % (wet.state, wet.maxed, wet.excess_rh))

# Nor may a latch rest while the light is on: that is occupancy, and the
# light-only rules own it. A rested release here would undo the
# LIGHT_ONLY_MAX_S latch on a bad LDR reading.
litup = armed(rise=80, fall=30, base=30842.0, raw=30600)
litup.rh = litup.baseline
litup.maxed = True
litup.maxed_at = CLOCK[0]
switch(litup, True)
advance(cfg.VOC_REST_S * 2)
check("a latch held with the light on does not rest",
      litup._odor_latch_rested() is False)
switch(litup, False)

# A sensor that has stood down cannot spin the fan back up on a rest. This
# is the same shape as the stand-down checks further down: the detector
# keeps its last reading, so an ungated rest would retry for ever on a
# number from a part that is gone.
gone = armed(rise=80, fall=30, base=30842.0, raw=30600)
gone.rh = gone.baseline
gone.maxed = True
gone.maxed_at = CLOCK[0]
gone.voc.ok = False
advance(cfg.VOC_REST_S * 2)
check("a stood-down sensor cannot rest the latch open",
      gone._odor_latch_rested() is False)

# The hold is about a CONTINUOUS deviation, so air that clears must reset
# the clock. Otherwise two unrelated events an hour apart would add up to a
# cap expiry and learn straight through the second one.
resets = armed(rise=80, fall=30, base=29000.0, raw=28700)
resets.rh = resets.baseline
resets.read_voc()
check("a hold is started by bad air", resets.voc_hold_since is not None)
resets.voc.voc = 29000
# Twice, for the same reason the learning gate needs two: the hold clock is
# read from the same pre-update snapshot the learning verdict is, so the
# sample that brings the good news is still judged on the bad. Keeping the
# two on one snapshot is the point -- a hold cleared a sample earlier than
# learning resumed would be two clocks disagreeing about the same air.
resets.read_voc()
check("...is still held on the clearing sample itself",
      resets.voc_hold_since is not None)
resets.read_voc()
check("...and cleared by the sample after it",
      resets.voc_hold_since is None)

# Both pixels come off one read, and the NOx baseline is learned on the same
# verdict as the VOC one. Nothing acts on it: the sign has not been measured
# on this part, so this asserts only that it is being recorded.
both = armed(raw=29000, base=29000.0)
both.read_voc()
check("the NOx pixel is read and learned alongside the VOC one",
      both.noxdet.raw == 18000 and both.noxdet.base == 18000.0,
      "raw=%s base=%s" % (both.noxdet.raw, both.noxdet.base))
check("...and reports its own deviation", both.excess_nox == 0,
      "%s" % both.excess_nox)

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
check("...for both pixels, not just the one the fan acts on",
      c.noxdet.raw is None, "raw=%s" % c.noxdet.raw)

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

# -- the LEDs ------------------------------------------------------------
#
# Two LEDs carry every bit of feedback this device gives without a network,
# so what they mean has to be unambiguous -- and the README's table is the
# contract. These tests read the pins drive_outputs actually leaves behind,
# because the bug worth catching is a new pattern that collides with an old
# one, and no amount of reading the function tells you that. step() above
# deliberately skips the LED half of drive_outputs; this is where it is paid
# for.
#
# A pattern is sampled over two phases 400 ms apart -- the blink period --
# and reduced to the SET of pin states seen. One state means solid, two
# means blinking, and the set says which LEDs took part.


class FakeProv:
    def __init__(self, is_open):
        self.is_open = is_open


def pattern(c, held=0.0, step_ms=400, n=2):
    """What the LEDs do, as a frozenset of (ok, run) states over n samples."""
    c.button.held_s = lambda: held
    seen = []
    for _ in range(n):
        c.drive_outputs()
        seen.append((c.led_ok.value(), c.led_run.value()))
        CLOCK[0] += step_ms
    return frozenset(seen)


SOLID_GREEN = frozenset({(1, 0)})
SOLID_RED = frozenset({(0, 1)})
SOLID_BOTH = frozenset({(1, 1)})
BLINK_GREEN = frozenset({(0, 0), (1, 0)})
BLINK_RED = frozenset({(0, 0), (0, 1)})
BLINK_BOTH = frozenset({(0, 0), (1, 1)})
ALTERNATING = frozenset({(1, 0), (0, 1)})

NAMES = {SOLID_GREEN: "green solid", SOLID_RED: "red solid",
         SOLID_BOTH: "both solid", BLINK_GREEN: "green blinking",
         BLINK_RED: "red blinking", BLINK_BOTH: "both blinking together",
         ALTERNATING: "alternating"}


def says(seq):
    return NAMES.get(seq, "unknown %s" % sorted(seq))


# The README's table, state by state. Every row is checked, not just the one
# the new code touches: the point of the exercise was to confirm the others
# are what they are claimed to be.
table = []
for state, want, label in (
        (main.IDLE, SOLID_GREEN, "IDLE"),
        (main.RUN, SOLID_RED, "RUN"),
        (main.LEARN, BLINK_GREEN, "LEARN"),
        (main.BOOST, BLINK_RED, "BOOST"),
        (main.FAULT, ALTERNATING, "FAULT"),
):
    c = build()
    c.state = state
    got = pattern(c)
    table.append((label, got))
    check("%s shows %s" % (label, says(want)), got == want, says(got))

# The two hold gestures, which the state machine must not be able to override:
# the cue is the thumb's business, not the room's. Checked from RUN, where the
# state machine wants the red LED solid and would mask a cue that deferred to
# it.
c = build()
c.state = main.RUN
got = pattern(c, held=cfg.LONG_PRESS_S)
table.append(("held 2 s", got))
check("holding past LONG_PRESS_S shows both solid", got == SOLID_BOTH,
      says(got))

c = build()
c.state = main.RUN
got = pattern(c, held=cfg.HOLD_PRESS_S)
table.append(("held 8 s", got))
check("holding past HOLD_PRESS_S shows both blinking together",
      got == BLINK_BOTH, says(got))

# Below the threshold the gesture has not happened yet, so the room keeps the
# LEDs. A cue that appeared on contact would make every boost press look like
# a relearn.
c = build()
got = pattern(c, held=cfg.LONG_PRESS_S - 0.1)
check("a hold short of the threshold leaves the state's own pattern alone",
      got == SOLID_GREEN, says(got))

# The progression has to be monotonic in the hold: nothing, then relearn,
# then WiFi, with no gap in between where the cue drops back to the state.
c = build()
steps = [(h, pattern(c, held=h)) for h in (0.0, 1.9, 2.0, 5.0, 7.9, 8.0, 12.0)]
check("the cue only ever advances as the button is held",
      [s[1] for s in steps] == [SOLID_GREEN, SOLID_GREEN, SOLID_BOTH,
                                SOLID_BOTH, SOLID_BOTH, BLINK_BOTH,
                                BLINK_BOTH],
      " ".join("%.1fs=%s" % (h, says(p)) for h, p in steps))

# No two meanings may look alike. This is the check that would have caught
# "both solid" had it already been in use, and the one to re-run when the
# next pattern is added.
seen = {}
collision = None
for label, got in table:
    if got in seen:
        collision = "%s and %s both show %s" % (seen[got], label, says(got))
    seen[got] = label
check("no two states or gestures show the same pattern", collision is None,
      collision or "%d distinct patterns" % len(seen))

# The relearn's own confirmation: the long press lands in LEARN, whose blink
# is the acknowledgement that the gesture took. Without this the only
# feedback a relearn gives is on the USB serial nobody has attached.
c = build()
c.rh = 61.0
c.handle_button("long")
check("a long press relearns and lands in LEARN",
      c.state == main.LEARN and c.baseline == 61.0,
      "%s baseline=%s" % (c.state, c.baseline))
check("...and reseeds both gas pixels, not just the one the fan acts on",
      c.vocdet.base is None and c.noxdet.base is None,
      "voc=%s nox=%s" % (c.vocdet.base, c.noxdet.base))

# The provisioning window's indicator. It was an even green blink told apart
# from LEARN's by rate alone -- 150 ms against 400 ms -- which is why these
# tests are about SHAPE. Rate is what a set of pin states cannot see, what a
# glance across a room does not reliably read, and what an edit to either
# period would quietly destroy while leaving a set-based test green.


def rhythm(c, slots=16, slot_ms=150):
    """The OK led's on/off sequence over a full cycle, as a string.

    Aligned to the 1200 ms cycle first, because a sequence sampled from
    wherever the clock happens to sit is a rotation of the pattern and
    compares equal to nothing.
    """
    c.button.held_s = lambda: 0.0
    CLOCK[0] += (1200 - CLOCK[0] % 1200) % 1200
    out = []
    for _ in range(slots):
        c.drive_outputs()
        out.append("1" if c.led_ok.value() else "0")
        CLOCK[0] += slot_ms
    return "".join(out)


c = build()
c.prov = FakeProv(True)
window = rhythm(c)
check("an open WiFi window double-blinks green, then pauses",
      window == "1010000010100000", window)

c = build()
c.state = main.LEARN
learn = rhythm(c)
check("...a rhythm LEARN's even blink cannot be mistaken for",
      learn != window and set(learn) == set("01"), learn)
check("...telling them apart no longer rests on the sampling rate",
      learn.count("1") != window.count("1"),
      "learn %d/16 on, window %d/16 on"
      % (learn.count("1"), window.count("1")))

# The red led stays dark throughout: the hold cues own every pattern that
# lights both, so a window that touched red would collide with one of them.
c = build()
c.prov = FakeProv(True)
c.button.held_s = lambda: 0.0
reds = set()
for _ in range(16):
    c.drive_outputs()
    reds.add(c.led_run.value())
    CLOCK[0] += 150
check("...and leaves the red led dark throughout, so neither hold cue is "
      "shadowed", reds == {0}, "red saw %s" % sorted(reds))

c = build()
c.prov = FakeProv(False)
check("a closed window leaves the state's pattern alone",
      pattern(c) == SOLID_GREEN)

# The VOC settle gate, after LEARN's minute. A solid green used to cover
# both "the odor pixel has finished" and "it may still be warming", and
# those are hours apart. The pulse is a shape, not a slower LEARN: one
# flash, then a long dark. Sampled on its own grid, the way the WiFi
# window is, because two samples 400 ms apart cannot see a 2.4 s cycle.


class _Gate:
    def __init__(self, settled, ok=True):
        self.settled_voc = settled
        self.ok = ok


def pulse(c, slots=12, slot_ms=200):
    c.button.held_s = lambda: 0.0
    period = slots * slot_ms
    CLOCK[0] += (period - CLOCK[0] % period) % period
    out = []
    reds = set()
    for _ in range(slots):
        c.drive_outputs()
        out.append("1" if c.led_ok.value() else "0")
        reds.add(c.led_run.value())
        CLOCK[0] += slot_ms
    return "".join(out), reds


c = build()
c.state = main.IDLE
c.voc = _Gate(False)
settling, reds = pulse(c)
check("an unsettled VOC pixel flashes green once per cycle",
      settling == "110000000000", settling)
check("...and leaves the red led dark, so it is not a fault or a run",
      reds == {0}, "red saw %s" % sorted(reds))

c = build()
c.state = main.LEARN
c.voc = _Gate(False)
learn_beat, _ = pulse(c)
check("...LEARN's even blink is not that flash",
      learn_beat != settling and learn_beat.count("1") != settling.count("1"),
      learn_beat)

c = build()
c.state = main.IDLE
c.voc = _Gate(True)
check("a settled VOC pixel returns to solid green",
      pattern(c) == SOLID_GREEN, says(pattern(c)))

c = build()
c.voc = _Gate(False, ok=False)
check("a stood-down sensor does not pulse forever",
      pattern(c) == SOLID_GREEN, says(pattern(c)))

c = build()
check("no odor sensor stays solid green",
      pattern(c) == SOLID_GREEN, says(pattern(c)))

c = build()
c.state = main.RUN
c.voc = _Gate(False)
check("a run keeps the red led while the pixel is still settling",
      pattern(c) == SOLID_RED, says(pattern(c)))

# -- why the fan is running, while it is running ---------------------------
#
# The reported case, 03 Nov: a shower started the fan on the light, the light
# went out, humidity held the run open for another half hour, and `why` still
# read "light on" over a room 14.7 % above baseline. set_state() writes the
# reason on a transition and a run outlives its own cause, so the label went
# stale exactly where somebody wanted it.
#
# These drive the state machine rather than calling the helper, because the
# bug was never in the string -- it was in nothing ever asking for a new one.


def running(rh=None, light=True):
    """A controller in RUN, past MIN_RUN_S, by whichever route is asked."""
    c = build()
    if light:
        switch(c, True)                     # a run the light started
    else:
        c.rh = c.baseline + cfg.RH_RISE_ON + 1.0
        step(c)
    advance(cfg.MIN_RUN_S + cfg.SAMPLE_S)
    if rh is not None:
        c.rh = c.baseline + rh
    step(c)
    return c


c = running()
check("a run started by the light says so", c.state_reason == "light on",
      c.state_reason)

# The exact failure. The light goes out, the room is wet, the fan keeps
# running -- and the label has to follow the reason, not the trigger.
c = running()
c.rh = c.baseline + 14.7
switch(c, False)
step(c)
check("...and once the light is out, humidity takes the credit",
      c.state == main.RUN and c.state_reason == "humidity +14.7%",
      "%s %r light_on=%s" % (c.state, c.state_reason, c.light_on))

# Both at once. Picking one would invent a precedence the exit chain does
# not have: each holds the run on its own.
c = running(rh=14.7)
check("both reasons are named when both apply",
      c.state_reason == "light on, humidity +14.7%", c.state_reason)

# A timer is a real answer, but only when it is the only answer. A wet room
# inside MIN_RUN_S is held by the room; saying "minimum run time" there would
# hide the thing worth reporting behind the thing that is merely also true.
c = build()
c.rh = c.baseline + 9.0
step(c)
check("a wet room outranks the timer inside MIN_RUN_S",
      c.state == main.RUN and c.fan_seconds() < cfg.MIN_RUN_S
      and c.state_reason == "humidity +9.0%",
      "%s ran=%ds %r" % (c.state, c.fan_seconds(), c.state_reason))

# And the timer alone, which needs a dry dark room past POST_LIGHT_S but
# still short of MIN_RUN_S -- a 60 s window on the shipped values, and the
# only way "the fan is on because it has not been on long enough" is true.
c = build()
switch(c, True)
c.rh = c.baseline
switch(c, False)
advance(cfg.POST_LIGHT_S + cfg.SAMPLE_S)
step(c)
check("a run held only by MIN_RUN_S says exactly that",
      c.state == main.RUN and c.state_reason == "minimum run time",
      "%s ran=%ds %r" % (c.state, c.fan_seconds(), c.state_reason))

# The run-on after the light, which is a reason of its own and used to be
# invisible: the fan keeps going in a dry dark room and nothing said why.
c = running()
c.rh = c.baseline                      # dry, so only POST_LIGHT_S holds it
switch(c, False)
step(c)
check("the post-light run-on is named rather than left as the old trigger",
      c.state == main.RUN and c.state_reason == "running on after the light",
      "%s %r" % (c.state, c.state_reason))

# Odor, the third reason, and the one the whole second sensor exists for.
c = armed(rise=200, fall=50, base=29000.0, raw=28700)
c.rh = c.baseline + cfg.RH_RISE_ON + 1.0
step(c)
advance(cfg.MIN_RUN_S + cfg.SAMPLE_S)
c.rh = c.baseline                      # dry; only the odor path objects now
step(c)
check("odor holds the run under its own name",
      c.state == main.RUN and c.state_reason == "odor +300",
      "%s %r exc=%s" % (c.state, c.state_reason, c.excess_voc))

# And the thing that must not happen: the live reason overwriting the exit's
# own reason on the pass that ends the run. "dry, +0.0%" is the answer to a
# different question and it is the right one to keep.
c = running()
c.rh = c.baseline
switch(c, False)
advance(cfg.POST_LIGHT_S + cfg.SAMPLE_S)
step(c)
check("the reason the run ENDED survives the pass that ends it",
      c.state == main.IDLE and c.state_reason.startswith("dry,"),
      "%s %r" % (c.state, c.state_reason))

# A changed reason has to reach the broker and the dashboard, or it is only
# true inside the device.
c = running()
c._push = False
c.rh = c.baseline + 14.7
switch(c, False)
step(c)
check("a changed reason is pushed, not just stored", c._push is True,
      "%r push=%s" % (c.state_reason, c._push))

c = running(rh=14.7)
step(c)
c._push = False
step(c)
check("...and an unchanged one is not pushed every pass", c._push is False,
      "%r push=%s" % (c.state_reason, c._push))

# -- the watchdog, and the OTA trial boot it used to kill ------------------
#
# boot_check() arms an 8 s watchdog whenever an update is pending, to catch
# main.py failing at import. The loop then declined to feed anything for its
# first 3 s, on reasoning that holds only when no watchdog exists yet. On a
# trial boot it spent three of the eight seconds the update had, WiFi
# association did not fit in the rest, two trials failed and the rollback
# reverted a perfectly good build. Measured 2026-11-03.
#
# The predicate is its own method so it can be tested at all: the arming
# lives inside run(), which no host test drives.

c = build()
c._ota_pending = False
check("an ordinary boot does not arm the watchdog immediately",
      c._wdt_due(0) is False and c._wdt_due(2999) is False)
check("...and does arm it once the loop has run clean for 3 s",
      c._wdt_due(3001) is True)

c._ota_pending = True
check("a trial boot adopts the watchdog on the very first pass",
      c._wdt_due(0) is True,
      "boot_check already armed one; waiting protects nothing")

# The escape hatch has to survive both paths, or a bench session with the
# watchdog off would reset anyway.
saved = cfg.WDT_TIMEOUT_MS
cfg.WDT_TIMEOUT_MS = 0
c._ota_pending = True
check("WDT_TIMEOUT_MS = 0 still disables it, trial boot or not",
      c._wdt_due(0) is False and c._wdt_due(99999) is False)
cfg.WDT_TIMEOUT_MS = saved

# And the flag must exist before run() sets it, or the predicate raises on
# the first pass of every boot.
fresh = build()
check("the pending flag exists from construction, not from run()",
      fresh._ota_pending is False, repr(getattr(fresh, "_ota_pending", "MISSING")))

# -- the combustion veto -------------------------------------------------
#
# Measured 4 Nov on one baseline a few hours apart: a gas hob in the kitchen
# reached the bathroom sensor as VOC +504 / NOx rise +1498, and a genuine
# bathroom event as VOC +899 / NOx rise +64. The unwanted event is the
# SMALLER one on VOC, which is why the veto reads NOx and why no VOC
# threshold could have done this. Both figures are replayed here.

COOK_VOC, COOK_NOX = 504, 1498      # kitchen hob, must NOT run the fan
REAL_VOC, REAL_NOX = 899, 64        # bathroom odor, must still run it


def armed_with(voc_exc, nox_rise, veto=300):
    """A controller with both baselines seeded and a given pair of excesses."""
    c = build()
    # Without a working sensor _voc_active() is False whatever the veto
    # says, and the three cooking assertions below then pass for the wrong
    # reason. Caught exactly that way: they passed while every "should
    # still run" case failed.
    c.voc = FakeSGP41()
    c.st.set("VOC_RISE_ON", 200)
    c.st.set("VOC_FALL_OFF", 70)
    c.st.set("VOC_NOX_VETO", veto)
    # excess() is base - raw for both pixels, and a NOx RISE is therefore a
    # negative excess. Seed baselines and place the raw counts accordingly.
    c.vocdet.base, c.vocdet.raw = 30000.0, 30000 - voc_exc
    c.noxdet.base, c.noxdet.raw = 16000.0, 16000 + nox_rise
    return c


c = armed_with(COOK_VOC, COOK_NOX)
check("cooking: the odor path stands down", c._voc_active() is False)
check("cooking: the fan is not armed on odor", c._voc_armed() is False)
check("cooking: the path raises no objection to stopping",
      c._voc_quiet() is True)

c = armed_with(REAL_VOC, REAL_NOX)
check("bathroom odor: the odor path is live", c._voc_active() is True)
check("bathroom odor: the fan is armed", c._voc_armed() is True)
check("bathroom odor: the path holds the run open",
      c._voc_quiet() is False)

# The margin is the whole claim, so test both sides of the line rather than
# only the measured points.
check("just under the veto still runs",
      armed_with(REAL_VOC, 299)._voc_active() is True)
check("just over the veto does not",
      armed_with(REAL_VOC, 300)._voc_active() is False)

# A veto of 0 is the documented off switch and must restore the old
# behavior exactly, or there is no way back from a bad threshold.
check("veto 0 disables the veto",
      armed_with(COOK_VOC, COOK_NOX, veto=0)._voc_active() is True)

# AN UNSEEDED NOx PIXEL NOW HOLDS THE ODOR PATH, and the two halves of
# that have to be kept apart or the next reader will not be able to tell
# which one is acting.
#
# Absence is still not a VETO: _combustion() has no opinion without a
# baseline and must say so, because a veto fires on evidence and there is
# none. What changed on 2026-11-05 is that the path now WAITS for the veto
# to be available before it may move the fan at all. Previously every boot
# opened a window where a cooking smell could start the fan with nothing
# able to object, which is the false positive the NOx pixel was fitted to
# reject. The comment that used to sit here justified that window on the
# grounds that waiting would cost two hours; measured, it costs a median
# of zero. See NOX_WAIT_MAX_S in config.py.
c = armed_with(REAL_VOC, COOK_NOX)
c.noxdet.base = None
check("an unseeded NOx pixel is still not a veto",
      c._combustion() is False)
check("...but it does hold the odor path until the veto exists",
      c._voc_active() is False)
check("...and the gate naming it is the ready one, not the veto",
      c._nox_ready() is False)

# BOUNDED. A pixel that never seeds must not leave the fan deaf for good,
# so the wait expires and the path goes live without a veto.
c = armed_with(REAL_VOC, COOK_NOX)
c.noxdet.base = None
c.voc_ready_since = _time.ticks_ms()
check("the wait has not expired a second early",
      c._voc_active() is False)
CLOCK[0] += int(cfg.NOX_WAIT_MAX_S * 1000)
check("...and past NOX_WAIT_MAX_S the path goes live without a veto",
      c._voc_active() is True, "waited %ds" % cfg.NOX_WAIT_MAX_S)

# With the veto switched off there is nothing to wait for, so the gate must
# not strand a device that deliberately runs without one.
c = armed_with(REAL_VOC, COOK_NOX, veto=0)
c.noxdet.base = None
c.voc_ready_since = None
check("veto 0 means there is nothing to wait for",
      c._nox_ready() is True and c._voc_active() is True)

c = armed_with(REAL_VOC, COOK_NOX)
c.noxdet.raw = None
check("no NOx reading does not veto", c._voc_active() is True)

# A VOC falling the wrong way must not veto either: the test is a RISE.
check("a NOx fall is not combustion",
      armed_with(REAL_VOC, -1498)._voc_active() is True)

# And the veto must leave the other two reasons alone, which is the point of
# putting it in _voc_active() and not in decide().
c = armed_with(0, COOK_NOX)
c.baseline, c.rh, c.temp = 50.0, 62.0, 22.0
check("the hob does not stand down the odor path's siblings",
      c._voc_active() is False and c.excess_rh > c.st.RH_RISE_ON,
      "voc_active=%s excess_rh=%.1f" % (c._voc_active(), c.excess_rh))

# -- the two DHT22 counters --------------------------------------------
#
# They exist because they answer different questions and only one of them
# survives being sampled. dht_fails is CONSECUTIVE and a single good read
# zeroes it, so a poller sees 0 almost always -- which is why the 4 Nov
# fault has no cause. dht_bad never resets. If a refactor ever makes
# dht_bad resettable, the next fault is anecdotal again, so pin it here.

c = build()
c.dht.measure = lambda: (_ for _ in ()).throw(OSError("no reply"))
for _ in range(4):
    c.read_climate()
check("four bad reads: four in a row, four in total",
      (c.dht_fails, c.dht_bad) == (4, 4), "%s" % ((c.dht_fails, c.dht_bad),))
check("...and four in a row has not faulted yet", c.state != main.FAULT)

# The good read is the whole point: it hides the run from dht_fails and
# cannot hide it from dht_bad.
c.dht.measure = lambda: None
c.read_climate()
check("one good read zeroes the streak but not the total",
      (c.dht_fails, c.dht_bad) == (0, 4), "%s" % ((c.dht_fails, c.dht_bad),))

# Five in a row is the documented fault threshold.
c.dht.measure = lambda: (_ for _ in ()).throw(OSError("no reply"))
for _ in range(5):
    c.read_climate()
c.check_fan_health()
check("five in a row faults on the sensor",
      c.state == main.FAULT and c.fault_kind == "sensor",
      "%s / %s" % (c.state, c.fault_kind))
check("...and the total kept counting through it", c.dht_bad == 9, c.dht_bad)

# Recovery must clear the streak and keep the history.
c.dht.measure = lambda: None
c.on_command("fan/bathroom/cmd", b"clear")
c.read_climate()
check("clear resets the streak and keeps the total",
      c.dht_fails == 0 and c.dht_bad == 9,
      "fails=%s bad=%s" % (c.dht_fails, c.dht_bad))

# That both reach the published payload is already enforced structurally,
# by tests/test_integration.py checking every _SENSORS field against the
# snapshot's keys. Asserting it a second time here would need telemetry
# imported into a suite that deliberately does not have it.

# -- the network stall watchdog -------------------------------------------
#
# The failure this exists for, measured 2026-11-04: the device was
# unreachable for 23 minutes while the control loop stayed perfectly
# healthy -- sensors read, fan driven, an unbroken 300 s history written to
# flash. The hardware watchdog asks whether the loop is running, so it had
# nothing to catch, and net.poll() retries forever behind a 60 s backoff
# cap without ever escalating. A human pulled the plug.
#
# A reset is a blunt instrument, so what matters most below is the set of
# cases that must NOT fire it.

class FakeLink:
    def __init__(self, down_s=0):
        self.down_s = down_s
        self.online = down_s == 0


def stalled(down_s, secs=900, fan_on=False, link=True, ota=False):
    """A controller offline for down_s, and whether it reset itself."""
    c = build()
    c.st.set("NET_STALL_S", secs)
    c.link = FakeLink(down_s) if link else None
    # set_fan, not an invented attribute. The first version of this helper
    # assigned c.fan_on, which the Controller does not have -- so the test
    # created the thing it was meant to be checking and passed while the
    # firmware raised AttributeError on the first pass after boot. Drive
    # the real API and the harness cannot fake the interface again.
    c.set_fan(fan_on)
    c._ota_pending = ota
    hits = []
    machine.reset = lambda: hits.append(1)
    try:
        c._net_stall_check()
    finally:
        machine.reset = lambda: None
    return bool(hits)


# The production path, with nothing poked. This is the check that was
# missing: every assertion below drives the helper, and the helper can
# only exercise attributes it has itself created. A pristine controller
# running the real method is the only thing that proves the method talks
# to the object that actually exists -- the first version read a
# self.fan_on that does not, and took the controller down on the first
# pass after boot while the suite stayed green.
_virgin = build()
_virgin.st.set("NET_STALL_S", 900)
_virgin.link = FakeLink(10)
try:
    _virgin._net_stall_check()
    _ok, _err = True, ""
except Exception as e:
    _ok, _err = False, "%s: %s" % (type(e).__name__, e)
check("it runs against an untouched controller", _ok, _err)

check("a brief outage is left alone", not stalled(60))
check("one minute under the limit is left alone", not stalled(840))
check("at the limit it resets", stalled(900))
check("past the limit it resets", stalled(1800))

# 0 is the off switch, and it has to be absolute: this is the one setting
# that can reboot a working device.
check("0 disables it entirely", not stalled(86400, secs=0))

# A reset interrupts a run. A stalled link is not a reason to stop
# extracting, so the check waits for the fan to stop.
check("it never interrupts a running fan", not stalled(1800, fan_on=True))
check("...and resets once the fan is off", stalled(1800, fan_on=False))

# Mid-update the staged files and the trial boot are at stake, and ota.py
# has its own reset path.
check("it stands clear of an OTA", not stalled(1800, ota=True))

# Off-device, and before the link exists at all.
check("no link means nothing to judge", not stalled(1800, link=False))

# Defensive: a link that raises must not take the fan down with it.
_c = build()
_c.st.set("NET_STALL_S", 900)


class AngryLink:
    @property
    def down_s(self):
        raise OSError("radio gone")


_c.link = AngryLink()
_c.fan_on = False
_hits = []
machine.reset = lambda: _hits.append(1)
try:
    _c._net_stall_check()
    _survived = True
except Exception:
    _survived = False
finally:
    machine.reset = lambda: None
check("a link that raises is survived, not obeyed", _survived and not _hits)

# Clamped like every other setting, so a bad payload cannot arm a reboot
# loop by asking for a five-second threshold.
# The hazard is behavioural, so assert the behavior: a 5 s setting must
# not reboot the device 5 s into an outage. main.py floors it at
# NET_STALL_MIN_S rather than trusting settings.json, which can be
# hand-edited and carried between builds.
check("a 5 s setting does not reboot 10 s into an outage",
      not stalled(10, secs=5))
check("...and still will not at 4 minutes", not stalled(240, secs=5))
check("...but the floor itself is honoured", stalled(400, secs=5))
check("the floor clears net.py's 60 s backoff ceiling by 5x",
      main.NET_STALL_MIN_S >= 300, main.NET_STALL_MIN_S)
_c = build()
_c.st.set("NET_STALL_S", 999999)
check("a huge value is clamped to the ceiling",
      _c.st.NET_STALL_S <= 3600, _c.st.NET_STALL_S)

# -- the light's own clock ------------------------------------------------
#
# Reported 5 Nov: "I had an event with the light off, then turned light on
# and fan stopped while light was on. I had to turn the light off and on
# again for it to start."
#
# LIGHT_ONLY_MAX_S is an allowance for the light. It was being measured
# against fan_seconds(), the whole run, so a run that started on odor or on
# a shower spent the light's allowance before the light was involved. In
# the worst case -- a run already past the allowance when the air clears --
# the light got none of it at all.

def lit_run(reason="odor", on_at=0.0, allowance=600, min_run=300,
            voc_exc=300):
    """A RUN started by something other than the light, joined by it later.

    `on_at` is seconds into the run at which the light comes on. Nothing is
    poked onto the controller that the firmware would not find there: the
    light is driven through the detector the way the LDR drives it.
    """
    # voc_exc=0 is clean air, which is what makes a run light-ONLY. The
    # default leaves the odor path objecting, which is the reported case.
    c = armed(rise=200, fall=70, base=30000.0, raw=30000 - voc_exc)
    c.st.set("MIN_RUN_S", min_run)
    c.st.set("LIGHT_ONLY_MAX_S", allowance)
    c.st.set("MAX_RUN_S", 3600)
    c.rh = c.baseline                      # dry throughout
    c.set_state(main.RUN, reason)
    c.set_fan(True)
    advance(on_at)
    return c


def light(c, on):
    """Turn the bathroom light on or off, through the detector."""
    LIGHT[0] = 60000 if on else 3000
    c.lightdet.amb = 3000.0
    c.light_on = bool(on)


# The reported sequence, to the minute. Odor run, light on at ten minutes,
# air clears at twelve. The old code latched at the first pass after the
# air cleared, because `ran` was already past 600.
c = lit_run(reason="odor +200", on_at=600)
light(c, True)
c.decide()
check("the light joining a 10 min odor run does not stop it",
      c.state == main.RUN, "%s %r" % (c.state, c.state_reason))
# Now the air clears. This is the exact pass that used to latch.
c.vocdet.raw = 30000
advance(60)
c.decide()
check("...nor does the air clearing a minute later",
      c.state == main.RUN, "%s %r" % (c.state, c.state_reason))
check("...and the fan is still actually turning", c.fan.value() == 1)

# The allowance is real, it just starts when the light becomes the reason.
advance(540)
c.decide()
check("the light still holds it at 9 of its own 10 minutes",
      c.state == main.RUN, "%s" % c.state)
advance(61)
c.decide()
check("...and lets go once its own 10 minutes are up",
      c.state == main.IDLE, "%s %r" % (c.state, c.state_reason))
check("...latching, so a light left on cannot restart it",
      c.maxed is True)

# A plain light-only run is unaffected: this is the case the allowance was
# written for and it must keep its old length to the second.
c = lit_run(reason="light on", voc_exc=0)
light(c, True)
c.decide()
advance(599)
c.decide()
check("a light-only run still runs its full allowance",
      c.state == main.RUN, "%s" % c.state)
advance(2)
c.decide()
check("...and still latches at the end of it",
      c.state == main.IDLE and c.maxed is True,
      "%s maxed=%s" % (c.state, c.maxed))

# The clock must not carry across runs.
c = lit_run(reason="light on", voc_exc=0)
light(c, True)
c.decide()
advance(400)
c.decide()
light(c, False)
c.decide()                                  # dark, dry, quiet -> run ends
advance(400)
c.decide()
check("a finished run leaves no clock behind", c.light_only_since is None)

# The light going out and coming back is a new occupancy event, and the
# allowance starts again with it rather than resuming.
c = lit_run(reason="light on", voc_exc=0)
light(c, True)
c.decide()
advance(500)
c.decide()
light(c, False)
c.decide()
light(c, True)
c.decide()
advance(300)
c.decide()
check("a second visit gets the whole allowance, not the remainder",
      c.state == main.RUN, "%s" % c.state)

# And a wet room is not a light-only run, so the allowance never starts.
c = lit_run(reason="humidity +9.0")
light(c, True)
c.rh = c.baseline + 9.0
c.decide()
advance(1200)
c.decide()
check("a wet room is not the light running the fan",
      c.state == main.RUN and c.light_only_since is None,
      "%s" % c.state)


# -- combustion is a rise, not a level ------------------------------------
#
# Measured 5 Nov. The hob peaked at a NOx rise of 1088 and decayed through
# 930, 638, 509, 475, 376, 316 over the next half hour -- every one above
# VOC_NOX_VETO. A level-only veto kept the odor path switched off for the
# whole decay, and a genuine bathroom event at 08:00 reading +381 on the
# VOC pixel, against a threshold of 140, was swallowed by the tail.

def trend(c, rises, secs=cfg.NOX_TREND_S):
    """Walk the NOx rise through a series, secs apart, as read_voc would."""
    for r in rises:
        c.noxdet.raw = int(c.noxdet.base + r)
        c._update_nox_trend()
        advance(secs)


# No history at all must still veto: arriving at a high level IS a rise,
# and the safe default for a veto is to apply it.
c = armed_with(COOK_VOC, COOK_NOX)
check("a high NOx reading with no trend yet is combustion",
      c._combustion() is True)
check("...so the odor path stands down as before", c._voc_armed() is False)

# The hob, climbing. Still combustion.
c = armed_with(COOK_VOC, 400)
trend(c, [400, 700, 1088])
check("a climbing NOx rise is combustion", c._combustion() is True)

# The hob, finished. The decay must give the odor path back.
c = armed_with(COOK_VOC, 400)
trend(c, [400, 700, 1088, 930, 638])
check("a decaying NOx rise is not combustion", c._combustion() is False)
check("...so the odor path is live again during the clear-out",
      c._voc_armed() is True)

# THE LATCH IS THE MECHANISM, and it is load-bearing in a way the original
# note here got wrong. That note claimed two minutes of decay moves 150-200
# counts, clearing NOX_TREND_EPS three or four times over. It does not: the
# figures came from history rows 300 s apart and were read as if they were
# 120 s apart. Checked against all eleven NOx episodes on flash, the MEDIAN
# 120 s window during a decay moves 12 to 104 counts, and most are UNDER
# NOX_TREND_EPS.
#
# What makes it work is that the decay is steepest just after the peak, so
# that first window clears EPS and latches _nox_decaying True -- and the
# long slow tail then cannot turn it back off, because only a genuine RISE
# of EPS does that. This pins exactly that, so a future tightening of EPS
# on the strength of a margin that does not exist fails here.
c = armed_with(COOK_VOC, 400)
trend(c, [400, 1206, 1100])        # the one fast window, straight off the peak
check("the first fast window off the peak releases the veto",
      c._combustion() is False)
tail = [1100 - 15 * n for n in range(1, 13)]   # 15 counts per window, all < EPS
check("...and every step of that tail really is under NOX_TREND_EPS",
      all(abs(a - b) < cfg.NOX_TREND_EPS
          for a, b in zip([1100] + tail, tail)),
      "steps of %d, EPS=%d" % (15, cfg.NOX_TREND_EPS))
trend(c, tail)
check("...so the slow tail cannot re-arm it, though no window clears EPS",
      c._combustion() is False, "rise still %+d" % -(c.excess_nox or 0))

# ...and a second pan going on IS a rise that clears EPS, so the veto comes
# straight back rather than being stuck open by the first event's latch.
trend(c, [1000, 1300])
check("a fresh climb re-arms the veto", c._combustion() is True)

# The counterfactual, so the dependence is written down rather than implied:
# a decay with NO window fast enough never releases at all.
c = armed_with(COOK_VOC, 400)
trend(c, [400, 1206] + [1206 - 15 * n for n in range(1, 13)])
check("a decay with no window clearing EPS stays vetoed",
      c._combustion() is True,
      "this is why the peak's steepness matters")

# The event that was missed, end to end: the morning's real numbers.
c = armed_with(0, 283)
trend(c, [283, 711, 1088, 930, 638, 509])
c.vocdet.raw = int(c.vocdet.base - 381)     # the 08:00 bathroom event
c.noxdet.raw = int(c.noxdet.base + 475)
check("the 08:00 event is seen through the hob's tail",
      c._voc_armed() is True and c.excess_voc == 381,
      "armed=%s exc=%s" % (c._voc_armed(), c.excess_voc))

# A second pan going on must be caught like the first.
c = armed_with(COOK_VOC, 400)
trend(c, [400, 1088, 638, 509])
check("the veto has stepped aside before the second pan",
      c._combustion() is False)
trend(c, [900, 1400])
check("a fresh climb re-arms the veto", c._combustion() is True)

# Below the veto level there is no episode, whatever the direction.
c = armed_with(REAL_VOC, REAL_NOX)
trend(c, [64, 40, 20])
check("a bathroom event is never combustion", c._combustion() is False)
check("...and dropping under the level clears the trend state",
      c._nox_decaying is False and c._nox_ref is None)

# Noise must not flip it. Moves under NOX_TREND_EPS say nothing.
c = armed_with(COOK_VOC, 1000)
trend(c, [1000, 1000 - (cfg.NOX_TREND_EPS - 1)])
check("a move smaller than the epsilon is not a fall",
      c._combustion() is True, "eps=%d" % cfg.NOX_TREND_EPS)

# The reference is refreshed on a timer, so samples taken faster than
# NOX_TREND_S must not be read as a trend.
c = armed_with(COOK_VOC, 1000)
trend(c, [1000, 500, 400], secs=1)
check("samples closer together than the trend window do not decide",
      c._combustion() is True)

# A veto of 0 is still the off switch.
check("veto 0 disables the direction test too",
      armed_with(COOK_VOC, COOK_NOX, veto=0)._combustion() is False)


# -- the veto must not feed the baselines ---------------------------------
#
# _voc_learning() asked _voc_quiet(), which answers True whenever the odor
# path is inert -- including when it is inert because of the veto. So a
# cooking event switched the path off and thereby switched baseline
# learning back ON, over the worst air of the day. Measured 5 Nov: raw fell
# 30427 to 30160 while the baseline followed it down 30703 to 30593.

c = armed_with(COOK_VOC, COOK_NOX)
c.rh = c.baseline
check("the veto does not re-enable learning", c._voc_learning() is False,
      "voc_exc=%s" % c.excess_voc)
check("...and _voc_quiet still says the fan may stop",
      c._voc_quiet() is True, "unchanged, it answers a different question")

# The distinction that matters: inert-because-absent still learns.
c = build()
check("no sensor at all still learns as before",
      c._voc_settled() is True)
c = armed(rise=0, fall=70, base=30000.0, raw=29000)
check("an observe-only rollout still learns as before",
      c._voc_settled() is True, "VOC_RISE_ON=0")

# The NOx pixel must not learn its own exhaust.
c = armed_with(0, COOK_NOX)
c.rh = c.baseline
check("the hob does not teach the NOx baseline it is normal",
      c._nox_settled() is False, "rise=%d" % -c.excess_nox)
c = armed_with(0, REAL_NOX)
c.rh = c.baseline
check("a quiet NOx pixel still learns", c._nox_settled() is True)

# ...and the predicate has to be WIRED IN, which is the half a test of the
# predicate alone cannot show. Reverting the term at the read_voc() call
# site broke none of the checks above, so this drives the real read and
# watches the baseline instead of asking the gate about itself.
def reading(nox_raw, nox_base=16000.0, voc_raw=30000, voc_base=30000.0):
    c = armed(rise=200, fall=70, base=voc_base, raw=voc_raw)
    c.voc.nox = nox_raw
    c.noxdet.base, c.noxdet.raw = nox_base, nox_raw
    c.st.set("VOC_NOX_VETO", 300)
    c.rh = c.baseline
    return c


hob = reading(nox_raw=17500)          # a 1500 rise: the hob
check("the room is otherwise perfectly learnable",
      hob._voc_learning() is True and hob.voc.settled_nox is True)
was = hob.noxdet.base
hob.read_voc()
check("yet read_voc leaves the NOx baseline where it was",
      hob.noxdet.base == was, "%s -> %s" % (was, hob.noxdet.base))

calm = reading(nox_raw=16064)          # a 64 rise: the bathroom
was = calm.noxdet.base
calm.read_voc()
check("while a quiet pixel still learns normally",
      calm.noxdet.base != was, "%s -> %s" % (was, calm.noxdet.base))

# ...including through the decay, which is exactly when the veto has
# stepped aside and the reading is still nowhere near normal.
c = armed_with(0, 400)
c.rh = c.baseline
trend(c, [400, 1088, 638])
check("the decay releases the veto but not the NOx baseline",
      c._combustion() is False and c._nox_settled() is False,
      "rise=%d" % -c.excess_nox)


print("\nthings a mutant could break in silence\n")

# Found by mutation testing on 2026-11-05: deliberately corrupt one decision
# at a time and see whether the suite notices. 244 of 389 mutants survived.
# Most were boundary nudges and log formatting, but the ones below change
# real behavior and nothing anywhere objected. None of them is a bug today.
# They are the places where tomorrow's edit would be.

# THE OLDEST RULE IN THE CONTROLLER. The humidity baseline is what every
# shower is measured against, and it may only be learned from a quiet, dark
# room. Dropping the `not` from `if not self.light_on` inverts it, so the
# baseline would learn only while somebody is in the bathroom -- it would
# climb to shower humidity, call that normal, and the fan would never run
# again. The entire suite passed with that inversion in place.
dark = build()
dark.baseline, dark.rh = 50.0, 53.7
was = dark.baseline
dark.decide()
check("a dark room teaches the humidity baseline", dark.baseline != was,
      "%.3f -> %.3f" % (was, dark.baseline))

lit = build()
lit.baseline, lit.rh = 50.0, 53.7
lit.light_on = True
was = lit.baseline
lit.decide()
check("a lit room does not", lit.baseline == was, "%.3f" % lit.baseline)

# ...nor does a room with no reading. Learning from None would raise, and
# raising inside decide() is a dead controller.
blind = build()
blind.baseline, blind.rh = 50.0, None
blind.decide()
check("and neither does a room with no humidity reading",
      blind.baseline == 50.0, "%s" % blind.baseline)

# LEAVING LEARN. Both halves matter and neither was pinned: without the
# baseline term the controller enters IDLE with baseline None, and
# excess_rh then reads 0.0 for ever against a baseline that does not exist,
# so humidity triggering is silently dead until a reboot.
early = build()
early.state = main.LEARN
early.entered_at = CLOCK[0]
early.baseline, early.rh = 53.7, 53.7
advance(30)
early.decide()
check("LEARN is not left inside the first minute",
      early.state == main.LEARN, early.state)
advance(40)
early.decide()
check("...and is left once the minute is up", early.state == main.IDLE,
      early.state)

unseeded = build()
unseeded.state = main.LEARN
unseeded.entered_at = CLOCK[0]
unseeded.baseline, unseeded.rh = None, 53.7
advance(300)
unseeded.decide()
check("LEARN is never left without a baseline",
      unseeded.state == main.LEARN, unseeded.state)

# A STALE FAULT READS AS A CURRENT ONE. fault_reason is published on every
# reading, so it has to be cleared on the way out of FAULT and left alone
# everywhere else. All three mutations of that condition survived.
recovered = build()
recovered.set_state(main.FAULT, "fan stalled")
recovered.fault_reason = "fan stalled at 0 rpm"
recovered.set_state(main.IDLE, "retrying")
check("leaving FAULT clears the reason it left behind",
      recovered.fault_reason == "", repr(recovered.fault_reason))

ordinary = build()
ordinary.fault_reason = "fan stalled at 0 rpm"
ordinary.set_state(main.RUN, "light on")
check("an ordinary transition does not clear it",
      ordinary.fault_reason == "fan stalled at 0 rpm",
      repr(ordinary.fault_reason))

# THE REST ESCAPE MUST REFUSE A STOOD-DOWN PATH. _odor_latch_rested() lets
# a latch held open only by odor try again, and its first guard is `if not
# armed or quiet: return False`. Turning that `or` into `and` survived,
# because every existing case had both halves agreeing. The case that
# separates them is a path reading loudly while stood down for post-steam
# recovery: armed is False, quiet is also False, and only `or` refuses.
stood_down = armed(rise=200, fall=70, base=30000.0, raw=29700)
stood_down.rh = stood_down.baseline
stood_down.maxed = True
stood_down.maxed_at = CLOCK[0] - (stood_down.st.VOC_REST_S + 60) * 1000
stood_down.voc_recover_until = CLOCK[0] + 600_000
check("a stood-down odor path cannot rest a latch open",
      stood_down._odor_latch_rested() is False,
      "armed=%s quiet=%s" % (stood_down._voc_armed(), stood_down._voc_quiet()))

# ...and the same controller with the stand-down lifted may, or the check
# above would pass on a path that simply never releases anything.
stood_down.voc_recover_until = None
check("...and may once the stand-down is lifted",
      stood_down._odor_latch_rested() is True)

# THE EDGES OF THE NEW NOx WORK, which are mine and were unpinned.
check("a NOx rise exactly at the veto is not settled enough to learn",
      armed_with(0, 300)._nox_settled() is False)
check("...and one tick under it is",
      armed_with(0, 299)._nox_settled() is True)

c = armed_with(COOK_VOC, 1000)
trend(c, [1000, 1000 - cfg.NOX_TREND_EPS])
check("a fall of exactly the epsilon is a fall",
      c._combustion() is False, "eps=%d" % cfg.NOX_TREND_EPS)
trend(c, [1000 - cfg.NOX_TREND_EPS + cfg.NOX_TREND_EPS])
check("...and a climb of exactly the epsilon re-arms the veto",
      c._combustion() is True)



# A MONITORING FIELD THAT LIES. state_reason is published on every reading
# and feeds the app and Home Assistant's Reason sensor, but only set_state()
# and _refresh_run_reason() ever wrote it -- and the latter runs only in RUN.
# So IDLE kept whatever ended the last run for as long as it sat there.
# Measured 5 Nov: the fan reported "max run time - latched until the room
# dries or the light comes back on" for forty minutes after the latch had
# released, which sent this session's own diagnosis down the wrong path.
stale = build()
stale.state = main.RUN
stale.set_state(main.IDLE, "max run time - latched until the room dries "
                           "or the light comes back on")
stale.maxed = True
stale.maxed_at = CLOCK[0]
stale.rh = stale.baseline              # dry
check("the latch message is right while the latch is on",
      "latched" in stale.state_reason)
stale.decide()
check("the latch releases in a settled room", stale.maxed is False)
check("...and stops being reported as latched",
      "latched" not in stale.state_reason, repr(stale.state_reason))
check("...and the change is pushed, not left for the next poll",
      stale._push is True)

# The odor-rest release says its own true thing rather than inheriting.
rested = armed(rise=200, fall=70, base=30000.0, raw=29700)
rested.state = main.RUN
rested.set_state(main.IDLE, "max run time - latched until the room dries "
                            "or the light comes back on")
rested.maxed = True
rested.maxed_at = CLOCK[0] - (rested.st.VOC_REST_S + 60) * 1000
rested.rh = rested.baseline
rested.decide()
check("a latch released by resting says so",
      rested.maxed is False and "rested" in rested.state_reason,
      repr(rested.state_reason))

# And a latch that is genuinely still holding must not be talked out of it.
holding = build()
holding.state = main.RUN
holding.set_state(main.IDLE, "max run time - latched until the room dries "
                             "or the light comes back on")
holding.maxed = True
holding.maxed_at = CLOCK[0]
holding.rh = holding.baseline + 9.0    # still wet
holding.decide()
check("a wet room keeps both the latch and its message",
      holding.maxed is True and "latched" in holding.state_reason,
      repr(holding.state_reason))


import light                                          # noqa: E402

print("\nthe light detector, driven directly\n")

# light.py's own docstring says "the tests drive it directly with four days
# of real readings". No test file imported the module at all. Mutation
# testing put numbers on what that cost: 13 of 17 mutants survived, including
# the one that matters most -- turning `jump AND rise` into `jump OR rise`,
# which is precisely the failure docs/daylight-is-brighter-than-the-bulb.md
# was written about. The fan ran 75 minutes on 10-27 and 175 on 10-28, in a
# bone-dry room, on sunlight.


class LightSt:
    """Just the three settings the detector reads."""
    LIGHT_STEP_ON = 10000
    LIGHT_RISE_ON = 8000
    LIGHT_RISE_OFF = 4000


def det(amb=3000):
    d = light.Detector(LightSt(), log=lambda *a: None)
    d.update(amb)          # the first reading owns the ambient
    return d


# BOTH TERMS, AND EACH CATCHES A DIFFERENT LIE. Either one alone is a
# detector that has already failed in this room.

# The sky. Climbing faster than the ambient slew can absorb, so a rise does
# build -- but no single sample ever steps like a switch. This is the exact
# shape that ran the fan for 175 minutes on sunlight.
sky = det()
raw = 3000
for _ in range(40):
    raw += cfg.LIGHT_AMB_UP + 500          # outruns the slew by 500/sample
    on = sky.update(raw)
check("a sunrise never trips the light, however high it climbs",
      on is False, "rise=%d after %d samples" % (sky.rise(raw), 40))
check("...and it did build a rise well past the threshold",
      sky.rise(raw) >= LightSt.LIGHT_RISE_ON,
      "%d >= %d" % (sky.rise(raw), LightSt.LIGHT_RISE_ON))

# A shadow. One sample steps hard enough to be a switch, but it only climbs
# back to where the room already was, so there is no rise above ambient.
shadow = det(20000)
shadow.update(5000)                         # into shade; ambient follows down
on = shadow.update(16000)                   # back out, a 11000 step
check("climbing out of a shadow is not a light coming on",
      on is False, "jump=11000 rise=%d" % shadow.rise(16000))

# The fixture. Both at once, which only a switch does.
real = det()
on = real.update(3000 + LightSt.LIGHT_STEP_ON + 1000)
check("the fixture, which does both at once, does trip it", on is True)

# The exact corner, both terms on their thresholds.
edge = det()
on = edge.update(3000 + LightSt.LIGHT_STEP_ON)
check("exactly the step and exactly the rise is on", on is True,
      "jump=%d rise=%d" % (LightSt.LIGHT_STEP_ON, edge.rise(3000 + 10000)))

one_short = det()
one_short.update(3000 + LightSt.LIGHT_STEP_ON - 1)
check("one count short of the step is not", one_short.on is False)


# LETTING GO. Three separate ways out, and a detector that cannot take any of
# them leaves the fan running on a reading nobody can see.

# The switch going off: a downward step, which also re-anchors the ambient --
# the sun may have climbed while the fan ran.
off_step = det()
off_step.update(53000)
check("the fixture is on", off_step.on is True)
off_step.update(53000 - LightSt.LIGHT_STEP_ON - 1)
check("a downward step turns it off", off_step.on is False)
check("...and re-anchors the ambient to the room it left behind",
      abs(off_step.amb - (53000 - LightSt.LIGHT_STEP_ON - 1)) < 1,
      "amb=%.0f" % off_step.amb)

# The offset simply closing, with no step: a dimmer, or an ambient that rose
# to meet the bulb.
off_rise = det()
off_rise.update(53000)
off_rise.amb = 53000 - LightSt.LIGHT_RISE_OFF
off_rise.update(53000 - 1)
check("the offset closing turns it off, with no step at all",
      off_rise.on is False, "rise=%d" % off_rise.rise(53000 - 1))

# Neither edge ever arrives. This is the backstop that bounds every light
# misreading whatever its cause, so it has to actually fire.
was = cfg.LIGHT_MAX_ON_S
cfg.LIGHT_MAX_ON_S = 1800
stuck = det()
stuck.update(53000)
check("still on just under the cap", stuck.on is True)
advance(cfg.LIGHT_MAX_ON_S - 1)
stuck.update(53000)
check("...still on at one second under", stuck.on is True)
advance(2)
stuck.update(53000)
check("past the cap it re-anchors and lets go", stuck.on is False)
check("...onto the reading it had been staring at",
      abs(stuck.amb - 53000) < 1, "amb=%.0f" % stuck.amb)
cfg.LIGHT_MAX_ON_S = was


# THE AMBIENT TRACKER, which is asymmetric on purpose: a falling reading
# cannot be a fixture switching on, so it is believed quickly; a rising one
# is absorbed only as fast as weather can explain.
fall = det(20000)
fall.update(10000)
check("a falling ambient is believed quickly",
      fall.amb < 20000 - 2000, "amb=%.0f" % fall.amb)

rise_cap = det(3000)
rise_cap.update(3000 + 100000)              # absurd, far past any slew
check("a rising ambient is slew-limited, not followed",
      rise_cap.amb <= 3000 + cfg.LIGHT_AMB_UP + 1, "amb=%.0f" % rise_cap.amb)

# And it must not track at all while the fixture is lit, or the ambient
# climbs to meet the bulb and the light reads off in a blazing room.
lit_amb = det()
lit_amb.update(53000)
check("the fixture is on before the ambient is checked", lit_amb.on is True)
amb_was = lit_amb.amb
for _ in range(20):
    lit_amb.update(53000)
check("the ambient does not climb while the bulb is lit",
      lit_amb.amb == amb_was, "%.0f -> %.0f" % (amb_was, lit_amb.amb))

# rise() is what every log line and app tile explains itself with, so it has
# to mean what it says before a baseline exists too.
fresh = light.Detector(LightSt(), log=lambda *a: None)
check("rise is 0 before any ambient is learned", fresh.rise(50000) == 0)
fresh.update(3000)
check("...and is the offset above it afterwards", fresh.rise(11000) == 8000)


print("\nsettings validation, which anything on the LAN can reach\n")

# Found by mutation testing: 52 of 67 mutants in settings.py survived, and
# the survivors include every early return in set(). Turning `return False,
# "unknown setting"` into `return True` broke nothing, anywhere. The only
# match for that string in tests/ was a STUB inside test_httpd.py imitating
# this module, never the module itself.
#
# This is not an internal helper. POST /api/set/<name> lands here with a
# body off the network, on a device with no authentication, so "what does
# it do with a value it should refuse" is worth more than one test.


def fresh_settings():
    s = settings.Settings(log=lambda *a: None)
    s.reset()
    return s


st = fresh_settings()

# An unknown key must be refused outright, not quietly stored. Storing it
# would put an unvalidated name into settings.json, which is read back at
# every boot.
ok, why = st.set("NOT_A_SETTING", 5)
check("an unknown setting is refused", ok is False, repr(why))
check("...and nothing is stored under it",
      not hasattr(st, "NOT_A_SETTING"))

ok, why = st.set("MIN_RUN_S", "banana")
check("a non-numeric value is refused", ok is False, repr(why))

ok, why = st.set("MIN_RUN_S", None)
check("None is refused rather than becoming 0.0", ok is False, repr(why))

# NaN is the one that gets through a naive check: every comparison against
# it is False, so a clamp silently passes it and the stored threshold can
# never be met again.
ok, why = st.set("MIN_RUN_S", float("nan"))
check("NaN is refused", ok is False, repr(why))
ok, why = st.set("MIN_RUN_S", float("inf"))
check("infinity is refused", ok is False, repr(why))
ok, why = st.set("MIN_RUN_S", float("-inf"))
check("negative infinity is refused", ok is False, repr(why))

# A refused write must leave the old value alone.
before = st.MIN_RUN_S
st.set("MIN_RUN_S", float("nan"))
check("a refused write does not disturb the setting",
      st.MIN_RUN_S == before, "%s" % st.MIN_RUN_S)

# Out of range is clamped rather than refused, which is deliberate -- but
# it must actually clamp, at both ends.
lo, hi = settings.SPECS["MIN_RUN_S"][1], settings.SPECS["MIN_RUN_S"][2]
ok, v = st.set("MIN_RUN_S", hi + 10_000)
check("far above the ceiling is clamped to it", ok is True and v == hi,
      "%s" % v)
ok, v = st.set("MIN_RUN_S", lo - 10_000)
check("far below the floor is clamped to it", ok is True and v == lo,
      "%s" % v)
ok, v = st.set("MIN_RUN_S", hi)
check("exactly the ceiling is kept, not clamped away",
      ok is True and v == hi, "%s" % v)

# An integer setting must come back an int. The app and HA both render
# these, and 300.0000001 seconds is not a thing anyone meant.
ok, v = st.set("MIN_RUN_S", 300.4)
check("an integer setting rounds to an int",
      isinstance(v, int) and v == 300, "%r" % v)

# MODE is the one select, and it is matched case-insensitively because the
# MQTT and HTTP paths both carry user-typed text.
ok, v = st.set("MODE", "force off")
check("a mode is matched regardless of case", ok is True and v == "Force off",
      "%r" % v)
ok, why = st.set("MODE", "sideways")
check("an unknown mode is refused", ok is False, repr(why))

# PAIRED THRESHOLDS. Whichever was written wins and the other moves out of
# its way, because refusing the write would tell the user no without saying
# which other value blocked them.
# Asserting only that the order still holds is NOT enough, and mutation
# testing is what showed it: `if changed == hi_name` picks WHICH of the two
# moves, and inverting it still leaves rise above fall -- by overriding the
# number the user just typed instead of its partner. Both tests below passed
# against that mutant. So each one now pins the written value as well.
st = fresh_settings()
st.set("RH_RISE_ON", 8.0)
st.set("RH_FALL_OFF", 3.0)
# 7.8, not 7.5: the gap is 0.5, so 7.5 under an 8.0 rise ALREADY satisfies
# it and nothing is supposed to move. The original test passed on a pair it
# never actually disturbed.
st.set("RH_FALL_OFF", 7.8)             # pushed up under the rise
check("writing the lower threshold pushes the upper one out of the way",
      st.RH_RISE_ON > st.RH_FALL_OFF,
      "rise=%s fall=%s" % (st.RH_RISE_ON, st.RH_FALL_OFF))
check("...and the value just written is the one that is KEPT",
      st.RH_FALL_OFF == 7.8,
      "fall=%s, wanted 7.8" % st.RH_FALL_OFF)
check("...while its partner is the one that moved",
      st.RH_RISE_ON > 8.0, "rise=%s, was 8.0" % st.RH_RISE_ON)

st = fresh_settings()
st.set("RH_RISE_ON", 8.0)
st.set("RH_FALL_OFF", 3.0)
st.set("RH_RISE_ON", 3.2)              # squeezed down onto the fall
check("writing the upper threshold pushes the lower one down",
      st.RH_RISE_ON > st.RH_FALL_OFF,
      "rise=%s fall=%s" % (st.RH_RISE_ON, st.RH_FALL_OFF))
check("...and here too the written value is the one that survives",
      st.RH_RISE_ON == 3.2,
      "rise=%s, wanted 3.2" % st.RH_RISE_ON)
check("...with the partner pushed down below it",
      st.RH_FALL_OFF < 3.0, "fall=%s, was 3.0" % st.RH_FALL_OFF)

# EXACTLY the gap apart is already legal, so nothing may be moved.
#
# This one does NOT kill the `>=` -> `>` mutant on that line, and the
# reason is worth writing down rather than leaving as a puzzle for whoever
# reads the survivor list next. Taking the branch at the boundary sets
# `lo = hi - gap`, which at exactly the gap is the value `lo` already
# holds -- so the mutant does the work and changes nothing. Verified by
# hand-mutating and re-running: rise=3.5 fall=3.0 either way. It is an
# equivalent mutant, not a hole. The test is kept because the behaviour is
# worth pinning on its own account.
gap = dict((h, g) for h, l, g in settings.ORDERED)["RH_RISE_ON"]
st = fresh_settings()
st.set("RH_FALL_OFF", 3.0)
st.set("RH_RISE_ON", 3.0 + gap)
check("a pair exactly the minimum gap apart is left alone",
      st.RH_RISE_ON == 3.0 + gap and st.RH_FALL_OFF == 3.0,
      "rise=%s fall=%s gap=%s" % (st.RH_RISE_ON, st.RH_FALL_OFF, gap))

# ...and the ordering survives a round trip through flash, or it is only
# true until the next boot.
st.set("RH_RISE_ON", 3.2)
again = settings.Settings(log=lambda *a: None)
check("the order still holds after reloading from flash",
      again.RH_RISE_ON > again.RH_FALL_OFF,
      "rise=%s fall=%s" % (again.RH_RISE_ON, again.RH_FALL_OFF))

# A TRAP RATHER THAN A BUG, left by mutation testing. set() rounds with
# `isinstance(getattr(cfg, name, 0), int) and step >= 1`, and turning that
# `and` into an `or` kills nothing -- because today no float setting has a
# step of 1 or more, so the two halves never disagree. The day someone adds
# one, every write to it is silently rounded to a whole number and the
# clamping tests above all still pass. This fails the moment that pairing
# appears, which is the only moment it matters.
floaty = []
for _name, _spec in settings.SPECS.items():
    if _spec[0] != "number":
        continue
    _step = _spec[3]
    _cur = getattr(cfg, _name, None)
    if isinstance(_cur, float) and _step >= 1:
        floaty.append("%s (step %s, default %s)" % (_name, _step, _cur))
check("no float setting has a whole-number step, which would round it away",
      not floaty, "; ".join(floaty) or "none")

# A zero upper bound is the documented off switch and must not be dragged
# up by enforcing a gap against it -- that would arm odor triggering at a
# few ticks against a signal whose quiet-room spread is hundreds.
st = fresh_settings()
st.set("VOC_RISE_ON", 0)
st.set("VOC_FALL_OFF", 70)
check("a disabled upper bound is not armed by writing its partner",
      st.VOC_RISE_ON == 0, "%s" % st.VOC_RISE_ON)


print("\nthe stall tally\n")

# 8 Nov, 01:35-06:55: a fan that spins up and then dies, retried about
# twelve times overnight without ever latching. The RUN branch cleared
# the tally on the spin-up window -- the one tach reading
# check_fan_health is not yet allowed to judge -- so each retry started
# at zero again. This replays that shape: each retry turns for one sample
# and is dead on the next.


def spin_then_die(c):
    """One retry of a dying fan. Leaves the controller wherever it fell."""
    FAN_RPM[0] = 4200
    step(c)                         # IDLE -> RUN, fan driven
    advance(2)
    step(c)                         # the spin-up window: it turns
    FAN_RPM[0] = 0
    advance(2)
    step(c)                         # past SPINUP_S: it has died


c = build()
c.rh = c.baseline + cfg.RH_RISE_ON + 2      # a reason to run that persists
for n in range(cfg.MAX_FAN_FAULTS):
    if n:
        advance(cfg.FAULT_RETRY_S + 1)
        step(c)                     # FAULT -> IDLE, "retrying after stall"
    spin_then_die(c)
check("a fan that spins up then dies is counted every time",
      c.fan_faults == cfg.MAX_FAN_FAULTS,
      "%d/%d" % (c.fan_faults, cfg.MAX_FAN_FAULTS))
check("...and latches at the limit",
      c.state == main.FAULT and "LATCHED" in c.fault_reason,
      c.fault_reason[:48])
advance(cfg.FAULT_RETRY_S * 3)
step(c, 3)
check("...and a latched stall is not retried on a timer",
      c.state == main.FAULT and c.fan.value() == 0,
      "%s fan=%d" % (c.state, c.fan.value()))

# The other half: a run that genuinely recovers must still clear the
# tally, or two stalls a week apart would add up to a latch.
c = build()
c.rh = c.baseline + cfg.RH_RISE_ON + 2
spin_then_die(c)
check("one stall is counted", c.fan_faults == 1, "%d" % c.fan_faults)
advance(cfg.FAULT_RETRY_S + 1)
step(c)                             # retry
FAN_RPM[0] = 4200
step(c)                             # IDLE -> RUN
advance(cfg.FAN_HEALTHY_S - 4)
step(c)
check("a run that is still young keeps the tally",
      c.fan_faults == 1 and c.state == main.RUN,
      "%d after %.0fs" % (c.fan_faults, c.fan_seconds()))
advance(5)
step(c)
check("a run healthy past FAN_HEALTHY_S clears it",
      c.fan_faults == 0 and c.state == main.RUN,
      "%d after %.0fs" % (c.fan_faults, c.fan_seconds()))


print("\nthe runtime day\n")

# "Runtime today" rolled over at UTC midnight -- 19:00 in New York -- because
# MicroPython's localtime() IS UTC. Checked here against the host's own tz
# database, every hour for seven years, which covers both transitions of
# every year and the hour either side of each.
import telemetry                                     # noqa: E402
from datetime import datetime, timezone              # noqa: E402
try:
    from zoneinfo import ZoneInfo                    # noqa: E402
    _zones = [("America/New_York", -5, "us"), ("America/Los_Angeles", -8, "us"),
              ("Europe/Berlin", 1, "eu"), ("Europe/London", 0, "eu"),
              ("Asia/Kolkata", 5.5, "")]
    for _ in _zones:
        ZoneInfo(_[0])
except Exception:                                    # no tz database here
    _zones = []
start = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp())
for name, std, rule in _zones:
    zi = ZoneInfo(name)
    bad = []
    for unix in range(start, start + 7 * 366 * 86400, 3600):
        want = int(datetime.fromtimestamp(unix, zi).utcoffset().total_seconds())
        got = telemetry.utc_offset_s(unix, std, rule)
        if got != want:
            bad.append((unix, got, want))
    check("the UTC offset matches the tz database: %s" % name, not bad,
          str(bad[:1]))
if not _zones:
    check("the tz database is here to check against", False, "no zoneinfo")

# 8 Nov 2026 (EST): 18:59 and 19:01 are the same local day -- the old code
# put them a day apart. 23:59 and 00:01 are not.
def _est(h, m, d=8):
    return int(datetime(2026, 11, d, h, m, tzinfo=timezone.utc).timestamp()) + 5 * 3600


ld = lambda u: telemetry.local_day(u, -5, "us")      # noqa: E731
check("19:00 local is not a new day",
      ld(_est(18, 59)) == ld(_est(19, 1)))
check("local midnight is",
      ld(_est(23, 59, 7)) + 1 == ld(_est(0, 1)))

# And roll_day acts on it: the counter survives 19:00 and resets at 00:00.
snap = telemetry.Snapshot(cfg)
_real_time = telemetry.time.time
_now = [_est(18, 0)]
telemetry.time.time = lambda: _now[0] - telemetry._EPOCH_OFFSET
try:
    snap.roll_day()
    snap._run_ms = 30 * 60000
    _now[0] = _est(19, 30)
    snap.roll_day()
    check("the runtime counter survives 19:00",
          snap.runtime_min() == 30.0, "%s min" % snap.runtime_min())
    _now[0] = _est(0, 5, 9)
    snap.roll_day()
    check("...and resets after local midnight",
          snap.runtime_min() == 0.0, "%s min" % snap.runtime_min())
finally:
    telemetry.time.time = _real_time


# -- OTA: the exact verb, and only over MQTT ------------------------------
#
# The audit's one high finding. The HTTP door refused only the exact word
# 'update', the dispatcher started an OTA for anything that merely began
# with it, so 'updatex http://H' from anyone on the LAN installed firmware
# from H. These run the real on_command behind the real webapi.Api, so the
# door and the dispatcher are tested together, not one stub each.

print("\nOTA is the exact verb 'update', and never from HTTP\n")

import httpd                                         # noqa: E402
import webapi                                        # noqa: E402

URL = " http://10.0.0.9:8000"
NEAR = ["update", "UPDATE", "Update", "updatex", "UPDATEX", "updates",
        "update_", "update-now", "update.", "updateupdate", "update\t",
        " update", "update  ", "\tupdate\t", "\nupdate"]


def post_cmd(c, body):
    req = httpd.Request("POST", "/api/cmd", {}, {}, body.encode())
    return webapi.Api(c, cfg, None).handle(req)


let_through = []
for verb in NEAR:
    for body in (verb + URL, verb.strip() + URL):
        c = build()
        post_cmd(c, body)
        if c.updater is not None:
            let_through.append(body)
check("no near-miss of 'update' starts OTA over HTTP",
      not let_through, "%r" % let_through)

c = build()
r = post_cmd(c, "updatex" + URL)
check("...and 'updatex' is answered, not run", c.updater is None
      and r.status in (200, 403), "status %s" % r.status)

# The flag alone must hold, so a door that forgets its own check is still
# safe: this skips webapi and calls the dispatcher the way it does.
leaked = []
for verb in NEAR:
    c = build()
    c.on_command("fan/bathroom/cmd", (verb + URL).encode(), from_http=True)
    if c.updater is not None:
        leaked.append(verb)
check("from_http=True refuses OTA without the door's help",
      not leaked, "%r" % leaked)

# MQTT still has its one way in, and only the exact verb.
c = build()
c.on_command("fan/bathroom/cmd", ("update" + URL).encode())
check("MQTT 'update <url>' still starts OTA",
      c.updater is not None and c.updater.base == URL.strip(),
      "%r" % (c.updater and c.updater.base))
c = build()
c.on_command("fan/bathroom/cmd", ("  UPDATE\t" + URL).encode())
check("...whatever its case and spacing", c.updater is not None)

wrong = []
for verb in ("updatex", "updates", "update_", "update-now", "updateupdate"):
    c = build()
    c.on_command("fan/bathroom/cmd", (verb + URL).encode())
    if c.updater is not None:
        wrong.append(verb)
check("MQTT ignores verbs that only start with 'update'",
      not wrong, "%r" % wrong)

c = build()
c.on_command("fan/bathroom/cmd", b"")
c.on_command("fan/bathroom/cmd", b"   ")
check("an empty command is ignored, not a crash", c.updater is None)

# The setup window lets anyone on the LAN choose the broker, so while it is
# open, or a broker is on trial, 'update' is refused even over MQTT.


class FakeProv:
    def __init__(self, changing):
        self.changing_broker = changing
        self.is_open = changing


c = build()
c.prov = FakeProv(True)
c.on_command("fan/bathroom/cmd", ("update" + URL).encode())
check("MQTT 'update' is refused while the setup window is open",
      c.updater is None)
c.prov = FakeProv(False)
c.on_command("fan/bathroom/cmd", ("update" + URL).encode())
check("...and works again once it has shut", c.updater is not None)

# The other verbs still work through the same door.
c = build()
r = post_cmd(c, "boost")
check("HTTP 'boost' still boosts", r.status == 200 and c.state == main.BOOST,
      "%s / %s" % (r.status, c.state))

failed = results.count(False)
print("\n%d/%d passed\n" % (len(results) - failed, len(results)))
sys.exit(1 if failed else 0)
