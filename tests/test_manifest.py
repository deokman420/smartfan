"""The shipping lists agree with each other and with the repo.

    python tests/test_manifest.py

Host only. No board, no network.

Why this exists
---------------
Two lists decide what reaches a device in a ceiling:

  telemetry.TRACKED   the files hashed into the `build` fingerprint
  ota_publish.SHIP    the files an OTA actually sends

A module missing from SHIP is never delivered. A module missing from
TRACKED is worse: the fingerprint does not change when that file changes,
so `ota_publish` compares local against device, prints **MATCH**, and the
device goes on running the old code. Nothing is broken, nothing is
reported, and the bug is invisible from the outside.

Both lists have silently drifted here already. `provision.py` was added to
SHIP and not to TRACKED, because the edit that was supposed to add it did
not match and `str.replace` does not raise when it changes nothing. This
file is the check that would have caught it the same minute.
"""

import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print("  %-46s %s %s" % (name, "PASS" if ok else "FAIL", detail))
    return ok


def names_in(path, symbol):
    """Read a tuple-of-strings constant without importing the module.

    telemetry.py imports MicroPython modules, so it cannot be imported on
    a host. Parsing the literal is the whole point: the value must be a
    plain literal that tooling can read, which is a property worth keeping.
    """
    src = io.open(os.path.join(ROOT, path), encoding="utf-8").read()
    m = re.search(symbol + r"\s*=\s*\(([^)]*)\)", src, re.S)
    return set(re.findall(r'"([^"]+)"', m.group(1))) if m else set()


# Deliberately never sent over the air, each for a stated reason.
EXCLUDED = {
    "boot.py": "recovery path -- a path you can break remotely is not one",
    "ota.py": "recovery path -- same",
    "secrets.py": "device-specific credentials",
    "secrets.example.py": "template, not code that runs",
}

print("shipping manifest (host, no board)\n")

tracked = names_in("telemetry.py", "TRACKED")
ship = names_in("tools/ota_publish.py", "SHIP")

check("TRACKED parses", bool(tracked), "%d files" % len(tracked))
check("SHIP parses", bool(ship), "%d files" % len(ship))

# The fingerprint answers "what is this device running". If it covers less
# than what gets shipped, it answers it wrongly.
check("every shipped file is fingerprinted", ship <= tracked,
      "missing from TRACKED: %s" % sorted(ship - tracked)
      if ship - tracked else "")
check("every fingerprinted file is shipped", tracked <= ship,
      "missing from SHIP: %s" % sorted(tracked - ship)
      if tracked - ship else "")

on_disk = {f for f in os.listdir(ROOT)
           if f.endswith(".py") and not f.startswith("_")}
expected = on_disk - set(EXCLUDED)

check("no shipped file is missing from the repo", ship <= on_disk,
      "absent: %s" % sorted(ship - on_disk) if ship - on_disk else "")

# The one that actually bites: a new module lands in the repo, gets
# imported by main.py, and nobody adds it to either list.
missing = expected - ship
check("every module in the repo is accounted for", not missing,
      "unlisted: %s" % sorted(missing) if missing else "")

for name, why in sorted(EXCLUDED.items()):
    if os.path.exists(os.path.join(ROOT, name)):
        check("excluded on purpose: %s" % name, name not in ship, why)

# ota.py refuses to overwrite the recovery path at runtime. That is the
# belt to this file's braces, and it is worth asserting both exist.
prot = names_in("ota.py", "PROTECTED")
check("ota.py protects boot.py at runtime", "boot.py" in prot, str(sorted(prot)))
check("ota.py protects secrets.py at runtime", "secrets.py" in prot)

passed = sum(1 for _, ok, _ in results if ok)
print("\n%d/%d passed" % (passed, len(results)))
fails = [n for n, ok, _ in results if not ok]
if fails:
    print("failed: " + ", ".join(fails))
sys.exit(0 if not fails else 1)
