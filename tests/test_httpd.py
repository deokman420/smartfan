"""The on-device server, its API and its stored history, on the host.

    python tests/test_httpd.py

No board required and no board touched. Everything here is portable Python --
httpd.py, webapi.py and store.py were written that way on purpose, because
the alternative is testing a web server by flashing a fan controller and
refreshing a phone.

What this can prove, and what the hardware suite cannot prove cheaply:

* the loop never blocks. Every request below is served by a server that is
  only ever given short, bounded step() calls from a single thread, exactly
  as the control loop gives them. A handler that blocked would hang here.
* a browser cannot reach outside the bundle, boost the fan by being on the
  wrong website, or push firmware through a door that has no credentials.
* the stored history is the shape the app expects, including after the
  failures that actually happen to a flash filesystem: a truncated last line
  and a rotation.
* bulk history streams rather than being assembled. That one is not a style
  preference: building a week of rows in memory costs about 890 bytes each on
  the device and raised MemoryError at 512 rows, so a test that lets the JSON
  version back in is a test that lets the charts stop working on hardware
  while passing on a laptop with gigabytes.

The one thing it cannot prove is that the radio behaves, which is what
test_device.py is for.
"""

import http.client
import io
import json
import os
import socket
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import httpd                                    # noqa: E402
import store                                    # noqa: E402
import webapi                                   # noqa: E402
from telemetry import Snapshot                   # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print("  %-52s %s %s" % (name, "PASS" if ok else "FAIL", detail))
    return ok


# --- a controller that is only as real as it needs to be --------------------

class _Pin:
    def __init__(self, v=0):
        self._v = v

    def value(self, v=None):
        if v is None:
            return self._v
        self._v = int(bool(v))


class _Settings:
    """Stands in for settings.Settings, with its clamping behaviour.

    The point of the API tests is that a rejected write is reported as a
    rejection, so this has to be able to reject -- a stub that accepts
    everything would pass a test that proves nothing.
    """

    RANGES = {"BOOST_S": (60, 3600), "RH_RISE_ON": (2.0, 30.0)}

    def __init__(self):
        self._vals = {"BOOST_S": 900, "RH_RISE_ON": 8.0, "RH_FALL_OFF": 3.0}

    def set(self, name, value):
        if name not in self.RANGES:
            return False, "unknown parameter"
        lo, hi = self.RANGES[name]
        try:
            v = float(value)
        except (TypeError, ValueError):
            return False, "not a number"
        if not lo <= v <= hi:
            return False, "outside %s..%s" % (lo, hi)
        if float(v).is_integer():
            v = int(v)
        self._vals[name] = v
        return True, v

    def as_dict(self):
        return dict(self._vals)

    def __getattr__(self, name):
        try:
            return self.__dict__["_vals"][name]
        except KeyError:
            raise AttributeError(name)


class FakeProv:
    """Just enough of wifi.Provisioner for the HTTP contract.

    The real one is exercised by tests/test_wifi.py, which can drive a fake
    radio and watch what does and does not reach the filesystem. What matters
    HERE is only the shape of the door: which status code a closed window
    gives, and that a POST to it is refused before any of that logic runs.
    """

    def __init__(self, is_open=False):
        self.is_open = is_open
        self.started = []
        self.state = "open" if is_open else "closed"
        self.scanned = 0

    def status(self):
        return {"state": self.state, "open": self.is_open,
                "secs_left": 120 if self.is_open else 0, "window_s": 180,
                "ssid": "old-net", "trying": "", "error": ""}

    def start(self, ssid, psk, country=None):
        self.started.append((ssid, psk, country))
        return True, "trying"

    def scan(self):
        self.scanned += 1
        return [{"ssid": "home", "rssi": -44, "secure": True}] if self.is_open else None


class FakeController:
    def __init__(self):
        self.state = "IDLE"
        self.temp = 21.4
        self.rh = 63.0
        self.baseline = 60.0
        self.light_on = False
        self.fan = _Pin(0)
        self.rpm = 0
        self.rpm_idle = 0
        self.fault_reason = ""
        self.boost_until = None
        self.st = _Settings()
        self.boot_reason = "power-on"
        self.raw_light = 5000
        self.link = None
        self.ip = "192.168.0.42"
        self.mqtt_host = ""
        self.ota_state = "idle"
        self._push = False
        self.commands = []

    # what Snapshot.payload reads
    def dew_point(self):
        return 14.2

    @property
    def excess_rh(self):
        return self.rh - self.baseline

    def rssi(self):
        return -55

    # the one command path, as main.py exposes it
    def on_command(self, topic, payload):
        self.commands.append((topic, payload.decode()))
        cmd = payload.decode().strip().lower()
        if cmd == "boost":
            self.state = "BOOST"
            self.fan.value(1)
        elif cmd == "cancel":
            self.state = "IDLE"
            self.fan.value(0)


