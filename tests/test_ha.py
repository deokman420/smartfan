"""Home Assistant integration regression suite.

    python tests/test_ha.py

Asserts that discovery still produces the entities the dashboard references,
that the dashboard's cards all point at something real, that the gauge bands
still match config.py, and that a command actually reaches the fan.

This is what Lighthouse cannot do. Lighthouse would audit Home Assistant's
own frontend -- its bundle, its accessibility, its render timing -- and score
just as well with every entity in the dashboard renamed out from under it.
The regressions that matter here are broken references and drifted
thresholds, which are assertions, not audits.

Needs HASS_LLAT in .env and the device online.
"""

import io
import json
import os
import re
import sys
import time
import urllib.request

import websocket

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
HA_HOST = os.environ.get("HA_HOST", "192.168.0.5:8123")
DASH = "bathroom-fan"

env = io.open(os.path.join(ROOT, ".env"), encoding="utf-8").read()
TOKEN = re.search(r'HASS_LLAT\s*=\s*"?([^"\s]+)', env).group(1)

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print("  %-44s %s %s" % (name, "PASS" if ok else "FAIL", detail))
    return ok


def api(path, data=None):
    r = urllib.request.Request(
        "http://%s%s" % (HA_HOST, path),
        headers={"Authorization": "Bearer " + TOKEN,
                 "Content-Type": "application/json"},
        data=json.dumps(data).encode() if data is not None else None)
    with urllib.request.urlopen(r, timeout=12) as f:
        return json.load(f)


def cfg_value(name):
    """Read a constant out of config.py without importing MicroPython bits."""
    src = io.open(os.path.join(ROOT, "config.py"), encoding="utf-8").read()
    m = re.search(r"^%s\s*=\s*([0-9.]+)" % name, src, re.M)
    return float(m.group(1)) if m else None


print("home assistant regression against %s\n" % HA_HOST)

# --- reachability ---------------------------------------------------------
try:
    api("/api/")
    check("HA API reachable and token valid", True)
except Exception as e:
    check("HA API reachable and token valid", False, str(e))
    sys.exit(1)

# The device suite leaves the board rebooting, and MQTT discovery plus the
# 60 s LEARN pass take a moment. Wait rather than reporting a false failure.
probe = "sensor.%s_humidity" % "bathroom_fan_controller"
for attempt in range(20):
    try:
        if api("/api/states/" + probe)["state"] not in ("unavailable", "unknown"):
            break
    except Exception:
        pass
    if attempt == 0:
        print("  waiting for the device to come online ...")
    time.sleep(3)

states = {e["entity_id"]: e for e in api("/api/states")}

# --- discovery produced what we expect ------------------------------------
PREFIX = "bathroom_fan_controller"
mine = {k: v for k, v in states.items() if PREFIX in k}
check("device discovered in HA", len(mine) > 0, "%d entities" % len(mine))
check("expected entity count", len(mine) >= 17, "%d found" % len(mine))

for kind, n in (("sensor.", 11), ("binary_sensor.", 3), ("button.", 3)):
    got = sum(1 for k in mine if k.startswith(kind))
    check("%s entities" % kind.rstrip("."), got >= n, "%d/%d" % (got, n))

unavail = [k for k, v in mine.items() if v["state"] in ("unavailable", "unknown")
           and not k.startswith("button.")]
check("no unavailable entities", not unavail, ", ".join(unavail[:3]))

# --- values are sane, not just present ------------------------------------
def val(key, lo, hi):
    e = states.get("sensor.%s_%s" % (PREFIX, key))
    if not e:
        return check("%s present" % key, False)
    try:
        v = float(e["state"])
    except ValueError:
        return check("%s numeric" % key, False, e["state"])
    return check("%s in range" % key, lo <= v <= hi,
                 "%s %s" % (e["state"], e["attributes"].get("unit_of_measurement", "")))

val("temperature", 32, 120)          # rendered in the HA unit system
val("humidity", 15, 95)
val("light_level", 0, 100)
val("wifi_signal", -90, -20)

# --- dashboard integrity --------------------------------------------------
ws = websocket.create_connection("ws://%s/api/websocket" % HA_HOST, timeout=15)
_nid = [0]


def wsend(msg):
    _nid[0] += 1
    msg["id"] = _nid[0]
    ws.send(json.dumps(msg))
    while True:
        r = json.loads(ws.recv())
        if r.get("id") == _nid[0]:
            return r


json.loads(ws.recv())
ws.send(json.dumps({"type": "auth", "access_token": TOKEN}))
check("websocket auth", json.loads(ws.recv())["type"] == "auth_ok")

r = wsend({"type": "lovelace/config", "url_path": DASH})
check("dashboard exists", r.get("success"), DASH)
view = r["result"]["views"][0]

refs = set()


def walk(o):
    if isinstance(o, dict):
        for k, v in o.items():
            if k == "entity" and isinstance(v, str):
                refs.add(v)
            else:
                walk(v)
    elif isinstance(o, list):
        for i in o:
            walk(i)


walk(view)
missing = sorted(e for e in refs if e not in states)
check("every card entity exists", not missing, ", ".join(missing[:3]))
check("dashboard references entities", len(refs) >= 15, "%d refs" % len(refs))

# The gauge bands encode the control thresholds. If someone retunes config.py
# and forgets the dashboard, the gauge quietly lies about when the fan runs.
gauges = [c for s in view["sections"] for c in s["cards"] if c["type"] == "gauge"]
if check("gauge present", len(gauges) == 1):
    sev = gauges[0].get("severity", {})
    check("gauge red band == RH_RISE_ON",
          sev.get("red") == cfg_value("RH_RISE_ON"),
          "gauge %s vs config %s" % (sev.get("red"), cfg_value("RH_RISE_ON")))
    check("gauge yellow band == RH_FALL_OFF",
          sev.get("yellow") == cfg_value("RH_FALL_OFF"),
          "gauge %s vs config %s" % (sev.get("yellow"), cfg_value("RH_FALL_OFF")))

ws.close()

# --- the command path actually reaches the fan ----------------------------
S = "sensor.%s_controller_state" % PREFIX
B = "binary_sensor.%s_fan_running" % PREFIX
start = api("/api/states/" + S)["state"]

api("/api/services/button/press",
    {"entity_id": "button.%s_boost_15_min" % PREFIX})
boosted = False
for _ in range(8):
    time.sleep(2)
    if api("/api/states/" + S)["state"] == "BOOST":
        boosted = True
        break
check("boost command reaches the device", boosted)

if boosted:
    check("fan runs during boost",
          api("/api/states/" + B)["state"] == "on")
    left = api("/api/states/sensor.%s_boost_remaining" % PREFIX)["state"]
    check("boost countdown populated", left not in ("0", "unknown"), "%s min" % left)

    api("/api/services/button/press",
        {"entity_id": "button.%s_cancel_boost" % PREFIX})
    cancelled = False
    for _ in range(8):
        time.sleep(2)
        if api("/api/states/" + S)["state"] != "BOOST":
            cancelled = True
            break
    check("cancel returns to automatic", cancelled)
    check("fan stops after cancel", api("/api/states/" + B)["state"] == "off")

# --- summary --------------------------------------------------------------
passed = sum(1 for _, ok, _ in results if ok)
print("\n%d/%d passed" % (passed, len(results)))
fails = [n for n, ok, _ in results if not ok]
if fails:
    print("failed: " + ", ".join(fails))
sys.exit(0 if not fails else 1)
