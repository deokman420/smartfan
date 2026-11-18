"""How often settings.json is rewritten, on the host with a fake clock.

    python tests/test_settings.py

Audit NV-6: every accepted write rewrote settings.json -- unchanged values
included, with no limit, from an API open to the whole LAN -- and _load
rewrote the file once per key at every boot. On a ceiling-mounted device
flash wear is a slow way to need a ladder. These count actual rewrites.
"""

import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

results = []


def check(name, ok, detail=""):
    results.append(ok)
    print("  %-56s %s %s" % (name, "PASS" if ok else "FAIL", detail))


os.chdir(tempfile.mkdtemp(prefix="fan-settings-"))

import settings                                      # noqa: E402

CLOCK = [1_000_000]
settings._ms = lambda: CLOCK[0]
settings._since = lambda start: CLOCK[0] - start

WRITES = [0]
_real_write = settings.Settings._write


def _counted(self):
    WRITES[0] += 1
    return _real_write(self)


settings.Settings._write = _counted


def advance(ms):
    CLOCK[0] += ms


def on_disk():
    try:
        return json.load(open(settings.FILE))
    except OSError:
        return None


print("\nsettings.json rewrites (host, fake clock)\n")

st = settings.Settings()
check("a fresh device with no file writes nothing at boot",
      WRITES[0] == 0 and on_disk() is None, "writes=%d" % WRITES[0])

ok, _ = st.set("BOOST_S", 300)
check("the first real change is written at once",
      ok and WRITES[0] == 1 and on_disk() == {"BOOST_S": 300},
      "writes=%d disk=%s" % (WRITES[0], on_disk()))

advance(settings.SAVE_GAP_MS + 1)
for _ in range(20):
    st.set("BOOST_S", 300)
check("the same value written again costs no flash",
      WRITES[0] == 1, "writes=%d" % WRITES[0])

# A slider dragged in HA, or a client hammering /api/set.
w0 = WRITES[0]
for v in range(100, 160):
    st.set("BOOST_S", v)
    advance(50)
check("a burst of 60 changes is one write, not 60",
      WRITES[0] - w0 == 1, "writes=%d" % (WRITES[0] - w0))
check("...and every change is live at once, written or not",
      st.BOOST_S == 159, str(st.BOOST_S))

st.flush()
check("flush inside the gap still waits", on_disk()["BOOST_S"] != 159,
      str(on_disk()))
advance(settings.SAVE_GAP_MS)
st.flush()
check("flush after the gap writes the latest value",
      on_disk() == {"BOOST_S": 159}, str(on_disk()))
w1 = WRITES[0]
st.flush()
advance(settings.SAVE_GAP_MS * 3)
st.flush()
check("...and nothing more once it is on flash", WRITES[0] == w1,
      "writes=%d" % (WRITES[0] - w1))

# The OTA and net-stall resets flush with force, so a change made just
# before an update is not lost to the reboot.
st.set("BOOST_S", 420)
st.set("BOOST_S", 421)
st.flush(force=True)
check("a forced flush writes inside the gap",
      on_disk() == {"BOOST_S": 421}, str(on_disk()))

# Hammered for an hour of fake time: bounded by the gap, not the request rate.
w2 = WRITES[0]
for i in range(36000):
    st.set("BOOST_S", 200 + (i % 2))
    advance(100)
    st.flush()
per_hour = WRITES[0] - w2
check("an hour of 10 writes/s is capped near 3600 s / gap",
      per_hour <= 3600 * 1000 // settings.SAVE_GAP_MS + 1,
      "%d writes" % per_hour)

# Boot: loading a file with several keys used to rewrite it once per key.
json.dump({"BOOST_S": 300, "MAX_RUN_S": 5400, "MODE": "Auto"},
          open(settings.FILE, "w"))
w3 = WRITES[0]
st2 = settings.Settings()
check("booting with a valid file writes nothing",
      WRITES[0] == w3 and st2.MAX_RUN_S == 5400, "writes=%d" % (WRITES[0] - w3))

# A file that load has to correct is written back once, not once per key.
json.dump({"BOOST_S": 10**9, "MAX_RUN_S": 5400, "nonsense": 1},
          open(settings.FILE, "w"))
w4 = WRITES[0]
st3 = settings.Settings()
check("a file load had to clamp is rewritten exactly once",
      WRITES[0] - w4 == 1 and "nonsense" not in on_disk(),
      "writes=%d disk=%s" % (WRITES[0] - w4, on_disk()))

# reset() on defaults is a no-op; on overrides it is one write.
advance(settings.SAVE_GAP_MS + 1)
w5 = WRITES[0]
st3.reset()
check("reset with overrides is one write", WRITES[0] - w5 == 1
      and on_disk() == {}, str(on_disk()))
advance(settings.SAVE_GAP_MS + 1)
st3.reset()
check("reset when already at defaults writes nothing",
      WRITES[0] - w5 == 1, "writes=%d" % (WRITES[0] - w5))

failed = results.count(False)
print("\n%d/%d passed\n" % (len(results) - failed, len(results)))
sys.exit(1 if failed else 0)
