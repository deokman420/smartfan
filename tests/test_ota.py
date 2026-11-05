"""The updater's planning step, on the host, with no radio and no board.

    python tests/test_ota.py

ota.py had no test, which is uncomfortable for the one module that is
deliberately excluded from its own update mechanism -- it is the recovery
path, so a broken one cannot be fixed remotely.

What this covers is the half of docs/TODO.md's OTA entry that matters most:
a batch too large to stage used to be discovered by dying between staging
and installing, and the error went only to a serial console nobody was
holding. From the network the symptom was a device that re-downloaded the
same files on every push, never reset, and kept running the old code.
Measured: ~47 kB installed, ~48 kB installed, ~62 kB installed, ~98 kB did
not, ~106 kB did not.

So the arithmetic now happens before the radio is spent, and these tests
are about that decision rather than about the download.
"""

import os
import sys
import tempfile
import io
import types

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

results = []


def check(name, ok, detail=""):
    results.append(ok)
    print("  %-56s %s %s" % (name, "PASS" if ok else "FAIL", detail))


# -- a requests module that answers from a dict, and counts what was asked --

class FakeResponse:
    """A response that can only be STREAMED, never buffered whole.

    `content` deliberately raises. Buffering the body is what capped an
    update at around 60 kB -- it needs one contiguous allocation the size
    of the file -- and main.py is 87 kB, so reintroducing it would quietly
    put the fan back out of reach of its own updater. A test that merely
    checked the bytes arrived would not notice; this makes it a failure.
    """

    def __init__(self, body, status=200):
        self._body = body if isinstance(body, bytes) else body.encode()
        self.status_code = status
        self.raw = io.BytesIO(self._body)
        self.closed = False

    def json(self):
        import json
        return json.loads(self._body)

    @property
    def content(self):
        raise AssertionError(
            "ota.py buffered a whole body again; stream r.raw instead")

    def close(self):
        self.closed = True


class FakeRequests:
    def __init__(self, routes):
        self.routes = routes
        self.asked = []

    def get(self, url, timeout=None):
        self.asked.append(url)
        for suffix, body in self.routes.items():
            if url.endswith(suffix):
                return FakeResponse(body)
        return FakeResponse("", 404)


def install_requests(routes):
    fake = FakeRequests(routes)
    mod = types.ModuleType("requests")
    mod.get = fake.get
    sys.modules["requests"] = mod
    return fake


import ota  # noqa: E402  (after sys.path)


def manifest(files):
    import json
    return json.dumps({"files": files})


def drive(up, limit=12):
    """step() until it finishes or the limit, returning the step count."""
    for i in range(limit):
        if not up.step() or up.done:
            return i + 1
    return limit


# -- free_bytes ------------------------------------------------------------
#
# os.statvfs is Unix-only, so on Windows this must answer None rather than
# raise. A planner that throws where it cannot measure would make the whole
# update path platform-specific.

fb = ota.free_bytes()
check("free_bytes answers a number or None, never raises",
      fb is None or isinstance(fb, int), repr(fb))

# -- the batch that cannot fit --------------------------------------------

work = tempfile.mkdtemp()
os.chdir(work)
with open("already.py", "w") as f:
    f.write("x = 1\n")

real_free = ota.free_bytes
ota.free_bytes = lambda: 30000

req = install_requests({
    ota.MANIFEST: manifest({
        "big.py": {"sha": "deadbeef", "size": 60000},
    }),
})
up = ota.Updater("http://host:8000", log=lambda *a: None)
drive(up)
check("a batch larger than free flash is refused",
      up.done and not up.ok, "%s / %s" % (up.done, up.ok))
check("...before a single body is downloaded",
      len(req.asked) == 1 and req.asked[0].endswith(ota.MANIFEST),
      "asked for %d url(s)" % len(req.asked))
check("...and the refusal names both numbers",
      "60000" in up.status and "30000" in up.status, up.status)

