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
import main                                          # noqa: E402


def advance(seconds):
    CLOCK[0] += int(seconds * 1000)


def build():
    """A controller in a dark, dry, settled room, fan off, in IDLE."""
    c = main.Controller()
    c.st._over.clear()
    c.state = main.IDLE
    c.entered_at = CLOCK[0]
    c.rh = 53.7
    c.temp = 21.0
    c.baseline = 53.7
    c.rpm = 0
    c.light_on = False
    c.dark_since = CLOCK[0] - 3600_000       # dark for an hour
    return c


def step(c, passes=1):
    """One pass of the loop's decision half: decide, then drive the fan.

    drive_outputs also blinks LEDs and reads the button, neither of which
    this file is about, so it stands in for just the line that matters.
    """
    for _ in range(passes):
        c.decide()
        c.set_fan(c.state in (main.RUN, main.BOOST))


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

failed = results.count(False)
print("\n%d/%d passed\n" % (len(results) - failed, len(results)))
sys.exit(1 if failed else 0)
