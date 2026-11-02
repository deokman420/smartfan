"""The device's own view of itself, and the Home Assistant MQTT surface.

Two classes, split along one line: whether a broker has to exist.

    Snapshot   what the controller currently is -- the fingerprint, the
               daily runtime accounting, and the JSON payload. Needs no
               network at all, and the on-device HTTP API reads it directly.
    Telemetry  publishing that payload over MQTT, plus Home Assistant
               discovery. Needs a live link.

The split is what makes a broker optional. Before it, the payload could only
be built by the object that owned an MQTT connection, so a user with no
broker -- and therefore no Home Assistant -- had no way to see any of this,
even from a page served by the device itself. See httpd.py and webapi.py.

Sensors and binary sensors report; buttons send commands back. Parameter
writes (thresholds, timings) are still to come -- those need clamping and
persistence, and are worth doing after a week of real data rather than
before.

Topic layout assumes more than one of these will exist:

    fan/<room>/status        online | offline   (LWT, retained)
    fan/<room>/state         JSON snapshot      (retained)
    fan/<room>/cmd           boost | cancel | relearn | clear
    fan/<room>/set/<PARAM>   a tunable's new value
    homeassistant/<kind>/<device>/<key>/config  (discovery, retained)

One JSON state topic rather than a topic per sensor: fewer publishes, one
atomic snapshot, and HA pulls fields out with value_template. Same shape the
thb1s bridge uses.
"""

import json
import time

from settings import SPECS, MODES

# MicroPython's monotonic millisecond clock, with a CPython fallback so this
# module -- and the payload every surface reads -- can be exercised by
# tests/test_httpd.py without a board attached.
try:
    _ticks = time.ticks_ms
    _diff = time.ticks_diff
except AttributeError:
    def _ticks():
        return int(time.monotonic() * 1000)

    def _diff(a, b):
        return a - b

FW_VERSION = "2c.3"

# Files whose content defines "what build is running". Hashed together into
# a short fingerprint so the installed code can be identified over the
# network -- without it, the only way to know what an OTA actually put on a
# device in a ceiling is to go and get it.
TRACKED = ("main.py", "config.py", "settings.py", "telemetry.py",
           "light.py", "sgp41.py", "voc.py", "net.py", "mqtt.py", "ringbuf.py",
           "calibrate.py", "provision.py", "improv.py", "httpd.py",
           "webapi.py", "store.py", "wifi.py")

# The app bundle is versioned separately and NOT hashed into the fingerprint.
# It is pushed by tools/build_www.py rather than by an OTA, changes on its own
# schedule, and folding it in would make every firmware fingerprint comparison
# report a mismatch after a purely cosmetic app change.
APP_MANIFEST = "www/build.json"


def fingerprint():
    """Short hash of the tracked files. Computed once, at construction."""
    try:
        import hashlib
        import binascii
        outer = hashlib.sha256()
        for name in sorted(TRACKED):
            h = hashlib.sha256()
            try:
                with open(name, "rb") as f:
                    while True:
                        b = f.read(512)
                        if not b:
                            break
                        h.update(b)
                digest = binascii.hexlify(h.digest()).decode()
            except OSError:
                digest = "-"
            outer.update((name + digest).encode())
        return binascii.hexlify(outer.digest()).decode()[:8]
    except Exception:
        return "unknown"

