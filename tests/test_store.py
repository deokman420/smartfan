"""The two files that hold the fan's week.

    python tests/test_store.py

Written after mutation testing scored store.py at 31 killed of 98. The
survivors were not boundary trivia: the line that chooses which of the two
files to truncate on rotation could be inverted, and the epoch offset could
be inverted, with every suite still green. Both silently destroy history --
which is the one thing this module exists to keep, and the one failure
nobody notices until they open the charts a week later.

test_httpd.py exercises History through the API, which is the right place
for the API. These are the file mechanics underneath it.
"""
import io
import os
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import store                                           # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append(ok)
    print("  %-56s %s %s" % (name, "PASS" if ok else "FAIL", detail))


class Cfg:
    HISTORY_INTERVAL_S = 300
    HISTORY_DAYS = 7


def hist(tmp, per_file=None):
    h = store.History(Cfg, log=lambda *a: None, where=tmp)
    if per_file is not None:
        # A real attribute, set small so a rotation costs five rows instead
        # of a thousand. Rotation is the behavior under test, not the size.
        h.per_file = per_file
    return h


def lines(path):
    try:
        return [ln for ln in io.open(path, encoding="utf-8").read().split("\n") if ln]
    except OSError:
        return []


def row(ts, fill=0):
    """A full-width row, the way _append expects one.

    `fill` exists because every other field being 0 made a whole class of
    mistake invisible: read() finishes with `out.sort(key=lambda r: r[0])`,
    and sorting by any OTHER column is indistinguishable when every other
    column is the same number. Pass a fill that runs opposite to the
    timestamp and a wrong sort key reverses the result.
    """
    return [ts] + [fill] * (len(store.FIELDS) - 1)


print("\nthe clock, which decides whether a row may be written at all\n")

# MicroPython on rp2 counts from 2000, CPython from 1970, and store.py asks
# rather than assumes. Inverting that test shifts every timestamp written on
# this host by thirty years -- the rows still look plausible, and the app
# then discards all of them as outside its seven-day window.
check("the epoch offset matches the host this runs on",
      abs(store._epoch() - time.time()) < 60,
      "_OFFSET=%d" % store._OFFSET)


class FakeTime:
    """time, with a settable year. Everything else is the real thing."""

    def __init__(self, year):
        self.year = year

    def localtime(self, *a):
        return (self.year, 1, 1, 0, 0, 0, 0, 1)

    def time(self):
        return 1793880000

    def gmtime(self, *a):
        return time.gmtime(*a)


real_time = store.time
try:
    store.time = FakeTime(2021)
    check("an unsynced clock writes no row at all", store._epoch() is None,
          "a gap the app already draws; a 2021 row drags every axis back")
    store.time = FakeTime(2023)
    check("...and the year before the cutoff is still refused",
          store._epoch() is None)
    store.time = FakeTime(2024)
    check("the cutoff year itself is accepted",
          store._epoch() is not None, "%s" % store._epoch())
finally:
    store.time = real_time


print("\nrotation, which must truncate the OTHER file\n")

tmp = tempfile.mkdtemp()
try:
    h = hist(tmp, per_file=4)
    f0 = os.path.join(tmp, store._FILES[0])
    f1 = os.path.join(tmp, store._FILES[1])

    for i in range(4):
        h._append(row(1793880000 + i * 300))
    check("the first file fills to its limit", len(lines(f0)) == 4,
          "%d rows" % len(lines(f0)))
    check("...and is the one being written to", h._target == store._FILES[0])

    h._append(row(1793880000 + 4 * 300))
    check("the next row rotates to the second file",
          h._target == store._FILES[1], h._target)
    # The whole point. Truncating the file just filled, instead of the stale
    # one, throws away the half of the window that was about to be served.
    check("...and the filled file is left ALONE, not truncated",
          len(lines(f0)) == 4, "%d rows left in %s" % (len(lines(f0)),
                                                       store._FILES[0]))
    check("...with the new row landing in the file it rotated to",
          len(lines(f1)) == 1, "%d rows" % len(lines(f1)))

    # And back again, so the pair alternates rather than marching on.
    for i in range(3):
        h._append(row(1793882000 + i * 300))
    check("the second file fills in its turn", len(lines(f1)) == 4,
          "%d rows" % len(lines(f1)))
    h._append(row(1793884000))
    check("rotating again comes back to the first file",
          h._target == store._FILES[0], h._target)
    check("...and THAT truncation does happen, so the pair stays bounded",
          len(lines(f0)) == 1, "%d rows" % len(lines(f0)))
finally:
    shutil.rmtree(tmp, ignore_errors=True)


print("\npicking up where the last boot left off\n")

