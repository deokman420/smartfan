"""A non-blocking HTTP server for the control loop.

The controller already has a working web server in provision.py, and this is
deliberately not it. That one owns the board while it runs and can afford to
block for four seconds on a slow phone. This one is stepped from inside the
control loop, where the watchdog is armed at 8 s and a single blocking read
would mean a reset -- so every socket here is non-blocking and every pass
does a bounded amount of work and then gives its turn back.

Why serve the app from the device at all: so that nothing else has to be up.
With the bundle on flash and these endpoints answering, a user with no Home
Assistant, no broker and no server has a working app at http://<device>/ --
the fan and its interface are one thing you plug in, not two services to
keep alive.

The rules this module lives by, in order:

1. **Control comes first.** step() is bounded by a byte and time budget, is
   called after the outputs are already driven, and swallows every
   exception. A wedged browser cannot stall the fan.
2. **No dynamic allocation of consequence.** Connections are capped, the
   request head is capped, bodies are capped, and files stream in fixed
   chunks. A large POST is refused, not buffered.
3. **It is a second transport, never a second implementation.** Routing ends
   at a handler; the handler calls the same controller methods MQTT does.
   See webapi.py.

Keep-alive is supported because the first load fetches around twenty files,
and a connection per file turns a fast page into a slow one. Connections over
the cap wait in the listener's backlog rather than being refused -- a browser
does not retry a 503, so refusing one cost the page its stylesheet. Idle
connections are dropped after IDLE_S, and a connection being closed is drained
for LINGER_MS first so its last response is not lost to a RST.
"""

import socket
import time

try:
    _ticks = time.ticks_ms
    _diff = time.ticks_diff
    _add = time.ticks_add
except AttributeError:                      # CPython, for the host tests
    def _ticks():
        return int(time.monotonic() * 1000)

    def _diff(a, b):
        return a - b

    def _add(a, b):
        return a + b


# EAGAIN/EWOULDBLOCK is the normal answer from a non-blocking socket with
# nothing to say. MicroPython reports 11, CPython on Windows 10035.
_WOULDBLOCK = (11, 35, 10035, 10036)

HEAD_MAX = 2048          # a request head larger than this is not ours
BODY_MAX = 1024          # commands and parameter writes; nothing needs more
CHUNK = 1024             # bytes written per connection per pass
IDLE_S = 5               # drop a connection that goes quiet mid-request
BACKLOG = 8              # connections the listener may hold before we accept
LINGER_MS = 400          # drain a closing connection this long, then let go
KEEPALIVE_REQS = 32      # requests per connection before we insist on close

_MIME = {
    "html": "text/html; charset=utf-8",
    "js": "text/javascript; charset=utf-8",
    "css": "text/css; charset=utf-8",
    "json": "application/json; charset=utf-8",
    "svg": "image/svg+xml",
    "webmanifest": "application/manifest+json",
    "ico": "image/x-icon",
    "png": "image/png",
    "woff2": "font/woff2",
    "txt": "text/plain; charset=utf-8",
}

_STATUS = {
    200: "OK", 204: "No Content", 304: "Not Modified",
    400: "Bad Request", 403: "Forbidden", 404: "Not Found",
    405: "Method Not Allowed", 413: "Payload Too Large",
    500: "Internal Server Error", 503: "Service Unavailable",
}


def _mime(path):
    ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    return _MIME.get(ext, "application/octet-stream")


def unquote(s):
    """Percent-decoding, including '+' for space. MicroPython has no urllib."""
    s = s.replace("+", " ")
    if "%" not in s:
        return s
    out = ""
    i = 0
    while i < len(s):
        c = s[i]
        if c == "%" and i + 2 < len(s):
            try:
                out += chr(int(s[i + 1:i + 3], 16))
                i += 3
                continue
            except ValueError:
                pass
        out += c
        i += 1
    return out


