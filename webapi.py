"""The device's own HTTP API. A second transport, not a second controller.

Every write here ends in `controller.on_command(topic, payload)` -- the same
function the MQTT subscription calls, reached by synthesising the topic that
command would have arrived on. That is the whole design rule of this module,
and it is worth being blunt about why: a parallel set of handlers would be a
second place for the validation, the clamping and the ordering rules to live,
and the two would drift. The first symptom would be a threshold the app
accepts and the broker rejects, or vice versa, which is a genuinely horrible
thing to diagnose from a ceiling.

So the device keeps one command path with two doors into it.

Endpoints:

    GET  /api/state              the snapshot, same JSON as fan/<room>/state
    GET  /api/info               identity, build, link and history status
    GET  /api/history?since=<s>  stored rows as CSV text, oldest first
    GET  /api/wifi               provisioning window state and current SSID
    GET  /api/wifi/scan          visible networks; only while the window is open
    POST /api/cmd                body: boost | cancel | relearn | clear | ...
    POST /api/set/<PARAM>        body: the new value
    POST /api/wifi               body: ssid, password, country, one per line
                                 (window only; see wifi.py)

Reads are GET and writes are POST, so a browser's prefetching or a crawler
cannot boost the fan by following a link.

There is no authentication, and that is a deliberate, documented choice
rather than an oversight: this is a LAN device whose physical control is a
button on the wall, and the alternative -- a password on a device with no
screen and no clock -- is a support burden that buys very little. The
protection that does exist is that writes need POST, and that CORS is
reflected only for local origins (see httpd._lan_origin), so a random page
on the internet cannot drive it. Anyone already on the network can. If that
is not acceptable, do not expose the port; the MQTT path with broker
credentials is still there.

/api/wifi is the one exception, and it proves the rule rather than breaking
it. Reading humidity and pressing Boost sit comfortably inside "anyone on the
LAN"; moving the fan onto a different network does not, because nothing about
being on the network says you are allowed to take the device off it. So it is
gated on someone holding the button on the fan, which is the same physical
control the rest of the argument rests on, and the same reasoning that
already refuses `update` here. See wifi.py.
"""

from httpd import Response, json_response, text_response

try:
    from store import FIELDS
except ImportError:                     # history disabled or not installed
    FIELDS = ()


def wifi_try_s():
    """How long an attempt is given, so the app can size its own wait."""
    try:
        import wifi
        return wifi.TRY_S
    except ImportError:
        return 45


class Rows:
    """A read(n) source over the stored history files, oldest first.

    This exists because the obvious implementation does not fit on the board.
    Parsing a week of history into a list and serialising it to JSON costs
    about 890 bytes of heap per row, measured on an RP2350 -- 1.8 MB for 2,016
    rows against roughly 430 kB of heap. It raised MemoryError at about 512
    rows, which is a day and a half. The charts would simply never have filled
    on real hardware, and the log would have said nothing more useful than
    "handler failed".

    So the stored files are sent as they are. They are already line-oriented
    text in the documented field order, so the cheapest possible thing to do
    is also the right one: read a line, hand it over, forget it. Memory use is
    one line plus the socket buffer, whatever the window.

    The body is CSV with one comment line at the top carrying the metadata
    that used to be JSON keys. A header would have needed
    Access-Control-Expose-Headers to survive a cross-origin read; a comment
    line needs nothing and travels with the data it describes.
    """

    def __init__(self, paths, interval_s, since=0):
        self._paths = list(paths)
        self._since = since
        self._f = None
        self._buf = b"#interval=%d fields=%s\n" % (
            interval_s, ",".join(FIELDS).encode() if FIELDS else b"")

    def read(self, n):
        """Up to n bytes. Whole lines, never split across two reads.

        Two paths, because the common one can avoid touching lines at all:

        `since=0` is the first backfill, the whole week, and the only request
        big enough for efficiency to matter. Nothing is being filtered, so the
        file is copied through in raw blocks -- one allocation per call, no
        line objects, no rebuilt buffers.

        `since>0` is a top-up covering the gap since the client last looked,
        which is small. That one reads lines so it can skip the old ones.

        The buffer is handed over whole and replaced, never sliced and
        re-concatenated. The first version of this did slice, and streaming
        100 kB produced roughly 400 kB of garbage on the device -- collectable,
        but it forced the collector to run repeatedly inside the control loop
        for no reason at all.
        """
        if self._buf:
            out, self._buf = self._buf, b""
            return out
        if not self._since:
            return self._read_block(n)

        parts = []
        size = 0
        while size < n:
            line = self._next_line()
            if line is None:
                break
            if size and size + len(line) > n:
                self._buf = line            # next call takes it whole
                break
            parts.append(line)
            size += len(line)
        if not parts:
            return b""
        return parts[0] if len(parts) == 1 else b"".join(parts)

    def _read_block(self, n):
        """n bytes straight off flash, crossing from one file to the next."""
        while True:
            if self._f is None:
                if not self._paths:
                    return b""
                try:
                    self._f = open(self._paths.pop(0), "rb")
                except OSError:
                    continue                # a file that is not there yet
            block = self._f.read(n)
            if block:
                return block
            self._close_file()

    def _next_line(self):
        while True:
            if self._f is None:
                if not self._paths:
                    return None
                try:
                    self._f = open(self._paths.pop(0), "rb")
                except OSError:
                    continue            # a file that is not there yet
            line = self._f.readline()
            if not line:
                self._close_file()
                continue
            if self._since:
                # Compare the timestamp as text. Parsing the row to decide
                # whether to skip it would reintroduce the allocation this
                # whole class exists to avoid.
                try:
                    if int(line.split(b",", 1)[0]) < self._since:
                        continue
                except ValueError:
                    continue            # a torn line; skip it as _parse does
            return line

    def _close_file(self):
        if self._f:
            try:
                self._f.close()
            except Exception:
                pass
            self._f = None

    def close(self):
        self._paths = []
        self._close_file()