class Cfg:
    ROOM = "bathroom"
    ROOM_NAME = "Bathroom"
    DEVICE_ID = "fan_bathroom"
    DEVICE_NAME = "Bathroom Smartfan"
    LIGHT_ON = 22000
    SAMPLE_S = 2
    PUBLISH_INTERVAL_S = 30
    HOSTNAME = "fan"
    HISTORY_INTERVAL_S = 300
    HISTORY_DAYS = 7
    HOLD_PRESS_S = 8.0


# --- the pump ---------------------------------------------------------------

class Pump:
    """Steps the server the way the control loop does: briefly, forever.

    Deliberately not "serve_forever in a thread". The property under test is
    that a real client is served by nothing but short step() calls, so the
    test harness must not be allowed to give it a longer turn than the fan
    controller would.
    """

    def __init__(self, server, budget_ms=8):
        self.server = server
        self.budget = budget_ms
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self.passes = 0
        self.worst_ms = 0.0

    def _run(self):
        while not self._stop.is_set():
            t0 = time.monotonic()
            self.server.step(self.budget)
            dt = (time.monotonic() - t0) * 1000
            self.worst_ms = max(self.worst_ms, dt)
            self.passes += 1
            time.sleep(0.002)

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._thread.join(timeout=2)


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def get(port, path, method="GET", body=None, headers=None):
    """One request, one connection. Returns (status, headers, bytes)."""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        conn.request(method, path, body=body, headers=headers or {})
        r = conn.getresponse()
        return r.status, dict(r.getheaders()), r.read()
    finally:
        conn.close()