class Response:
    """What a handler returns.

    Three kinds of body, in order of how much memory they cost:

        body    bytes already in hand. Fine for a reading; fatal for a week
                of history, which is the lesson behind `source` below.
        path    a file on flash, streamed in CHUNK-sized pieces.
        source  any object with read(n) and close(). Length is not known in
                advance, so the response is delimited by closing the
                connection -- the HTTP/1.0 convention, which every client
                still understands, and the only one that needs no buffering
                and no chunked-encoding machinery on a board with 400 kB of
                heap.
    """

    __slots__ = ("status", "body", "ctype", "headers", "path", "source")

    def __init__(self, status=200, body=b"", ctype="text/plain; charset=utf-8",
                 headers=None, path=None, source=None):
        if isinstance(body, str):
            body = body.encode()
        self.status = status
        self.body = body
        self.ctype = ctype
        self.headers = headers or {}
        self.path = path            # set instead of body to stream a file
        self.source = source        # set instead of body to stream anything


def json_response(obj, status=200):
    import json
    return Response(status, json.dumps(obj), "application/json; charset=utf-8",
                    {"Cache-Control": "no-store"})


def text_response(status, msg):
    return Response(status, msg + "\n", "text/plain; charset=utf-8",
                    {"Cache-Control": "no-store"})


class Request:
    __slots__ = ("method", "path", "query", "headers", "body", "origin")

    def __init__(self, method, path, query, headers, body):
        self.method = method
        self.path = path
        self.query = query
        self.headers = headers
        self.body = body
        self.origin = headers.get("origin", "")

    def text(self):
        try:
            return self.body.decode().strip()
        except Exception:
            return ""


def _parse_query(qs):
    out = {}
    for pair in qs.split("&"):
        if not pair:
            continue
        k, _, v = pair.partition("=")
        out[unquote(k)] = unquote(v)
    return out


def _lan_origin(origin):
    """True if this Origin is something on the local network.

    A device on the LAN with no authentication is reachable by any page the
    owner happens to have open, so it must not hand out a blanket
    Access-Control-Allow-Origin. But the dev server on a laptop
    (http://192.168.0.x:5173) genuinely needs to talk to a real device, and
    refusing that would mean developing the app against a fake.

    So: reflect the Origin only when it is plainly local -- loopback, an
    RFC1918 literal, or a .local name. A page on the public internet gets no
    CORS header and the browser drops its response, which is the protection
    that matters here.

    The prefix has to be checked against a parsed address rather than against
    the string, because `http://192.168.0.9.evil.example` starts with
    "192.168." and is a domain someone else owns. A "looks private" test that
    a registrar can satisfy is not a test at all.
    """
    if not origin.startswith("http://"):
        return False
    host = origin[7:].split("/")[0].lower()
    if host.startswith("["):
        return False                    # IPv6 literal; not a case we serve
    host = host.split(":")[0]
    if host in ("localhost", "127.0.0.1") or host.endswith(".local"):
        return True
    return _private_ip(host)


def _private_ip(host):
    """True for an RFC1918 or loopback IPv4 literal, and only for a literal."""
    parts = host.split(".")
    if len(parts) != 4:
        return False
    nums = []
    for part in parts:
        if not part or not part.isdigit() or len(part) > 3:
            return False
        n = int(part)
        if n > 255:
            return False
        nums.append(n)
    if nums[0] == 10 or nums[0] == 127:
        return True
    if nums[0] == 192 and nums[1] == 168:
        return True
    if nums[0] == 172 and 16 <= nums[1] <= 31:
        return True
    # 169.254/16, which is what a device falls back to with no DHCP server.
    return nums[0] == 169 and nums[1] == 254


class _Conn:
    """One client connection, advanced a little at a time."""

    __slots__ = ("sock", "buf", "out", "file", "state", "seen", "need",
                 "head", "reqs", "close_after", "refused", "linger_until")

    def __init__(self, sock):
        self.sock = sock
        self.buf = b""          # inbound, until the head is complete
        self.out = b""          # pending bytes to write
        self.file = None        # open file being streamed
        self.state = "head"
        self.seen = _ticks()
        self.need = 0           # Content-Length still to read
        self.head = None        # (method, path, query, headers)
        self.reqs = 0
        self.close_after = False
        self.refused = False    # a 503 on its way out; not a served client
        self.linger_until = 0   # set once we are draining a closing socket

    def close(self):
        """Close politely, so the last response is not thrown away.

        Closing a socket that still has unread bytes in its receive queue
        sends a RST, and a RST discards anything we just wrote -- so the
        client sees a dropped connection instead of the 503 or the 413 that
        explains itself. This is exactly how a refused connection behaves: its
        request was never read, because refusing it was the point.

        So: drain what is pending, shut the write side down, then close.
        """
        if self.file:
            try:
                self.file.close()
            except Exception:
                pass
            self.file = None
        try:
            for _ in range(8):
                if not self.sock.recv(512):
                    break
        except Exception:
            pass
        try:
            self.sock.shutdown(getattr(socket, "SHUT_WR", 1))
        except Exception:
            pass                # not every MicroPython port has shutdown()
        try:
            self.sock.close()
        except Exception:
            pass


