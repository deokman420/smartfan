"""Home Assistant MQTT discovery and state publishing.

Phase 2a: reporting only. Nothing here can change how the fan behaves --
there is no subscribe path yet, by design. That arrives in 2b, once a week
of real data has shown what the thresholds should actually be.

Topic layout assumes more than one of these will exist:

    fan/<room>/status        online | offline   (LWT, retained)
    fan/<room>/state         JSON snapshot      (retained)
    homeassistant/<kind>/<device>/<key>/config  (discovery, retained)

One JSON state topic rather than a topic per sensor: fewer publishes, one
atomic snapshot, and HA pulls fields out with value_template. Same shape the
thb1s bridge uses.
"""

import json
import time

FW_VERSION = "2a"

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
)

# Binary sensors: key -> (label, json field, device_class, icon)
_BINARY = (
    ("fan_on",   "Fan running",    "fan_on",   "running", "mdi:fan"),
    ("light_on", "Light detected", "light_on", "light",   None),
    ("fault",    "Fault",          "fault",    "problem", None),
)


class Telemetry:
    def __init__(self, cfg, link, log=None):
        self.cfg = cfg
        self.link = link
        self._log = log or (lambda *a: None)

        room = cfg.ROOM
        self.status_topic = "fan/%s/status" % room
        self.state_topic = "fan/%s/state" % room

        self._announced = False
        self._last_pub = None
        self._last_sig = None

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
            "name": "%s fan controller" % self.cfg.ROOM_NAME,
            "model": "Pico 2 W humidity fan controller",
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

        if ok:
            self.link.publish(self.status_topic, "online", retain=True)
            self._announced = True
            self._log("telemetry: announced %d entities"
                      % (len(_SENSORS) + len(_BINARY)))
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
            "rpm":      c.rpm,
            "fault":    c.state == "FAULT",
            "reason":   c.fault_reason or None,
            "run_min":  self._runtime_min(),
            "rssi":     rssi,
            "up_s":     time.ticks_ms() // 1000,
        }

    @staticmethod
    def _signature(p):
        """Fields whose change should publish immediately rather than wait."""
        return (p["state"], p["fan_on"], p["light_on"], p["fault"])

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
