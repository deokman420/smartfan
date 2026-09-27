"""Build the bathroom fan Lovelace dashboard over the HA WebSocket API.

Lovelace config cannot be written over REST -- only the WebSocket API
exposes lovelace/config/save. The token is read from .env and never printed.

Written for someone who did not build the thing: every section says what it
is for, thresholds appear next to the readings they govern, and nothing
claims a state the device is not actually in.
"""
import io, json, re, sys
import websocket

HA_WS = "ws://192.168.0.5:8123/api/websocket"
URL_PATH = "bathroom-fan"

import os
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
env = io.open(os.path.join(ROOT, ".env"), encoding="utf-8").read()
TOKEN = re.search(r'HASS_LLAT\s*=\s*"?([^"\s]+)', env).group(1)

NL = chr(10)          # written via heredocs; avoids escaping backslash-n

P = "sensor.bathroom_fan_controller_"
B = "binary_sensor.bathroom_fan_controller_"
N = "number.bathroom_fan_controller_"

E = {
    "state":      P + "controller_state",
    "temp":       P + "temperature",
    "rh":         P + "humidity",
    "dp":         P + "dew_point",
    "base":       P + "humidity_baseline",
    "exc":        P + "excess_humidity",
    "light":      P + "light_level",
    "rpm":        P + "fan_speed",
    "runtime":    P + "runtime_today",
    "rssi":       P + "wifi_signal",
    "boost_left": P + "boost_remaining",
    "build":      P + "build",
    "uptime":     P + "uptime",
    "reconn":     P + "link_reconnects",
    "outage":     P + "last_outage",
    "boot":       P + "last_boot_reason",
    "fan_on":     B + "fan_running",
    "light_on":   B + "light_detected",
    "fault":      B + "fault",
    "btn_boost":  "button.bathroom_fan_controller_boost_15_min",
    "btn_cancel": "button.bathroom_fan_controller_cancel_boost",
    "btn_relearn": "button.bathroom_fan_controller_relearn_baseline",
    "mode":       "select.bathroom_fan_controller_mode",
    "rh_on":      N + "humidity_rise_to_start",
    "rh_off":     N + "humidity_fall_to_stop",
    "l_on":       N + "light_on_threshold",
    "l_off":      N + "light_off_threshold",
    "min_run":    N + "minimum_run",
    "max_run":    N + "maximum_run",
    "post_light": N + "run_after_lights_out",
    "boost_dur":  N + "boost_duration",
}


def heading(text, icon):
    return {"type": "heading", "heading": text, "heading_style": "title",
            "icon": icon}


def sub(text):
    return {"type": "heading", "heading": text, "heading_style": "subtitle"}


def tile(entity, name=None, **kw):
    c = {"type": "tile", "entity": entity}
    if name:
        c["name"] = name
    c.update(kw)
    return c


def note(text):
    return {"type": "markdown", "content": text}


def only_when(card, entity, state):
    card = dict(card)
    card["visibility"] = [{"condition": "state", "entity": entity,
                           "state": state}]
    return card


