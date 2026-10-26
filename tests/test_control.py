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


class FakeADC:
    def __init__(self, pin):
        pass

    def read_u16(self):
        return 5000           # dark


class FakeWDT:
    def __init__(self, timeout=0):
        pass

    def feed(self):
        pass


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

failed = results.count(False)
print("\n%d/%d passed\n" % (len(results) - failed, len(results)))
sys.exit(1 if failed else 0)