# Sensors: key -> (label, json field, unit, device_class, state_class, icon)
_SENSORS = (
    ("temperature", "Temperature",       "temp",     "°C", "temperature", "measurement",      None),
    ("humidity",    "Humidity",          "rh",       "%",       "humidity",    "measurement",      None),
    ("dew_point",   "Dew point",         "dp",       "°C", "temperature", "measurement",      None),
    ("baseline",    "Humidity baseline", "base",     "%",       "humidity",    "measurement",      None),
    ("excess",      "Excess humidity",   "exc",      "%",       None,          "measurement",      "mdi:water-percent"),
    ("light",       "Light level",       "light_pc", "%",       None,          "measurement",      "mdi:brightness-6"),
    ("voc",         "Odor raw",          "voc",      None,      None,          "measurement",      "mdi:scent"),
    ("voc_base",    "Odor baseline",     "voc_base", None,      None,          "measurement",      "mdi:scent"),
    ("voc_excess",  "Excess odor",       "voc_exc",  None,      None,          "measurement",      "mdi:scent"),
    ("rpm",         "Fan speed",         "rpm",      "rpm",     None,          "measurement",      "mdi:fan"),
    ("state",       "Controller state",  "state",    None,      None,          None,               "mdi:state-machine"),
    ("runtime",     "Runtime today",     "run_min",  "min",     None,          "total_increasing", "mdi:timer-outline"),
    ("rssi",        "WiFi signal",       "rssi",     "dBm",     "signal_strength", "measurement",   None),
    ("boost_left",  "Boost remaining",   "boost_left", "min",   "duration",    None,               "mdi:timer-sand"),
    ("uptime",      "Uptime",            "up_min",   "min",     "duration",    None,               "mdi:clock-outline"),
    ("reconnects",  "Link reconnects",   "reconn",   None,      None,          "total_increasing", "mdi:lan-connect"),
    ("last_outage", "Last outage",       "outage_s", "s",       "duration",    None,               "mdi:lan-disconnect"),
    ("boot_reason", "Last boot reason",  "boot",     None,      None,          None,               "mdi:restart"),
    ("build",       "Build",             "fw",       None,      None,          None,               "mdi:package-variant"),
)

# Buttons: key -> (label, payload sent on press, icon)
_BUTTONS = (
    ("boost",   "Boost 15 min",   "boost",   "mdi:fan-plus"),
    ("cancel",  "Cancel boost",   "cancel",  "mdi:fan-off"),
    ("relearn", "Relearn baseline", "relearn", "mdi:refresh"),
)

# Binary sensors: key -> (label, json field, device_class, icon)
_BINARY = (
    ("fan_on",   "Fan running",    "fan_on",   "running", "mdi:fan"),
    ("light_on", "Light detected", "light_on", "light",   None),
    ("fault",    "Fault",          "fault",    "problem", None),
    # So a sensor that has stood down after VOC_MAX_FAILS is visible, rather
    # than silently inert with its three sensors frozen on a last good value.
    ("voc_ok",   "Odor sensor",    "voc_ok",   None,      "mdi:scent"),
)


def app_build():
    """The app bundle's version, or None if no bundle is installed.

    Reported alongside the firmware fingerprint because the two are now
    separate deployables: a device can be running current firmware and a
    stale app, and that difference has to be visible from the app itself.
    """
    try:
        with open(APP_MANIFEST) as f:
            return json.load(f).get("build")
    except Exception:
        return None


