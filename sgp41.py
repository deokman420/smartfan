"""The odour sensor's bus, and nothing else.

An SGP41 measures gas resistance across two heated metal-oxide pixels and
reports them as raw tick counts. It does not report a smell, a concentration,
or a threshold -- turning ticks into a verdict is voc.py's job, and keeping
that split is what lets the detector be tested against recorded data with no
hardware present.

Measured on this build's part (serial 000006DB4C81) on 2026-11-01, one part
alone on I2C0 at 100 kHz, in still indoor air at 23.9 C / 46 %RH:

    sraw_voc, settled                28712-29331  (spread 619 over 20 s)
    sraw_nox, still warming          19480->17834 (monotonic, ~80/s)
    measure_raw read before 50 ms    NAK, every time
    CRC valid                        20/20 reads

Two things that look like broken wiring and are not:

READING TOO EARLY. measure_raw NAKs until its 50 ms is up -- verified here at
30 and 40 ms. The NAK is correct behaviour, not a fault, so a short delay
reads as an I2C error rather than as bad data.

TWO PARTS ON ONE BUS. The address 0x59 is fixed in silicon with no select
pin, so a second SGP4x cannot be given a different one. I2C is open-drain:
both parts ACK, both drive the read, and the master sees the bitwise AND of
two different replies. That produces smooth, plausible, in-range values whose
CRC never validates -- which looks exactly like a signal-integrity problem
and is not one. It was diagnosed here by the statistic that gives it away
(bytes averaging 1.98 bits set where one part gives 4.0), and confirmed by
reading each part alone: 0x06DB & 0x068B = 0x068B and 0x4C81 & 0x380D =
0x0801, matching the ANDed serial exactly. If CRCs fail on every multi-word
read while a single-word read is clean, suspect a duplicate part first.

SAMPLING CADENCE. Sensirion specify a 1 s interval; this controller's loop
samples every cfg.SAMPLE_S (2 s), set by what a DHT22 will tolerate. That is
a deliberate deviation and it is safe *here* only because nothing compares
these ticks to an absolute figure: the element cools a little further between
reads at 2 s, which shifts the raw level, and voc.py learns its baseline from
readings taken at the same cadence, so the deviation it reports is unaffected.
What this does rule out is porting Sensirion's VOC Index algorithm later and
feeding it this stream -- that algorithm is specified against 1 s sampling and
would need the loop split, not just a new module.

CONDITIONING MUST NOT BLOCK. The VOC pixel needs ~10 s at full heater current
before its readings mean anything, and the control loop arms a WDT at
cfg.WDT_TIMEOUT_MS (8000). A blocking 10 s wait anywhere in the loop is a
guaranteed reset loop. So start() only records a deadline; read() issues the
conditioning command and returns None until the deadline passes. The 50 ms
measurement delay does block, which is well inside the budget -- the loop
already blocks longer than that on the DHT22.
"""

import time

# Command words and their delays, all verified against this part rather than
# from memory. A wrong delay here reads as a CRC failure or an I2C error,
# which is indistinguishable from bad wiring at the bench.
_SERIAL       = 0x3682   # ->3 words,    1 ms
_SELF_TEST    = 0x280E   # ->1 word,   320 ms; 0xD400 = both pixels pass
_CONDITIONING = 0x2612   # ->1 word,    50 ms; VOC pixel only, max 10 s total
_MEASURE_RAW  = 0x2619   # ->2 words,   50 ms; VOC then NOx
_HEATER_OFF   = 0x3615   # no reply,     1 ms

# Compensation defaults, used until a climate reading is available: the
# datasheet's own neutral pair, 50 %RH and 25 C.
_RH_DEFAULT = 0x8000
_T_DEFAULT  = 0x6666

_CONDITION_MS = 10000

_SELF_TEST_PASS = 0xD400


def crc8(data):
    """Sensirion CRC8: polynomial 0x31, init 0xFF. Test vector 0xBEEF -> 0x92."""
    crc = 0xFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = ((crc << 1) ^ 0x31) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


def ticks(rh, t):
    """Climate to compensation ticks, clamped to the sensor's own ranges.

    rh_ticks = RH% * 65535/100, t_ticks = (T+45) * 65535/175. A reading
    outside the representable range is clamped rather than allowed to wrap:
    a nonsense tick value silently biases every reading after it.
    """
    if rh is None or t is None:
        return _RH_DEFAULT, _T_DEFAULT
    rh = 0.0 if rh < 0.0 else (100.0 if rh > 100.0 else rh)
    t = -45.0 if t < -45.0 else (130.0 if t > 130.0 else t)
    return int(rh * 65535 / 100), int((t + 45) * 65535 / 175)


