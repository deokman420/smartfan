"""Hardware regression suite. Run with the Pico attached.

    python tests/test_device.py [COM5]

Exercises every subsystem against the live board and exits non-zero on any
failure, so it can gate a flash. This is the commissioning test made
repeatable -- most of the faults it checks for are ones that actually
happened during the build, and several were only found because a number
looked wrong rather than because anything crashed.
"""

import subprocess
import sys
import time

PORT = sys.argv[1] if len(sys.argv) > 1 else "COM5"
results = []


def mp(code, timeout=120):
    """Run code on the device, return stdout."""
    p = subprocess.run(
        [sys.executable, "-m", "mpremote", "connect", PORT, "exec", code],
        capture_output=True, text=True, timeout=timeout)
    return p.stdout


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print("  %-38s %s %s" % (name, "PASS" if ok else "FAIL", detail))
    return ok


SAFE_FILE = "safemode"


def reset():
    subprocess.run([sys.executable, "-m", "mpremote", "connect", PORT, "exec",
                    "import machine; machine.reset()"],
                   capture_output=True, timeout=30)


def set_safe_mode(on):
    """Enter or leave safe mode via the flag file, then reboot into it.

    Required, not optional. main.py arms an 8 s watchdog that cannot be
    stopped once running, so any exec block below that outlasts it gets
    reset mid-measurement -- which is exactly what happened the first time
    these tests met the watchdog. Safe mode skips both the loop and the WDT.
    """
    if on:
        code = "open(%r,'w').close(); print('safe')" % SAFE_FILE
    else:
        code = ("import os\ntry:\n    os.remove(%r)\nexcept OSError:\n"
                "    pass\nprint('normal')" % SAFE_FILE)
    subprocess.run([sys.executable, "-m", "mpremote", "connect", PORT,
                    "exec", code], capture_output=True, timeout=30)
    reset()
    time.sleep(4)


print("device regression on %s\n" % PORT)
print("entering safe mode (the watchdog would reset us mid-test) ...")
set_safe_mode(True)

# --- firmware and filesystem ---------------------------------------------
out = mp("""
import sys, os, gc
gc.collect()
print('IMPL', '.'.join(str(x) for x in sys.implementation.version))
print('MACH', os.uname().machine)
print('FILES', ','.join(sorted(os.listdir())))
print('FREE', gc.mem_free())
""")
d = dict(l.split(" ", 1) for l in out.splitlines() if " " in l)
check("micropython present", d.get("IMPL", "").startswith("1."), d.get("IMPL", ""))
check("board is a Pico 2 W", "Pico 2 W" in d.get("MACH", ""), d.get("MACH", ""))

need = {"main.py", "config.py", "calibrate.py", "mqtt.py", "net.py",
        "telemetry.py", "ringbuf.py"}
have = set(d.get("FILES", "").split(","))
check("all modules on device", need <= have,
      "missing %s" % sorted(need - have) if need - have else "")
free = int(d.get("FREE", 0))
check("free RAM > 200 KB", free > 200_000, "%d bytes" % free)

# --- every module imports -------------------------------------------------
out = mp("""
for m in ('config','mqtt','ringbuf','net','telemetry','calibrate','main'):
    try:
        __import__(m); print('OK', m)
    except Exception as e:
        print('ERR', m, e)
""")
bad = [l for l in out.splitlines() if l.startswith("ERR")]
check("all modules import", not bad, bad[0] if bad else "")