CONFIG = {
    "views": [{
        "title": "Bathroom fan",
        "path": "main",
        "icon": "mdi:fan",
        "type": "sections",
        "max_columns": 3,
        "sections": [

            # ---- 1. state at a glance ---------------------------------
            {"type": "grid", "cards": [
                heading("Right now", "mdi:eye-outline"),
                tile(E["state"], "Controller", icon="mdi:state-machine",
                     color="blue"),
                tile(E["fan_on"], "Fan", icon="mdi:fan", color="cyan"),
                tile(E["light_on"], "Bathroom light", color="amber"),
                tile(E["fault"], "Fault", color="red"),
                tile(E["rpm"], "Fan speed", icon="mdi:speedometer"),
                note(
                    "**IDLE** waiting &nbsp;·&nbsp; **RUN** extracting "
                    "&nbsp;·&nbsp; **BOOST** manual &nbsp;·&nbsp; "
                    "**LEARN** first minute after a restart "
                    "&nbsp;·&nbsp; **FAULT** needs a look" + NL + NL +
                    "Fan speed reads 0 whenever the fan is off. The "
                    "tachometer is only meaningful while it is turning."
                ),
            ]},

            # ---- 2. manual control ------------------------------------
            {"type": "grid", "cards": [
                heading("Manual control", "mdi:gesture-tap-button"),
                tile(E["btn_boost"], "Boost 15 min", icon="mdi:fan-plus",
                     color="cyan", tap_action={"action": "toggle"}),
                only_when(tile(E["boost_left"], "Time remaining",
                               icon="mdi:timer-sand", color="cyan"),
                          E["state"], "BOOST"),
                only_when(tile(E["btn_cancel"], "Cancel boost",
                               icon="mdi:fan-off", color="red",
                               tap_action={"action": "toggle"}),
                          E["state"], "BOOST"),
                tile(E["mode"], "Mode", icon="mdi:tune",
                     features=[{"type": "select-options"}]),
                note(
                    "**Boost** runs the fan for 15 minutes whatever the "
                    "light and humidity are doing, then hands back to "
                    "automatic." + NL + NL +
                    "**Force off** stops it without cutting power — for a "
                    "long bath, or to silence a misbehaving sensor while "
                    "you are away. **Force on** airs the room out. Both "
                    "override everything except a fault."
                ),
            ]},

            # ---- 3. the number it decides on ---------------------------
            {"type": "grid", "column_span": 2, "cards": [
                heading("The decision", "mdi:water-percent"),
                {
                    "type": "gauge",
                    "entity": E["exc"],
                    "name": "Excess above baseline",
                    "min": -5,
                    "max": 20,
                    "needle": True,
                    # Bands mirror the thresholds below. Green settled,
                    # amber would keep a run going, red would start one.
                    "severity": {"green": -5, "yellow": 3, "red": 8},
                },
                {
                    "type": "history-graph",
                    "title": "Humidity against its learned baseline",
                    "hours_to_show": 24,
                    "entities": [
                        {"entity": E["rh"], "name": "Humidity"},
                        {"entity": E["base"], "name": "Baseline"},
                        {"entity": E["exc"], "name": "Excess"},
                    ],
                },
                sub("Thresholds in force"),
                tile(E["rh_on"], "Start at"),
                tile(E["rh_off"], "Stop below"),
                tile(E["btn_relearn"], "Relearn baseline",
                     icon="mdi:refresh", tap_action={"action": "toggle"}),
                note(
                    "Humidity alone means nothing — a muggy August day sits "
                    "at 65 % by itself. What matters is the **rise above "
                    "what this room normally sits at**, which the controller "
                    "learns continuously while idle and dark." + NL + NL +
                    "It will not learn while the light is on or the fan is "
                    "running, so a shower can never teach it that steam is "
                    "normal." + NL + NL +
                    "**Relearn** snaps the baseline to whatever the room "
                    "reads this instant. Useful after a window has been open "
                    "for a day. Avoid it while the room is damp — it would "
                    "take the wet reading as normal, and roughly an hour of "
                    "idle, dark time to walk back down." + NL + NL +
                    "*A real shower here peaked at +38.5 %.*"
                ),
            ]},

            # ---- 4. what it has been doing -----------------------------
            {"type": "grid", "column_span": 2, "cards": [
                heading("Activity", "mdi:chart-timeline-variant"),
                {
                    "type": "history-graph",
                    "title": "Fan, light and state",
                    "hours_to_show": 24,
                    "entities": [
                        {"entity": E["fan_on"], "name": "Fan"},
                        {"entity": E["light_on"], "name": "Light"},
                        {"entity": E["state"], "name": "State"},
                    ],
                },
                tile(E["runtime"], "Run time today",
                     icon="mdi:timer-outline"),
                note(
                    "A healthy run: light on, fan starts, light goes out, "
                    "fan **keeps going** until the room is dry or the "
                    "45-minute ceiling is reached." + NL + NL +
                    "A run ending exactly on 45 minutes means the room had "
                    "not dried in time — worth watching if it becomes the "
                    "usual outcome rather than the exception."
                ),
            ]},

            # ---- 5. climate --------------------------------------------
            {"type": "grid", "cards": [
                heading("Climate", "mdi:thermometer"),
                tile(E["temp"], "Temperature"),
                tile(E["rh"], "Humidity"),
                tile(E["dp"], "Dew point"),
                {
                    "type": "history-graph",
                    "title": "Temperature and dew point",
                    "hours_to_show": 24,
                    "entities": [
                        {"entity": E["temp"], "name": "Temperature"},
                        {"entity": E["dp"], "name": "Dew point"},
                    ],
                },
            ]},

            # ---- 6. light sensing, calibrated --------------------------
            {"type": "grid", "cards": [
                heading("Light sensing", "mdi:brightness-6"),
                tile(E["light"], "Light level"),
                tile(E["light_on"], "Reads as"),
                sub("Thresholds in force"),
                tile(E["l_on"], "On above"),
                tile(E["l_off"], "Off below"),
                {
                    "type": "history-graph",
                    "title": "Light level",
                    "hours_to_show": 12,
                    "entities": [{"entity": E["light"], "name": "Light"}],
                },
                note(
                    "**Calibrated in place.** Measured with the sensor where "
                    "it now sits: dark **4,800–6,225**, lit "
                    "**30,000–32,500** raw." + NL + NL +
                    "Thresholds sit at roughly one third and two thirds of "
                    "that span, leaving about 7,800 counts of clearance "
                    "either side. The wide gap between them is deliberate — "
                    "it stops a flickering LED bulb chattering the fan." + NL + NL +
                    "*A bathroom is never truly dark. An earlier bench "
                    "calibration put the off-threshold inside the dark-state "
                    "noise and the fan ran indefinitely. Recalibrate from "
                    "this graph if the fixture or the sensor moves.*"
                ),
            ]},

            # ---- 7. timings ---------------------------------------------
            {"type": "grid", "cards": [
                heading("Timings", "mdi:timer-cog-outline"),
                tile(E["min_run"], "Minimum run"),
                tile(E["max_run"], "Maximum run"),
                tile(E["post_light"], "After lights out"),
                tile(E["boost_dur"], "Boost length"),
                note(
                    "Changes apply immediately and survive a restart — they "
                    "are written to the device, not held in Home "
                    "Assistant." + NL + NL +
                    "Every value is also clamped in firmware, and paired "
                    "settings are kept in order: raising *stop below* past "
                    "*start at* pushes the other out of the way rather than "
                    "leaving a fan that starts and stops on the same breath."
                ),
            ]},

            # ---- 8. health ----------------------------------------------
            {"type": "grid", "column_span": 2, "cards": [
                heading("Health", "mdi:heart-pulse"),
                tile(E["build"], "Build", icon="mdi:package-variant"),
                tile(E["uptime"], "Uptime"),
                tile(E["rssi"], "WiFi signal"),
                tile(E["boot"], "Last restart", icon="mdi:restart"),
                tile(E["reconn"], "Reconnects", icon="mdi:lan-connect"),
                tile(E["outage"], "Last outage", icon="mdi:lan-disconnect"),
                note(
                    "**Build** is a hash of the code actually running. It is "
                    "how an over-the-air update is confirmed without going "
                    "to fetch the device." + NL + NL +
                    "**Reconnects** and **last outage** report gaps honestly "
                    "rather than papering over them — readings taken while "
                    "offline are not replayed, because they would land at "
                    "the wrong time and corrupt the history." + NL + NL +
                    "A restart reason of *watchdog or reset* is normal after "
                    "an update. Repeated ones with no update are not: the "
                    "watchdog only fires when the control loop has stopped."
                ),
            ]},
        ],
    }],
}


