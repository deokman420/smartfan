"""Build the bathroom fan Lovelace dashboard over the HA WebSocket API.

Lovelace config cannot be written over REST -- only the WebSocket API
exposes lovelace/config/save. The token is read from .env and never printed.

Written for someone who did not build the thing: every section says what it
is for, thresholds appear next to the readings they govern, and nothing
claims a state the device is not actually in.
"""
import io, json, re, sys
import websocket

HA_WS = "wss://ha.local.phbeks.com/api/websocket"
# Deliberately NOT renamed alongside the display title. This script
# creates-or-updates by url_path, so changing it would leave the old
# dashboard orphaned beside a new one -- and break the bookmark.
URL_PATH = "bathroom-fan"

import os
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
env = io.open(os.path.join(ROOT, ".env"), encoding="utf-8").read()
TOKEN = re.search(r'HASS_LLAT\s*=\s*"?([^"\s]+)', env).group(1)

NL = chr(10)          # written via heredocs; avoids escaping backslash-n

# Domains only. The prefix is resolved per entity, because this device
# answers to two of them: Home Assistant builds an entity_id from the
# device name the first time it sees an entity and never rewrites it, so
# renaming this device to "Bathroom SmartFan" left everything announced
# before the rename under bathroom_fan_controller_* and everything after
# it -- the whole odor path, and the four light settings from the
# ambient-tracking rework -- under bathroom_smartfan_*. One prefix written
# here pointed the Odor card at six entities that do not exist.
P = "sensor."
B = "binary_sensor."
N = "number."

PREFIXES = ("bathroom_fan_controller", "bathroom_smartfan")


def _live_entities():
    """Entity ids HA currently holds, or an empty set if it cannot be asked.

    Read over HTTP before the config is built, because the config needs the
    answers and the WebSocket session does not open until main(). An empty
    set is not fatal: resolution falls back to the first prefix and the
    missing-entity report at the end of main() still catches it.
    """
    import urllib.request
    host = HA_WS.split("://", 1)[1].split("/", 1)[0]
    for scheme in ("https", "http"):
        try:
            r = urllib.request.Request(
                "%s://%s/api/states" % (scheme, host),
                headers={"Authorization": "Bearer " + TOKEN})
            with urllib.request.urlopen(r, timeout=15) as f:
                return {e["entity_id"] for e in json.load(f)}
        except Exception:
            continue
    print("warning: could not read HA states; entity ids unverified")
    return set()


def resolve(domain_and_suffix):
    """'sensor.humidity' -> the real entity_id, whichever prefix HA gave it."""
    domain, suffix = domain_and_suffix.split(".", 1)
    for prefix in PREFIXES:
        candidate = "%s.%s_%s" % (domain, prefix, suffix)
        if candidate in LIVE:
            return candidate
    return "%s.%s_%s" % (domain, PREFIXES[0], suffix)

E = {
    "state":      P + "controller_state",
    "temp":       P + "temperature",
    "rh":         P + "humidity",
    "dp":         P + "dew_point",
    "base":       P + "humidity_baseline",
    "exc":        P + "excess_humidity",
    "light":      P + "light_level",
    "voc":        P + "odour_raw",        # id frozen at the old spelling
    "voc_base":   P + "odour_baseline",   # see PREFIXES: HA never renames
    "voc_exc":    P + "excess_odour",     # an entity_id it has issued
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
    "voc_ok":     B + "odour_sensor",
    "btn_boost":  "button.boost_15_min",
    "btn_cancel": "button.cancel_boost",
    "btn_relearn": "button.relearn_baseline",
    "mode":       "select.mode",
    "rh_on":      N + "humidity_rise_to_start",
    "rh_off":     N + "humidity_fall_to_stop",
    "voc_on":     N + "odour_rise_to_start",
    "voc_off":    N + "odour_fall_to_stop",
    # The four that actually drive the light path. light_on_threshold and
    # light_off_threshold were here until now, and both were DEAD: the
    # ambient-tracking rework replaced absolute levels with a rate test, so
    # the firmware stopped announcing them and stopped reading them. They
    # survived only as retained discovery messages on the broker, which is
    # why the dashboard could still show two dials that moved nothing.
    "l_step":     N + "light_switch_step",
    "l_rise_on":  N + "light_rise_to_start",
    "l_rise_off": N + "light_rise_to_stop",
    "l_only":     N + "light_only_run",
    "min_run":    N + "minimum_run",
    "max_run":    N + "maximum_run",
    "post_light": N + "run_after_lights_out",
    "boost_dur":  N + "boost_duration",
}

LIVE = _live_entities()
E = {k: resolve(v) for k, v in E.items()}
_unresolved = sorted(v for v in E.values() if LIVE and v not in LIVE)
if _unresolved:
    print("warning: %d entity id(s) not in HA: %s"
          % (len(_unresolved), ", ".join(_unresolved)))


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