tmp = tempfile.mkdtemp()
try:
    # Nothing on flash yet. The choice has to be deterministic, or two boots
    # of the same device disagree about which file they are filling.
    check("a fresh device starts on the first file",
          hist(tmp)._target == store._FILES[0], hist(tmp)._target)

    # The previous run was filling whichever file ends later. Modification
    # time cannot answer this: the filesystem has no clock before NTP.
    io.open(os.path.join(tmp, store._FILES[0]), "w").write(
        store.format_row(row(1793880000)))
    io.open(os.path.join(tmp, store._FILES[1]), "w").write(
        store.format_row(row(1793890000)))
    check("the file with the later last row is resumed",
          hist(tmp)._target == store._FILES[1], hist(tmp)._target)

    io.open(os.path.join(tmp, store._FILES[0]), "w").write(
        store.format_row(row(1793899000)))
    check("...and the other way round too",
          hist(tmp)._target == store._FILES[0], hist(tmp)._target)
finally:
    shutil.rmtree(tmp, ignore_errors=True)


print("\nreading rows back, across the generations on flash\n")

# The device appends to FIELDS and never reorders, so a file written weeks
# ago is narrower than today's. Dropping those is not a parse failure, it is
# the whole stored week thrown away -- which is exactly what happened on the
# app side of this same seam, three separate times.
full = store._parse(store.format_row(row(1793880000)).strip())
check("a row of today's width parses", full is not None and full[0] == 1793880000)
check("...to exactly the field count", full is not None and len(full) == len(store.FIELDS),
      "%s" % (len(full) if full else None))

for width in (11, 12, 15):
    line = ",".join(["1793880000"] + ["0"] * (width - 1))
    got = store._parse(line)
    check("a %d-field row from an older firmware is kept" % width,
          got is not None and len(got) == len(store.FIELDS),
          "%s" % (len(got) if got else None))
    check("...padded with None, never 0" % (),
          got is not None and got[-1] is None, "%r" % (got[-1] if got else None))

# A torn last line is a row that lost power mid-append. Padding it would
# invent a reading that was never taken.
check("a torn line is skipped, not padded into a fake row",
      store._parse("1793880000,55.1,54") is None)
check("a line of nonsense is skipped",
      store._parse("not,a,row,at,all,x,y,z,1,2,3") is None)
check("an empty line is skipped", store._parse("") is None)

# Timestamps are the index everything else is found by, so a bad one is not
# recoverable by padding.
check("a row with an unparseable timestamp is skipped",
      store._parse(",".join(["x"] + ["0"] * (len(store.FIELDS) - 1))) is None)


print("\nthe read cap, which bounds what one request can cost\n")

tmp = tempfile.mkdtemp()
try:
    h = hist(tmp, per_file=10_000)
    for i in range(store.MAX_ROWS + 25):
        h._append(row(1793880000 + i * 300))
    got = h.read(0, store.MAX_ROWS + 1000)
    check("a request for more than the cap is capped",
          len(got) <= store.MAX_ROWS, "%d of %d asked" % (len(got), store.MAX_ROWS + 1000))
    check("...and returns the NEWEST rows, not the oldest",
          got[-1][0] == 1793880000 + (store.MAX_ROWS + 24) * 300,
          "last ts %s" % got[-1][0])
    since = 1793880000 + (store.MAX_ROWS + 20) * 300
    got = h.read(since, store.MAX_ROWS)
    check("`since` drops everything before it",
          all(r[0] >= since for r in got), "%d rows" % len(got))
    # AT since, not after it. Asserting only "nothing older" is satisfied by
    # a filter that drops the boundary row too, and the app asks with the
    # timestamp of the newest row it already holds -- so an exclusive filter
    # loses exactly one row per reconnect, for ever.
    check("...and keeps the row exactly ON it",
          any(r[0] == since for r in got), "since=%d" % since)
finally:
    shutil.rmtree(tmp, ignore_errors=True)


print("\nreading across both files, in the right order\n")

tmp = tempfile.mkdtemp()
try:
    # read() picks the older file by the same rule rotation does, inverted.
    # Getting it backwards returns the two halves of the week swapped, and
    # every chart drawn from it is nonsense in a way that still looks like
    # data.
    h = hist(tmp, per_file=4)
    # Humidity DESCENDS as the timestamp ascends. With every field equal,
    # `out.sort(key=lambda r: r[0])` could sort on any column and look
    # right; against this data, sorting on rh returns the week backwards.
    for i in range(7):
        h._append(row(1793880000 + i * 300, fill=70 - i))
    check("the rows landed in both files", h._target == store._FILES[1])
    got = h.read(0, 100)
    check("every row written is read back", len(got) == 7, "%d rows" % len(got))
    check("...in ascending time order, across the file boundary",
          [r[0] for r in got] == sorted(r[0] for r in got),
          "%s" % [r[0] - 1793880000 for r in got])
    check("...starting at the oldest and ending at the newest",
          got[0][0] == 1793880000 and got[-1][0] == 1793880000 + 6 * 300)
    # The assertion that pins the sort KEY rather than just the sortedness.
    check("...sorted on the timestamp, not on whatever column came first",
          [r[1] for r in got] == [70 - i for i in range(7)],
          "rh order %s" % [r[1] for r in got])
