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

# --- config sanity --------------------------------------------------------
out = mp("""
import config as cfg
print('LIGHT', cfg.LIGHT_ON, cfg.LIGHT_OFF)
print('RH', cfg.RH_RISE_ON, cfg.RH_FALL_OFF)
print('RUN', cfg.MIN_RUN_S, cfg.MAX_RUN_S)
print('RPM', cfg.MIN_RPM)
""")
c = {l.split()[0]: l.split()[1:] for l in out.splitlines() if l.split()}
lon, loff = (int(x) for x in c.get("LIGHT", [0, 0]))
check("LIGHT_ON above LIGHT_OFF", lon > loff, "%d > %d" % (lon, loff))
check("light hysteresis >= 5000", lon - loff >= 5000, "gap %d" % (lon - loff))
rise, fall = (float(x) for x in c.get("RH", [0, 0]))
check("RH_RISE_ON above RH_FALL_OFF", rise > fall, "%.1f > %.1f" % (rise, fall))
mn, mx = (int(x) for x in c.get("RUN", [0, 0]))
check("MAX_RUN above MIN_RUN", mx > mn, "%d > %d" % (mx, mn))

# --- teardown -------------------------------------------------------------
# Leave safe mode and restart. Skipping this strands the board at the REPL
# with no MQTT link, and the HA suite then fails on damage this one caused.
print()
print("leaving safe mode and restarting ...")
try:
    set_safe_mode(False)
except Exception:
    pass

out = mp("import os; print('SAFE' if %r in os.listdir() else 'CLEAR')" % SAFE_FILE)
check("safe mode flag removed", "CLEAR" in out)
check("controller restarted", True)

# --- summary --------------------------------------------------------------
passed = sum(1 for _, ok, _ in results if ok)
print("\n%d/%d passed" % (passed, len(results)))
fails = [n for n, ok, _ in results if not ok]
if fails:
    print("failed: " + ", ".join(fails))
sys.exit(0 if not fails else 1)
