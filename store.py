"""A week of readings on flash, so history does not live in one browser.

Without Home Assistant there is no recorder, and the app's own history is a
rolling 24 h in localStorage -- which means a phone that opens the app for
the first time opens it on an empty chart, and clearing site data throws the
week away. That is the honest cost the app documents, and this is the part of
it the device can fix.

What is kept is deliberately not what is published. Telemetry goes out every
30 s; keeping that for a week would be 20,000 rows and a flash write every
half minute. Instead each bucket is a **five-minute mean**, written once,
which is 2,016 rows for seven days and 288 writes a day.

Two files, alternating, rather than one file rewritten in place:

    hist.0, hist.1     append-only, newest file last written

Appending a line is one small write. When the newer file is full the older
one is truncated and becomes the new target, so the pair holds between one
half and one full window and no record is ever rewritten. The failure mode
is bounded and obvious: lose power mid-append and the last line is short,
which parse() skips. A single file rewritten in place would put every record
at risk on every write, to save half the flash of something that fits
comfortably anyway.

Row order matches the app's `F` field indices in app/src/lib/history.js, so
a backfill drops straight into the same buffer the live stream feeds. The
order is append-only for the same reason it is there: changing it silently
reinterprets every row already on a device.
"""

import time

FIELDS = ("t", "rh", "base", "exc", "temp", "dp", "light", "rpm", "fan",
          "lit", "st")

STATE_CODE = {"LEARN": 0, "IDLE": 1, "RUN": 2, "BOOST": 3, "FAULT": 4}

_FILES = ("hist.0", "hist.1")

# The most rows read() will ever hold in memory at once. See its docstring:
# each one costs roughly 890 bytes on the device, so this is about 210 kB
# worst case, and even that is far more than anything should be asking for.
MAX_ROWS = 240


# MicroPython on rp2 counts from 2000-01-01, CPython from 1970. Ask rather
# than assume, so the same rows are written on the device and in the tests.
_OFFSET = 946684800 if time.gmtime(0)[0] == 2000 else 0


def _epoch():
    """Unix seconds, or None if the clock has not been set.

    A row stamped with the RP2350's power-on default lands in 2021 and drags
    every chart axis back four years, so an unsynced clock means no rows at
    all -- a gap the app already knows how to draw.
    """
    if time.localtime()[0] < 2024:
        return None
    return int(time.time()) + _OFFSET


