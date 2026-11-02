"""The Home Assistant integration against the firmware it talks to.

    python tests/test_integration.py

Needs no Home Assistant and no device. It imports the integration's tables as
plain data -- the platform modules import `homeassistant`, so the descriptions
are read out of the source rather than executed -- and checks them against
telemetry.py and settings.py.

WHY THIS EXISTS. The integration restates the device's entity tables, because
Home Assistant needs static keys: translations are looked up in strings.json
by a key that has to exist before the device is reachable, so the entity list
cannot be generated from whatever /api/state happens to return. That makes it
a second copy of a list, and this project has been bitten by a second copy of
a list more than once -- the app's FIELD_COUNT sat at 12 while the device sent
15 and a whole week of history was silently discarded, and a chart count
written down went stale inside an afternoon. Three new fields went in today.

So the copy is checked rather than trusted: every sensor the firmware
announces must have an entity, every setting must have a control, and every
entity must have a name in strings.json. A field added to the firmware fails
this suite until the integration catches up, which is the whole point.
"""

import ast
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
COMPONENT = os.path.join(ROOT, "custom_components", "smartfan")

sys.path.insert(0, ROOT)
import settings                                                  # noqa: E402
import telemetry                                                 # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print("  %-56s %s %s" % (name, "PASS" if ok else "FAIL", detail))
    return ok


def descriptions(module, classname):
    """Every description literal in a platform module, as dicts of kwargs.

    Parsed rather than imported: these modules import homeassistant, which is
    not a dependency of this repo and should not become one just to assert
    that a table is complete. Only literal keywords are read -- every field
    this checks is a plain string or number in the source.
    """
    path = os.path.join(COMPONENT, module)
    tree = ast.parse(io.open(path, encoding="utf-8").read())
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = getattr(func, "id", None) or getattr(func, "attr", None)
        if name != classname:
            continue
        kwargs = {}
        for kw in node.keywords:
            if kw.arg is None:
                continue
            try:
                kwargs[kw.arg] = ast.literal_eval(kw.value)
            except ValueError:
                kwargs[kw.arg] = "<expr>"
        out.append(kwargs)
    return out


print("the Home Assistant integration against the firmware\n")

manifest = json.load(io.open(os.path.join(COMPONENT, "manifest.json"),
                             encoding="utf-8"))
strings = json.load(io.open(os.path.join(COMPONENT, "strings.json"),
                            encoding="utf-8"))

# --- the manifest ---------------------------------------------------------
# A custom integration without a version does not load at all, and Home
# Assistant's error for it points at the integration rather than at the
# missing field.
check("manifest declares a version", bool(manifest.get("version")),
      manifest.get("version", ""))
check("manifest declares a config flow", manifest.get("config_flow") is True)
check("config_flow.py exists, as the manifest promises",
      os.path.isfile(os.path.join(COMPONENT, "config_flow.py")))
check("iot_class says local", "local" in manifest.get("iot_class", ""),
      manifest.get("iot_class", ""))
# Nothing from PyPI yet: api.py is bundled. The moment that changes, the
# dependency has to be declared here or it will not be installed.
check("no undeclared third-party imports", manifest.get("requirements") == [],
      "%s" % manifest.get("requirements"))

# --- every sensor the firmware announces reaches an entity ---------------
sensors = descriptions("sensor.py", "SmartfanSensorDescription")
binaries = descriptions("binary_sensor.py", "SmartfanBinaryDescription")
buttons = descriptions("button.py", "SmartfanButtonDescription")
numbers = descriptions("number.py", "SmartfanNumberDescription")

sensor_fields = {d["field"] for d in sensors}
binary_fields = {d["field"] for d in binaries}

fw_sensor_fields = {spec[2] for spec in telemetry._SENSORS}
fw_binary_fields = {spec[2] for spec in telemetry._BINARY}


def snapshot_keys():
    """Every key /api/state can carry, read out of telemetry.snapshot().

    Not the same set as _SENSORS, and the difference is the point. _SENSORS
    is what the firmware ANNOUNCES to Home Assistant over MQTT discovery;
    the snapshot is what it PUBLISHES. Two fields are in the second and not
    the first -- `app`, the app bundle's build, and `why`, the controller's
    own words for the state it is in -- so they reach /api/state and the
    device's own page but have never had an entity. The integration gives
    them one, which is a small thing it offers that the MQTT path does not,
    and it would fail a check written against _SENSORS alone.
    """
    src = io.open(os.path.join(ROOT, "telemetry.py"), encoding="utf-8").read()
    best = set()
    for node in ast.walk(ast.parse(src)):
        if not isinstance(node, ast.Dict):
            continue
        keys = {k.value for k in node.keys
                if isinstance(k, ast.Constant) and isinstance(k.value, str)}
        # The snapshot is far and away the biggest string-keyed dict literal
        # in the file; picking it by size beats pinning a function name that
        # may move.
        if len(keys) > len(best):
            best = keys
    return best


payload_fields = snapshot_keys()
check("the snapshot's keys were found at all", len(payload_fields) > 20,
      "%d keys" % len(payload_fields))

missing = sorted(fw_sensor_fields - sensor_fields)
check("every announced sensor field has an entity", not missing,
      "missing %s" % missing if missing else "%d fields" % len(sensor_fields))

missing = sorted(fw_binary_fields - binary_fields)
check("every announced binary sensor field has an entity", not missing,
      "missing %s" % missing if missing else "%d fields" % len(binary_fields))