# The margin is load-bearing: a batch that exactly fills the disk leaves the
# history and the log with nowhere to go.
ota.free_bytes = lambda: 60000 + ota.MIN_FREE - 1
req = install_requests({ota.MANIFEST: manifest(
    {"big.py": {"sha": "deadbeef", "size": 60000}})})
up = ota.Updater("http://host:8000", log=lambda *a: None)
drive(up)
check("a batch that would leave less than MIN_FREE is refused too",
      up.done and not up.ok, up.status)

# -- the batch that fits ---------------------------------------------------

ota.free_bytes = lambda: 60000 + ota.MIN_FREE + 1
req = install_requests({
    ota.MANIFEST: manifest({"big.py": {"sha": "deadbeef", "size": 60000}}),
    "big.py": "x = 2\n",
})
up = ota.Updater("http://host:8000", log=lambda *a: None)
up.step()
check("a batch that fits is planned, not refused",
      not up.done and up.need == 60000, "need=%s done=%s" % (up.need, up.done))

# -- an older manifest, with no sizes in it -------------------------------
#
# The publisher has written `size` for a while, but a device may be pointed
# at an older build server. Refusing to update because the manifest is a
# format behind would be a worse failure than the one being fixed.

ota.free_bytes = lambda: 1000
req = install_requests({
    ota.MANIFEST: manifest({"big.py": {"sha": "deadbeef"}}),
    "big.py": "x = 3\n",
})
up = ota.Updater("http://host:8000", log=lambda *a: None)
up.step()
check("a manifest without sizes is planned anyway, not refused",
      not up.done and up.need == 0, "need=%s status=%r" % (up.need, up.status))

# -- what never gets queued ------------------------------------------------

ota.free_bytes = lambda: 10_000_000
req = install_requests({ota.MANIFEST: manifest({
    "secrets.py": {"sha": "x", "size": 10},
    "boot.py": {"sha": "x", "size": 10},
    "settings.json": {"sha": "x", "size": 10},
    "thing.py.bak": {"sha": "x", "size": 10},
    "new.py": {"sha": "x", "size": 10},
})})
up = ota.Updater("http://host:8000", log=lambda *a: None)
up.step()
queued = sorted(n for n, _ in up._queue)
check("protected files and backups are never queued",
      queued == ["new.py"], "%s" % queued)

# A file already matching its published hash is not re-fetched. This is what
# makes a repeated push cheap, and it is also why a silent install failure
# looked like a loop: the same files qualified every time.
sha = ota._sha("already.py")
req = install_requests({ota.MANIFEST: manifest({
    "already.py": {"sha": sha, "size": 10},
})})
up = ota.Updater("http://host:8000", log=lambda *a: None)
drive(up)
check("a file already at the published hash is skipped",
      up.done and up.ok and "up to date" in up.status, up.status)

# -- the swap, and surviving an interruption inside it ---------------------
#
# The marker used to be written AFTER the rename loop, which documented a
# swap that had already finished -- the one case needing no documentation.
# A power cut inside the loop left no marker, so boot_check returned False,
# armed no watchdog and rolled nothing back. And in the instant between a
# file's two renames the file does not exist at all: if that file were
# main.py the board drops to the REPL at import, unreachable, with nothing
# left able to rescue it. That is the exact outcome this module exists to
# prevent, so it gets a test.


RESETS = []


def install_machine():
    """A machine module that records a reset instead of performing one.

    Not one that raises: boot_check wraps everything in `except Exception`
    on purpose, so that it can never be the reason a board fails to boot.
    An exception-based stub is therefore swallowed, and every assertion
    about rolling back would pass for the wrong reason.
    """
    mod = types.ModuleType("machine")
    mod.reset = lambda: RESETS.append(1)

    class WDT:
        def __init__(self, timeout=0):
            pass

    mod.WDT = WDT
    sys.modules["machine"] = mod
    return mod


install_machine()
ota.free_bytes = lambda: 10_000_000


def stage(names, bodies=None):
    """A device mid-update: originals on disk, verified .new beside them."""
    for n in names:
        with open(n, "w") as f:
            f.write("old %s" % n)
        with open(n + ".new", "w") as f:
            f.write((bodies or {}).get(n, "new %s" % n))