def odor_settle_card():
    """When the odor baseline will exist, while it does not.

    The heater starts at boot. The flatness gate cannot fire before three
    five-minute blocks exist (sgp41._FLAT_BLOCKS), and VOC_SETTLE_MAX_S
    seeds a baseline at two hours even if the reading never goes flat.
    Only the second of those is an ETA. Uptime is that clock to within the
    second or two the sensor takes to start, which is why this card does
    not wait on a firmware field: the board can be in the ceiling and the
    cable can be in another room.

    Hidden once a baseline exists. The baseline tile is the answer then,
    and a countdown beside a learned number would be a second opinion.
    """
    up = E["uptime"]
    base = E["voc_base"]
    content = (
        "{%% set up_s = states('%s') %%}\n"
        "{%% if up_s in ['unknown', 'unavailable', 'none', ''] %%}\n"
        "Settle time unavailable.\n"
        "{%% else %%}\n"
        "{%% set latest = ((120 - (up_s | float)) * 60) | round(0) %%}\n"
        "{%% set early = ((15 - (up_s | float)) * 60) | round(0) %%}\n"
        "{%% if latest <= 0 %%}\n"
        "Past the 2 hour limit. It seeds once the room is quiet.\n"
        "{%% elif early > 0 %%}\n"
        "Earliest **{{ (early / 60) | round(0) }} min**, "
        "by **{{ (latest / 60) | round(0) }} min**.\n"
        "{%% elif latest >= 5400 %%}\n"
        "By **{{ latest // 3600 }} h {{ (latest %% 3600) // 60 }} m**. "
        "Sooner if the reading goes flat.\n"
        "{%% else %%}\n"
        "By **{{ (latest / 60) | round(0) }} min**. "
        "Sooner if the reading goes flat.\n"
        "{%% endif %%}\n"
        "{%% endif %%}"
    ) % up
    return {
        "type": "markdown",
        "content": content,
        "visibility": [{
            "condition": "template",
            "value_template": (
                "{{ states('%s') in ['unknown', 'unavailable', 'none', ''] }}"
                % base
            ),
        }],
    }


def only_when(card, entity, state):
    card = dict(card)
    card["visibility"] = [{"condition": "state", "entity": entity,
                           "state": state}]
    return card


