"""Over-the-air updates for the application files, with automatic rollback.

Transferring files is the easy half. The half that matters is not bricking a
device you need a ladder to reach, so the whole design is shaped by one
question: what happens when the code we just installed does not come up?

    0. Verify manifest.json against manifest.sig (HMAC with ota.key, which
       only arrives over USB) and refuse a build older than the last one.
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
# bytes it is about to write. Staging writes every file as `name.new` while
# the original is still there, so a batch needs its own size free before the
# install step can begin.
#
# THIS IS A GUARD, NOT THE EXPLANATION, and the difference was measured on
# 2026-11-03 rather than assumed. docs/TODO.md recorded a ceiling around
# 60 kB -- ~47, ~48 and ~62 kB installed; ~98 and ~106 kB did not -- and
# attributed it to space. The board has 596 kB of flash free and 325 kB of
# heap free while running. Neither number is anywhere near a constraint at
# 106 kB, so the failures were not room.
#
# The check stays because it costs one subtraction and turns the one failure
# it does cover -- a batch that would fill the filesystem -- into a sentence
# instead of a silence. The ceiling itself is closed: it was r.content
# asking for one contiguous allocation the size of the file, and the download
# below streams in CHUNK reads so it never asks. An 88 kB main.py has been
# installed over the air. Do not reopen this as a size limit.
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
PROTECTED = ("boot.py", "secrets.py", "settings.json", "identity.py", PENDING,
             "ota.py", "ota.key", "ota_seq")

# -- who may update this device ---------------------------------------------
#
# The manifest used to be trusted because it arrived. It names a sha256 for
# every file, but it comes from the same plain-HTTP server as the files, so
# whoever could answer that request -- or publish `update <url>` on the
# command topic, which every broker account that can press Boost can do --
# chose the code this device runs. (Security audit 2026-11-18, NV-2/NV-3.)
#
# Now the manifest must carry an HMAC-SHA256 made with a key only the owner's
# build machine and this device hold. The key is ota.key, a file that is
# copied over USB and that an update can neither replace nor read out: it is
# PROTECTED, it is not in the static root, and nothing serves it. Without it
# this device refuses every update rather than accepting unsigned ones,
# because "verify unless the key is missing" is a check an attacker turns off
# by deleting a file.
#
# HMAC rather than a public-key signature on purpose: Ed25519 in pure
# MicroPython takes long enough to starve the 8 s watchdog, hashlib.sha256 is
# built in, and the only party that signs is the same machine that already
# holds secrets.py. A stolen build machine is game over either way.
#
# `seq` stops a replay. Signed builds are still served over plain HTTP, so an
# old one can be captured and offered again later; a manifest whose seq is
# below the last one installed is refused. ota_seq is PROTECTED for the same
# reason ota.key is.
KEY_FILE = "ota.key"
SEQ_FILE = "ota_seq"
SIG = "manifest.sig"
SIG_DOMAIN = b"smartfan-ota-v1\n"      # a key signs nothing else by accident
MANIFEST_MAX = 16384                    # the real one is ~2 kB


def _hmac_sha256(key, msg):
    """RFC 2104 by hand; MicroPython ships hashlib but not hmac."""
    import hashlib
    if len(key) > 64:
        key = hashlib.sha256(key).digest()
    key = key + b"\x00" * (64 - len(key))
    inner = hashlib.sha256(bytes(b ^ 0x36 for b in key))
    inner.update(msg)
    outer = hashlib.sha256(bytes(b ^ 0x5C for b in key))
    outer.update(inner.digest())
    return outer.digest()


def _same(a, b):
    """Compare without stopping at the first differing byte."""
    if len(a) != len(b):
        return False
    d = 0
    for x, y in zip(a, b):
        d |= x ^ y
    return d == 0


def load_key():
    """The device's OTA key as bytes, or None if it was never installed."""
    import binascii
    try:
        with open(KEY_FILE) as f:
            k = binascii.unhexlify(f.read().strip())
    except (OSError, ValueError):
        return None
    return k if len(k) >= 16 else None