def run_install(names, break_after=None):
    """Drive just the install step, optionally dying partway through it."""
    up = ota.Updater("http://host:8000", log=lambda *a: None)
    up._queue = []
    up._staged = list(names)
    real_rename = os.rename
    calls = [0]

    def counted(a, b):
        calls[0] += 1
        if break_after is not None and calls[0] > break_after:
            raise OSError("power cut")
        return real_rename(a, b)

    os.rename = counted
    try:
        up.step()
    finally:
        os.rename = real_rename
    return up


import json as _json  # noqa: E402

# A clean install leaves a marker with no swapping flag: nothing to undo.
ota._remove(ota.PENDING)
stage(["a.py", "b.py"])
up = run_install(["a.py", "b.py"])
pend = _json.load(open(ota.PENDING))
check("a completed install leaves a marker that is not mid-swap",
      pend.get("swapping") is None and pend["files"] == ["a.py", "b.py"],
      "%s" % pend)
check("...and the new files are in place",
      open("a.py").read().startswith("new"), open("a.py").read().strip())

# Now the failure. Break after the first rename of the second file, which
# is the instant where b.py has become b.py.bak and nothing is named b.py.
ota._remove(ota.PENDING)
for n in ("a.py", "b.py"):
    ota._remove(n + ".bak")
stage(["a.py", "b.py"])
up = run_install(["a.py", "b.py"], break_after=3)
check("a cut mid-swap really does leave a file missing",
      not ota._exists("b.py"), "b.py present after the cut")
pend = _json.load(open(ota.PENDING)) if ota._exists(ota.PENDING) else None
check("...but the marker is already on disk, flagged mid-swap",
      pend is not None and pend.get("swapping") is True,
      "%s" % pend if pend else
      "NO MARKER - boot_check will return False and rescue nothing")

# And boot_check must undo it at once, without spending an attempt on code
# that is a mixture of two builds.
del RESETS[:]
ota.boot_check(log=lambda *a: None)
check("boot_check rolls an interrupted swap straight back",
      ota._exists("b.py") and open("b.py").read().startswith("old"),
      open("b.py").read().strip() if ota._exists("b.py") else "missing")
check("...restoring the files that HAD swapped, not just the broken one",
      open("a.py").read().startswith("old"), open("a.py").read().strip())
check("...clearing the marker so the next boot is ordinary",
      not ota._exists(ota.PENDING))
check("...and sweeping the staged copies, so a retry plans afresh",
      not ota._exists("a.py.new") and not ota._exists("b.py.new"))

# The ordinary pending path is unchanged: first boot validates, it does not
# roll back. Breaking that would undo every good update.
ota._write_json(ota.PENDING, {"tries": 0, "files": ["a.py"]})
del RESETS[:]
ota.boot_check(log=lambda *a: None)
check("a normal pending update still gets its attempts",
      not RESETS and _json.load(open(ota.PENDING))["tries"] == 1,
      "resets=%d" % len(RESETS))

ota._write_json(ota.PENDING, {"tries": ota.MAX_TRIES, "files": ["a.py"]})
del RESETS[:]
ota.boot_check(log=lambda *a: None)
check("...and still rolls back once they are spent",
      len(RESETS) == 1 and not ota._exists(ota.PENDING),
      "resets=%d" % len(RESETS))

ota.free_bytes = real_free
os.chdir(ROOT)


# -- downloading, which nothing tested ------------------------------------
#
# Found 2026-11-05 while asking whether the fan could still update itself
# from a ceiling. Every test above either plans a batch and refuses it, or
# stages .new files by hand and installs them. Not one went through the
# download, so `data = r.content` could be put back with the whole suite
# still green -- and that single line is what capped an update at roughly
# 60 kB, because buffering a body needs one CONTIGUOUS allocation the size
# of the file. main.py is 87 kB.
#
# FakeResponse.content now raises for that reason. These drive the real
# download step so the raise can actually be reached.

import hashlib as _hashlib

os.chdir(tempfile.mkdtemp())
ota.free_bytes = lambda: 10_000_000


