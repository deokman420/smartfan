"""Runtime-writable settings, layered over config.py.

Resolution runs one way:

    config.py defaults  <-  settings.json overrides  <-  MQTT writes

Reads fall through to config.py, so a missing or corrupt settings.json
costs you your tuning and nothing else. A controller that refuses to boot
because of a bad settings write is a ladder trip; a controller that boots
on defaults is an inconvenience.

Every write is clamped here, in firmware. Home Assistant advertises min and
max on its number entities, but MQTT is writable by anything on the broker
and a bad payload must not be able to set MAX_RUN_S to zero. Clamp, report
what was actually stored, carry on.
"""

import json

import config as cfg

FILE = "settings.json"

# name -> (kind, lo, hi, step, unit, label, icon)
# Ranges are what the hardware and the logic can actually honour, not what
# seems reasonable -- they are the last line of defence.
SPECS = {
    "RH_RISE_ON":   ("number", 3.0, 20.0, 0.5, "%",   "Humidity rise to start", "mdi:water-plus"),
    "RH_FALL_OFF":  ("number", 1.0, 10.0, 0.5, "%",   "Humidity fall to stop",  "mdi:water-minus"),
    "LIGHT_STEP_ON":    ("number", 2000, 40000, 500, None, "Light switch step",  "mdi:brightness-6"),
    "LIGHT_RISE_ON":    ("number", 1000, 40000, 500, None, "Light rise to start", "mdi:brightness-5"),
    "LIGHT_RISE_OFF":   ("number", 500, 39000, 500, None, "Light rise to stop",  "mdi:brightness-4"),
    "LIGHT_ONLY_MAX_S": ("number", 60, 3600, 60, "s",      "Light-only run",     "mdi:timer-sand-complete"),
    "MIN_RUN_S":    ("number", 30, 900, 10, "s",      "Minimum run",            "mdi:timer-play"),
    "MAX_RUN_S":    ("number", 300, 7200, 60, "s",    "Maximum run",            "mdi:timer-alert"),
    "POST_LIGHT_S": ("number", 0, 900, 10, "s",       "Run after lights out",   "mdi:timer-sand"),
    "BOOST_S":      ("number", 60, 3600, 60, "s",     "Boost duration",         "mdi:fan-plus"),
    "MODE":         ("select", None, None, None, None, "Mode",                  "mdi:tune"),
}

MODES = ("Auto", "Force off", "Force on")

# Pairs that must stay ordered. Range clamping alone cannot express this:
# RH_FALL_OFF above RH_RISE_ON would give a fan that starts and stops on the
# same breath, and a light-off rise above the light-on rise removes the
# hysteresis entirely.
ORDERED = (
    ("RH_RISE_ON", "RH_FALL_OFF", 0.5),            # rise must exceed fall
    ("LIGHT_RISE_ON", "LIGHT_RISE_OFF", 1000),     # on must exceed off
    ("MAX_RUN_S", "MIN_RUN_S", 60),                # max must exceed min
)


def _clamp(lo, v, hi):
    return lo if v < lo else (hi if v > hi else v)


class Settings:
    """Attribute reads fall through to config.py for anything not overridden."""

    def __init__(self, log=None):
        self._over = {}
        self._log = log or (lambda *a: None)
        self._load()

    # -- reading -----------------------------------------------------------

    def __getattr__(self, name):
        # Only reached for attributes not found normally, so _over and _log
        # never come through here.
        over = self.__dict__.get("_over") or {}
        if name in over:
            return over[name]
        return getattr(cfg, name)

    @property
    def mode(self):
        return self._over.get("MODE", "Auto")

    def as_dict(self):
        """Current effective values, for publishing back to HA."""
        out = {}
        for name in SPECS:
            out[name] = self.mode if name == "MODE" else getattr(self, name)
        return out

    # -- writing -----------------------------------------------------------

    def set(self, name, raw):
        """Validate, clamp, store, persist. Returns (ok, value_or_reason)."""
        spec = SPECS.get(name)
        if spec is None:
            return False, "unknown setting"
        kind, lo, hi, step, _u, _l, _i = spec

        if kind == "select":
            val = str(raw).strip()
            match = [m for m in MODES if m.lower() == val.lower()]
            if not match:
                return False, "not one of %s" % (MODES,)
            val = match[0]
        else:
            try:
                val = float(raw)
            except (TypeError, ValueError):
                return False, "not a number"
            if val != val or val in (float("inf"), float("-inf")):
                return False, "not finite"
            clamped = _clamp(lo, val, hi)
            if clamped != val:
                self._log("settings: %s %s clamped to %s" % (name, val, clamped))
            val = clamped
            if isinstance(getattr(cfg, name, 0), int) and step >= 1:
                val = int(round(val))

        self._over[name] = val
        self._enforce_order(name)
        self._save()
        return True, self._over[name]

    def _enforce_order(self, changed):
        """Keep paired settings in the right order after a write.

        Whichever one was just written wins; the other moves out of its way.
        Rejecting the write instead would be worse -- the user would be told
        no with no indication of which other value was blocking them.
        """
        for hi_name, lo_name, gap in ORDERED:
            if changed not in (hi_name, lo_name):
                continue
            hi_v, lo_v = getattr(self, hi_name), getattr(self, lo_name)
            if hi_v - lo_v >= gap:
                continue
            if changed == hi_name:
                new = hi_v - gap
                lo_lo = SPECS[lo_name][1]
                self._over[lo_name] = max(lo_lo, new)
                self._log("settings: %s pushed to %s to stay below %s"
                          % (lo_name, self._over[lo_name], hi_name))
            else:
                new = lo_v + gap
                hi_hi = SPECS[hi_name][2]
                self._over[hi_name] = min(hi_hi, new)
                self._log("settings: %s pushed to %s to stay above %s"
                          % (hi_name, self._over[hi_name], lo_name))

    def reset(self):
        self._over = {}
        self._save()
        self._log("settings: reset to config.py defaults")

    # -- persistence -------------------------------------------------------

    def _load(self):
        try:
            with open(FILE) as f:
                data = json.load(f)
        except (OSError, ValueError):
            return                                  # absent or corrupt: defaults
        if not isinstance(data, dict):
            return
        # Re-validate on load. A file edited by hand, or half-written when the
        # power went, must not put the controller somewhere it could not have
        # reached through set().
        for name, val in data.items():
            if name in SPECS:
                self.set(name, val)
        self._log("settings: loaded %d overrides" % len(self._over))

    def _save(self):
        try:
            tmp = FILE + ".tmp"
            with open(tmp, "w") as f:
                json.dump(self._over, f)
            try:
                import os
                os.remove(FILE)
            except OSError:
                pass
            import os
            os.rename(tmp, FILE)
        except Exception as e:
            self._log("settings: save failed (%s)" % e)
