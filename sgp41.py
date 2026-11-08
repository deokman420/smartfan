"""The odor sensor's bus, and nothing else.

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
30 and 40 ms. The NAK is correct behavior, not a fault, so a short delay
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

READABLE IS NOT SETTLED, AND SETTLING IS NOT A TIMER. Conditioning ends at
10 s and the readings after it are real, but the element is still coming to
temperature. A baseline seeded inside that ramp is wrong by however far the
raw count still has to travel, and at cfg.VOC_BASELINE_A (0.0005, ~67 min)
the odor path then spends an hour deaf. That was observed, not predicted:
`base 29593 / raw 30094 / odr -500` on 2026-11-02.

This used to be a 60 s timer, from a measurement that said sraw_voc climbed
about 530 ticks over the first minute and was flat after. BOTH HALVES OF
THAT WERE WRONG, and in the same way: the ramp was watched for minutes and
pronounced finished.

  - The VOC pixel was still moving about 4 ticks/min at 30 minutes. Seeded
    at 60 s, it read +129 against a start threshold of 80 half an hour
    later, on air nobody could smell.
  - The NOx pixel takes about TWO HOURS. Measured over a 9.2 h overnight
    series in an empty room, 2-3 Nov: the hourly drift ran -2.6, -3.8,
    -0.9 ticks/min and only then crossed into noise. A 20-minute figure
    taken earlier that day came from a two-minute flatness test, which a
    long exponential satisfies in passing without having settled at all.

So the gate asks the sensor rather than the clock: a pixel is settled once
its raw count has stayed inside its own flatness limit across _FLAT_BLOCKS
consecutive blocks of _FLAT_BLOCK_MS. Per pixel, because the two differ by
twenty-fold and one gate for both would hold the VOC baseline -- the one the
fan acts on -- hostage to the NOx pixel for two hours after every power cut.

WHAT THIS CANNOT DO. The late ramp and the settled wander overlap. In that
same overnight series the pixels drifted +-2.5 ticks/min for hours after
settling, tracking room humidity, which is the same order as the ramp's last
hour. No flatness test separates those, and this one does not claim to: it
removes the steep part, roughly 1400 ticks of NOx ramp down to a ~150 tick
residual, and the rest is a job for how the trigger reads the signal rather
than for when the baseline is seeded.

So there are three deadlines, not one. `conditioning` gates whether there is
a reading at all; `settled_voc` and `settled_nox` gate whether that pixel's
reading may seed a baseline; and a ceiling gates how long either may refuse,
because a gate that never opens is a sensor that never works. Readings
between them are published and displayed as normal, because they are the
sensor's honest output; they are only withheld from the baseline. main.py
decides what to do with that, in _voc_learning().
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

# What "stopped moving" means. Three consecutive five-minute blocks, judged
# min-to-max ACROSS ALL THREE at once rather than block by block -- which is
# what makes a slow monotonic ramp fail. At 4 ticks/min a ramp looks calm
# inside any one block (20 ticks) and is obvious across three (60).
#
# One number cannot sit between both pixels, which is why these are two.
#
# 45 is the NOx limit, from the 2-3 Nov overnight series. Over 15-minute
# spans in the settled region that pixel moved a median of 23 ticks and a
# p90 of 36, and the ramp's second hour moved 57. Its gate already fires,
# on a 31-tick window, and a looser limit would only make that slack.
#
# 70 is the VOC limit, set 2026-11-06. On the 4 Nov bathroom boot the
# flattest 15-minute window was 51 ticks, so 45 can never fire and that
# pixel always left through the two-hour ceiling. 70 also clears a
# 4 tick/min ramp, which is 60 ticks across this window and the slope the
# VOC pixel still had at 30 minutes -- so it may now seed during the last
# of the warm-up, about 35 minutes sooner, onto a count the ceiling was
# already landing on. Taken with that ramp in view.
#
# Not cfg-tunable: these are properties of the element. The CEILING is a
# preference -- how long you are willing to be deaf -- and lives in
# config.py, passed in.
_FLAT_BLOCK_MS = 300000
_FLAT_BLOCKS   = 3
_FLAT_TICKS_NOX = 45
_FLAT_TICKS_VOC = 70
_CEILING_MS    = 7200000        # 2 h, if the caller names none

_SELF_TEST_PASS = 0xD400


class _Flat:
    """Has one pixel's raw count stopped climbing?

    Tumbling blocks, not a sliding window: at the loop's 2 s cadence a
    15-minute sliding window is 450 samples per pixel, which is real memory
    on this part for a question that does not need the resolution. Each
    completed block contributes only its min and max.

    The verdict is taken across every retained block at once. Judging block
    by block would pass a ramp -- each block individually calm, each one
    lower than the last -- which is precisely the failure that made the old
    60 s figure look right.
    """

    def __init__(self, limit):
        self.limit = limit
        self.reset()

    def reset(self):
        self._blocks = []       # (lo, hi) per completed block
        self._lo = None
        self._hi = None
        self._since = None

    def feed(self, raw, now):
        if self._since is None:
            self._since = now
            self._lo = self._hi = raw
            return
        if raw < self._lo:
            self._lo = raw
        elif raw > self._hi:
            self._hi = raw
        if time.ticks_diff(now, self._since) < _FLAT_BLOCK_MS:
            return
        self._blocks.append((self._lo, self._hi))
        if len(self._blocks) > _FLAT_BLOCKS:
            self._blocks.pop(0)
        self._since = now
        self._lo = self._hi = raw

    @property
    def span(self):
        """Ticks between the lowest and highest retained block, or None."""
        if len(self._blocks) < _FLAT_BLOCKS:
            return None
        return (max(b[1] for b in self._blocks)
                - min(b[0] for b in self._blocks))

    @property
    def flat(self):
        span = self.span
        return span is not None and span <= self.limit


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

    def __init__(self, i2c, addr=0x59, max_fails=10, log=print,
                 settle_ceiling_s=None):
        self.i2c = i2c
        self.addr = addr
        self.max_fails = max_fails
        self._log = log
        self.ok = True          # False once the path has stood down for good
        self.fails = 0          # consecutive failures, reset by any good read
        self._until = None      # conditioning deadline; None until start()
        self._ceiling_at = None # when a pixel settles whether flat or not
        self._ceiling_ms = (_CEILING_MS if settle_ceiling_s is None
                            else int(settle_ceiling_s * 1000))
        # Per pixel, and latched. See _check_settled for why it latches.
        self._flat = (_Flat(_FLAT_TICKS_VOC), _Flat(_FLAT_TICKS_NOX))
        self._settled = [False, False]

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
        """Arm the deadlines and drop any settling evidence. Returns at once.

        A restart means the heater has been off, so whatever flatness was
        established before it describes a different element temperature and
        must not be carried over.
        """
        now = time.ticks_ms()
        self._until = time.ticks_add(now, _CONDITION_MS)
        self._ceiling_at = time.ticks_add(now, self._ceiling_ms)
        self._flat[0].reset()
        self._flat[1].reset()
        self._settled = [False, False]

    @property
    def conditioning(self):
        if self._until is None:
            return True
        return time.ticks_diff(self._until, time.ticks_ms()) > 0

    @property
    def settled_voc(self):
        """May the VOC pixel's reading seed a baseline?"""
        return self._settled[0]

    @property
    def settled_nox(self):
        """May the NOx pixel's reading seed a baseline?

        Separate from settled_voc and routinely much later: this pixel takes
        about two hours against the VOC pixel's minutes. Nothing acts on NOx,
        so the wait costs nothing the fan needs.
        """
        return self._settled[1]

    def settle_left(self, index=0):
        """Seconds until this pixel may settle, and until it will.

        ``(ready_s, left_s)`` while the pixel is still waiting, or None once
        it has latched, the sensor has stood down, or start() was never
        called. ``ready_s`` is when three blocks will exist, so the flatness
        gate is allowed to fire. It is 0 once that window is full. It is not
        a promise the pixel will be flat then: a ramp fails the gate on
        every full window until it actually stops. ``left_s`` is the ceiling.
        That one is a promise. The baseline is seeded then whether or not
        the reading ever went flat.
        """
        if index not in (0, 1) or not self.ok or self._ceiling_at is None:
            return None
        if self._settled[index]:
            return None
        now = time.ticks_ms()
        left_ms = time.ticks_diff(self._ceiling_at, now)
        if left_ms < 0:
            left_ms = 0
        flat = self._flat[index]
        have = len(flat._blocks)
        if have >= _FLAT_BLOCKS:
            ready_ms = 0
        elif flat._since is None:
            # No sample yet. The window starts at the first reading, which
            # is after conditioning. Counting only the blocks would tell
            # the page the pixel can settle fifteen minutes from a heater
            # that is still in its first ten seconds.
            cond = 0
            if self._until is not None:
                cond = time.ticks_diff(self._until, now)
                if cond < 0:
                    cond = 0
            ready_ms = cond + _FLAT_BLOCKS * _FLAT_BLOCK_MS
        else:
            age = time.ticks_diff(now, flat._since)
            if age < 0:
                age = 0
            ready_ms = (_FLAT_BLOCKS - have) * _FLAT_BLOCK_MS - age
            if ready_ms < 0:
                ready_ms = 0
        return (ready_ms // 1000, left_ms // 1000)

    def _check_settled(self, now):
        """Latch each pixel the first time it looks settled.

        LATCHED, deliberately. Flatness is being used to DETECT the end of
        warm-up, not as a standing condition -- an element does not un-warm.
        Left unlatched it would go False again during any real event, which
        is exactly when main.py's hold-cap escape needs learning to be
        permitted: _voc_learning() would then refuse for ever and the odor
        path would deadlock on a stale baseline, which is the bug
        cfg.VOC_HOLD_MAX_S exists to prevent. Re-arming happens in start(),
        and only there.
        """
        if self._until is None:
            return
        ceiling = time.ticks_diff(self._ceiling_at, now) <= 0
        for i, name in ((0, "voc"), (1, "nox")):
            if self._settled[i]:
                continue
            if self._flat[i].flat:
                self._settled[i] = True
                self._log("%s: settled, %d ticks over %d min"
                          % (name, self._flat[i].span,
                             _FLAT_BLOCK_MS * _FLAT_BLOCKS // 60000))
            elif ceiling:
                self._settled[i] = True
                span = self._flat[i].span
                self._log("%s: settling gave up at the %d min ceiling "
                          "(span %s) - baseline may be seeded mid-ramp"
                          % (name, self._ceiling_ms // 60000,
                             "unknown" if span is None else "%d ticks" % span))

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
        # Settling evidence is gathered here, at the one place a reading
        # exists, so no caller can forget to feed it and silently never
        # settle.
        now = time.ticks_ms()
        self._flat[0].feed(voc, now)
        self._flat[1].feed(nox, now)
        self._check_settled(now)
        return voc, nox
