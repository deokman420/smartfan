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
HA_HOST = os.environ.get("HA_HOST", "ha.local.phbeks.com")
DASH = "bathroom-fan"

env = io.open(os.path.join(ROOT, ".env"), encoding="utf-8").read()
TOKEN = re.search(r'HASS_LLAT\s*=\s*"?([^"\s]+)', env).group(1)

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print("  %-44s %s %s" % (name, "PASS" if ok else "FAIL", detail))
    return ok


def _scheme():
    """http or https, whichever this HA answers WITHOUT redirecting.

    This host moved behind TLS. urllib follows an http->https redirect for
    a GET, so every read kept working and the move went unnoticed -- but on
    a 302 it also turns a POST into a GET, so every service call arrived as
    405 Method Not Allowed. The suite looked broken when the host had
    simply moved, which is why the scheme is settled once, up front, rather
    than left to a redirect to paper over.
    """
    for scheme in ("https", "http"):
        try:
            r = urllib.request.Request(
                "%s://%s/api/" % (scheme, HA_HOST),
                headers={"Authorization": "Bearer " + TOKEN})
            with urllib.request.urlopen(r, timeout=12) as f:
                if f.status == 200 and f.url.startswith(scheme + "://"):
                    return scheme
        except Exception:
            pass
    return "http"


SCHEME = _scheme()