# --- sensors --------------------------------------------------------------
out = mp("""
from machine import Pin, ADC
import dht, time, config as cfg
d = dht.DHT22(Pin(cfg.PIN_DHT)); good=0; t=h=None
for _ in range(3):
    try:
        d.measure(); good+=1; t=d.temperature(); h=d.humidity()
    except OSError: pass
    time.sleep(2)
print('DHT', good, t, h)
a = ADC(Pin(cfg.PIN_LDR)); v=[a.read_u16() for _ in range(5)]
print('LDR', min(v), max(v))
print('BTN', Pin(cfg.PIN_BUTTON, Pin.IN, Pin.PULL_UP).value())
""", timeout=120)
vals = {l.split()[0]: l.split()[1:] for l in out.splitlines() if l.split()}
dht_ok = vals.get("DHT", ["0"])[0] == "3"
check("DHT22 reads 3/3", dht_ok, " ".join(vals.get("DHT", [])[1:]))
if dht_ok:
    t, h = float(vals["DHT"][1]), float(vals["DHT"][2])
    check("temperature plausible", 0 < t < 50, "%.1f C" % t)
    check("humidity plausible", 15 < h < 95, "%.1f %%" % h)
lo, hi = (int(x) for x in vals.get("LDR", [0, 0]))
check("LDR reads in range", 0 < lo <= hi < 65536, "%d..%d" % (lo, hi))
check("LDR is not pinned", hi - lo < 20000 and lo > 100,
      "spread %d" % (hi - lo))
check("button idle high", vals.get("BTN", ["0"])[0] == "1")

# --- fan drive and tachometer --------------------------------------------
out = mp("""
from machine import Pin
import time, config as cfg
from calibrate import _Pulses
fan = Pin(cfg.PIN_FAN, Pin.OUT, value=0)
c = _Pulses(); t = Pin(cfg.PIN_TACH, Pin.IN, Pin.PULL_UP)
t.irq(trigger=Pin.IRQ_FALLING, handler=c.tick)
print('BOOT', fan.value())
fan.value(0); time.sleep(3); c.n=0; time.sleep(2)
print('OFF', c.n*60//(2*cfg.TACH_PPR))
fan.value(1); time.sleep(3); c.n=0; time.sleep(3)
print('ON', c.n*60//(3*cfg.TACH_PPR))
fan.value(0); time.sleep(3); c.n=0; time.sleep(2)
print('OFF2', c.n*60//(2*cfg.TACH_PPR))
""", timeout=180)
f = {l.split()[0]: int(l.split()[1]) for l in out.splitlines()
     if l.split() and l.split()[-1].lstrip("-").isdigit()}
check("fan pin low at boot", f.get("BOOT", 1) == 0)
check("fan stops when commanded off", f.get("OFF", 1) == 0,
      "%d rpm" % f.get("OFF", -1))
check("fan runs when commanded on", f.get("ON", 0) > 800,
      "%d rpm" % f.get("ON", -1))
check("fan stops again", f.get("OFF2", 1) == 0, "%d rpm" % f.get("OFF2", -1))
# Regression guard for the PNP: a part that conducts regardless of the base
# gives identical readings in both states.
check("transistor actually switches",
      f.get("ON", 0) > 800 and f.get("OFF", 1) == 0,
      "on=%d off=%d" % (f.get("ON", -1), f.get("OFF", -1)))

# --- state machine drives the right outputs -------------------------------
out = mp("""
import main
c = main.Controller()
for st in (main.LEARN, main.IDLE, main.RUN, main.BOOST, main.FAULT):
    c.state = st; c.drive_outputs()
    print(st, c.fan.value())
c.fan.value(0); c.led_ok.value(0); c.led_run.value(0)
""")
st = {l.split()[0]: int(l.split()[1]) for l in out.splitlines()
      if len(l.split()) == 2 and l.split()[1].isdigit()}
check("fan off in LEARN", st.get("LEARN") == 0)
check("fan off in IDLE", st.get("IDLE") == 0)
check("fan on in RUN", st.get("RUN") == 1)
check("fan on in BOOST", st.get("BOOST") == 1)
check("fan off in FAULT", st.get("FAULT") == 0)

# --- OTA recovery path ----------------------------------------------------
out = mp("""
import os
print('FILES', ','.join(sorted(os.listdir())))
import ota
print('PROT', ','.join(ota.PROTECTED))
print('TRIES', ota.MAX_TRIES)
print('COMMIT', ota.COMMIT_S)
""")
o = {l.split()[0]: l.split()[1] if len(l.split()) > 1 else ""
     for l in out.splitlines() if l.split()}