class Api:
    """Routes bound to one controller. Pass `handle` to httpd.Server."""

    def __init__(self, controller, cfg, snap, history=None, log=None):
        self.c = controller
        self.cfg = cfg
        self.snap = snap
        self.history = history
        self._log = log or (lambda *a: None)
        self.room = cfg.ROOM
        self.hits = 0

    # -- the handler -------------------------------------------------------

    def handle(self, req):
        """httpd calls this first. None means 'not mine, serve a file'."""
        if not req.path.startswith("/api/"):
            return None
        self.hits += 1
        route = req.path[5:].rstrip("/")

        if route == "state":
            return self._state(req)
        if route == "info":
            return self._info(req)
        if route == "history":
            return self._history(req)
        if route == "wifi":
            return self._wifi(req)
        if route == "wifi/scan":
            return self._wifi_scan(req)
        if route == "cmd":
            return self._cmd(req)
        if route.startswith("set/"):
            return self._set(req, route[4:])
        return text_response(404, "no such endpoint")

    # -- reads -------------------------------------------------------------

    def _state(self, req):
        if req.method not in ("GET", "HEAD"):
            return text_response(405, "GET only")
        return json_response(self._payload())

    def _payload(self):
        """The live snapshot.

        raw_light comes from the controller's last reading rather than from
        the ADC: reading here would blank the status LEDs and sample the
        sensor from inside a web request, which is both slower than it looks
        and a second reader of a sensor that has exactly one owner.
        """
        return self.snap.payload(self.c, self.c.raw_light,
                                 self.c.rssi(), self.c.link)

    def _info(self, req):
        if req.method not in ("GET", "HEAD"):
            return text_response(405, "GET only")
        link = self.c.link
        return json_response({
            "device": getattr(self.cfg, "DEVICE_ID", "fan"),
            "name": getattr(self.cfg, "DEVICE_NAME", "Smartfan"),
            "room": self.room,
            "fw": self.snap.fw,
            "app": self.snap.app,
            "mqtt": getattr(link, "state", "disabled"),
            "mqtt_host": getattr(self.c, "mqtt_host", "") or None,
            "ip": getattr(self.c, "ip", None),
            "hostname": getattr(self.cfg, "HOSTNAME", None),
            "sample_s": self.cfg.SAMPLE_S,
            "publish_s": self.cfg.PUBLISH_INTERVAL_S,
            "history": self.history.stats() if self.history else None,
            "api": 1,
        })

    def _history(self, req):
        """The stored week, streamed rather than assembled. See Rows."""
        if req.method not in ("GET", "HEAD"):
            return text_response(405, "GET only")
        if not self.history:
            # An empty body, not an error: the app's own live buffer is still
            # perfectly usable, and a 404 here would look like a fault.
            return Response(200, "#interval=0\n", "text/csv; charset=utf-8",
                            {"Cache-Control": "no-store"})
        try:
            since = int(req.query.get("since", "0"))
        except ValueError:
            since = 0
        headers = {"Cache-Control": "no-store"}
        if req.method == "HEAD":
            return Response(200, b"", "text/csv; charset=utf-8", headers)
        return Response(200, b"", "text/csv; charset=utf-8", headers,
                        source=Rows(self.history.paths(),
                                    self.history.interval_s, since))

    # -- wifi ---------------------------------------------------------------

    def _prov(self):
        return getattr(self.c, "prov", None)

    def _wifi(self, req):
        prov = self._prov()
        if prov is None:
            return json_response({"supported": False,
                                  "reason": "no wifi module on this build"}, 501)
        if req.method in ("GET", "HEAD"):
            st = prov.status()
            st["supported"] = True
            # The gesture belongs to config.py, not to the provisioner, and
            # the app should not be hardcoding "8 seconds" in its own copy of
            # a number the device can simply tell it.
            st["hold_s"] = getattr(self.cfg, "HOLD_PRESS_S", 8)
            return json_response(st)
        if req.method != "POST":
            return text_response(405, "GET or POST")

        # The window, checked here as well as in Provisioner.start, so a
        # closed door answers 403 rather than 400. The app shows a different
        # thing for "not allowed yet" than for "that did not work", and the
        # status code is what tells it which.
        if not prov.is_open:
            return json_response({
                "ok": False,
                "reason": "hold the button on the fan for %ds first"
                          % int(getattr(self.cfg, "HOLD_PRESS_S", 8)),
                "open": False,
            }, 403)

        body = req.text() or ""
        if len(body) > 200:
            return text_response(413, "too long")
        # Newline-separated, like /api/cmd's plain-text body: a password may
        # legitimately contain any printable character including the ones
        # that would need escaping in JSON or a query string, and splitting
        # on the one character it cannot contain is the simplest thing that
        # cannot corrupt it.
        parts = body.split("\n")
        ssid = parts[0].strip() if parts else ""
        psk = parts[1] if len(parts) > 1 else ""
        country = parts[2].strip() if len(parts) > 2 else None

        ok, why = prov.start(ssid, psk, country)
        if not ok:
            return json_response({"ok": False, "reason": why}, 400)

        # 202, not 200: this has been accepted and not yet done, and the
        # answer cannot come back on this socket. A successful switch takes
        # the radio off the network this request arrived on, so the reply
        # would die with the association. The app reads GET /api/wifi after.
        return json_response({"ok": True, "state": "trying", "ssid": ssid,
                              "poll": "/api/wifi", "try_s": wifi_try_s()}, 202)

    def _wifi_scan(self, req):
        if req.method not in ("GET", "HEAD"):
            return text_response(405, "GET only")
        prov = self._prov()
        if prov is None:
            return json_response({"supported": False}, 501)
        nets = prov.scan()
        if nets is None:
            return json_response({"ok": False, "open": False,
                                  "reason": "the window is closed"}, 403)
        return json_response({"ok": True, "networks": nets})

    # -- writes ------------------------------------------------------------

    def _cmd(self, req):
        if req.method != "POST":
            return text_response(405, "POST only")
        cmd = req.text()
        if not cmd:
            return text_response(400, "empty command")
        if len(cmd) > 200:
            return text_response(413, "command too long")

        # 'update <url>' pulls firmware from a host it is told about, which is
        # the one command that can change what the device runs. Over MQTT it
        # arrives from an authenticated broker client; here it would arrive
        # from anyone on the LAN. Refused rather than accepted quietly -- the
        # OTA path stays on the channel that has credentials.
        if cmd.split(None, 1)[0].lower() == "update":
            return text_response(403, "OTA is MQTT-only; see tools/ota_publish.py")

        before = self.c.state
        self.c.on_command("fan/%s/cmd" % self.room, cmd.encode())
        return json_response({"ok": True, "was": before, "state": self.c.state,
                              "fan_on": bool(self.c.fan.value())})

    def _set(self, req, name):
        if req.method != "POST":
            return text_response(405, "POST only")
        name = name.strip().upper()
        if not name or len(name) > 40:
            return text_response(400, "bad parameter name")
        value = req.text()
        if not value:
            return text_response(400, "empty value")

        # The settings object is the authority on ranges, so ask it rather
        # than validating here. on_command logs a rejection but returns
        # nothing, so the check runs first to give the app a real answer --
        # the same answer, from the same code, one call earlier.
        ok, result = self.c.st.set(name, value)
        if not ok:
            return json_response({"ok": False, "param": name,
                                  "reason": str(result)}, 400)
        self.c._push = True
        return json_response({"ok": True, "param": name, "value": result,
                              "cfg": self.c.st.as_dict()})