# The reverse direction matters too: an entity reading a field the device
# never puts in the payload is an entity that reads None for ever, and that
# looks like a broken sensor rather than a stale table. Checked against what
# /api/state actually carries, not against the MQTT announcement list -- see
# snapshot_keys() for why those two sets are not the same.
extra = sorted(sensor_fields - payload_fields)
check("no sensor entity reads a field the device does not publish",
      not extra, "extra %s" % extra if extra else "")
extra = sorted(binary_fields - payload_fields)
check("no binary entity reads a field the device does not publish",
      not extra, "extra %s" % extra if extra else "")

# --- settings ------------------------------------------------------------
number_settings = {d["setting"] for d in numbers}
fw_numbers = {n for n, spec in settings.SPECS.items() if spec[0] == "number"}
fw_selects = {n for n, spec in settings.SPECS.items() if spec[0] == "select"}

missing = sorted(fw_numbers - number_settings)
check("every number setting has a control", not missing,
      "missing %s" % missing if missing else "%d settings" % len(number_settings))
extra = sorted(number_settings - fw_numbers)
check("no control writes a setting the firmware does not have", not extra,
      "extra %s" % extra if extra else "")

# Bounds are restated in the integration because Home Assistant needs them to
# draw a box. Restated wrongly, the box refuses a value the device would have
# accepted -- or offers one it will silently clamp, which is worse because
# the user sees their number land and then change.
wrong = []
for d in numbers:
    spec = settings.SPECS[d["setting"]]
    _kind, lo, hi, step = spec[0], spec[1], spec[2], spec[3]
    if (d.get("native_min_value") != lo or d.get("native_max_value") != hi
            or d.get("native_step") != step):
        wrong.append("%s HA(%s,%s,%s) fw(%s,%s,%s)" % (
            d["setting"], d.get("native_min_value"), d.get("native_max_value"),
            d.get("native_step"), lo, hi, step))
check("every control's bounds match the firmware's", not wrong,
      "; ".join(wrong) if wrong else "%d checked" % len(numbers))

# --- the select ----------------------------------------------------------
sys.path.insert(0, COMPONENT)
select_src = io.open(os.path.join(COMPONENT, "select.py"),
                     encoding="utf-8").read()
tree = ast.parse(select_src)
ha_modes = None
for node in ast.walk(tree):
    if (isinstance(node, ast.Assign) and node.targets
            and getattr(node.targets[0], "id", None) == "MODES"):
        ha_modes = tuple(ast.literal_eval(node.value))
check("the mode options match the firmware's, in order",
      ha_modes == settings.MODES, "%s vs %s" % (ha_modes, settings.MODES))

# --- names ---------------------------------------------------------------
# Under has_entity_name an entity with no string entry gets no name at all,
# which shows up as a bare device name repeated down the whole device page.
by_platform = {
    "sensor": [d["key"] for d in sensors],
    "binary_sensor": [d["key"] for d in binaries],
    "button": [d["key"] for d in buttons],
    "number": [d["key"] for d in numbers],
}
unnamed = []
for platform, keys in by_platform.items():
    have = strings.get("entity", {}).get(platform, {})
    unnamed += ["%s.%s" % (platform, k) for k in keys
                if k not in have or not have[k].get("name")]
check("every entity has a name in strings.json", not unnamed,
      "unnamed %s" % unnamed if unnamed else
      "%d entities" % sum(len(v) for v in by_platform.values()))

# And nothing the other way: a string for an entity that no longer exists is
# dead weight that reads as an entity someone forgot to register.
orphans = []
for platform, keys in by_platform.items():
    for key in strings.get("entity", {}).get(platform, {}):
        if key not in keys:
            orphans.append("%s.%s" % (platform, key))
check("no strings for entities that do not exist", not orphans,
      "orphans %s" % orphans if orphans else "")

# strings.json and the English translation are the same file; Home Assistant
# reads the first and ships the second, and a copy that drifts means the UI
# shows raw keys for whatever was only added to one of them.
en = json.load(io.open(os.path.join(COMPONENT, "translations", "en.json"),
                       encoding="utf-8"))
check("translations/en.json matches strings.json", en == strings)

# --- the API contract ----------------------------------------------------
# The client refuses a device whose /api/info reports a higher `api` than it
# knows. That is only a safety net if the two numbers start out equal.
api_src = io.open(os.path.join(COMPONENT, "api.py"), encoding="utf-8").read()
supported = None
for node in ast.walk(ast.parse(api_src)):
    if (isinstance(node, ast.Assign) and node.targets
            and getattr(node.targets[0], "id", None) == "SUPPORTED_API"):
        supported = ast.literal_eval(node.value)
webapi_src = io.open(os.path.join(ROOT, "webapi.py"), encoding="utf-8").read()
import re                                                        # noqa: E402
m = re.search(r'"api":\s*(\d+)', webapi_src)
device_api = int(m.group(1)) if m else None
check("the client's supported API matches what the device serves",
      supported == device_api, "client %s, device %s" % (supported, device_api))

# --- summary -------------------------------------------------------------
passed = sum(1 for _, ok, _ in results if ok)
print("\n%d/%d passed" % (passed, len(results)))
fails = [n for n, ok, _ in results if not ok]
if fails:
    print("failed: " + ", ".join(fails))
sys.exit(0 if not fails else 1)