class History:
    """Five-minute means on flash. Every method is safe to call blindly."""

    def __init__(self, cfg, log=None, where=""):
        self._log = log or (lambda *a: None)
        # Where the two files live. Empty means the filesystem root, which is
        # the only place they live on the device -- MicroPython has no working
        # directory to speak of. The tests set it, because a relative path
        # read by a server thread while the test moves the working directory
        # is a bug that looks exactly like an empty database.
        self._where = where.rstrip("/") + "/" if where else ""
        self.interval_s = getattr(cfg, "HISTORY_INTERVAL_S", 300)
        self.days = getattr(cfg, "HISTORY_DAYS", 7)
        # Rows per file: half the window each, so the pair always holds at
        # least the window and never more than twice it.
        self.per_file = max(16, (self.days * 86400 // self.interval_s) // 2)
        self.rows = 0            # rows written since boot, for /api/info
        self.writes = 0
        self.failed = 0

        self._bucket = None      # bucket index currently accumulating
        self._sum = {}
        self._cnt = {}
        self._n = 0
        self._flags = {}
        self._target = self._newest()

    def _path(self, name):
        return self._where + name

    # -- accumulation ------------------------------------------------------

    def note(self, p):
        """Fold one telemetry payload in. Writes a row when a bucket closes.

        Called from the control loop on every sample, so it must be cheap:
        the arithmetic is a running sum, and the only I/O is one line every
        five minutes.
        """
        try:
            now = _epoch()
            if now is None:
                return False
            bucket = now // self.interval_s
            if self._bucket is None:
                self._bucket = bucket
            if bucket == self._bucket:
                self._add(p)
                return False
            # The bucket rolled over. Close the old one, then start the new
            # one with this payload -- dropping it would lose a sample every
            # five minutes, which is one in ten.
            closed = self._close(self._bucket)
            self._bucket = bucket
            self._add(p)
            return self._append(closed) if closed else False
        except Exception as e:
            self.failed += 1
            self._log("history: note (%s)" % e)
            return False

    def _add(self, p):
        for k in ("rh", "base", "exc", "temp", "dp", "light_pc", "rpm"):
            v = p.get(k)
            if v is None:
                continue
            try:
                v = float(v)
            except (TypeError, ValueError):
                continue
            self._sum[k] = self._sum.get(k, 0.0) + v
            self._cnt[k] = self._cnt.get(k, 0) + 1
        # Booleans are ORed, not averaged. A fan that ran for one minute of
        # the five did run, and a mean of 0.2 would read as "off" on a chart
        # whose whole job is to show that it ran.
        if p.get("fan_on"):
            self._flags["fan"] = 1
        if p.get("light_on"):
            self._flags["lit"] = 1
        # The most severe state in the bucket wins, for the same reason.
        code = STATE_CODE.get(p.get("state"))
        if code is not None:
            prev = self._flags.get("st")
            self._flags["st"] = code if prev is None else max(prev, code)
        self._n += 1

    def _close(self, bucket):
        """Turn the accumulator into a row. Returns the row, or None."""
        n = self._n
        sums, cnts, flags = self._sum, self._cnt, self._flags
        self._sum, self._cnt, self._flags, self._n = {}, {}, {}, 0
        if not n:
            return None

        def mean(key, digits=1):
            count = cnts.get(key, 0)
            if not count:
                return None
            return round(sums.get(key, 0.0) / count, digits)

        return [
            bucket * self.interval_s,
            mean("rh"), mean("base"), mean("exc"), mean("temp"), mean("dp"),
            mean("light_pc"), int(mean("rpm", 0) or 0),
            flags.get("fan", 0), flags.get("lit", 0), flags.get("st"),
        ]

    # -- files -------------------------------------------------------------

    def _append(self, row):
        line = ",".join("" if v is None else str(v) for v in row) + "\n"
        try:
            if self._count(self._path(self._target)) >= self.per_file:
                self._target = self._rotate()
            with open(self._path(self._target), "a") as f:
                f.write(line)
            self.rows += 1
            self.writes += 1
            return True
        except OSError as e:
            # A full or failing filesystem must not stop the fan or the API.
            self.failed += 1
            self._log("history: write failed (%s)" % e)
            return False

    def _rotate(self):
        """Truncate the older file and write to it from now on."""
        other = _FILES[1] if self._target == _FILES[0] else _FILES[0]
        try:
            with open(self._path(other), "w"):
                pass
        except OSError as e:
            self._log("history: rotate failed (%s)" % e)
            return self._target
        self._log("history: rotated to %s" % other)
        return other

    @staticmethod
    def _count(path):
        try:
            with open(path) as f:
                return sum(1 for _ in f)
        except OSError:
            return 0

    def _newest(self):
        """Which file to append to after a boot.

        The one with the later last timestamp, because that is the one the
        previous run was filling. Deciding by modification time would be
        wrong: the filesystem has no clock before NTP lands.
        """
        best, best_ts = _FILES[0], -1
        for name in _FILES:
            ts = self._last_ts(self._path(name))
            if ts > best_ts:
                best, best_ts = name, ts
        return best

    @staticmethod
    def _last_ts(path):
        ts = -1
        try:
            with open(path) as f:
                for line in f:
                    head = line.split(",", 1)[0]
                    try:
                        ts = int(head)
                    except ValueError:
                        continue
        except OSError:
            return -1
        return ts

    # -- reading -----------------------------------------------------------

    def read(self, since=0, limit=MAX_ROWS):
        """Rows at or after `since` (unix seconds), oldest first.

        **This costs about 890 bytes of heap per row on the device**, measured
        on an RP2350 -- a parsed row is a list of eleven boxed numbers, and
        they are not cheap. A full week is 2,016 rows, so asking for all of it
        needs roughly 1.8 MB and the board has about 430 kB. It raised
        MemoryError at around 512 rows.

        So `limit` is a real bound now, applied as rows are read rather than
        by trimming a list that has already been built. It used to be the
        latter, which meant the argument that existed to protect memory
        allocated everything first and protected nothing.

        For bulk history, do not call this at all: webapi streams the stored
        files straight out, one line at a time, at constant memory. This stays
        for small windows and for the tests.
        """
        if limit > MAX_ROWS:
            limit = MAX_ROWS
        out = []
        # Older file first. Which that is follows from the rotation: the file
        # we are NOT appending to holds the earlier half.
        older = _FILES[1] if self._target == _FILES[0] else _FILES[0]
        for name in (older, self._target):
            try:
                with open(self._path(name)) as f:
                    for line in f:
                        row = _parse(line)
                        if row is None or row[0] < since:
                            continue
                        out.append(row)
                        # Drop the oldest as we go. During an outage the
                        # recent rows are the ones worth having, which is the
                        # same reasoning ringbuf.py uses.
                        if len(out) > limit:
                            del out[0]
            except OSError:
                continue
        out.sort(key=lambda r: r[0])
        return out

    def paths(self):
        """The two files, oldest first, for something that wants to stream.

        Order matters and is not alphabetical: the file being appended to is
        the newer half, so the other one comes first.
        """
        older = _FILES[1] if self._target == _FILES[0] else _FILES[0]
        return (self._path(older), self._path(self._target))

    def clear(self):
        for name in _FILES:
            try:
                with open(self._path(name), "w"):
                    pass
            except OSError:
                pass
        self._bucket = None
        self._sum, self._cnt, self._flags, self._n = {}, {}, {}, 0
        self._target = _FILES[0]
        self._log("history: cleared")

    def stats(self):
        return {
            "rows": (self._count(self._path(_FILES[0]))
                     + self._count(self._path(_FILES[1]))),
            "interval_s": self.interval_s,
            "days": self.days,
            "writes": self.writes,
            "failed": self.failed,
        }


def _parse(line):
    """One stored line back into a row, or None if it is not one.

    A truncated last line -- power lost mid-append -- is the expected bad
    case, and it must be skipped rather than raised.
    """
    line = line.strip()
    if not line:
        return None
    parts = line.split(",")
    if len(parts) != len(FIELDS):
        return None
    try:
        ts = int(parts[0])
    except ValueError:
        return None
    row = [ts]
    for p in parts[1:]:
        if p == "":
            row.append(None)
        else:
            try:
                row.append(float(p) if "." in p else int(p))
            except ValueError:
                row.append(None)
    return row
