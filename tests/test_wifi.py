"""Changing the WiFi from the app, tested on the host with a fake radio.

    python tests/test_wifi.py

No board required and no board touched. A real association cannot be
exercised here, but the things that decide whether this is *safe* can be, and
they are the whole reason the feature is shaped the way it is:

  * that nothing is written to secrets.py until the radio has actually
    associated -- the property that makes a typo cost a retry rather than a
    ladder, and the one a refactor would silently remove;
  * that a failed attempt leaves the device exactly as it was, with no file
    touched and the old credentials still in force;
  * that the door is shut unless somebody held the button, and shuts again
    on its own.

The fake Link below is deliberately not a mock of the real one's internals.
It implements the four calls wifi.py is allowed to make -- try_wifi,
stop_trying, adopt, drop -- plus wifi_ok, and records what happened. If the
real Link's contract changes, test_httpd.py and the device tests catch it;
what this file asserts is the decision logic on top.
"""

import io
import os
import sys
import tempfile
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print("  %-56s %s %s" % (name, "PASS" if ok else "FAIL", detail))
    return ok


# --- MicroPython stubs ------------------------------------------------------
# ticks_ms and friends are MicroPython's, and they wrap at a word rather than
# counting from an epoch. CPython's time module accepts the attributes, so
# wifi.py runs unmodified here instead of growing a host fallback it would
# only ever use under test.
import time  # noqa: E402

if not hasattr(time, "ticks_ms"):
    time.ticks_ms = lambda: int(time.monotonic() * 1000)
    time.ticks_diff = lambda a, b: a - b
    time.ticks_add = lambda t, d: t + d

# wifi.py imports `network` and falls back to None off-device, which is the
# path a host test would otherwise take. Give it a real-looking one so scan()
# is exercised rather than skipped.
class _WLAN:
    scanned = []

    def __init__(self, *a):
        pass

    def active(self, *a):
        return True

    def scan(self):
        return list(_WLAN.scanned)


network = types.ModuleType("network")
network.STA_IF = 0
network.AP_IF = 1
network.WLAN = _WLAN
sys.modules.setdefault("network", network)


# wifi.py reaches into provision.py for the one thing worth sharing -- the
# atomic secrets write -- rather than keeping a second copy of it, so
# provision's own imports have to resolve here too. Stubbed to the point of
# importing and no further: nothing in this file runs the portal.
class _Pin:
    OUT = 1
    IN = 0
    PULL_UP = 2

    def __init__(self, *a, **k):
        self._v = 0

    def value(self, v=None):
        if v is None:
            return self._v
        self._v = int(bool(v))


machine = types.ModuleType("machine")
machine.unique_id = lambda: b"\xe6\x61\x38\x64\x83\x9b\x2f\x21"
machine.reset = lambda: None
machine.Pin = _Pin
sys.modules.setdefault("machine", machine)

import wifi  # noqa: E402


class FakeLink:
    """The four calls wifi.py may make, and a switch for whether it worked."""

    def __init__(self, ssid="old-net"):
        self._ssid = ssid
        self.wifi_ok = False
        self.trying = None
        self.calls = []
        self.last_error = ""

    @property
    def ssid(self):
        return self.trying or self._ssid

    def try_wifi(self, ssid, psk):
        self.trying = ssid
        self.psk = psk
        self.calls.append("try:%s" % ssid)

    def stop_trying(self):
        self.trying = None
        self.calls.append("rollback")

    def adopt(self):
        self._ssid = self.trying
        self.trying = None
        self.calls.append("adopt")

    def drop(self, why=""):
        self.calls.append("drop")


def fresh(tmp):
    """A provisioner in a scratch directory, so writes land nowhere real.

    sys.path matters as much as the working directory here, and that is not
    obvious. `provision._carry_over()` does `import secrets` to keep the MQTT
    settings through a re-provision; chdir alone leaves the repo's own
    secrets.py first on the path, so the carry-over test was reading this
    machine's real broker credentials and comparing them against fixtures.
    Writes always went to the scratch directory -- but a test that reads real
    secrets is measuring the wrong file, and would pass or fail depending on
    whose laptop it ran on.
    """
    os.chdir(tmp)
    if sys.path[0] != tmp:
        sys.path.insert(0, tmp)
    sys.modules.pop("secrets", None)     # or the first import is cached forever
    link = FakeLink()
    return link, wifi.Provisioner(link, log=lambda *a: None)


tmp = tempfile.mkdtemp()
here = os.getcwd()
print("wifi provisioning regression\n")

# --- the window -------------------------------------------------------------
link, prov = fresh(tmp)
check("closed until somebody presses the button", not prov.is_open)
check("a closed window refuses credentials",
      prov.start("new-net", "pw") == (False, "the window is closed"))
check("and writes nothing while refusing", not os.path.exists("secrets.py"))
check("a scan is refused too", prov.scan() is None)

prov.open("test")
check("the button opens it", prov.is_open)
check("it reports the time it has left", 0 < prov.seconds_left <= wifi.WINDOW_S)

prov._opened_at = wifi._now() - (wifi.WINDOW_S + 1) * 1000
prov.poll()
check("it shuts itself when the time runs out", not prov.is_open)
check("and refuses credentials again once shut",
      prov.start("new-net", "pw")[0] is False)