have = set(o.get("FILES", "").split(","))
check("boot.py present", "boot.py" in have)
check("ota.py present", "ota.py" in have)
# boot.py rescues a failed update. If OTA could replace it, a broken update
# could take the recovery path down with it.
prot = o.get("PROT", "")
check("boot.py protected from OTA", "boot.py" in prot, prot)
check("secrets.py protected from OTA", "secrets.py" in prot)
check("no OTA leftovers", not [f for f in have
                              if f.endswith((".bak", ".new")) or f == "ota_pending"],
      ", ".join(f for f in have if f.endswith((".bak", ".new"))) or "")
check("rollback after <= 2 tries", o.get("TRIES") == "2", o.get("TRIES", "?"))

# --- config sanity --------------------------------------------------------
out = mp("""
import config as cfg
print('LIGHT', cfg.LIGHT_RISE_ON, cfg.LIGHT_RISE_OFF, cfg.LIGHT_STEP_ON)
print('LIGHTAMB', cfg.LIGHT_AMB_UP, cfg.LIGHT_ONLY_MAX_S)
print('RH', cfg.RH_RISE_ON, cfg.RH_FALL_OFF)
print('RUN', cfg.MIN_RUN_S, cfg.MAX_RUN_S)
print('RPM', cfg.MIN_RPM)
""")
c = {l.split()[0]: l.split()[1:] for l in out.splitlines() if l.split()}
lon, loff, lstep = (int(x) for x in c.get("LIGHT", [0, 0, 0]))
check("LIGHT_RISE_ON above LIGHT_RISE_OFF", lon > loff, "%d > %d" % (lon, loff))
check("light hysteresis >= 2000", lon - loff >= 2000, "gap %d" % (lon - loff))
# A switch-like jump has to be at least as big as the offset it is supposed
# to establish, or the step test is the slack one and does no filtering.
check("LIGHT_STEP_ON at least LIGHT_RISE_ON", lstep >= lon,
      "%d >= %d" % (lstep, lon))
aup, lonly = (int(x) for x in c.get("LIGHTAMB", [0, 0]))
# The whole mechanism rests on this gap: the ambient must absorb anything the
# sky can do and still leave the fixture's own edge standing well clear.
# Measured in this room, the sky's fastest is 51 counts per sample and the
# fixture's edge is 51511.
check("ambient slew above the measured sky rate", aup >= 500,
      "%d counts/sample" % aup)
check("ambient slew well below a switch edge", aup * 4 <= lstep,
      "%d vs step %d" % (aup, lstep))
check("light-only cap below MAX_RUN", lonly > 0, "%ds" % lonly)
rise, fall = (float(x) for x in c.get("RH", [0, 0]))
check("RH_RISE_ON above RH_FALL_OFF", rise > fall, "%.1f > %.1f" % (rise, fall))
mn, mx = (int(x) for x in c.get("RUN", [0, 0]))
check("MAX_RUN above MIN_RUN", mx > mn, "%d > %d" % (mx, mn))

# --- teardown -------------------------------------------------------------
# Leave safe mode and restart. Skipping this strands the board at the REPL
# with no MQTT link, and the HA suite then fails on damage this one caused.
print()
print("leaving safe mode and restarting ...")

# Verify BEFORE the final reset, never after. Every mpremote exec stops
# main.py, so a check placed after the restart kills the controller it just
# started -- and then reports PASS for having done so. This suite left the
# device dead at the REPL after every run until that was noticed from the
# outside: no LEDs lit, entities unavailable, filesystem perfectly fine.
CLEAR_AND_CHECK = """
import os
try:
    os.remove({f!r})
except OSError:
    pass
print('SAFE' if {f!r} in os.listdir() else 'CLEAR')
""".format(f=SAFE_FILE)

out = mp(CLEAR_AND_CHECK)
check("safe mode flag removed", "CLEAR" in out, out.strip().splitlines()[-1:] or "")