def main():
    ws = websocket.create_connection(HA_WS, timeout=15)
    nid = [0]

    def send(msg):
        nid[0] += 1
        msg["id"] = nid[0]
        ws.send(json.dumps(msg))
        while True:
            r = json.loads(ws.recv())
            if r.get("id") == nid[0]:
                return r

    hello = json.loads(ws.recv())
    assert hello["type"] == "auth_required", hello
    ws.send(json.dumps({"type": "auth", "access_token": TOKEN}))
    auth = json.loads(ws.recv())
    if auth["type"] != "auth_ok":
        print("AUTH FAILED:", auth.get("message"))
        return 1
    print("authenticated to HA %s" % auth.get("ha_version"))

    existing = send({"type": "lovelace/dashboards/list"})
    paths = {d["url_path"]: d for d in existing.get("result", [])}
    if URL_PATH in paths:
        print("updating dashboard '%s'" % URL_PATH)
    else:
        r = send({"type": "lovelace/dashboards/create",
                  "url_path": URL_PATH, "title": "Bathroom Fan",
                  "icon": "mdi:fan", "mode": "storage",
                  "show_in_sidebar": True, "require_admin": False})
        if not r.get("success"):
            print("create failed:", r.get("error"))
            return 1
        print("created dashboard '%s'" % URL_PATH)

    r = send({"type": "lovelace/config/save",
              "url_path": URL_PATH, "config": CONFIG})
    if not r.get("success"):
        print("config save failed:", r.get("error"))
        return 1

    v = CONFIG["views"][0]
    cards = sum(len(s["cards"]) for s in v["sections"])
    refs = set()

    def walk(o):
        if isinstance(o, dict):
            for k, val in o.items():
                if k == "entity" and isinstance(val, str):
                    refs.add(val)
                else:
                    walk(val)
        elif isinstance(o, list):
            for i in o:
                walk(i)

    walk(v)
    print("saved: %d sections, %d cards, %d entities"
          % (len(v["sections"]), cards, len(refs)))

    live = {e["entity_id"] for e in send({"type": "get_states"})["result"]}
    missing = sorted(e for e in refs if e not in live)
    print("missing entities: %s" % (", ".join(missing) if missing else "none"))
    unused = sorted(e for e in live
                    if "bathroom_fan_controller" in e and e not in refs)
    print("entities not shown: %s" % (", ".join(unused) if unused else "none"))
    print("URL: http://192.168.0.5:8123/%s/main" % URL_PATH)
    ws.close()
    return 0


sys.exit(main())
