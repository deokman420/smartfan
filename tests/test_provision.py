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

    # --- the country, which is the field nobody thinks about ---------------
    # Getting it wrong restricts the channel list, so a router on 12 or 13 is
    # invisible and the symptom reads as a wrong password.
    provision._write_secrets("Home WiFi", "pw", "gb")
    ns = {}
    exec(compile(io.open("secrets.py", encoding="utf-8").read(),
                 "secrets.py", "exec"), ns)
    check("country is saved, normalised to upper case",
          ns.get("WIFI_COUNTRY") == "GB", ns.get("WIFI_COUNTRY"))

    # The property that matters is that nonsense never reaches the radio.
    # It falls back to what was carried over rather than to the submitted
    # value -- which here is the template default, because the stubbed
    # secrets module is what _carry_over sees.
    provision._write_secrets("Home WiFi", "pw", "ZZ")
    ns = {}
    exec(compile(io.open("secrets.py", encoding="utf-8").read(),
                 "secrets.py", "exec"), ns)
    check("a country the radio would not accept is never written",
          ns.get("WIFI_COUNTRY") != "ZZ"
          and ns.get("WIFI_COUNTRY") in provision._COUNTRIES,
          ns.get("WIFI_COUNTRY"))

    # --- identity.py, which decides what the device is called --------------
    slug = provision._write_identity("Master Bath")
    written = io.open("identity.py", encoding="utf-8").read()
    ns = {}
    exec(compile(written, "identity.py", "exec"), ns)
    check("identity is importable python", "ROOM" in ns)
    check("the room is slugged for topics and DNS",
          slug == "master-bath" and ns["ROOM"] == "master-bath", slug)
    check("the display name keeps what was typed",
          ns["ROOM_NAME"] == "Master Bath", ns.get("ROOM_NAME"))
    check("the hostname is the room, not a bare 'fan'",
          ns["HOSTNAME"] == "master-bath-fan", ns.get("HOSTNAME"))
    check("no temp identity left behind", not os.path.exists("identity.new"))

    # A name that survives the slug but not much else.
    provision._write_identity("!!!")
    ns = {}
    exec(compile(io.open("identity.py", encoding="utf-8").read(),
                 "identity.py", "exec"), ns)
    check("an unusable room falls back on both halves together",
          ns["ROOM"] == "bathroom" and ns["ROOM_NAME"] == "Bathroom",
          "%s / %s" % (ns["ROOM"], ns["ROOM_NAME"]))

    # The room is typed by a person on a phone. Whatever they type has to
    # come out as a legal DNS label and a legal topic segment, because it
    # becomes both.
    bad = []
    for typed in ("Master Bath", "  ensuite  ", "loft/attic", "WC #2",
                  "a" * 60, "-- --", "Bathroom", "salle de bain"):
        got = provision._slug(typed)
        if (not got or got != got.lower() or got.startswith("-")
                or got.endswith("-") or len(got) > 24
                or any(not (c.isalnum() or c == "-") for c in got)):
            bad.append((typed, got))
    check("every room name slugs to a legal label", not bad, str(bad[:2]))
finally:
    os.chdir(cwd)
    sys.modules.pop("secrets", None)
    sys.modules.pop("identity", None)

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

# --- the fields setup actually asks for -------------------------------------
form = provision._page([])
check("the form asks for the room", "name=room" in form)
check("the room defaults to bathroom", 'value="bathroom"' in form)
check("the form asks for the country", "name=country" in form)
check("the country list is inline, not fetched",
      "<option value=US" in form and "GB" in form)

# The phone's locale is a better default than US-for-everyone, but only
# until the owner answers for themselves. A retry after a wrong password
# must not quietly reset their choice to whatever the handset says.
check("a fresh form asks the phone where it is",
      "data-guess=1" in provision._page([]))
check("a remembered choice is left alone",
      "data-guess" not in provision._page([], "", "bathroom", "GB", False))
check("the remembered country is the selected one",
      "<option value=GB selected>" in
      provision._page([], "", "bathroom", "GB", False))

# A room name is user input that gets rendered back into the form on a retry.
hostile_room = provision._page([], "", '"><script>x</script>', "US")
check("a hostile room name cannot break out of its attribute",
      '"><script>' not in hostile_room and "&quot;&gt;" in hostile_room)

# --- the hand-off, which is the whole reason setup ends anywhere ------------
wait = provision._wait_page("ensuite-fan.local")
check("the waiting page names the address to open",
      "http://ensuite-fan.local/" in wait)
check("the waiting page still explains the disappearing network",
      "shutting down" in wait and "green light" in wait)
check("the waiting page makes no external requests",
      "https://" not in wait)
check("one room, one hostname, one definition",
      provision._hostname_for("Master Bath") == "master-bath-fan")

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

# --- Improv shares the one join path ---------------------------------------
# provision.py grew a second way in. Both routes must land on the same
# _attempt(), or one of them writes credentials the other never validates.
import improv                                                  # noqa: E402

p2 = provision.Portal(log=lambda *a: None)
p2.networks = [("Hollow Bastion", -41, True)]
calls = []


def fake_attempt(ssid, psk):
    calls.append((ssid, psk))
    p2.sta_ip = "192.168.0.42"
    return psk == "right"


p2._attempt = fake_attempt
url = p2._on_improv_wifi("Hollow Bastion", "right")
check("improv joins through the shared _attempt", calls == [
    ("Hollow Bastion", "right")])
# The .local name, not the raw address. A browser handed 192.168.0.42 has
# been given something that stops working at the next DHCP lease, and the
# whole point of the name is that it does not.
check("improv reports the device URL back",
      url == "http://bathroom-fan.local", str(url))
check("a successful improv join ends the portal", p2.done is True)

p2.done = False
check("a failed improv join returns nothing",
      p2._on_improv_wifi("Hollow Bastion", "wrong") is None)
check("and does not end the portal", p2.done is False)

p3 = provision.Portal(log=lambda *a: None)
p3.networks = [("Hollow Bastion", -41, True), ("Guest", -70, False)]
p3._improv = improv.Improv(("Smartfan", "t", "RP2350", "Smartfan"),
                           p3._on_improv_wifi, scan=lambda: p3.networks)
reply = p3._improv.feed(improv.build(
    improv.TYPE_RPC_COMMAND,
    bytes([improv.CMD_GET_WIFI_NETWORKS, 0])))
check("improv serves the same scan the portal shows",
      b"Hollow Bastion" in reply and b"Guest" in reply)

check("improv step is inert before the portal arms it",
      provision.Portal(log=lambda *a: None)._improv_step() is None)

# --- summary ----------------------------------------------------------------
passed = sum(1 for _, ok, _ in results if ok)
print("\n%d/%d passed" % (passed, len(results)))
fails = [n for n, ok, _ in results if not ok]
if fails:
    print("failed: " + ", ".join(fails))
sys.exit(0 if not fails else 1)