class SGP41:
    """Commands, CRCs and a conditioning deadline. No policy, no thresholds.

    The bus is passed in rather than built here, because the climate sensor
    is meant to share it and two I2C objects on one pin pair is a bug waiting
    to happen.
    """

    def __init__(self, i2c, addr=0x59, max_fails=10, log=print):
        self.i2c = i2c
        self.addr = addr
        self.max_fails = max_fails
        self._log = log
        self.ok = True          # False once the path has stood down for good
        self.fails = 0          # consecutive failures, reset by any good read
        self._until = None      # conditioning deadline; None until start()

    # --- bus ---------------------------------------------------------
    def _xfer(self, cmd, params=(), delay_ms=10, nwords=0):
        out = bytearray(cmd.to_bytes(2, "big"))
        for word in params:
            wb = word.to_bytes(2, "big")
            out += wb + bytes((crc8(wb),))
        self.i2c.writeto(self.addr, out)
        time.sleep_ms(delay_ms)
        if not nwords:
            return ()
        raw = self.i2c.readfrom(self.addr, nwords * 3)
        words = []
        for i in range(nwords):
            chunk = raw[i * 3:i * 3 + 2]
            if crc8(chunk) != raw[i * 3 + 2]:
                raise OSError("sgp41: CRC fail, word %d of 0x%04X" % (i, cmd))
            words.append((chunk[0] << 8) | chunk[1])
        return words

    def _failed(self, exc):
        """Count a failure, and stand the path down once it is clearly gone.

        Transient I2C errors happen; a sensor that has fallen off its cable
        does not come back. Standing down stops a dead part from costing a
        bus transaction and a 55 ms block on every pass of the control loop.
        """
        self.fails += 1
        if self.fails >= self.max_fails and self.ok:
            self.ok = False
            self._log("sgp41: %d consecutive failures, standing down (%s)"
                      % (self.fails, exc))
            try:
                self.heater_off()
            except Exception:
                pass
        return None

    # --- identity and health ----------------------------------------
    def serial(self):
        """48-bit serial as a hex string, or None.

        Doubles as a duplicate-part check: with two SGP4x on one bus this
        returns either None or a value whose CRC fails, never a stable
        serial. See the module docstring.
        """
        try:
            w = self._xfer(_SERIAL, delay_ms=5, nwords=3)
            return "%04X%04X%04X" % (w[0], w[1], w[2])
        except Exception:
            return None

    def self_test(self):
        """True if both pixels pass. Blocks 350 ms, so startup only."""
        try:
            return self._xfer(_SELF_TEST, delay_ms=350,
                              nwords=1)[0] == _SELF_TEST_PASS
        except Exception:
            return False

    def heater_off(self):
        self._xfer(_HEATER_OFF, delay_ms=5)

    # --- the loop ----------------------------------------------------
    def start(self):
        """Arm the conditioning window. Returns at once -- see the docstring."""
        self._until = time.ticks_add(time.ticks_ms(), _CONDITION_MS)

    @property
    def conditioning(self):
        if self._until is None:
            return True
        return time.ticks_diff(self._until, time.ticks_ms()) > 0

    def read(self, rh=None, t=None):
        """(sraw_voc, sraw_nox), or None while conditioning or stood down.

        None is not an error: it means "no opinion yet". Callers must treat
        it as the absence of a reading, never as a zero.
        """
        if not self.ok:
            return None
        if self._until is None:
            self.start()
        rh_ticks, t_ticks = ticks(rh, t)
        try:
            if self.conditioning:
                # The VOC pixel is being brought to temperature. The reply is
                # a real sraw_voc, but it is pre-conditioning and means
                # nothing, so it is read (to honour the protocol) and dropped.
                self._xfer(_CONDITIONING, (rh_ticks, t_ticks),
                           delay_ms=55, nwords=1)
                return None
            voc, nox = self._xfer(_MEASURE_RAW, (rh_ticks, t_ticks),
                                  delay_ms=55, nwords=2)
        except Exception as e:
            return self._failed(e)
        self.fails = 0
        return voc, nox