CONFIG = {
    "views": [{
        "title": "Bathroom Smartfan",
        "path": "main",
        "icon": "mdi:fan",
        "type": "sections",
        # Four. Three was tried: Home Assistant caps a column's width, so
        # three columns at 2560px drew a 1220px grid stranded in the middle
        # of the screen with a dead margin down the left. Four fills it.
        "max_columns": 4,
        "sections": [

            # Home Assistant's sections view is a ROW GRID, not masonry: every
            # section in a row is as tall as the tallest one in it. A
            # four-tile section beside one carrying a graph gets padded out
            # with dead space, which is what made this look sparse.
            #
            # So the sections are grouped by height, not only by topic: the
            # four tile-only ones fill row 1, the four graph-bearing ones fill
            # row 2, and within each row the contents are shuffled until the
            # heights are close. That is why Fan speed and Run today sit with
            # the activity graph rather than under "Right now".
            #
            # Prose lives in the README. A dashboard is glanced at, and long
            # notes turn a section into a tall thin column with a gap beside
            # it -- and an endless scroll on a phone.

            # ---- row 1: tiles only ------------------------------------
            {"type": "grid", "cards": [
                heading("Right now", "mdi:eye-outline"),
                tile(E["state"], "Controller", icon="mdi:state-machine",
                     color="blue"),
                tile(E["fan_on"], "Fan", icon="mdi:fan", color="cyan"),
                tile(E["light_on"], "Light", color="amber"),
                tile(E["fault"], "Fault", color="red"),
                tile(E["mode"], "Mode", icon="mdi:tune",
                     features=[{"type": "select-options"}]),
            ]},

            {"type": "grid", "cards": [
                heading("Control", "mdi:gesture-tap-button"),
                tile(E["btn_boost"], "Boost 15 min", icon="mdi:fan-plus",
                     color="cyan", tap_action={"action": "toggle"}),
                tile(E["boost_dur"], "Boost length",
                     icon="mdi:timer-cog-outline"),
                only_when(tile(E["boost_left"], "Time remaining",
                               icon="mdi:timer-sand", color="cyan"),
                          E["state"], "BOOST"),
                only_when(tile(E["btn_cancel"], "Cancel boost", icon="mdi:fan-off",
                               color="red", tap_action={"action": "toggle"}),
                          E["state"], "BOOST"),
                tile(E["btn_relearn"], "Relearn base", icon="mdi:refresh",
                     tap_action={"action": "toggle"}),
            ]},

            {"type": "grid", "cards": [
                heading("Thresholds", "mdi:tune-vertical"),
                tile(E["rh_on"], "Start at"),
                tile(E["rh_off"], "Stop below"),
                # Both ship at 0, which means the odor path watches and
                # publishes but never touches the fan. Raise "Odor start"
                # off 0 to arm it -- and pick the number off a real event in
                # the history below, not off the datasheet.
                tile(E["voc_on"], "Odor start"),
                tile(E["voc_off"], "Odor stop"),
                # The fixture is found by how FAST the reading jumps away
                # from the learned ambient, never by a level -- daylight in
                # this room reads higher than the bulb. These four are that
                # test: the jump that counts as a switch, how far above
                # ambient it must land, how close to ambient it must return,
                # and how long light alone may run the fan.
                tile(E["l_step"], "Switch step"),
                tile(E["l_rise_on"], "Light start"),
                tile(E["l_rise_off"], "Light stop"),
                tile(E["l_only"], "Light-only run"),
                tile(E["min_run"], "Min run"),
                tile(E["max_run"], "Max run"),
                tile(E["post_light"], "After lights"),
            ]},

            {"type": "grid", "cards": [
                heading("Health", "mdi:heart-pulse"),
                tile(E["build"], "Build", icon="mdi:package-variant"),
                tile(E["uptime"], "Uptime"),
                tile(E["rssi"], "WiFi"),
                tile(E["boot"], "Last restart", icon="mdi:restart"),
                tile(E["reconn"], "Reconnects", icon="mdi:lan-connect"),
                tile(E["outage"], "Last outage", icon="mdi:lan-disconnect"),
            ]},

            # ---- row 2: each carries exactly one graph -----------------
            {"type": "grid", "cards": [
                heading("Humidity", "mdi:water-percent"),
                {
                    "type": "gauge",
                    "entity": E["exc"],
                    "name": "Above baseline",
                    "min": -5, "max": 20, "needle": True,
                    "severity": {"green": -5, "yellow": 3, "red": 8},
                },
                tile(E["rh"], "Now"),
                tile(E["base"], "Baseline"),
                {
                    "type": "history-graph",
                    "title": "Humidity against its baseline",
                    "hours_to_show": 24,
                    "entities": [
                        {"entity": E["rh"], "name": "Humidity"},
                        {"entity": E["base"], "name": "Baseline"},
                        {"entity": E["exc"], "name": "Excess"},
                    ],
                },
            ]},

            # Odor sits directly under humidity, because the two are read
            # together: this card answers "was there a reason to run that the
            # humidity could not see?", which is the question the sensor was
            # bought to answer.
            {"type": "grid", "cards": [
                heading("Odor", "mdi:scent"),
                {
                    "type": "gauge",
                    "entity": E["voc_exc"],
                    "name": "Below baseline",
                    # Ticks, not a percentage. The quiet-room spread measured
                    # 619 over 20 s, so the green band has to cover ordinary
                    # noise or the gauge cries wolf all day.
                    "min": -500, "max": 3000, "needle": True,
                    "severity": {"green": -500, "yellow": 700, "red": 1500},
                },
                tile(E["voc"], "Raw"),
                tile(E["voc_base"], "Baseline"),
                tile(E["voc_ok"], "Sensor", icon="mdi:scent"),
                odor_settle_card(),
                {
                    "type": "history-graph",
                    "title": "Odor against its baseline",
                    "hours_to_show": 24,
                    "entities": [
                        {"entity": E["voc"], "name": "Raw ticks"},
                        {"entity": E["voc_base"], "name": "Baseline"},
                        {"entity": E["voc_exc"], "name": "Excess"},
                    ],
                },
            ]},

            {"type": "grid", "cards": [
                heading("Activity", "mdi:chart-timeline-variant"),
                tile(E["rpm"], "Fan speed", icon="mdi:speedometer"),
                tile(E["runtime"], "Run today", icon="mdi:timer-outline"),
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
            ]},

            {"type": "grid", "cards": [
                heading("Light", "mdi:brightness-6"),
                tile(E["light"], "Level"),
                tile(E["light_on"], "Reads as"),
                {
                    "type": "history-graph",
                    "title": "Light level",
                    "hours_to_show": 12,
                    "entities": [{"entity": E["light"], "name": "Light"}],
                },
                note("Calibrated in place: dark 4,800–6,225, "
                     "lit 30,000–32,500 raw."),
            ]},

            {"type": "grid", "cards": [
                heading("Climate", "mdi:thermometer"),
                tile(E["temp"], "Temperature"),
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
                  "url_path": URL_PATH, "title": "Bathroom Smartfan",
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
                    if any(p in e for p in PREFIXES) and e not in refs)
    print("entities not shown: %s" % (", ".join(unused) if unused else "none"))
    print("URL: https://ha.local.phbeks.com/%s/main" % URL_PATH)
    ws.close()
    return 0


# Guarded. This was a bare `sys.exit(main())`, so merely importing this
# module to inspect the entity map SAVED the dashboard to Home Assistant --
# an import that reads like a read and writes to a live system. Anything
# wanting the resolved ids can now import this safely.
if __name__ == "__main__":
    sys.exit(main())
