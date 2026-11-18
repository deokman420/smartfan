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

# The shortest spacing between two rewrites of settings.json. A slider
# dragged in Home Assistant, or a client hammering /api/set, now costs at
# most one flash write per gap instead of one per message.
SAVE_GAP_MS = 10000


def _ms():
    import time
    t = getattr(time, "ticks_ms", None)
    return t() if t else int(time.time() * 1000)


def _since(start):
    import time
    d = getattr(time, "ticks_diff", None)
    return d(_ms(), start) if d else _ms() - start

# name -> (kind, lo, hi, step, unit, label, icon)
# Ranges are what the hardware and the logic can actually honour, not what
# seems reasonable -- they are the last line of defense.
SPECS = {
    "RH_RISE_ON":   ("number", 3.0, 20.0, 0.5, "%",   "Humidity rise to start", "mdi:water-plus"),
    "RH_FALL_OFF":  ("number", 1.0, 10.0, 0.5, "%",   "Humidity fall to stop",  "mdi:water-minus"),
    # Ceiling 1000, not 300. 300 was a guess made before any event had been
    # measured, and the first real one peaked at +3972 ticks against a noise
    # floor of about 30 and room wander of about 100 -- so the useful range
    # is far wider than the guess allowed. Worse, the threshold was armed AT
    # 300, which left Home Assistant able to make the fan more sensitive and
    # not less: the only answer to a 3 a.m. false start would have been to
    # disable the path. A cap you are sitting on is not a cap.
    "VOC_RISE_ON":  ("number", 0, 1000, 5, None,      "Odor rise to start",    "mdi:scent"),
    "VOC_FALL_OFF": ("number", 0, 1000, 5, None,      "Odor fall to stop",     "mdi:scent-off"),
    # Ceiling 3000: cooking peaked at a 1498-tick NOx rise and a closer or
    # larger source has room above that. 0 turns the veto off.
    "VOC_NOX_VETO": ("number", 0, 3000, 25, None,     "Ignore odor if combustion", "mdi:fire-off"),
    "LIGHT_STEP_ON":    ("number", 2000, 40000, 500, None, "Light switch step",  "mdi:brightness-6"),
    "LIGHT_RISE_ON":    ("number", 1000, 40000, 500, None, "Light rise to start", "mdi:brightness-5"),
    "LIGHT_RISE_OFF":   ("number", 500, 39000, 500, None, "Light rise to stop",  "mdi:brightness-4"),
    "LIGHT_ONLY_MAX_S": ("number", 60, 3600, 60, "s",      "Light-only run",     "mdi:timer-sand-complete"),
    "MIN_RUN_S":    ("number", 30, 900, 10, "s",      "Minimum run",            "mdi:timer-play"),
    "MAX_RUN_S":    ("number", 300, 7200, 60, "s",    "Maximum run",            "mdi:timer-alert"),
    # 0 disables. The floor is 300 rather than 60 because anything near the
    # 60 s backoff ceiling would reboot the device over an ordinary retry.
    "NET_STALL_S":  ("number", 0, 3600, 60, "s",     "Reboot if offline for",  "mdi:wifi-alert"),
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
    ("VOC_RISE_ON", "VOC_FALL_OFF", 5),            # same, in ticks

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
        # Flash wear (audit NV-6). Every accepted write used to rewrite
        # settings.json, unchanged values included, with no limit -- and
        # /api/set is open to the whole LAN. _saved is what is on flash, so
        # a write that changes nothing writes nothing; _last_ms spaces real
        # writes SAVE_GAP_MS apart, with flush() catching up from the loop.
        self._saved = None
        self._dirty = False
        self._last_ms = None
        self._loading = True
        self._load()
        self._loading = False
        if self._saved is None:
            self._saved = {}        # no file: defaults, nothing to write
        if self._saved != self._over:
            self._save()            # once, if load clamped or dropped a key

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
            # A zero upper bound means the feature is switched off -- that is
            # what VOC_RISE_ON of 0 is for, and what the odor path ships at.
            # There is no ordering to keep between two disabled thresholds,
            # and enforcing the gap here would push the upper bound up to it:
            # writing VOC_FALL_OFF would silently ARM odor triggering at 5
            # ticks, against a signal whose quiet-room spread is 619. Caught
            # by tests/test_control.py, which is the only reason it is not
            # still in here.
            if hi_v == 0:
                continue
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
        self._saved = dict(data)
        # Re-validate on load. A file edited by hand, or half-written when the
        # power went, must not put the controller somewhere it could not have
        # reached through set().
        for name, val in data.items():
            if name in SPECS:
                self.set(name, val)
        self._log("settings: loaded %d overrides" % len(self._over))

    def _save(self):
        """Persist, unless nothing changed or the last write was too recent.

        A deferred write is not lost: the value is already live in _over,
        and flush() writes it from the control loop once the gap has passed.
        What a power cut inside the gap costs is that one change.
        """
        if self._loading:
            return
        if self._saved is not None and self._over == self._saved:
            self._dirty = False
            return
        if self._last_ms is not None and _since(self._last_ms) < SAVE_GAP_MS:
            self._dirty = True
            return
        self._write()

    def flush(self, force=False):
        """Write a deferred change once the gap allows. Call every pass."""
        if not self._dirty:
            return
        if force or self._last_ms is None or _since(self._last_ms) >= SAVE_GAP_MS:
            self._write()

    def _write(self):
        self._last_ms = _ms()
        self._dirty = False
        self._saved = dict(self._over)
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