def published(bodies):
    """A manifest plus bodies, served to a real Updater."""
    files = {}
    routes = {}
    for name, body in bodies.items():
        raw = body if isinstance(body, bytes) else body.encode()
        files[name] = {"sha": _hashlib.sha256(raw).hexdigest(), "size": len(raw)}
        routes[name] = raw
    routes[ota.MANIFEST] = _json.dumps({"files": files})
    return install_requests(routes)


BODY = b"# a real module\nVALUE = 1\n" * 40

req = published({"mod_a.py": BODY})
up = ota.Updater("http://host:8000", log=lambda *a: None)
drive(up)
check("a published file is downloaded and staged",
      os.path.exists("mod_a.py.new") or os.path.exists("mod_a.py"),
      "staged=%s installed=%s" % (os.path.exists("mod_a.py.new"),
                                  os.path.exists("mod_a.py")))
landed = "mod_a.py" if os.path.exists("mod_a.py") else "mod_a.py.new"
check("...with the bytes intact end to end",
      io.open(landed, "rb").read() == BODY,
      "%d bytes" % os.path.getsize(landed))
check("...and the update reports success", up.done and up.ok,
      "done=%s ok=%s" % (up.done, up.ok))

# THE REGRESSION GUARD. 87 kB is main.py's real size and sits between the
# largest body that ever installed (~62 kB) and the smallest that did not
# (~98 kB). A streamed download does not care; a buffered one is exactly
# where it used to stop.
os.chdir(tempfile.mkdtemp())
BIG = (b"# padding to make this bigger than the old ceiling\n" * 2400)
check("the regression body really is past the old ceiling",
      len(BIG) > 98_000, "%d bytes" % len(BIG))
req = published({"big_mod.py": BIG})
up = ota.Updater("http://host:8000", log=lambda *a: None)
drive(up, limit=40)
landed = "big_mod.py" if os.path.exists("big_mod.py") else "big_mod.py.new"
check("a body far past the old 60 kB ceiling downloads whole",
      os.path.exists(landed) and io.open(landed, "rb").read() == BIG,
      "%d bytes" % (os.path.getsize(landed) if os.path.exists(landed) else -1))
check("...in CHUNK-sized reads, never one allocation",
      ota.CHUNK <= 4096, "CHUNK=%d" % ota.CHUNK)

# A SHORT BODY must be named as short. It used to be impossible to tell
# from a checksum mismatch, which says only "these bytes are not those
# bytes" and sends you looking at the publisher.
os.chdir(tempfile.mkdtemp())
full = b"abcdefghij" * 50
routes = {
    "cut.py": full[:120],                      # the server lies about length
    ota.MANIFEST: _json.dumps({"files": {
        "cut.py": {"sha": _hashlib.sha256(full).hexdigest(), "size": len(full)},
    }}),
}
install_requests(routes)
up = ota.Updater("http://host:8000", log=lambda *a: None)
said = []
up._log = lambda *a: said.append(" ".join(str(x) for x in a))
drive(up, limit=40)
check("a truncated body is refused", up.done and not up.ok,
      "done=%s ok=%s" % (up.done, up.ok))
check("...and is called short, not a checksum mismatch",
      any("short" in s for s in said), "; ".join(said)[-90:])
check("...leaving nothing staged to be installed later",
      not os.path.exists("cut.py"), "cut.py exists")

# A 404 mid-batch must fail cleanly rather than stage a page of HTML.
os.chdir(tempfile.mkdtemp())
install_requests({ota.MANIFEST: _json.dumps({"files": {
    "gone.py": {"sha": "0" * 64, "size": 10},
}})})
up = ota.Updater("http://host:8000", log=lambda *a: None)
drive(up, limit=40)
check("a missing file fails the batch", up.done and not up.ok,
      "done=%s ok=%s" % (up.done, up.ok))
check("...and installs nothing", not os.path.exists("gone.py"))

failed = results.count(False)
print("\n%d/%d passed\n" % (len(results) - failed, len(results)))
sys.exit(1 if failed else 0)