def api(path, data=None):
    r = urllib.request.Request(
        "%s://%s%s" % (SCHEME, HA_HOST, path),
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
# TWO prefixes, on purpose. Home Assistant builds an entity_id from the
# device name the first time it sees an entity and never rewrites it, so
# renaming this device to "Bathroom SmartFan" left everything announced
# before the rename under bathroom_fan_controller_* and everything after it
# under bathroom_smartfan_*. One device, two prefixes, and a suite that
# knows only the older one silently stops checking every entity added since
# -- which is exactly what happened: this read `>= 31` against 31 old-prefix
# entities while ten newer ones went unexamined.
PREFIXES = ("bathroom_fan_controller", "bathroom_smartfan")
mine = {k: v for k, v in states.items() if any(p in k for p in PREFIXES)}
check("device discovered in HA", len(mine) > 0, "%d entities" % len(mine))


def eid(domain, suffix):
    """An entity_id for this device, under whichever prefix HA gave it.

    See PREFIXES: entities announced before and after the device was renamed
    carry different prefixes, so nothing here may assume one of them.
    """
    for prefix in PREFIXES:
        key = "%s.%s_%s" % (domain, prefix, suffix)
        if key in states:
            return key
    return "%s.%s_%s" % (domain, PREFIXES[0], suffix)

# Counted from the device's own tables rather than written down. Every count
# in this project that was written down has gone stale, so none of them are
# written down any more.
sys.path.insert(0, ROOT)
import settings                                              # noqa: E402
import telemetry                                             # noqa: E402

ANNOUNCED = ([("sensor.", lab) for _, lab, *_ in telemetry._SENSORS]
             + [("binary_sensor.", lab) for _, lab, *_ in telemetry._BINARY]
             + [("button.", lab) for _, lab, *_ in telemetry._BUTTONS]
             + [(("select." if spec[0] == "select" else "number."), spec[5])
                for spec in settings.SPECS.values()])


def slug(text):
    """A label as HA would have slugged it into an entity_id.

    British spellings are folded to American ones. HA freezes an entity_id
    at the name it first saw and never rewrites it, so the odor entities
    are registered as ..._odour_* while their labels now read "Odor". The
    id is the historical record and the label is the current display name;
    matching one against the other has to allow for the spelling having
    moved on. Entity ids are not renamed to match, because renaming one
    orphans every dashboard and automation that references it.
    """
    text = text.lower().replace("odour", "odor")
    return re.sub(r"[^a-z0-9]+", "_", text).strip("_")


check("expected entity count", len(mine) >= len(ANNOUNCED),
      "%d found, %d announced" % (len(mine), len(ANNOUNCED)))

# Named, not counted: a count says eight are missing, a name says which.
absent = [(kind, lab) for kind, lab in ANNOUNCED
          if not any(k.startswith(kind) and slug(lab) in slug(k)
                     for k in mine)]
check("every announced entity reached HA", not absent,
      ", ".join("%s%s" % (k, lab) for k, lab in absent[:4]))

for kind in ("sensor.", "binary_sensor.", "button.", "number.", "select."):
    want = sum(1 for k, _ in ANNOUNCED if k == kind)
    got = sum(1 for k in mine if k.startswith(kind))
    check("%s entities" % kind.rstrip("."), got >= want, "%d/%d" % (got, want))

# An odor sensor that is absent, or has failed, publishes None and
# so reads `unknown` in HA. That is the sensor reporting absence correctly,
# not a broken entity, and a suite that fails on it fails on every board
# without an SGP41. The binary sensor is the authority on whether one is
# there, so the odor readings are only required to have values when it
# says yes.
odor_fitted = states.get(eid("binary_sensor", "odour_sensor"), {})     .get("state") == "on"
exempt = () if odor_fitted else ("odour", "excess_odour")

# A third state this used to have no idea about: a sensor that is fitted,
# healthy, and has simply not learned a baseline yet. The gas baselines live
# in RAM and are seeded only once the pixel looks settled, which sgp41.py
# allows up to VOC_SETTLE_MAX_S for -- two hours. So for two hours after any
# reboot the baseline and excess entities correctly read unknown, and this
# check failed every time, for a reason that was never a fault.
#
# Caught 5 Nov, half an hour after a deliberate reboot. It is the same shape
# as BASELINE_DEADLINE in test_device.py: a test encoding "a fitted sensor
# always has a baseline", which the firmware openly and deliberately does
# not promise.
#
# Bounded by uptime rather than waived, so a baseline still missing tomorrow
# is still a failure.
WARMUP_MIN = 125                       # VOC_SETTLE_MAX_S, plus a little
try:
    up_min = float(states.get(eid("sensor", "uptime"), {}).get("state"))
except (TypeError, ValueError):
    up_min = WARMUP_MIN                # unknown uptime: excuse nothing
if up_min < WARMUP_MIN:
    exempt = exempt + ("odour_baseline", "excess_odour",
                       "nox_baseline", "excess_nox")
    print("  (device up %.0f min; gas baselines may still be warming)"
          % up_min)
unavail = [k for k, v in mine.items()
           if v["state"] in ("unavailable", "unknown")
           and not k.startswith("button.")
           and not any(w in k for w in exempt)]
check("no unavailable entities", not unavail,
      ", ".join(unavail[:3]) or ("odor exempt" if not odor_fitted else ""))

# --- values are sane, not just present ------------------------------------
def val(key, lo, hi):
    e = states.get(eid("sensor", key))
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
val("uptime", 0, 100000)

# 2c diagnostics: an outage must be reported, not silently papered over.
for key in ("link_reconnects", "last_outage", "last_boot_reason", "build"):
    e = states.get(eid("sensor", key))
    check("diagnostic present: %s" % key, e is not None,
          e["state"] if e else "missing")

# --- dashboard integrity --------------------------------------------------
def ha_websocket():
    """Open HA's websocket, over TLS if that is how this host answers.

    This asked for ws:// unconditionally. The HA in front of us now
    redirects the handshake to https, and websocket-client follows that
    redirect and then refuses its own target with "scheme https is
    invalid" -- a failure that reads like a library bug and is really
    "this host is TLS now". Trying wss first and falling back keeps the
    suite working against a plain HA and a fronted one alike.
    """
    errs = []
    for url in ("wss://%s/api/websocket" % HA_HOST,
                "ws://%s/api/websocket" % HA_HOST):
        try:
            return websocket.create_connection(url, timeout=15)
        except Exception as e:
            errs.append("%s: %s" % (url.split(":")[0], e))
    raise RuntimeError("; ".join(errs))


ws = ha_websocket()
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
#
# Found by the entity it shows, not by being the only gauge on the page.
# This asserted `len(gauges) == 1`, which was true until the odor card
# brought a second one, and then the whole threshold check stopped running
# -- the band assertions are nested under it, so a passing suite went from
# 38 checks to 36 and the two that mattered were the ones that vanished.
gauges = [c for s in view["sections"] for c in s["cards"]
          if c.get("type") == "gauge"]
rh_gauge = [g for g in gauges
            if g.get("entity") == eid("sensor", "excess_humidity")]
check("odor gauge present", any(
    g.get("entity") == eid("sensor", "excess_odour") for g in gauges))
if check("humidity gauge present", len(rh_gauge) == 1,
         "%d of %d gauges" % (len(rh_gauge), len(gauges))):
    sev = rh_gauge[0].get("severity", {})
    check("gauge red band == RH_RISE_ON",
          sev.get("red") == cfg_value("RH_RISE_ON"),
          "gauge %s vs config %s" % (sev.get("red"), cfg_value("RH_RISE_ON")))
    check("gauge yellow band == RH_FALL_OFF",
          sev.get("yellow") == cfg_value("RH_FALL_OFF"),
          "gauge %s vs config %s" % (sev.get("yellow"), cfg_value("RH_FALL_OFF")))

ws.close()

# --- writable settings: round-trip, clamping, and mode --------------------
N = eid("number", "humidity_rise_to_start")
before = api("/api/states/" + N)["state"]


def wait_for(eid, want, secs=20):
    for _ in range(secs // 2):
        time.sleep(2)
        if api("/api/states/" + eid)["state"] == want:
            return True
    return False


api("/api/services/number/set_value", {"entity_id": N, "value": 11})
check("parameter write round-trips", wait_for(N, "11.0"),
      api("/api/states/" + N)["state"])

# Firmware must clamp independently of the range HA advertises, because
# anything on the broker can publish to the set topic. Publishing a wildly
# out-of-range value through HA is refused client-side, so this goes at the
# entity's own maximum and checks it is honoured rather than exceeded.
api("/api/services/number/set_value", {"entity_id": N, "value": 20})
ok_hi = wait_for(N, "20.0")
check("accepts the documented maximum", ok_hi,
      api("/api/states/" + N)["state"])

api("/api/services/number/set_value", {"entity_id": N, "value": float(before)})
check("restored to %s" % before, wait_for(N, before))

M = eid("select", "mode")
S_ = eid("sensor", "controller_state")
B_ = eid("binary_sensor", "fan_running")
api("/api/services/select/select_option", {"entity_id": M, "option": "Force on"})
forced = wait_for(S_, "RUN")
check("Force on overrides automatic", forced, api("/api/states/" + S_)["state"])
if forced:
    check("fan runs when forced on", wait_for(B_, "on"),
          api("/api/states/" + B_)["state"])

api("/api/services/select/select_option", {"entity_id": M, "option": "Force off"})
check("Force off stops the fan", wait_for(B_, "off"),
      api("/api/states/" + B_)["state"])

api("/api/services/select/select_option", {"entity_id": M, "option": "Auto"})
check("returns to Auto", wait_for(M, "Auto"))

# --- the command path actually reaches the fan ----------------------------
S = eid("sensor", "controller_state")
B = eid("binary_sensor", "fan_running")
start = api("/api/states/" + S)["state"]

api("/api/services/button/press",
    {"entity_id": eid("button", "boost_15_min")})
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
    left = api("/api/states/" + eid("sensor", "boost_remaining"))["state"]
    check("boost countdown populated", left not in ("0", "unknown"), "%s min" % left)

    api("/api/services/button/press",
        {"entity_id": eid("button", "cancel_boost")})
    canceled = False
    for _ in range(8):
        time.sleep(2)
        if api("/api/states/" + S)["state"] != "BOOST":
            canceled = True
            break
    check("cancel returns to automatic", canceled)
    # Cancel returns control to the automatic logic -- it does not mean
    # "stop". With the room light on, automatic correctly keeps running.
    # Asserting the fan stops here fails whenever the lights happen to be
    # on, which is exactly when someone would be using the bathroom.
    #
    # That fix named ONE of automatic's reasons, and there are three. It
    # went red on 2026-11-02 with `light=off fan=on`, on a controller that
    # was behaving perfectly: state RUN, reason "odor +84", because the odor
    # path had just been armed at 80 ticks and the room was at 92. Humidity
    # would have done the same thing on any run that happened during a
    # shower. Every sensed reason added to the controller would have to be
    # added here too, and a test that re-implements a subset of the rules it
    # is checking goes stale the moment the rules grow -- which is the same
    # mistake as the one the paragraph above describes, one sensor later.
    #
    # So ask the controller instead of guessing. It publishes the state it
    # reached and the reason it reached it, and "the fan follows automatic"
    # IS the claim that the output agrees with that state. This still has
    # teeth: a fan left energised after a cancel while the controller says
    # IDLE -- the actual regression worth catching here -- fails it.
    state_now = api("/api/states/" + S)["state"]
    why = api("/api/states/" + S)["attributes"].get("why")
    fan_now = api("/api/states/" + B)["state"]
    expect = "off" if state_now == "IDLE" else "on"
    check("fan follows automatic after cancel", fan_now == expect,
          "state=%s fan=%s expected=%s why=%s"
          % (state_now, fan_now, expect, why))

# --- summary --------------------------------------------------------------
passed = sum(1 for _, ok, _ in results if ok)
print("\n%d/%d passed" % (passed, len(results)))
fails = [n for n, ok, _ in results if not ok]
if fails:
    print("failed: " + ", ".join(fails))
sys.exit(0 if not fails else 1)
