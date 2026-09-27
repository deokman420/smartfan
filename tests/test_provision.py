"""Provisioning logic, tested on the host with MicroPython stubbed.

    python tests/test_provision.py

No board required and no board touched. The radio calls cannot be exercised
here, but everything that decides whether provisioning is *correct* can be:
the derived credentials, the form decoding, the secrets file it writes, and
the escaping of network names it did not choose.

That last one is the reason this file exists. Nearby SSIDs are
attacker-controlled strings that the portal renders, and the portal runs in
a captive sheet on the owner's phone.
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
    print("  %-46s %s %s" % (name, "PASS" if ok else "FAIL", detail))
    return ok


# --- MicroPython stubs ------------------------------------------------------
_uid = b"\xe6\x61\x38\x64\x83\x9b\x2f\x21"

machine = types.ModuleType("machine")
machine.unique_id = lambda: _uid
machine.reset = lambda: None


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


machine.Pin = _Pin
sys.modules["machine"] = machine

network = types.ModuleType("network")
network.STA_IF, network.AP_IF = 0, 1


class _WLAN:
    SEC_WPA_WPA2 = 3

    def __init__(self, *a):
        pass

    def active(self, *a):
        return True

    def scan(self):
        return []

    def config(self, **k):
        pass

    def connect(self, *a):
        pass

    def isconnected(self):
        return False

    def ifconfig(self):
        return ("192.168.4.1",) * 4


network.WLAN = _WLAN
sys.modules["network"] = network

import provision                                              # noqa: E402

print("provisioning logic (host, no board)\n")

# --- derived identity -------------------------------------------------------
ssid, psk = provision.identity()
again = provision.identity()
check("identity is deterministic", (ssid, psk) == again, ssid)
check("SSID is namespaced", ssid.startswith("Smartfan-"), ssid)

# WPA2 refuses anything shorter than 8. Getting this wrong means the AP
# silently comes up OPEN -- the exact hole this was built to close.
check("PSK meets the WPA2 minimum", len(psk) >= 8, "%d chars" % len(psk))
check("PSK is 10 characters", len(psk) == 10, psk)

amb = set("01OILoil")
check("no ambiguous characters to mistype",
      not (set(psk) & amb), "".join(sorted(set(psk) & amb)) or "clean")

# The SSID is broadcast to everyone in range. If the PSK could be derived
# from it, WPA2 would be decoration.
check("SSID suffix does not appear in the PSK",
      ssid.split("-")[1] not in psk, "%s vs %s" % (ssid, psk))

other = b"\x00" * 8
machine.unique_id = lambda: other
s2, p2 = provision.identity()
machine.unique_id = lambda: _uid
check("different chip, different credentials", p2 != psk and s2 != ssid)

# --- form decoding ----------------------------------------------------------
check("decodes percent escapes", provision._unquote("a%40b") == "a@b")
check("decodes plus as space", provision._unquote("my+net") == "my net")
check("decodes a trailing escape", provision._unquote("x%21") == "x!")
check("survives a malformed escape", provision._unquote("100%") == "100%")
check("passes a plain password through",
      provision._unquote("p%3Ass%2Fw%26rd") == "p:ss/w&rd")

# --- the secrets file it writes --------------------------------------------
# chdir first: _write_secrets writes secrets.py into the CWD, and the repo
# has a real one.
tmp = tempfile.mkdtemp()
cwd = os.getcwd()
os.chdir(tmp)
try:
    sys.modules.pop("secrets", None)
    fake = types.ModuleType("secrets")
    fake.WIFI_SSID, fake.WIFI_PASS = "old", "old"
    fake.MQTT_HOST, fake.MQTT_USER = "192.168.0.5", "pico-fan-bathroom"
    fake.MQTT_PASS, fake.MQTT_CLIENT_ID = "brokerpw", "pico-fan-bathroom"
    sys.modules["secrets"] = fake

    provision._write_secrets("Home WiFi", "p'ass\"w\\rd")
    written = io.open("secrets.py", encoding="utf-8").read()

    ns = {}
    exec(compile(written, "secrets.py", "exec"), ns)
    check("writes valid importable python", "WIFI_SSID" in ns)
    check("SSID round-trips", ns.get("WIFI_SSID") == "Home WiFi")
    check("quotes and backslashes round-trip",
          ns.get("WIFI_PASS") == "p'ass\"w\\rd", repr(ns.get("WIFI_PASS")))
    # Re-provisioning WiFi must not cost the broker account.
    check("carries MQTT settings over",
          ns.get("MQTT_USER") == "pico-fan-bathroom"
          and ns.get("MQTT_PASS") == "brokerpw")
    check("no temp file left behind", not os.path.exists("secrets.new"))
finally:
    os.chdir(cwd)
    sys.modules.pop("secrets", None)

# --- rendering names chosen by somebody else --------------------------------
hostile = [
    ("<script>alert(1)</script>", -40, True),
    ("'><script>x</script>", -50, True),
    ('" onmouseover="x', -55, True),
    ("back\\slash", -60, False),
    ("tom & jerry", -65, True),
]
page = provision._page(hostile)

# Assert on the SSID's own rendering, not on the substring "<script>":
# the page legitimately contains a <script> tag of its own, so the naive
# check fails while the escaping is perfectly correct. The same false
# failure bit the UI suite over the word BOOST -- see tests/README.md.
check("hostile SSID is escaped, not executed",
      "&lt;script&gt;alert(1)&lt;/script&gt;" in page
      and "<script>alert(1)" not in page)
check("the page's own script block is the only one",
      page.count("<script>") == 1)
check("no raw double quote escapes an attribute", '" onmouseover="' not in page)
check("ampersand is encoded", "tom &amp; jerry" in page)

# The backslash case is why the inline onclick was removed: inside a quoted
# JS string argument, a trailing backslash escapes the closing quote.
check("no SSID is interpolated into script context",
      "back\\slash" not in page.split("<script>")[-1])
check("names are carried as markup text, not attributes",
      "<b>back\\slash</b>" in page)
check("error text is escaped too",
      "&lt;b&gt;" in provision._page([], "<b>boom</b>"))

check("empty scan still renders a usable form",
      "No networks found" in provision._page([]) and "name=ssid" in
      provision._page([]))

size = len(page.encode("utf-8"))
check("page fits the 20 KB budget", size < 20480, "%d bytes" % size)
check("page makes no external requests",
      "http://" not in page and "https://" not in page)

# --- DNS responder ----------------------------------------------------------
query = (b"\x12\x34\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00"
         b"\x07captive\x05apple\x03com\x00\x00\x01\x00\x01")


class _Sock:
    def __init__(self, data):
        self.data, self.sent = data, None

    def recvfrom(self, n):
        if self.data is None:
            raise OSError
        d, self.data = self.data, None
        return d, ("192.168.4.2", 5353)

    def sendto(self, payload, addr):
        self.sent = payload


p = provision.Portal(log=lambda *a: None)
sock = _Sock(query)
p._dns_step(sock)
r = sock.sent or b""
check("answers the captive probe", bool(r))
check("keeps the query id", r[:2] == b"\x12\x34")
check("sets the response flag", r[2:4] == b"\x81\x80")
check("answer count matches question count", r[6:8] == query[4:6])
check("points the name at the portal", r.endswith(bytes([192, 168, 4, 1])))
check("advertises an A record", b"\xc0\x0c\x00\x01\x00\x01" in r)

sock = _Sock(b"\x00\x01")                       # runt
p._dns_step(sock)
check("ignores a truncated query", sock.sent is None)

# --- summary ----------------------------------------------------------------
passed = sum(1 for _, ok, _ in results if ok)
print("\n%d/%d passed" % (passed, len(results)))
fails = [n for n, ok, _ in results if not ok]
if fails:
    print("failed: " + ", ".join(fails))
sys.exit(0 if not fails else 1)