class Server:
    """HTTP on `port`, routed through `handler`, falling back to `root`.

    handler(req) -> Response or None. None means "not mine", and the request
    is served from the static root instead, with a single-page fallback to
    index.html so the app's client-side routes work on a reload.
    """

    def __init__(self, handler=None, root="www", port=80, log=None,
                 max_conns=4, spa="index.html", names=()):
        # Host names this device answers to, besides IP literals and
        # localhost, which are always accepted. See _host_ok.
        self.names = frozenset(n.lower().rstrip(".") for n in names if n)
        self._refused_hosts = set()
        self.handler = handler
        self.root = root
        self.port = port
        self.spa = spa
        self.max_conns = max_conns
        self._log = log or (lambda *a: None)
        self.listener = None
        self.conns = []
        self.requests = 0
        self.errors = 0
        self.bound = False

    # -- lifecycle ---------------------------------------------------------

    def start(self):
        """Bind and listen. Returns True if we are open for business.

        Binding 0.0.0.0 means a new DHCP lease needs no rebind, so this is
        called once and the socket outlives any number of WiFi drops.
        """
        if self.bound:
            return True
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("0.0.0.0", self.port))
            # A deeper backlog than the serving cap on purpose: a browser
            # opens several connections at once and the extras wait here
            # instead of being turned away.
            s.listen(BACKLOG)
            s.setblocking(False)
            self.listener = s
            self.bound = True
            self._log("http: listening on :%d" % self.port)
            return True
        except Exception as e:
            self._log("http: bind failed (%s)" % e)
            try:
                s.close()
            except Exception:
                pass
            return False

    def stop(self):
        for c in self.conns:
            c.close()
        self.conns = []
        if self.listener:
            try:
                self.listener.close()
            except Exception:
                pass
            self.listener = None
        self.bound = False

    # -- the step ----------------------------------------------------------

    def step(self, budget_ms=8):
        """One bounded pass: accept what is waiting, move each connection on.

        Never raises. A failure on one connection closes that connection and
        leaves everything else -- most of all the control loop -- alone.
        """
        if not self.bound:
            return
        deadline = _add(_ticks(), budget_ms)
        try:
            self._accept()
        except Exception as e:
            self.errors += 1
            self._log("http: accept (%s)" % e)

        for c in self.conns[:]:
            try:
                self._advance(c)
            except Exception as e:
                self.errors += 1
                self._log("http: conn (%s)" % e)
                self._drop(c)
                continue
            if _diff(deadline, _ticks()) <= 0:
                break                       # finish the rest next pass

        # Reap anything finished or gone quiet. A half-open socket left in
        # the list would otherwise hold one of only three slots forever.
        now = _ticks()
        for c in self.conns[:]:
            if c.state == "done":
                self._drop(c)
            elif _diff(now, c.seen) > IDLE_S * 1000:
                self._drop(c)

    def _drop(self, c):
        c.close()
        try:
            self.conns.remove(c)
        except ValueError:
            pass

    def _accept(self):
        """Take on new connections, up to the cap. Never refuse one.

        This used to answer 503 once the cap was reached, on the reasoning
        that refusing is more honest than queueing work the loop has no
        budget for. A browser proved that wrong in the most direct way
        available: it opens six connections at once to load a page, three got
        their 503, and **it did not retry a single one of them**. The
        stylesheet and half the scripts simply never arrived, and the app was
        a blank screen. See tests/test_device_app.py.

        So a connection over the cap is left in the listener's backlog, which
        is what a backlog is for -- the client waits its turn rather than
        being told to go away, and we still only ever serve `max_conns` at
        once. The one case that needs help is a browser holding connections
        open after it has finished with them: an idle keep-alive connection is
        closed to make room, so a second visitor is never stuck behind the
        first one's politeness.
        """
        while self.listener is not None:
            if self._active() >= self.max_conns and not self._evict_idle():
                return                      # full; the backlog holds the rest
            try:
                sock, _addr = self.listener.accept()
            except OSError as e:
                if e.args and e.args[0] in _WOULDBLOCK:
                    return
                return
            except Exception:
                return
            sock.setblocking(False)
            self.conns.append(_Conn(sock))

    def _evict_idle(self):
        """Close one connection that is kept alive but not asking for anything.

        Only a connection that has already had a request answered and has
        nothing buffered counts: that is a client being kept warm, not a
        client mid-sentence. Returns whether a slot was freed.
        """
        for c in self.conns:
            if (c.state == "head" and c.reqs > 0 and not c.buf
                    and not c.refused):
                self._drop(c)
                return True
        return False

    def _active(self):
        """Connections being served. A closing drain is not one."""
        n = 0
        for c in self.conns:
            if not c.refused and c.state not in ("linger", "done"):
                n += 1
        return n

    def _advance(self, c):
        if c.state == "head":
            self._read_head(c)
        if c.state == "body":
            self._read_body(c)
        if c.state == "send":
            self._write(c)
        if c.state == "linger":
            self._linger(c)

    # -- reading -----------------------------------------------------------

    def _recv(self, c, n):
        """Non-blocking read. Returns bytes, b'' for nothing, None for EOF."""
        try:
            data = c.sock.recv(n)
        except OSError as e:
            if e.args and e.args[0] in _WOULDBLOCK:
                return b""
            raise
        if data == b"":
            return None
        return data

    def _read_head(self, c):
        data = self._recv(c, 512)
        if data is None and not c.buf:
            c.state = "done"
            return
        if data:
            c.seen = _ticks()
            c.buf += data
        if len(c.buf) > HEAD_MAX:
            self._respond(c, text_response(413, "request head too large"))
            c.close_after = True        # drained by _begin_linger, not RST
            return
        if b"\r\n\r\n" not in c.buf:
            return

        head, _, rest = c.buf.partition(b"\r\n\r\n")
        c.buf = rest
        try:
            lines = head.decode("utf-8", "replace").split("\r\n")
            parts = lines[0].split(" ")
            method = parts[0].upper()
            target = parts[1] if len(parts) > 1 else "/"
            proto = parts[2] if len(parts) > 2 else "HTTP/1.0"
        except Exception:
            self._respond(c, text_response(400, "unparseable request"))
            c.close_after = True
            return

        headers = {}
        for line in lines[1:]:
            k, _, v = line.partition(":")
            if v:
                headers[k.strip().lower()] = v.strip()

        path, _, qs = target.partition("?")
        c.head = (method, path, _parse_query(qs), headers)
        c.reqs += 1
        c.close_after = (
            headers.get("connection", "").lower() == "close"
            or proto == "HTTP/1.0"
            or c.reqs >= KEEPALIVE_REQS)

        try:
            c.need = int(headers.get("content-length", "0"))
        except ValueError:
            c.need = 0
        if c.need > BODY_MAX:
            self._respond(c, text_response(413, "body too large"))
            c.close_after = True
            return
        c.state = "body"

    def _read_body(self, c):
        while len(c.buf) < c.need:
            data = self._recv(c, min(512, c.need - len(c.buf)))
            if data is None:
                c.state = "done"
                return
            if not data:
                return                      # more next pass
            c.seen = _ticks()
            c.buf += data
        body, c.buf = c.buf[:c.need], c.buf[c.need:]
        method, path, query, headers = c.head
        self._dispatch(c, Request(method, path, query, headers, body))

    # -- routing -----------------------------------------------------------

    def _host_ok(self, host):
        """Is this Host header one of this device's own names?

        DNS rebinding (audit NV-5): a page on evil.example re-points its own
        name at this device's LAN address, and from then on the browser
        treats the device as same-origin with that page -- CORS no longer
        applies, and it can read everything. The one thing the attacker
        cannot change is the Host header, which still says evil.example.

        An IP literal cannot be rebound, so any is fine: the address moves
        with DHCP, and the app and Home Assistant are often pointed at it.
        So is localhost. Names must be on the list: the hostname, its .local
        form, and config.HTTP_HOSTS for a router's own DNS name (fan.lan,
        fan.fritz.box) or a reverse proxy. A missing Host -- HTTP/1.0 --
        is not a browser and is let through.
        """
        if not host:
            return True
        h = host.strip().lower()
        if h.startswith("["):
            return "]" in h                     # an IPv6 literal
        h = h.rsplit(":", 1)[0] if ":" in h else h
        h = h.rstrip(".")
        if h == "localhost":
            return True
        parts = h.split(".")
        if len(parts) == 4 and all(p.isdigit() and len(p) <= 3 and int(p) < 256
                                   for p in parts):
            return True
        return h in self.names

    def _dispatch(self, c, req):
        self.requests += 1
        resp = None
        host = req.headers.get("host", "")
        if not self._host_ok(host):
            # Logged once per name, so a misconfigured client is visible on
            # the serial console without flooding it.
            if host not in self._refused_hosts and len(self._refused_hosts) < 8:
                self._refused_hosts.add(host)
                self._log("http: refused Host %r -- if that is a name you use "
                          "for this fan, add it to HTTP_HOSTS in config.py"
                          % host)
            self._respond(c, text_response(
                421, "unknown host name; add it to HTTP_HOSTS in config.py"))
            return
        if req.method == "OPTIONS":
            # Preflight, which the dev server's cross-origin POSTs trigger.
            resp = Response(204, b"", "text/plain", {
                "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
                "Access-Control-Allow-Headers": "Content-Type",
                "Access-Control-Max-Age": "600",
            })
        elif (req.method not in ("GET", "HEAD") and req.origin
              and not _lan_origin(req.origin)):
            # A write from a page that is not on the LAN. CORS only decides
            # whether a browser may READ the answer; a plain-text POST needs
            # no preflight, so without this a public page open on any phone
            # in the house could boost the fan or switch extraction off, and
            # never see the reply it did not need. Browsers always send
            # Origin on a cross-site POST (as "null" when a page hides it),
            # so refusing a foreign one closes that. A client with no Origin
            # at all -- curl, Home Assistant's aiohttp -- is not a browser
            # page and is unaffected. The same check refuses a DNS-rebound
            # page, whose Origin is the attacker's name, not this device's.
            resp = text_response(403, "cross-site write refused")
        elif self.handler:
            try:
                resp = self.handler(req)
            except Exception as e:
                self.errors += 1
                self._log("http: handler (%s)" % e)
                resp = text_response(500, "handler failed")
        if resp is None:
            resp = self._static(req)
        if _lan_origin(req.origin):
            resp.headers["Access-Control-Allow-Origin"] = req.origin
            resp.headers["Vary"] = "Origin"
        self._respond(c, resp)

    def _static(self, req):
        if req.method not in ("GET", "HEAD"):
            return text_response(405, "method not allowed")
        if not self.root:
            return text_response(404, "no static root")

        rel = req.path.lstrip("/") or self.spa
        # Nothing may climb out of the root. '..' has no legitimate use in a
        # request for a bundled asset, so it is refused rather than resolved.
        if ".." in rel or rel.startswith("/"):
            return text_response(403, "forbidden")

        found = self._find(rel)
        if not found and "." not in rel.rsplit("/", 1)[-1]:
            # A client-side route reloaded. The app is a single page, so the
            # shell is the right answer -- not a 404 the user has to
            # understand.
            found = self._find(self.spa)
        if not found:
            return text_response(404, "not found")

        path, gzipped, size = found
        headers = {"Content-Length": str(size)}
        if gzipped:
            # Only the compressed copy is on flash: the bundle is a third of
            # the size that way, and flash is the scarce resource here. Every
            # browser since the nineties accepts it. curl needs --compressed.
            headers["Content-Encoding"] = "gzip"
        # Three cases, not two. Hashed assets never change under their own
        # name, so they are cached forever. version.json is how the running
        # app finds out it is stale, and a cached answer to that question is
        # worse than no answer. The shell is no-store because no-cache was
        # not enough: no-cache asks a browser to revalidate, and iOS keeps a
        # home-screen app's shell regardless -- a fix went live on the fan
        # and stayed invisible on the phone the app is mostly used from.
        if "immutable" in rel:
            cc = "public, max-age=31536000, immutable"
        elif rel.endswith("version.json") or rel.endswith(".html") or rel == "":
            cc = "no-store"
        else:
            cc = "no-cache"
        headers["Cache-Control"] = cc
        if req.method == "HEAD":
            return Response(200, b"", _mime(rel), headers)
        return Response(200, b"", _mime(rel), headers, path=path)

    def _find(self, rel):
        """(path, gzipped, size) for a request path, or None."""
        base = self.root + "/" + rel
        for path, gz in ((base + ".gz", True), (base, False)):
            size = _size(path)
            if size is not None:
                return path, gz, size
        return None

    # -- writing -----------------------------------------------------------

    def _respond(self, c, resp):
        status = "%d %s" % (resp.status, _STATUS.get(resp.status, "OK"))
        lines = ["HTTP/1.1 " + status, "Content-Type: " + resp.ctype]

        if resp.source is not None:
            # Length unknown until it has all been sent, so the close IS the
            # terminator. Keep-alive is given up for this one response, which
            # costs a connection and saves holding the whole body in RAM.
            c.close_after = True
        else:
            body_len = resp.headers.get("Content-Length")
            if body_len is None:
                body_len = str(len(resp.body))
            lines.append("Content-Length: " + body_len)
        lines.append("Connection: " + ("close" if c.close_after else "keep-alive"))

        for k, v in resp.headers.items():
            if k != "Content-Length":
                lines.append("%s: %s" % (k, v))
        c.out = ("\r\n".join(lines) + "\r\n\r\n").encode() + resp.body

        c.file = resp.source
        if c.file is None and resp.path:
            try:
                c.file = open(resp.path, "rb")
            except OSError:
                c.file = None
        c.state = "send"

    def _write(self, c):
        sent_this_pass = 0
        while sent_this_pass < CHUNK:
            if not c.out:
                if c.file:
                    chunk = c.file.read(CHUNK)
                    if chunk:
                        c.out = chunk
                    else:
                        c.file.close()
                        c.file = None
                if not c.out:
                    break
            try:
                n = c.sock.send(c.out)
            except OSError as e:
                if e.args and e.args[0] in _WOULDBLOCK:
                    return                  # socket full; resume next pass
                raise
            if n <= 0:
                return
            c.out = c.out[n:]
            sent_this_pass += n
            c.seen = _ticks()

        if c.out or c.file:
            return                          # more to send next pass
        if c.close_after:
            self._begin_linger(c)
            return
        # Keep-alive. Anything still buffered is the next request, and
        # _read_head parses the buffer whether or not the socket had more to
        # give -- which is what makes a pipelined request work rather than sit
        # there until the idle timeout.
        c.head = None
        c.need = 0
        c.state = "head"

    def _begin_linger(self, c):
        """Shut the write side and drain, instead of closing on the spot.

        Closing a socket whose receive queue still holds bytes sends a RST,
        and a RST throws away the response we just wrote. That is not a
        hypothetical: the two cases that answer without reading the whole
        request are the two that most need their answer understood -- 503 when
        the connection cap is reached, and 413 for an oversized body -- and
        both arrived at the client as a dropped connection until this existed.
        """
        try:
            c.sock.shutdown(getattr(socket, "SHUT_WR", 1))
        except Exception:
            pass                # not every MicroPython port has shutdown()
        c.linger_until = _add(_ticks(), LINGER_MS)
        c.state = "linger"

    def _linger(self, c):
        """Read and discard until the client hangs up, or the budget ends."""
        for _ in range(4):
            data = self._recv(c, 512)
            if data is None:                # EOF: the client has let go
                c.state = "done"
                return
            if not data:
                break
        if _diff(_ticks(), c.linger_until) >= 0:
            c.state = "done"


def _size(path):
    """File size, or None if it is not a readable file."""
    try:
        import os
        st = os.stat(path)
        # S_IFDIR: a directory is not something we can send.
        if st[0] & 0x4000:
            return None
        return st[6]
    except OSError:
        return None