finally:
    shutil.rmtree(tmp, ignore_errors=True)


print("\nthe limit, across both files, which is where the order bites\n")

tmp = tempfile.mkdtemp()
try:
    # read() chooses which file to open FIRST with
    #     older = _FILES[1] if self._target == _FILES[0] else _FILES[0]
    # and inverting that line survived every test in this repo. The reason
    # is the `out.sort()` on its last line: whatever order the two halves
    # are read in, the result comes back in time order anyway, so nothing
    # about ORDER can catch it.
    #
    # What it does change is which rows the limit THROWS AWAY. The trim is
    # `del out[0]` as rows arrive, so it discards whatever was read first.
    # Read the newer file first and the trim eats the newest rows and keeps
    # the oldest -- the opposite of what a capped read is for, and invisible
    # to a test whose rows all sit in one file. The cap test above uses
    # per_file=10_000 for its own reasons, so it never spanned the pair.
    # This one does.
    h = hist(tmp, per_file=4)
    for i in range(7):
        h._append(row(1793880000 + i * 300))
    check("the rows really did land in both files",
          h._target == store._FILES[1], h._target)
    got = h.read(0, 3)
    check("a capped read spanning both files returns 3 rows",
          len(got) == 3, "%d rows" % len(got))
    check("...and they are the NEWEST three, not the oldest",
          [r[0] for r in got] == [1793880000 + i * 300 for i in (4, 5, 6)],
          "%s" % [r[0] - 1793880000 for r in got])

    # paths() carries the same line, and there it has no sort behind it to
    # hide a mistake: webapi streams these two files back to back, a line at
    # a time, exactly as given. Inverted, a week of history arrives with its
    # halves swapped.
    older, newer = h.paths()
    check("paths() hands back the stale file first, for streaming",
          older.endswith(store._FILES[0]) and newer.endswith(store._FILES[1]),
          "%s then %s" % (os.path.basename(older), os.path.basename(newer)))

    # ...and the other way round once it rotates, or the rule is only half
    # written and merely happens to match the fresh-device case.
    for i in range(4):
        h._append(row(1793890000 + i * 300))
    check("the pair rotated back to the first file",
          h._target == store._FILES[0], h._target)
    older, newer = h.paths()
    check("...and paths() follows it round",
          older.endswith(store._FILES[1]) and newer.endswith(store._FILES[0]),
          "%s then %s" % (os.path.basename(older), os.path.basename(newer)))
finally:
    shutil.rmtree(tmp, ignore_errors=True)


print("\nbucketing, which decides when a row is written at all\n")

tmp = tempfile.mkdtemp()
try:
    h = hist(tmp)
    clock = [1793880000]
    real_epoch = store._epoch
    store._epoch = lambda: clock[0]
    try:
        p = {"rh": 55.0, "temp": 21.0, "fan_on": False}
        check("the first sample opens a bucket and writes nothing",
              h.note(p) is False)
        clock[0] += 60
        check("another sample inside the same bucket writes nothing",
              h.note(p) is False)
        check("...and nothing has reached flash yet", h.rows == 0,
              "rows=%d" % h.rows)
        clock[0] += Cfg.HISTORY_INTERVAL_S
        check("crossing into the next bucket writes the closed one",
              h.note(p) is True)
        check("...exactly one row", h.rows == 1, "rows=%d" % h.rows)
        # The sample that crossed the boundary belongs to the NEW bucket and
        # must not be dropped: at a 2 s loop that is one sample in ten.
        clock[0] += Cfg.HISTORY_INTERVAL_S
        h.note(p)
        got = h.read(0, 10)
        check("the crossing sample opened the new bucket rather than vanishing",
              len(got) == 2, "%d rows" % len(got))
    finally:
        store._epoch = real_epoch

    # An unset clock must write nothing at all rather than stamping 2021.
    store._epoch = lambda: None
    try:
        h2 = hist(tmp)
        check("with no clock, note() refuses quietly",
              h2.note({"rh": 55.0}) is False)
        check("...and counts no row", h2.rows == 0)
    finally:
        store._epoch = real_epoch
finally:
    shutil.rmtree(tmp, ignore_errors=True)


print("\nfailing safely, because the fan must not stop for a full disk\n")

tmp = tempfile.mkdtemp()
try:
    h = hist(tmp)
    h._where = "/nonexistent-directory-for-this-test/"
    ok = h._append(row(1793880000))
    check("a write to a broken path returns False rather than raising",
          ok is False)
    check("...and is counted, so the failure is visible", h.failed == 1,
          "failed=%d" % h.failed)
    check("...and no row is counted as written", h.rows == 0, "rows=%d" % h.rows)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

failed = results.count(False)
print("\n%d/%d passed\n" % (len(results) - failed, len(results)))
sys.exit(1 if failed else 0)