def _boost_left_min(c):
    """Whole minutes left on a boost, or 0 when none is running."""
    if c.state != "BOOST" or c.boost_until is None:
        return 0
    ms = _diff(c.boost_until, _ticks())
    return max(0, int((ms + 59999) // 60000))      # round up: 1 s left reads 1


class Snapshot:
    """What the controller is right now, in one dict. No network needed.

    Owns the two pieces of state that only make sense over time -- the daily
    fan runtime and the build fingerprint -- so that whoever asks for the
    payload gets the same numbers, whether that is an MQTT publish or an
    HTTP request from a browser on the LAN.
    """

    def __init__(self, cfg, log=None):
        self.cfg = cfg
        self._log = log or (lambda *a: None)

        self.fw = fingerprint()
        self.app = app_build()
        self._log("telemetry: build %s, app %s" % (self.fw, self.app))

        self._run_ms = 0                # fan runtime accumulator, today
        self._run_since = None
        self._run_day = None

    # -- runtime accounting --------------------------------------------------

    def note_fan(self, on):
        """Track fan-on time. Called whenever the commanded state changes."""
        now = _ticks()
        if on and self._run_since is None:
            self._run_since = now
        elif not on and self._run_since is not None:
            self._run_ms += _diff(now, self._run_since)
            self._run_since = None

    def runtime_min(self):
        total = self._run_ms
        if self._run_since is not None:
            total += _diff(_ticks(), self._run_since)
        return round(total / 60000.0, 1)

    def roll_day(self):
        """Reset the daily counter at local midnight, once the clock is sane.

        Before NTP lands the RTC sits at its power-on default, so yearday is
        meaningless. Accumulate rather than reset until it looks real --
        a wrong reset loses data, a late one only delays it.
        """
        t = time.localtime()
        if t[0] < 2024:
            return
        day = t[7]
        if self._run_day is None:
            self._run_day = day
        elif day != self._run_day:
            self._run_day = day
            self._run_ms = 0
            if self._run_since is not None:
                self._run_since = _ticks()

    # -- state ---------------------------------------------------------------

    def payload(self, c, raw_light, rssi=None, link=None):
        """The one snapshot every surface reads.

        `link` is optional: with no broker configured there is none, and the
        link fields report the absence rather than being left out. A missing
        key would make the app draw an empty tile where the truthful answer
        is "there is no broker, by choice".
        """
        self.roll_day()
        # Read through getattr, because an OTA batch has to stay small enough
        # to install and so telemetry.py can legitimately be running a push
        # AHEAD of the main.py that grew these. Asking a Controller that
        # predates them raises inside the one call every surface depends on:
        # measured, it took out /api/state and the history writer for the eight
        # minutes between two pushes, while the fan itself carried on fine.
        amb = getattr(c, "light_amb", None)
        rise = c.light_rise() if hasattr(c, "light_rise") else 0
        # Same getattr discipline for the odor path, and for the same
        # reason: voc.py and sgp41.py cannot land in the same OTA batch as
        # the main.py that grows these attributes, so this file will run for
        # a while against a Controller that has none of them.
        vdet = getattr(c, "vocdet", None)
        voc_present = getattr(c, "voc", None) is not None
        voc_raw = getattr(vdet, "raw", None) if vdet is not None else None
        voc_base = getattr(vdet, "base", None) if vdet is not None else None
        voc_exc = getattr(c, "excess_voc", None)
        return {
            "state":    c.state,
            "temp":     round(c.temp, 1) if c.temp is not None else None,
            "rh":       round(c.rh, 1) if c.rh is not None else None,
            "dp":       round(c.dew_point(), 1) if c.dew_point() is not None else None,
            "base":     round(c.baseline, 1) if c.baseline is not None else None,
            "exc":      round(c.excess_rh, 1),
            "light":    raw_light,
            "light_pc": round(min(100.0, raw_light * 100.0 / 65535), 1),
            "light_on": bool(c.light_on),
            # The learned ambient, and how far the reading sits above it.
            # `light` alone cannot explain a light verdict now that the verdict
            # is made on the difference -- an app or a log showing only the raw
            # number cannot tell a lit room from a sunny one.
            "light_amb": int(amb) if amb is not None else None,
            "light_rise": rise,
            # As a percentage too, because the chart plots it against
            # `light_pc` and two series cannot share an axis in different
            # units. The raw pair above stays for the readings card.
            "light_amb_pc": (round(amb * 100.0 / 65535, 1)
                             if amb is not None else None),
            # None, not 0, when the sensor is absent or still conditioning:
            # Home Assistant shows "unavailable" for a null and a plausible
            # reading for a zero, and a fake zero on a MOX signal would read
            # as the worst air the room has ever had.
            "voc":      int(voc_raw) if voc_raw is not None else None,
            "voc_base": int(voc_base) if voc_base is not None else None,
            # None-preserving: excess_voc is None when there is no opinion
            # and 0 when the air is exactly as clean as normal, and int()
            # on the first would raise.
            "voc_exc":  (None if voc_exc is None else int(voc_exc)),
            "voc_ok":   (bool(getattr(c.voc, "ok", False))
                         if voc_present else None),
            "fan_on":   bool(c.fan.value()),
            # What the fan is actually doing, not what it was told. A fan
            # turning while commanded off must read non-zero, or the one
            # number that would reveal a bypassed switch stays hidden.
            "rpm":      c.rpm or getattr(c, "rpm_idle", 0),
            "fault":    c.state == "FAULT",
            "reason":   c.fault_reason or None,
            "run_min":  self.runtime_min(),
            "boost_left": _boost_left_min(c),
            "rssi":     rssi,
            "up_min":   round(_ticks() / 60000.0, 1),
            "reconn":   getattr(link, "connects", 0),
            "outage_s": getattr(link, "outage_s", 0),
            "mqtt":     getattr(link, "state", "disabled"),
            # The address the app can reach this fan on, which the
            # broker route has no other way to learn: a reading arrives
            # over MQTT with no return path in it, so a broker-mode
            # browser could not ask for the stored history it is sitting
            # beside. None while WiFi is down, which the app treats the
            # same as absent.
            "ip":       getattr(link, "ip", None),
            # How the power-on self-test went: 'passed', 'failed', or None
            # for a boot that did not run one. A failure is already loud --
            # it is a FAULT with a reason -- but a pass was indistinguishable
            # from never having run, which makes the whole test unverifiable
            # by the person relying on it. Not in _signature(), so it
            # triggers no extra publishes, and not in _SENSORS, so Home
            # Assistant gains no entity for something that changes once a
            # boot.
            "selftest": getattr(c, "selftest", None),
            "boot":     getattr(c, "boot_reason", "unknown"),
            "fw":       self.fw,
            "app":      self.app,
            "cfg":      c.st.as_dict(),
        }



class Telemetry:
    """Publishing a Snapshot over MQTT, plus Home Assistant discovery.

    Only constructed when there is a link to publish over. Everything that
    is true of the controller with or without a broker lives in Snapshot.
    """

    def __init__(self, cfg, link, snap, log=None):
        self.cfg = cfg
        self.link = link
        self.snap = snap
        self._log = log or (lambda *a: None)

        room = cfg.ROOM
        self.status_topic = "fan/%s/status" % room
        self.state_topic = "fan/%s/state" % room
        self.cmd_topic = "fan/%s/cmd" % room
        self.set_topic = "fan/%s/set" % room

        self._announced = False
        self._last_pub = None
        self._last_sig = None

    @property
    def fw(self):
        """The fingerprint, which belongs to the build, not to the link."""
        return self.snap.fw

    # -- discovery -----------------------------------------------------------

    def _device(self):
        return {
            "identifiers": [self.cfg.DEVICE_ID],
            "name": self.cfg.DEVICE_NAME,
            "model": "Smartfan controller (Pico 2 W)",
            "manufacturer": "deokman420",
            "sw_version": FW_VERSION,
        }

    def announce(self):
        """Publish HA discovery, retained. Returns True if all of it landed.

        Retained matters: if HA restarts before the device republishes,
        non-retained configs leave the entities gone until the next boot.
        """
        if self._announced or not self.link.online:
            return False
        dev = self._device()
        ok = True

        for key, label, field, unit, dclass, sclass, icon in _SENSORS:
            cfgd = {
                "name": label,
                "unique_id": "%s_%s" % (self.cfg.DEVICE_ID, key),
                "state_topic": self.state_topic,
                "value_template": "{{ value_json.%s }}" % field,
                "availability_topic": self.status_topic,
                "expire_after": self.cfg.EXPIRE_AFTER_S,
                "device": dev,
            }
            if unit:
                cfgd["unit_of_measurement"] = unit
            if dclass:
                cfgd["device_class"] = dclass
            if sclass:
                cfgd["state_class"] = sclass
            if icon:
                cfgd["icon"] = icon
            if key in ("rssi", "uptime", "reconnects", "last_outage",
                       "boot_reason", "build"):
                cfgd["entity_category"] = "diagnostic"
            ok &= self.link.publish(
                "homeassistant/sensor/%s/%s/config" % (self.cfg.DEVICE_ID, key),
                json.dumps(cfgd), retain=True)

        for key, label, field, dclass, icon in _BINARY:
            cfgd = {
                "name": label,
                "unique_id": "%s_%s" % (self.cfg.DEVICE_ID, key),
                "state_topic": self.state_topic,
                "value_template": "{{ 'ON' if value_json.%s else 'OFF' }}" % field,
                "payload_on": "ON",
                "payload_off": "OFF",
                "availability_topic": self.status_topic,
                "expire_after": self.cfg.EXPIRE_AFTER_S,
                "device": dev,
            }
            if dclass:
                cfgd["device_class"] = dclass
            if icon:
                cfgd["icon"] = icon
            ok &= self.link.publish(
                "homeassistant/binary_sensor/%s/%s/config" % (self.cfg.DEVICE_ID, key),
                json.dumps(cfgd), retain=True)

        for key, label, payload, icon in _BUTTONS:
            cfgd = {
                "name": label,
                "unique_id": "%s_%s" % (self.cfg.DEVICE_ID, key),
                "command_topic": self.cmd_topic,
                "payload_press": payload,
                "availability_topic": self.status_topic,
                "icon": icon,
                "device": dev,
            }
            ok &= self.link.publish(
                "homeassistant/button/%s/%s/config" % (self.cfg.DEVICE_ID, key),
                json.dumps(cfgd), retain=True)

        # Writable settings. min/max here are advisory -- Home Assistant uses
        # them to draw the control, but firmware clamps independently because
        # anything on the broker can publish to these topics.
        for name, (kind, lo, hi, step, unit, label, icon) in SPECS.items():
            base = {
                "name": label,
                "unique_id": "%s_%s" % (self.cfg.DEVICE_ID, name.lower()),
                "command_topic": "%s/%s" % (self.set_topic, name.lower()),
                "state_topic": self.state_topic,
                "value_template": "{{ value_json.cfg.%s }}" % name,
                "availability_topic": self.status_topic,
                "icon": icon,
                "entity_category": "config",
                "device": dev,
            }
            if kind == "select":
                base["options"] = list(MODES)
                topic = "homeassistant/select/%s/%s/config"
            else:
                base.update({"min": lo, "max": hi, "step": step,
                             "mode": "box"})
                if unit:
                    base["unit_of_measurement"] = unit
                topic = "homeassistant/number/%s/%s/config"
            ok &= self.link.publish(topic % (self.cfg.DEVICE_ID, name.lower()),
                                    json.dumps(base), retain=True)

        if ok:
            self.link.publish(self.status_topic, "online", retain=True)
            self._announced = True
            self._log("telemetry: announced %d entities"
                      % (len(_SENSORS) + len(_BINARY) + len(_BUTTONS)
                         + len(SPECS)))
        return ok

    def on_disconnect(self):
        """Re-announce after a reconnect; the broker may have lost retained state."""
        self._announced = False

    @staticmethod
    def _signature(p):
        """Fields whose change should publish immediately rather than wait.

        boost_left is whole minutes, so the countdown publishes once a minute
        while a boost runs and stays quiet otherwise.
        """
        # rpm is bucketed to 500 rather than included raw. Raw would publish
        # on every sample and flood the recorder; omitting it entirely leaves
        # Home Assistant showing the spin-up reading -- around 2,500 -- for up
        # to 30 s after the fan has actually settled near 4,000.
        # The odor excess is bucketed to 25 ticks for exactly the reason rpm
        # is bucketed to 500: a raw MOX signal moves on every 2 s sample, and
        # putting it in the signature unbucketed turns the 30 s publish
        # interval into a 2 s one and floods the recorder. voc and voc_base
        # are deliberately absent -- the excess is the one that carries a
        # decision. voc_ok is included unbucketed, because a sensor standing
        # down should be visible at once.
        return (p["state"], p["fan_on"], p["light_on"], p["fault"],
                p["boost_left"], (p["rpm"] or 0) // 500,
                # Bucketed, but None keeps a bucket of its own. Folding it
                # in with 0 would have meant a sensor appearing or going
                # away -- None to 0, or 0 to None -- changed nothing in the
                # signature and so published nothing, leaving every surface
                # showing the old value until the next interval.
                p.get("voc_ok"),
                (None if p.get("voc_exc") is None
                 else p["voc_exc"] // 25),
                tuple(sorted(p["cfg"].items())))

    def publish(self, c, raw_light, rssi=None, force=False, payload=None):
        """Publish state on the interval, or at once when something changes.

        Thirty seconds keeps history readable; an immediate publish on any
        state, fan, light or fault transition keeps it truthful. A fan that
        ran for 20 s should not be invisible between two samples.

        `payload` lets the caller pass a snapshot it has already built, so the
        stored history and the broker see the identical sample rather than two
        readings taken microseconds apart.
        """
        if not self.link.online:
            return False
        if not self._announced and not self.announce():
            return False

        p = payload if payload is not None else self.snap.payload(
            c, raw_light, rssi, self.link)
        sig = self._signature(p)
        due = (self._last_pub is None or
               _diff(_ticks(), self._last_pub)
               >= self.cfg.PUBLISH_INTERVAL_S * 1000)

        if not (force or due or sig != self._last_sig):
            return False

        if self.link.publish(self.state_topic, json.dumps(p), retain=True):
            self._last_pub = _ticks()
            self._last_sig = sig
            return True
        return False