def sign(key, manifest_bytes):
    """Hex signature over the exact manifest bytes. Shared with the host."""
    import binascii
    return binascii.hexlify(
        _hmac_sha256(key, SIG_DOMAIN + manifest_bytes)).decode()


def verify(key, manifest_bytes, sig_hex):
    try:
        want = sign(key, manifest_bytes).encode()
        got = sig_hex.strip().lower().encode() if isinstance(sig_hex, str) \
            else bytes(sig_hex).strip().lower()
    except Exception:
        return False
    return _same(want, got)


def last_seq():
    d = _read_json(SEQ_FILE)
    try:
        return int(d["seq"]) if d else 0
    except (KeyError, TypeError, ValueError):
        return 0


def _plain_name(name):
    """A bare file name in the root: no directories, no tricks."""
    return (isinstance(name, str) and name and "/" not in name
            and "\\" not in name and ".." not in name
            and not name.startswith("."))


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

def _rollback(files, log, why):
    """Put every backed-up file back, drop the marker, and reset.

    Tolerant of a partial set on purpose: a file with no `.bak` was never
    swapped, so it is already the old one and skipping it is correct. That
    is what lets an interrupted swap be undone with the same code as a
    failed boot. Any `.new` left lying about goes too, so the next push
    plans against the files that are actually there.
    """
    log("ota: %s, rolling back %d file(s)" % (why, len(files)))
    for name in files:
        bak = name + ".bak"
        if _exists(bak):
            _remove(name)
            os.rename(bak, name)
            log("ota:   restored %s" % name)
        _remove(name + ".new")
    _remove(PENDING)
    log("ota: rolled back, resetting")
    import machine
    machine.reset()


