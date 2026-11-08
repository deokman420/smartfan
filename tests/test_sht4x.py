"""SHT4x command bytes, CRC, conversion, and the DHT-shaped contract.

    python tests/test_sht4x.py

Host only. No board, no network. The fake bus replies with frames this file
builds, so a driver that skips the CRC or sends the wrong command fails here
rather than on a part that has not been bought.

main.py is not switched over. The bathroom unit still has a DHT22, and these
checks are what has to be true before that one line changes.
"""

import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# sleep_ms is a MicroPython name. Record the waits instead of sleeping.
SLEPT = []
time.sleep_ms = lambda ms: SLEPT.append(ms)

import sht4x                                          # noqa: E402
import sgp41                                          # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print("  %-46s %s %s" % (name, "PASS" if ok else "FAIL", detail))
    return ok


class Bus:
    def __init__(self, reply=b"", fail=False):
        self.writes = []
        self.reply = reply
        self.fail = fail
        self.addr = None
        self.n = None

    def writeto(self, addr, buf):
        if self.fail:
            raise OSError(5)
        self.writes.append((addr, bytes(buf)))

    def readfrom(self, addr, n):
        self.addr = addr
        self.n = n
        return self.reply


def frame(t_raw, rh_raw):
    out = bytearray()
    for word in (t_raw, rh_raw):
        chunk = bytes(((word >> 8) & 0xFF, word & 0xFF))
        out += chunk + bytes((sht4x.crc8(chunk),))
    return bytes(out)


# --- CRC, shared with the odor driver ---------------------------------
check("CRC 0xBEEF is 0x92",
      sht4x.crc8(b"\xBE\xEF") == 0x92 and sgp41.crc8(b"\xBE\xEF") == 0x92)
drift = [(b, sht4x.crc8(bytes((b,))) == sgp41.crc8(bytes((b,))))
         for b in range(256)]
check("CRC matches sgp41.crc8 on every byte", all(ok for _, ok in drift))

# --- conversion, written out so it cannot call the driver -------------
# 0 and 65535 are the ends of the formula. 25 C is raw (25+45)/175*65535.
raw_25c = 26214
expect_25c = -45 + 175 * raw_25c / 65535
check("0 ticks is -45 C", sht4x._degc(0) == -45)
check("65535 ticks is 130 C", sht4x._degc(65535) == 130)
check("26214 ticks is 25 C", abs(sht4x._degc(raw_25c) - expect_25c) < 1e-9,
      "%.5f" % sht4x._degc(raw_25c))
check("RH clamps a negative formula result to 0", sht4x._rh(0) == 0.0)
check("RH clamps a result past 100 to 100", sht4x._rh(65535) == 100.0)
raw_50 = 29360
expect_50 = -6 + 125 * raw_50 / 65535
check("a mid-scale RH is the formula, unclamped",
      abs(sht4x._rh(raw_50) - expect_50) < 1e-9)

# --- a good high-precision read ---------------------------------------
SLEPT[:] = []
bus = Bus(frame(raw_25c, raw_50))
s = sht4x.SHT4x(bus)
s.measure()
check("measure writes 0xFD to 0x44",
      bus.writes == [(0x44, b"\xFD")])
check("measure waits 10 ms", SLEPT == [10], str(SLEPT))
check("measure reads 6 bytes", bus.n == 6)
check("temperature is the converted ticks",
      abs(s.temperature() - expect_25c) < 1e-9)
check("humidity is the converted ticks",
      abs(s.humidity() - expect_50) < 1e-9)

# --- a bad CRC must not look like a reading ---------------------------
good_t = s.temperature()
good_rh = s.humidity()
bad = bytearray(frame(1000, 2000))
bad[2] ^= 0xFF
bus.reply = bytes(bad)
raised = False
try:
    s.measure()
except OSError:
    raised = True
check("a bad CRC raises OSError", raised)
check("a bad CRC leaves the previous reading",
      s.temperature() == good_t and s.humidity() == good_rh)

# --- NACK --------------------------------------------------------------
bus.fail = True
raised = False
try:
    s.measure()
except OSError:
    raised = True
check("a NACK raises OSError", raised)
check("a NACK leaves the previous reading", s.temperature() == good_t)

# --- nothing measured yet ---------------------------------------------
fresh = sht4x.SHT4x(Bus(frame(1, 1)))
raised = False
try:
    fresh.temperature()
except OSError:
    raised = True
check("temperature before measure raises", raised)

# --- medium precision is an explicit command, not the default ---------
SLEPT[:] = []
bus = Bus(frame(raw_25c, raw_50))
sht4x.SHT4x(bus).measure(0xF6)
check("medium precision writes 0xF6 and waits 5 ms",
      bus.writes == [(0x44, b"\xF6")] and SLEPT == [5], str(SLEPT))

# --- serial and reset --------------------------------------------------
SLEPT[:] = []
bus = Bus(frame(0x0006, 0xDB4C))
s = sht4x.SHT4x(bus)
check("serial is the two words joined", s.serial() == 0x0006DB4C)
check("serial writes 0x89",
      bus.writes == [(0x44, b"\x89")] and SLEPT == [1])
raised = False
try:
    s.temperature()
except OSError:
    raised = True
check("serial does not store a climate reading", raised)
s.measure()
SLEPT[:] = []
bus.writes[:] = []
s.reset()
check("reset writes 0x94 and waits 1 ms",
      bus.writes == [(0x44, b"\x94")] and SLEPT == [1])
raised = False
try:
    s.temperature()
except OSError:
    raised = True
check("reset clears the stored reading", raised)

# --- heater plumbing, not the sample path -----------------------------
SLEPT[:] = []
bus = Bus(frame(raw_25c, raw_50))
s = sht4x.SHT4x(bus)
s.heat(0x39)
check("200 mW for 1 s writes 0x39 and waits 1110 ms",
      bus.writes == [(0x44, b"\x39")] and SLEPT == [1110], str(SLEPT))
check("the heater's reading is stored",
      abs(s.temperature() - expect_25c) < 1e-9)
raised = False
try:
    s.heat(0x00)
except OSError:
    raised = True
check("an unknown heater command raises", raised)

# --- the other end of the address --------------------------------------
bus = Bus(frame(1, 1))
sht4x.SHT4x(bus, addr=0x45).measure()
check("a non-default address is the one written", bus.writes[0][0] == 0x45)

passed = sum(1 for _, ok, _ in results if ok)
print("\n%d/%d passed" % (passed, len(results)))
fails = [n for n, ok, _ in results if not ok]
if fails:
    print("failed: " + ", ".join(fails))
sys.exit(0 if not fails else 1)
