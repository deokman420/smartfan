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
    def __init__(self, body, status=200):
        self._body = body
        self.status_code = status

    def json(self):
        import json
        return json.loads(self._body)

    @property
    def content(self):
        return self._body if isinstance(self._body, bytes) else self._body.encode()

    def close(self):
        pass


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

ota.free_bytes = real_free
os.chdir(ROOT)

failed = results.count(False)
print("\n%d/%d passed\n" % (len(results) - failed, len(results)))
sys.exit(1 if failed else 0)