def boot_check(log=print):
    """Roll back if the last update failed to come up. Never raises."""
    try:
        pend = _read_json(PENDING)
        if not pend:
            return False

        tries = pend.get("tries", 0) + 1
        files = pend.get("files", [])

        # An interrupted swap is never trusted, and never gets an attempt.
        #
        # The marker is written before the first rename, so this flag means
        # power was lost somewhere inside the swap loop. The file set is
        # then arbitrary: some new, some old, and -- in the instant between
        # the two renames for one file -- one of them missing outright. If
        # that one were main.py the board would drop to the REPL at import,
        # unreachable, with nothing left to roll it back.
        #
        # Validating it would be worse than rolling back, because a
        # half-swapped set that happens to run would be COMMITTED after
        # COMMIT_S, making the mixture permanent.
        if pend.get("swapping"):
            _rollback(files, log, "install was interrupted mid-swap")
            return True

        if tries > MAX_TRIES:
            _rollback(files, log, "attempt %d failed" % (tries - 1))
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
        self.seq = None        # the verified build number

    def _fetch_small(self, requests, name):
        """A small file, streamed and capped. None (after _fail) on error.

        Streamed like the big ones, so nothing here asks for an allocation
        the server chose the size of.
        """
        r = requests.get("%s/%s" % (self.base, name), timeout=10)
        if r.status_code != 200:
            r.close()
            self._fail("%s http %d" % (name, r.status_code))
            return None
        buf = b""
        while True:
            b = r.raw.read(CHUNK)
            if not b:
                break
            buf += b
            if len(buf) > MANIFEST_MAX:
                r.close()
                self._fail("%s larger than %d B" % (name, MANIFEST_MAX))
                return None
        r.close()
        return buf

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
            # The key is checked before the radio is spent: with no key,
            # there is no manifest this device could accept.
            key = load_key()
            if key is None:
                return self._fail("refused: no %s on the device, so no "
                                  "manifest can be verified (copy it over "
                                  "USB with ota.py)" % KEY_FILE)

            raw = self._fetch_small(requests, MANIFEST)
            if raw is None:
                return False
            sig = self._fetch_small(requests, SIG)
            if sig is None:
                return False
            # Verified as bytes, before json ever sees them: what is parsed
            # is exactly what was signed.
            if not verify(key, raw, sig.decode("utf-8", "replace")):
                return self._fail("refused: manifest signature does not "
                                  "match this device's key")
            man = json.loads(raw)
            del raw
            try:
                seq = int(man["seq"])
            except (KeyError, TypeError, ValueError):
                return self._fail("refused: signed manifest has no seq")
            if seq < last_seq():
                return self._fail("refused: build %d is older than the "
                                  "installed %d" % (seq, last_seq()))
            self.seq = seq

            want = []
            for name, meta in man.get("files", {}).items():
                if name in PROTECTED or not _plain_name(name) \
                        or name.endswith((".bak", ".new")):
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
                if self.seq > last_seq():
                    _write_json(SEQ_FILE, {"seq": self.seq})
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
                r.close()
                return self._fail("%s http %d" % (name, r.status_code))

            # STRAIGHT FROM THE SOCKET TO FLASH, CHUNK at a time.
            #
            # This used to be `data = r.content`, which buffers the entire
            # body before a byte reaches the filesystem -- and that needs
            # one CONTIGUOUS allocation the size of the file. It is what
            # actually capped an update at somewhere around 60 kB while
            # 596 kB of flash and 325 kB of heap sat free: ~47, ~48 and
            # ~62 kB installed, ~98 and ~106 kB did not. The comment at
            # MIN_FREE named fragmentation as the likeliest cause and left
            # it open; this closes it by never asking for the run at all.
            #
            # It matters because main.py is 87 kB on its own, which sits in
            # the untested gap between the largest success and the smallest
            # failure. A controller that cannot replace its own main.py
            # over the air is a controller that needs a ladder for every
            # change, and this one lives in a bathroom ceiling.
            #
            # Bounded by the manifest's declared size rather than read to
            # EOF, so a connection that stays open after the body does not
            # hang the loop, and a body that arrives short is caught here
            # rather than as a checksum mismatch that says nothing useful.
            want = meta.get("size")
            n = 0
            try:
                with open(name + ".new", "wb") as f:
                    while want is None or n < want:
                        b = r.raw.read(CHUNK)
                        if not b:
                            break
                        f.write(b)
                        n += len(b)
            except Exception as e:
                r.close()
                return self._fail("%s download failed (%s)" % (name, e))
            r.close()
            gc.collect()

            if want is not None and n != want:
                return self._fail("%s short: %d of %d bytes" % (name, n, want))
            got = _sha(name + ".new")
            if got != meta["sha"]:
                return self._fail("%s checksum mismatch" % name)

            self._staged.append(name)
            self._queue.pop(0)
            self._log("ota: staged %s (%d bytes)" % (name, n))
            return True

        # Everything verified. Swap in one pass so a reset mid-update cannot
        # leave a half-new, half-old set of files.
        #
        # The marker goes down BEFORE the first rename, not after the last.
        # Written afterwards it documented a swap that had already finished,
        # which is the one case that needs no documenting: a power cut
        # inside the loop left no marker at all, so boot_check returned
        # False, armed no watchdog and rolled nothing back. In the instant
        # between a file's two renames it does not exist, and if that file
        # were main.py the board would drop to the REPL at import with
        # nothing left able to rescue it. That is a cable job on a device
        # whose whole point is not needing one.
        self.status = "installing"
        # Recorded before the swap, and kept even if this build rolls back:
        # a build that failed is not one to be talked into again by
        # whoever captured it. The owner's next publish has a higher seq.
        if self.seq is not None and self.seq > last_seq():
            _write_json(SEQ_FILE, {"seq": self.seq})
        _write_json(PENDING, {"tries": 0, "files": self._staged,
                              "swapping": True})
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
