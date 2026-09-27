"""Home Assistant MQTT discovery and state publishing.

Sensors and binary sensors report; buttons send commands back. Parameter
writes (thresholds, timings) are still to come -- those need clamping and
persistence, and are worth doing after a week of real data rather than
before.

Topic layout assumes more than one of these will exist:

    fan/<room>/status        online | offline   (LWT, retained)
    fan/<room>/state         JSON snapshot      (retained)
    fan/<room>/cmd           boost | cancel | relearn | clear
    homeassistant/<kind>/<device>/<key>/config  (discovery, retained)

One JSON state topic rather than a topic per sensor: fewer publishes, one
atomic snapshot, and HA pulls fields out with value_template. Same shape the
thb1s bridge uses.
"""

import json
import time

from settings import SPECS, MODES

FW_VERSION = "2c.2"

# Files whose content defines "what build is running". Hashed together into
# a short fingerprint so the installed code can be identified over the
# network -- without it, the only way to know what an OTA actually put on a
# device in a ceiling is to go and get it.
TRACKED = ("main.py", "config.py", "settings.py", "telemetry.py",
           "net.py", "mqtt.py", "ringbuf.py", "calibrate.py")


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
)


def _boost_left_min(c):
    """Whole minutes left on a boost, or 0 when none is running."""
    if c.state != "BOOST" or c.boost_until is None:
        return 0
    ms = time.ticks_diff(c.boost_until, time.ticks_ms())
    return max(0, int((ms + 59999) // 60000))      # round up: 1 s left reads 1


class Telemetry:
    def __init__(self, cfg, link, log=None):
        self.cfg = cfg
        self.link = link
        self._log = log or (lambda *a: None)

        room = cfg.ROOM
        self.status_topic = "fan/%s/status" % room
        self.state_topic = "fan/%s/state" % room
        self.cmd_topic = "fan/%s/cmd" % room
        self.set_topic = "fan/%s/set" % room

        self._announced = False
        self._last_pub = None
        self._last_sig = None

        self.fw = fingerprint()
        self._log("telemetry: build %s" % self.fw)

        self._run_ms = 0                # fan runtime accumulator, today
        self._run_since = None
        self._run_day = None

    # -- runtime accounting --------------------------------------------------

    def note_fan(self, on):
        """Track fan-on time. Called whenever the commanded state changes."""
        now = time.ticks_ms()
        if on and self._run_since is None:
            self._run_since = now
        elif not on and self._run_since is not None:
            self._run_ms += time.ticks_diff(now, self._run_since)
            self._run_since = None

    def _runtime_min(self):
        total = self._run_ms
        if self._run_since is not None:
            total += time.ticks_diff(time.ticks_ms(), self._run_since)
        return round(total / 60000.0, 1)

    def _roll_day(self):
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
                self._run_since = time.ticks_ms()

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

    # -- state ---------------------------------------------------------------

    def _payload(self, c, raw_light, rssi):
        span = max(1, self.cfg.LIGHT_ON)
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
            "fan_on":   bool(c.fan.value()),
            # What the fan is actually doing, not what it was told. A fan
            # turning while commanded off must read non-zero, or the one
            # number that would reveal a bypassed switch stays hidden.
            "rpm":      c.rpm or getattr(c, "rpm_idle", 0),
            "fault":    c.state == "FAULT",
            "reason":   c.fault_reason or None,
            "run_min":  self._runtime_min(),
            "boost_left": _boost_left_min(c),
            "rssi":     rssi,
            "up_min":   round(time.ticks_ms() / 60000.0, 1),
            "reconn":   self.link.connects,
            "outage_s": self.link.outage_s,
            "boot":     getattr(c, "boot_reason", "unknown"),
            "fw":       self.fw,
            "cfg":      c.st.as_dict(),
        }

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
        return (p["state"], p["fan_on"], p["light_on"], p["fault"],
                p["boost_left"], (p["rpm"] or 0) // 500,
                tuple(sorted(p["cfg"].items())))

    def publish(self, c, raw_light, rssi=None, force=False):
        """Publish state on the interval, or at once when something changes.

        Thirty seconds keeps history readable; an immediate publish on any
        state, fan, light or fault transition keeps it truthful. A fan that
        ran for 20 s should not be invisible between two samples.
        """
        self._roll_day()
        if not self.link.online:
            return False
        if not self._announced and not self.announce():
            return False

        p = self._payload(c, raw_light, rssi)
        sig = self._signature(p)
        due = (self._last_pub is None or
               time.ticks_diff(time.ticks_ms(), self._last_pub)
               >= self.cfg.PUBLISH_INTERVAL_S * 1000)

        if not (force or due or sig != self._last_sig):
            return False

        if self.link.publish(self.state_topic, json.dumps(p), retain=True):
            self._last_pub = time.ticks_ms()
            self._last_sig = sig
            return True
        return False