def wait_for(pred, timeout=3.0):
    """Wait for a server-side condition. Returns whether it arrived.

    The alternative is a sleep long enough to be safe, which is a test that
    is slow when it passes and flaky when it fails.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.01)
    return pred()


def gunzip(raw):
    import gzip
    return gzip.decompress(raw)


# --- the bundle on "flash" --------------------------------------------------

INDEX = b"<!doctype html><title>Smartfan</title><body>app</body>"
ASSET = b"export const x = 1;\n" * 40


def make_www(root):
    import gzip
    os.makedirs(os.path.join(root, "_app", "immutable"), exist_ok=True)
    io.open(os.path.join(root, "index.html.gz"), "wb").write(
        gzip.compress(INDEX, 9, mtime=0))
    io.open(os.path.join(root, "_app", "immutable", "app.js.gz"), "wb").write(
        gzip.compress(ASSET, 9, mtime=0))
    io.open(os.path.join(root, "build.json"), "w").write(
        '{"build": "abc12345", "files": 2}\n')


# --- tests -----------------------------------------------------------------

def test_server(tmp):
    root = os.path.join(tmp, "www")
    make_www(root)

    c = FakeController()
    snap = Snapshot(Cfg, log=lambda *a: None)
    hist = store.History(Cfg, log=lambda *a: None, where=tmp)
    api = webapi.Api(c, Cfg, snap, hist)

    port = free_port()
    server = httpd.Server(handler=api.handle, root=root, port=port,
                          log=lambda *a: None)
    check("server binds", server.start())
    pump = Pump(server)
    pump.start()
    try:
        # -- static -------------------------------------------------------
        st, hdr, body = get(port, "/")
        check("GET / serves the shell", st == 200 and gunzip(body) == INDEX,
              "status %d" % st)
        check("gzip is declared, not guessed",
              hdr.get("Content-Encoding") == "gzip")
        check("Content-Length matches the bytes sent",
              int(hdr.get("Content-Length", 0)) == len(body))

        st, hdr, body = get(port, "/_app/immutable/app.js")
        check("immutable assets are served",
              st == 200 and gunzip(body) == ASSET)
        check("immutable assets are cached forever",
              "immutable" in hdr.get("Cache-Control", ""))
        st, hdr, _ = get(port, "/")
        check("the shell is not cached forever",
              hdr.get("Cache-Control") == "no-cache")

        st, _, body = get(port, "/charts")
        check("a client-side route falls back to the shell",
              st == 200 and gunzip(body) == INDEX, "status %d" % st)
        st, _, _ = get(port, "/_app/immutable/nope.js")
        check("a missing asset is a 404, not the shell", st == 404)

        # A traversal must not resolve, whichever way it is spelled. The
        # unencoded form is what a curious person types; the encoded one is
        # what a scanner sends.
        st1, _, _ = get(port, "/../secrets.py")
        st2, _, _ = get(port, "/..%2fsecrets.py")
        check("path traversal is refused", st1 in (400, 403, 404) and
              st2 in (400, 403, 404), "%d / %d" % (st1, st2))

        # -- history, streamed --------------------------------------------
        # A fixed clock, set BEFORE the first sample. Setting it afterwards
        # stamps that one with the real time, which is years later than the
        # rest and makes the rows arrive out of order -- a mistake this test
        # made, and caught, on its first run.
        clock = [1_790_000_000]
        store._epoch = lambda: clock[0]
        for i in range(41):
            hist.note({"rh": 60.0 + i, "base": 60.0, "exc": float(i),
                       "temp": 21.0, "dp": 14.0, "light_pc": 5.0, "rpm": 0,
                       "fan_on": i % 2 == 0, "light_on": False,
                       "state": "IDLE"})
            clock[0] += Cfg.HISTORY_INTERVAL_S

        st, hdr, body = get(port, "/api/history")
        text = body.decode()
        lines = [ln for ln in text.split("\n") if ln]
        check("GET /api/history answers CSV, not JSON",
              st == 200 and hdr.get("Content-Type", "").startswith("text/csv"),
              hdr.get("Content-Type", "none"))
        check("the interval travels with the data",
              lines and lines[0].startswith("#interval="), lines[0][:40]
              if lines else "empty")
        check("every row has one field per documented field",
              all(len(ln.split(",")) == len(store.FIELDS)
                  for ln in lines[1:]),
              "%d rows" % (len(lines) - 1))
        check("rows are oldest first",
              [int(ln.split(",")[0]) for ln in lines[1:]] ==
              sorted(int(ln.split(",")[0]) for ln in lines[1:]))

        # The response has no Content-Length -- its length is not known until
        # it has all been read off flash -- so the close is the terminator.
        check("a streamed response is delimited by the close",
              "Content-Length" not in hdr and
              hdr.get("Connection", "").lower() == "close",
              "len=%s conn=%s" % (hdr.get("Content-Length"),
                                  hdr.get("Connection")))

        cut = int(lines[-1].split(",")[0])
        st, _, body = get(port, "/api/history?since=%d" % cut)
        got = [ln for ln in body.decode().split("\n") if ln and ln[0] != "#"]
        check("since= skips what the caller already has",
              len(got) == 1 and int(got[0].split(",")[0]) == cut,
              "%d rows" % len(got))

        # The whole point of the rewrite: no version of this request may build
        # the rows in memory. read() is now bounded so that it cannot, even if
        # something calls it by mistake.
        check("read() bounds its own memory",
              len(hist.read(0, 10)) == 10 and
              len(hist.read(0, 10_000)) <= store.MAX_ROWS,
              "cap %d" % store.MAX_ROWS)

        # -- the API ------------------------------------------------------
        st, hdr, body = get(port, "/api/state")
        p = json.loads(body)
        check("GET /api/state answers JSON", st == 200)
        check("the payload is the same shape MQTT publishes",
              all(k in p for k in ("state", "rh", "temp", "fan_on", "cfg",
                                   "fw", "up_min", "mqtt")),
              "keys: %d" % len(p))
        check("no broker is reported as 'disabled', not as a fault",
              p["mqtt"] == "disabled", p["mqtt"])
        check("state is never cached", hdr.get("Cache-Control") == "no-store")

        st, _, body = get(port, "/api/info")
        info = json.loads(body)
        check("GET /api/info identifies the device",
              st == 200 and info["room"] == "bathroom" and info["api"] == 1)

        # -- commands -----------------------------------------------------
        st, _, body = get(port, "/api/cmd", "POST", b"boost")
        check("POST /api/cmd reaches the one command path",
              st == 200 and c.commands and c.commands[-1][1] == "boost",
              "status %d" % st)
        check("the command went in on the topic MQTT would have used",
              c.commands[-1][0] == "fan/bathroom/cmd")
        check("the answer reports what the fan actually did",
              json.loads(body)["fan_on"] is True)

        st, _, _ = get(port, "/api/cmd")
        check("a command cannot be issued by GET", st == 405, "status %d" % st)
        st, _, _ = get(port, "/api/state", "POST", b"x")
        check("a read cannot be issued by POST", st == 405, "status %d" % st)

        st, _, body = get(port, "/api/cmd", "POST",
                          b"update http://10.0.0.9:8000")
        check("OTA is refused over HTTP", st == 403, "status %d" % st)

        st, _, _ = get(port, "/api/nope")
        check("an unknown endpoint is a 404", st == 404)

        # -- parameter writes ---------------------------------------------
        st, _, body = get(port, "/api/set/boost_s", "POST", b"600")
        ok = json.loads(body)
        check("a valid write is accepted and echoed back",
              st == 200 and ok["value"] == 600 and c.st.BOOST_S == 600)
        check("the write comes back with the whole settings block",
              ok["cfg"]["BOOST_S"] == 600)

        st, _, body = get(port, "/api/set/boost_s", "POST", b"99999")
        bad = json.loads(body)
        check("an out-of-range write is refused with a reason",
              st == 400 and bad["ok"] is False and "outside" in bad["reason"],
              "status %d" % st)
        check("a refused write changes nothing", c.st.BOOST_S == 600)
        st, _, _ = get(port, "/api/set/nonsense", "POST", b"1")
        check("an unknown parameter is refused", st == 400)

        # -- CORS ---------------------------------------------------------
        _, hdr, _ = get(port, "/api/state", headers={
            "Origin": "http://192.168.0.9:5173"})
        check("the dev server on the LAN is allowed",
              hdr.get("Access-Control-Allow-Origin") == "http://192.168.0.9:5173")
        _, hdr, _ = get(port, "/api/state", headers={
            "Origin": "https://evil.example"})
        check("a page on the internet is not",
              "Access-Control-Allow-Origin" not in hdr)
        st, hdr, _ = get(port, "/api/cmd", "OPTIONS", headers={
            "Origin": "http://localhost:5173"})
        check("preflight is answered", st == 204 and
              "POST" in hdr.get("Access-Control-Allow-Methods", ""))

        # -- limits -------------------------------------------------------
        st, _, _ = get(port, "/api/cmd", "POST", b"x" * (httpd.BODY_MAX + 64))
        check("an oversized body is refused, not buffered", st == 413,
              "status %d" % st)

        # -- keep-alive and the connection cap ----------------------------
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/api/state")
        first = conn.getresponse()
        first.read()
        conn.request("GET", "/api/info")
        second = conn.getresponse()
        second.read()
        conn.close()
        check("a connection serves more than one request",
              first.status == 200 and second.status == 200)

        # Start from a quiet server: a connection still draining from an
        # earlier request occupies a slot, and this test would then pass or
        # fail on timing rather than on behaviour.
        wait_for(lambda: not server.conns)
        held = []
        try:
            for _ in range(server.max_conns):
                s = socket.create_connection(("127.0.0.1", port), timeout=5)
                s.sendall(b"GET /api/state HTTP/1.1\r\nHost: x\r\n")
                held.append(s)           # deliberately unfinished
            filled = wait_for(lambda: server._active() >= server.max_conns)

            # Every slot is taken by a client mid-sentence, so this one has to
            # wait in the backlog -- and then be served, not refused. A
            # browser does not retry a 503; it drops the asset and renders a
            # broken page, which is exactly how this was found.
            st, _, _ = get(port, "/api/state")
            check("a connection over the cap waits and is then served",
                  filled and st == 200,
                  "status %d, %d of %d slots taken" % (
                      st, server._active(), server.max_conns))
        finally:
            for s in held:
                s.close()
        # Those sockets were left mid-request; the idle reaper has to take
        # them back or the server is now permanently full.
        time.sleep(httpd.IDLE_S + 1)
        st, _, _ = get(port, "/api/state")
        check("a half-open connection is reaped, not leaked forever",
              st == 200, "status %d" % st)

        # A browser that has finished loading keeps its connections open. They
        # must not hold the server against the next visitor.
        wait_for(lambda: not server.conns)
        warm = []
        try:
            for _ in range(server.max_conns):
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                conn.request("GET", "/api/state")
                conn.getresponse().read()   # answered, and left open
                warm.append(conn)
            st, _, _ = get(port, "/api/state")
            check("an idle keep-alive connection yields its slot",
                  st == 200, "status %d" % st)
        finally:
            for conn in warm:
                conn.close()

        check("no pass ever ran long (%.1f ms worst of %d)"
              % (pump.worst_ms, pump.passes), pump.worst_ms < 250,
              "%.1f ms" % pump.worst_ms)
        check("nothing raised out of step()", server.errors == 0,
              "%d errors" % server.errors)
    finally:
        pump.stop()
        server.stop()


def test_store(tmp):
    """The five-minute buckets, and the ways a flash file actually fails."""
    os.makedirs(tmp, exist_ok=True)
    hist = store.History(Cfg, log=lambda *a: None, where=tmp)

    # A fixed clock. The real one comes from NTP and is not ours to move.
    now = [1_800_000_000]
    store._epoch = lambda: now[0]

    def sample(rh, fan=False, lit=False, state="IDLE", rpm=0):
        hist.note({"rh": rh, "base": 60.0, "exc": rh - 60.0, "temp": 21.0,
                   "dp": 14.0, "light_pc": 8.0, "rpm": rpm, "fan_on": fan,
                   "light_on": lit, "state": state})

    # Three samples in one bucket, one of them with the fan on.
    sample(60.0)
    sample(62.0, fan=True, state="RUN", rpm=4000)
    sample(64.0)
    check("nothing is written mid-bucket", hist.stats()["rows"] == 0)

    now[0] += Cfg.HISTORY_INTERVAL_S          # roll into the next bucket
    sample(70.0)
    rows = hist.read()
    check("a closed bucket is one row", len(rows) == 1, "%d rows" % len(rows))
    row = rows[0]
    check("the value stored is the mean of the bucket",
          abs(row[1] - 62.0) < 0.01, str(row[1]))
    check("a fan that ran at all reads as having run", row[8] == 1)
    check("the most severe state in the bucket wins",
          row[10] == store.STATE_CODE["RUN"], str(row[10]))
    check("the row is stamped at the bucket, not at the write",
          row[0] % Cfg.HISTORY_INTERVAL_S == 0, str(row[0]))
    check("the row has one field per documented field",
          len(row) == len(store.FIELDS))

    # The sample that rolled the bucket must belong to the new one.
    now[0] += Cfg.HISTORY_INTERVAL_S
    sample(80.0)
    rows = hist.read()
    check("the sample that rolled the bucket was not dropped",
          len(rows) == 2 and abs(rows[1][1] - 70.0) < 0.01,
          str([r[1] for r in rows]))

    # An unset clock writes nothing rather than writing 2021.
    store._epoch = lambda: None
    before = hist.stats()["rows"]
    for _ in range(5):
        sample(90.0)
    check("an unsynced clock stores nothing", hist.stats()["rows"] == before)
    store._epoch = lambda: now[0]

    # since= is what the app's backfill uses to ask only for the gap.
    cut = rows[1][0]
    check("read(since) returns only newer rows",
          [r[0] for r in hist.read(cut)] == [cut])

    # The failure that actually happens: power lost mid-append.
    with open(hist._path(hist._target), "a") as f:
        f.write("1800001234,55.0,60.0")        # no newline, half the fields
    rows = hist.read()
    check("a truncated last line is skipped, not raised",
          len(rows) == 2, "%d rows" % len(rows))

    # Rotation, which is what bounds the pair of files.
    hist.per_file = 4
    for i in range(12):
        now[0] += Cfg.HISTORY_INTERVAL_S
        sample(60.0 + i)
    total = hist.stats()["rows"]
    check("rotation bounds the files", total <= 2 * hist.per_file + 1,
          "%d rows across both files" % total)
    kept = hist.read()
    check("rotation keeps the newest rows",
          kept and kept[-1][0] == (now[0] // Cfg.HISTORY_INTERVAL_S - 1)
          * Cfg.HISTORY_INTERVAL_S, str(kept[-1][0]) if kept else "none")
    check("rows come back oldest first",
          all(kept[i][0] < kept[i + 1][0] for i in range(len(kept) - 1)))

    # A fresh History after a reboot must not append into the older file.
    again = store.History(Cfg, log=lambda *a: None, where=tmp)
    check("after a restart it appends to the file it was filling",
          again._target == hist._target,
          "%s vs %s" % (again._target, hist._target))

    hist.clear()
    check("clear empties both files", hist.stats()["rows"] == 0)


def test_origins():
    """The CORS rule on its own, since it is the whole access policy."""
    cases = [
        ("http://localhost:5173", True),
        ("http://127.0.0.1:4173", True),
        ("http://192.168.0.9:5173", True),
        ("http://10.1.2.3", True),
        ("http://172.16.4.5", True),
        ("http://172.32.4.5", False),      # outside the private range
        ("http://fan.local", True),
        ("https://evil.example", False),
        ("http://evil.example", False),
        # A domain that merely starts like a private address. Someone else
        # owns this, and registering it is the whole attack.
        ("http://192.168.0.9.evil.example", False),
        ("http://192.168.0.999", False),
        ("http://169.254.7.7", True),          # no DHCP server on the network
        ("", False),
    ]
    bad = [o for o, want in cases if httpd._lan_origin(o) != want]
    check("local origins are told apart from the internet", not bad, str(bad))


def test_wifi(tmp):
    """The provisioning door, over real HTTP."""
    root = os.path.join(tmp, "www")
    make_www(root)
    c = FakeController()
    snap = Snapshot(Cfg, log=lambda *a: None)
    api = webapi.Api(c, Cfg, snap, None)
    port = free_port()
    server = httpd.Server(handler=api.handle, root=root, port=port,
                          log=lambda *a: None)
    server.start()
    pump = Pump(server)
    pump.start()
    try:
        # A build without wifi.py answers honestly rather than 404ing, so the
        # app can tell "this fan cannot" from "you typed the URL wrong".
        st, _, body = get(port, "/api/wifi")
        check("no provisioner answers 501, not 404", st == 501, "status %d" % st)
        check("and says it is unsupported",
              json.loads(body.decode()).get("supported") is False)

        # -- the window shut --------------------------------------------
        c.prov = FakeProv(is_open=False)
        st, _, body = get(port, "/api/wifi")
        doc = json.loads(body.decode())
        check("a shut window still reports the current network",
              st == 200 and doc["ssid"] == "old-net")
        check("it says it is shut", doc["open"] is False)
        check("it tells the app how long the button press is",
              doc.get("hold_s") == Cfg.HOLD_PRESS_S, str(doc.get("hold_s")))

        st, _, body = get(port, "/api/wifi", method="POST", body=b"new-net\npw")
        check("a shut window refuses credentials with 403", st == 403,
              "status %d" % st)
        check("and the provisioner was never asked", c.prov.started == [])
        check("the refusal says how to open it",
              "button" in json.loads(body.decode())["reason"])

        st, _, _ = get(port, "/api/wifi/scan")
        check("a shut window refuses a scan too", st == 403, "status %d" % st)

        # -- the window open --------------------------------------------
        c.prov = FakeProv(is_open=True)
        st, _, body = get(port, "/api/wifi", method="POST",
                          body=b"new-net\nhunter2\nGB")
        check("an open window accepts credentials with 202", st == 202,
              "status %d" % st)
        check("ssid, password and country arrive intact",
              c.prov.started == [("new-net", "hunter2", "GB")],
              str(c.prov.started))
        doc = json.loads(body.decode())
        check("the reply points at where the answer will be",
              doc.get("poll") == "/api/wifi")

        # A password may legitimately contain anything printable; the split is
        # on the one character it cannot contain.
        get(port, "/api/wifi", method="POST", body=b"net\np:a,s#s w=d")
        check("an awkward password is not mangled",
              c.prov.started[-1][1] == "p:a,s#s w=d", repr(c.prov.started[-1][1]))

        st, _, body = get(port, "/api/wifi/scan")
        check("an open window scans", st == 200 and
              json.loads(body.decode())["networks"][0]["ssid"] == "home")

        # GET must never be a write, here least of all.
        before = len(c.prov.started)
        get(port, "/api/wifi")
        check("GET /api/wifi changes nothing", len(c.prov.started) == before)
    finally:
        pump.stop()
        server.stop()


def main():
    print("on-device server (host, no board)\n")
    with tempfile.TemporaryDirectory() as tmp:
        cwd = os.getcwd()
        try:
            test_server(tmp)
            print()
            test_store(os.path.join(tmp, "hist"))
            print()
            test_wifi(tmp)
            print()
            test_origins()
        finally:
            os.chdir(cwd)

    failed = [n for n, ok, _ in results if not ok]
    print("\n%d/%d passed" % (len(results) - len(failed), len(results)))
    if failed:
        for n in failed:
            print("  FAILED: %s" % n)
        sys.exit(1)


if __name__ == "__main__":
    main()