reset()                       # last word to the device: run normally

# Wait for it to actually BE back, and assert that it is.
#
# This was `time.sleep(6)` and `check("controller restarted", True)` -- a
# fixed pause followed by an assertion that asserts nothing. Six seconds is
# enough for the REPL to answer and nowhere near enough for WiFi to
# associate, the HTTP server to bind, the broker to reconnect, or the SGP41
# to clear its 60 s settle gate. Nothing noticed while this suite ran alone.
#
# Back to back it broke the two suites that run after it, and the failures
# looked like bugs in them rather than unfinished business here: on
# 2026-11-02 test_device_http could not reach the device at all, and test_ha
# reported odour_baseline, excess_odour and nox_baseline "unavailable" --
# which was true and correct, because a freshly reset board publishes None
# for a learned baseline it has not learned yet. A suite that power-cycles
# the hardware owes the next suite a device that is actually up, and owes
# itself a check that says so.
#
# Both deadlines are waited out rather than just reachability, because the
# odor baselines are the slower of the two and they are what the HA suite
# reads.
import json                                                   # noqa: E402
import urllib.request                                         # noqa: E402

HTTP_HOST = "fan.local"
UP_DEADLINE = 150
# How long to allow for the sensor to answer again after the reset. NOT how
# long the baseline takes: see the note at the check itself.
SENSOR_DEADLINE = 120


def device_state(timeout=5):
    """The device's own state over HTTP, or None if it is not answering."""
    try:
        with urllib.request.urlopen(
                "http://%s/api/state" % HTTP_HOST, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except Exception:
        return None


began = time.time()
state = None
while time.time() - began < UP_DEADLINE:
    state = device_state()
    if state is not None:
        break
    time.sleep(3)
check("controller restarted and answers over the network", state is not None,
      "after %ds" % int(time.time() - began))

# What the reset actually risks is the SGP41 failing to re-initialize on the
# bus. That is what is checked here.
#
# It used to wait for voc_base instead, with a 120 s deadline, and that was
# never a test the device could pass from cold. sgp41.py seeds a baseline
# only once the pixel looks settled, and settled means flat across
# _FLAT_BLOCKS (3) blocks of _FLAT_BLOCK_MS (5 min) -- a structural floor of
# fifteen minutes, with a ceiling of VOC_SETTLE_MAX_S (2 h) beyond that.
# Measured on this board 2026-11-05: a reboot at 23:25 reseeded at 00:05,
# forty minutes later. So the old check could only pass when the suite was
# run against a board that had already been up for an hour, which is
# precisely the case in which it demonstrated nothing -- and it failed,
# correctly but uselessly, whenever the reset above had actually just
# happened.
#
# Waiting the real forty minutes would be honest and would also make this
# suite unrunnable. So this asks the deterministic half of the question --
# did the sensor come back and is it producing readings -- and leaves the
# warm-up to the clock.
#
# Gated on voc_ok, not on voc. A reading of None means "no opinion yet",
# which is what a FITTED sensor reports for the first ten seconds of its
# conditioning window. voc_ok is None only when no sensor answered the bus
# at boot, so a board with no SGP41 skips this entirely.
if state is not None and state.get("voc_ok") is not None:
    began = time.time()
    while (state.get("voc") is None
           and time.time() - began < SENSOR_DEADLINE):
        time.sleep(5)
        state = device_state() or state
    check("...and the odor sensor is reading again after the reset",
          state.get("voc_ok") is True and state.get("voc") is not None,
          "ok=%s voc=%s base=%s after %ds"
          % (state.get("voc_ok"), state.get("voc"), state.get("voc_base"),
             int(time.time() - began)))

# --- summary --------------------------------------------------------------
passed = sum(1 for _, ok, _ in results if ok)
print("\n%d/%d passed" % (passed, len(results)))
fails = [n for n, ok, _ in results if not ok]
if fails:
    print("failed: " + ", ".join(fails))
sys.exit(0 if not fails else 1)