# --- a good change ----------------------------------------------------------
link, prov = fresh(tmp)
prov.open("test")
ok, _ = prov.start("new-net", "hunter2")
check("an open window accepts credentials", ok)
check("it asks the link to try them", link.calls == ["try:new-net"], str(link.calls))

# THE assertion this whole design exists for.
check("nothing is written while the radio is still trying",
      not os.path.exists("secrets.py"))

link.wifi_ok = True
prov.poll()
check("once associated, secrets.py is written", os.path.exists("secrets.py"))
check("the link is told to keep the new network", "adopt" in link.calls)
check("and the state says ok", prov.state == wifi.OK)

body = io.open("secrets.py", encoding="utf-8").read()
check("the new ssid is in the file", "new-net" in body)
check("the passphrase is in the file", "hunter2" in body)
check("it is valid python", compile(body, "secrets.py", "exec") is not None)

# --- a bad change -----------------------------------------------------------
os.remove("secrets.py")
link, prov = fresh(tmp)
prov.open("test")
prov.start("typo-net", "wrong")
link.wifi_ok = False
prov._started_at = wifi._now() - (wifi.TRY_S + 1) * 1000
prov.poll()
check("an attempt that never associates fails", prov.state == wifi.FAILED)
check("NOTHING is written when it fails", not os.path.exists("secrets.py"))
check("the link is rolled back to the old network", link.calls[-1] == "rollback")
check("and the old network is what it reports", link.ssid == "old-net")
check("the failure is readable afterwards", bool(prov.status()["error"]))

# --- the attempt is not abandoned halfway ------------------------------------
link, prov = fresh(tmp)
prov.open("test")
prov.start("slow-net", "pw")
check("an in-flight attempt refuses a second one",
      prov.start("other-net", "pw")[0] is False)
check("and cannot be closed out from under itself", prov.close() is False)
prov._opened_at = wifi._now() - (wifi.WINDOW_S + 1) * 1000
prov.poll()
check("an expiring window does not kill an attempt in flight",
      prov.state == wifi.TRYING)

# --- input it did not choose -------------------------------------------------
link, prov = fresh(tmp)
prov.open("test")
check("an empty ssid is refused", prov.start("", "pw")[0] is False)
check("an over-long ssid is refused", prov.start("x" * 33, "pw")[0] is False)
check("an over-long passphrase is refused",
      prov.start("net", "y" * 64)[0] is False)
check("still nothing written after three refusals",
      not os.path.exists("secrets.py"))

# A passphrase with quotes and backslashes has to survive the round trip:
# _write_secrets uses repr(), and this is what proves it.
prov.start("net", "a'b\"c\\d")
link.wifi_ok = True
prov.poll()
saved = {}
exec(io.open("secrets.py", encoding="utf-8").read(), saved)
check("a passphrase with quotes and backslashes round-trips",
      saved.get("WIFI_PASS") == "a'b\"c\\d", repr(saved.get("WIFI_PASS")))

# --- MQTT settings survive ---------------------------------------------------
# Values that could not be mistaken for a real config. The first version of
# this used 192.168.0.5 and "pico-fan", which are also what this developer's
# actual secrets.py says -- so while the path leak in fresh() was live, the
# host assertion passed by coincidence and only the password caught it.
os.chdir(tmp)
io.open("secrets.py", "w").write(
    "WIFI_SSID = 'old'\nWIFI_PASS = 'p'\nMQTT_HOST = '10.254.0.1'\n"
    "MQTT_USER = 'fixture-user'\nMQTT_PASS = 'fixture-pass'\n")
link, prov = fresh(tmp)
prov.open("test")
prov.start("third-net", "pw")
link.wifi_ok = True
prov.poll()
saved = {}
exec(io.open("secrets.py", encoding="utf-8").read(), saved)
check("changing the wifi keeps the broker settings",
      saved.get("MQTT_HOST") == "10.254.0.1"
      and saved.get("MQTT_PASS") == "fixture-pass"
      and saved.get("MQTT_USER") == "fixture-user",
      "%s / %s" % (saved.get("MQTT_HOST"), saved.get("MQTT_USER")))
check("and still changes the wifi", saved.get("WIFI_SSID") == "third-net")

# --- the scan ----------------------------------------------------------------
_WLAN.scanned = [
    (b"home", b"", 6, -44, 3, False),
    (b"home", b"", 11, -71, 3, False),      # the same mesh, second radio
    (b"", b"", 1, -50, 3, False),           # hidden
    (b"cafe", b"", 1, -80, 0, False),       # open
]
link, prov = fresh(tmp)
check("a scan needs the window open too", prov.scan() is None)
prov.open("test")
nets = prov.scan()
names = [n["ssid"] for n in nets]
check("a hidden network is not offered", "" not in names)
check("one entry per name, not one per radio", names.count("home") == 1)
check("the strongest radio wins for a repeated name",
      [n for n in nets if n["ssid"] == "home"][0]["rssi"] == -44)
check("strongest first", names == sorted(names, key=lambda s: -dict(
      (n["ssid"], n["rssi"]) for n in nets)[s]))
check("an open network is marked open",
      [n for n in nets if n["ssid"] == "cafe"][0]["secure"] is False)

os.chdir(here)
failed = [n for n, ok, _ in results if not ok]
print("\n%d/%d passed" % (len(results) - len(failed), len(results)))
sys.exit(1 if failed else 0)
