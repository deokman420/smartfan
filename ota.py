"""Over-the-air updates for the application files, with automatic rollback.

Transferring files is the easy half. The half that matters is not bricking a
device you need a ladder to reach, so the whole design is shaped by one
question: what happens when the code we just installed does not come up?

    1. Download each file to <name>.new and verify its sha256.
    2. Only once ALL files verify, swap them in, keeping <name>.bak.
    3. Write ota_pending and reset.
    4. boot.py calls boot_check() BEFORE main.py runs. If ota_pending shows a
       previous attempt already failed, it restores the .bak files and resets.
    5. If the new code runs healthily for COMMIT_S, it calls commit(), which
       deletes ota_pending and the backups.

The failure that needs catching is main.py raising at import: MicroPython
then drops to the REPL and simply sits there, unreachable over the network
and with no second boot to trigger a rollback. So boot_check() arms a
watchdog whenever an update is pending. Healthy code takes over feeding it;
broken code does not, the board resets, and the attempt counter reaches its
limit and rolls back. A boot loop that repairs itself in about twenty
seconds.

boot.py is never updated over the air. It is the recovery path, and a
recovery path you can break remotely is not one.
"""

import gc
import json
import os

MANIFEST = "manifest.json"
PENDING = "ota_pending"
MAX_TRIES = 2          # attempts before rolling back
COMMIT_S = 60          # healthy running time before an update is kept

# Spare flash a batch must leave behind after staging, over and above the
# bytes it is about to write.
#
# Staging writes every file as `name.new` while the original is still there,
# so a batch needs its own size free before the install step can begin.
# Measured on this device: ~47 kB installed, ~48 kB installed, ~62 kB
# installed, ~98 kB did not, ~106 kB did not. The failures staged every file
# and then died, and said so only to a serial console nobody was holding --
# from the network the symptom was a device that re-downloaded the same
# files on every push and kept running the old code.
#
# So the arithmetic is done BEFORE anything is downloaded, and a batch that
# cannot fit is refused in a sentence that names both numbers. That is not
# the whole fix -- streaming each body to its file instead of buffering it,
# and staging one file at a time, is what would remove the ceiling -- but a
# failure you can read beats one you have to bisect.
MIN_FREE = 24576       # 24 kB, room for history and a log to keep working


def free_bytes():
    """Free flash in bytes, or None where the question has no answer."""
    try:
        s = os.statvfs("/")
        return s[0] * s[3]
    except Exception:
        return None
CHUNK = 512

# Never replaced over the air: boot.py is what rescues a bad update, and
# secrets/settings are this device's own, not the build's.
# Never replaced or removed by an update. boot.py and secrets.py are the
# recovery path and the credentials; settings.json and identity.py are what
# this particular device is -- its tuning and its name -- as opposed to what
# the product is.
PROTECTED = ("boot.py", "secrets.py", "settings.json", "identity.py", PENDING)


def _exists(name):
    try:
        os.stat(name)
        return True
    except OSError:
        return False


def _remove(name):
    try:
        os.remove(name)
    except OSError:
        pass


def _read_json(name):
    try:
        with open(name) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _write_json(name, obj):
    with open(name, "w") as f:
        json.dump(obj, f)


def _sha(name):
    import hashlib
    import binascii
    h = hashlib.sha256()
    try:
        with open(name, "rb") as f:
            while True:
                b = f.read(CHUNK)
                if not b:
                    break
                h.update(b)
    except OSError:
        return None
    return binascii.hexlify(h.digest()).decode()


# ---------------------------------------------------------------------------
# boot path -- runs before main.py, from boot.py
# ---------------------------------------------------------------------------

def boot_check(log=print):
    """Roll back if the last update failed to come up. Never raises."""
    try:
        pend = _read_json(PENDING)
        if not pend:
            return False

        tries = pend.get("tries", 0) + 1
        files = pend.get("files", [])

        if tries > MAX_TRIES:
            log("ota: attempt %d failed, rolling back %d file(s)"
                % (tries - 1, len(files)))
            for name in files:
                bak = name + ".bak"
                if _exists(bak):
                    _remove(name)
                    os.rename(bak, name)
                    log("ota:   restored %s" % name)
            _remove(PENDING)
            log("ota: rolled back, resetting")
            import machine
            machine.reset()
            return True

        pend["tries"] = tries
        _write_json(PENDING, pend)
        log("ota: validating update, attempt %d of %d" % (tries, MAX_TRIES))

        # Healthy code takes over feeding this within seconds. Code that
        # cannot even import will not, and the reset it causes is what
        # advances the counter toward a rollback.
        from machine import WDT
        WDT(timeout=8000)
        return True
    except Exception as e:                       # never block the boot
        try:
            log("ota: boot_check failed (%s)" % e)
        except Exception:
            pass
        return False


def pending():
    return _exists(PENDING)


def commit(log=print):
    """Keep the update: drop the marker and the backups."""
    pend = _read_json(PENDING)
    if not pend:
        return False
    for name in pend.get("files", []):
        _remove(name + ".bak")
    _remove(PENDING)
    log("ota: update committed")
    return True


# ---------------------------------------------------------------------------
# update path -- driven one step per control-loop pass
# ---------------------------------------------------------------------------

class Updater:
    """Stepwise updater. step() does at most one unit of work and returns.

    One file per pass keeps the rule that only the control loop feeds the
    watchdog: a download that hangs stops the loop, and the reset that
    follows is the correct outcome rather than something to be papered over.
    """

    def __init__(self, base_url, log=print):
        self.base = base_url.rstrip("/")
        self._log = log
        self.done = False
        self.ok = False
        self.status = "starting"
        self._queue = None
        self._staged = []
        self.need = 0          # bytes this batch will stage
        self.free = None       # bytes free when it was planned

    def _fail(self, why):
        self.status = why
        self.done, self.ok = True, False
        self._log("ota: %s" % why)
        for name in self._staged:
            _remove(name + ".new")
        return False

    def step(self):
        try:
            return self._step()
        except Exception as e:
            return self._fail("error: %s" % e)

    def _step(self):
        import requests

        if self._queue is None:
            self.status = "fetching manifest"
            self._log("ota: %s from %s" % (self.status, self.base))
            r = requests.get(self.base + "/" + MANIFEST, timeout=10)
            if r.status_code != 200:
                return self._fail("manifest http %d" % r.status_code)
            man = r.json()
            r.close()

            want = []
            for name, meta in man.get("files", {}).items():
                if name in PROTECTED or name.endswith((".bak", ".new")):
                    continue
                if _sha(name) != meta["sha"]:
                    want.append((name, meta))
            self._queue = want
            gc.collect()

            # Refuse a batch that cannot fit, before spending the radio on
            # it. The old code found this out by dying between staging and
            # installing, which looked from outside like nothing happening
            # at all.
            self.need = sum(m.get("size", 0) for _, m in want)
            self.free = free_bytes()
            if want and self.free is not None and self.need:
                if self.free < self.need + MIN_FREE:
                    return self._fail(
                        "batch needs %d B staged with %d B free, short by "
                        "%d B - push fewer files at once"
                        % (self.need, self.free,
                           self.need + MIN_FREE - self.free))
            if not want:
                self.status = "already up to date"
                self.done, self.ok = True, True
                self._log("ota: %s" % self.status)
                return True
            self._log("ota: %d file(s), %d B to stage, %s B free: %s"
                      % (len(want), self.need,
                         "?" if self.free is None else self.free,
                         ", ".join(n for n, _ in want)))
            return True

        if self._queue:
            name, meta = self._queue[0]
            self.status = "downloading %s" % name
            r = requests.get("%s/%s" % (self.base, name), timeout=15)
            if r.status_code != 200:
                return self._fail("%s http %d" % (name, r.status_code))
            data = r.content
            r.close()
            gc.collect()

            with open(name + ".new", "wb") as f:
                f.write(data)
            got = _sha(name + ".new")
            if got != meta["sha"]:
                return self._fail("%s checksum mismatch" % name)

            self._staged.append(name)
            self._queue.pop(0)
            self._log("ota: staged %s (%d bytes)" % (name, len(data)))
            return True

        # Everything verified. Swap in one pass so a reset mid-update cannot
        # leave a half-new, half-old set of files.
        self.status = "installing"
        for name in self._staged:
            if _exists(name):
                _remove(name + ".bak")
                os.rename(name, name + ".bak")
            os.rename(name + ".new", name)
        _write_json(PENDING, {"tries": 0, "files": self._staged})
        self.status = "installed %d file(s), resetting" % len(self._staged)
        self.done, self.ok = True, True
        self._log("ota: %s" % self.status)
        return True
