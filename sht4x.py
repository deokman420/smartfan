"""SHT4x temperature and humidity. The bus is passed in.

The bathroom unit still reads a DHT22. This module is the drop-in for that
part, and main.py does not construct it yet: pointing read_climate at an
SHT40 while the ceiling unit still has the other sensor would fault the fan.
When the part is fitted, the controller keeps its contract and only the
constructor changes.

    self.dht = sht4x.SHT4x(i2c)     # one bus, shared with anything else on it
    self.dht.measure()              # raises OSError on NACK or a bad CRC
    self.temp = self.dht.temperature()
    self.rh = self.dht.humidity()

Build the I2C object once, at 100 kHz, and hand it in. A second I2C object
on the same pins is a bug, and the grille cable is long enough that a faster
clock is the wrong saving. Address 0x44 is the Adafruit 4885. The breakout's
pull-ups are the bus pull-ups. Do not add another pair.

Commands, delays and the conversion are Sensirion's SHT4x datasheet 7.3
(June 2026). High repeatability takes 8.3 ms at most; the datasheet's own
example waits 10 ms, and a read inside that window NAKs. The CRC is the same
function as sgp41.crc8 (polynomial 0x31, init 0xFF, 0xBEEF -> 0x92). The two
copies are checked against each other by tests/test_sht4x.py. sgp41.py is
left alone so a humidity driver does not become a dependency of the odor one.
"""

import time

# (command, milliseconds to wait before the read). The wait is past the
# datasheet maximum: high 8.3 ms, medium 4.5, low 1.6.
_MEASURE_MS = {0xFD: 10, 0xF6: 5, 0xE0: 2}

# Heater command, then the high-precision measurement that starts when the
# pulse ends. Table 5 max pulse is 1.1 s or 0.11 s; the measurement adds the
# same 10 ms as a normal high-precision read.
_HEAT_MS = {
    0x39: 1110, 0x2F: 1110, 0x1E: 1110,   # 200 / 110 / 20 mW, 1 s
    0x32: 120,  0x24: 120,  0x15: 120,    # same powers, 0.1 s
}

_SERIAL = 0x89
_RESET = 0x94


def crc8(data):
    """Sensirion CRC8: polynomial 0x31, init 0xFF. Test vector 0xBEEF -> 0x92."""
    crc = 0xFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = ((crc << 1) ^ 0x31) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


def _degc(raw):
    return -45 + 175 * raw / 65535


def _rh(raw):
    # The formula is allowed to land a little outside 0..100. A 100.4 %
    # reading would walk the baseline off the top of the range.
    rh = -6 + 125 * raw / 65535
    if rh > 100:
        return 100.0
    if rh < 0:
        return 0.0
    return rh


class SHT4x:
    """measure / temperature / humidity, the same three calls a DHT22 has.

    A CRC failure raises OSError, same as a NACK, so the caller's existing
    except counts it. A corrupt frame must not look like a healthy reading.
    """

    def __init__(self, i2c, addr=0x44):
        self.i2c = i2c
        self.addr = addr
        self._t = None
        self._rh = None

    def _command(self, cmd, wait_ms, nwords):
        self.i2c.writeto(self.addr, bytearray((cmd,)))
        time.sleep_ms(wait_ms)
        if not nwords:
            return ()
        raw = self.i2c.readfrom(self.addr, nwords * 3)
        if len(raw) != nwords * 3:
            raise OSError("sht4x: short read")
        words = []
        for i in range(nwords):
            chunk = raw[i * 3:i * 3 + 2]
            if crc8(chunk) != raw[i * 3 + 2]:
                raise OSError("sht4x: CRC")
            words.append((chunk[0] << 8) | chunk[1])
        return tuple(words)

    def measure(self, cmd=0xFD):
        """One high-precision reading, unless cmd says medium (0xF6) or low (0xE0)."""
        if cmd not in _MEASURE_MS:
            raise OSError("sht4x: unknown measure command")
        raw_t, raw_rh = self._command(cmd, _MEASURE_MS[cmd], 2)
        self._t = _degc(raw_t)
        self._rh = _rh(raw_rh)

    def temperature(self):
        if self._t is None:
            raise OSError("sht4x: no reading")
        return self._t

    def humidity(self):
        if self._rh is None:
            raise OSError("sht4x: no reading")
        return self._rh

    def serial(self):
        """32-bit serial. Two words, each with its own CRC. Not a sample."""
        hi, lo = self._command(_SERIAL, 1, 2)
        return (hi << 16) | lo

    def reset(self):
        """Soft reset. The next temperature() raises until measure() succeeds."""
        self._command(_RESET, 1, 0)
        self._t = None
        self._rh = None

    def heat(self, cmd=0x39):
        """Run one heater pulse and store the measurement the part appends.

        Not for the control loop. The long pulses block for over a second,
        and the heater's duty cycle is 10 %. Clearing a saturated element is
        a deliberate action, not a sample.
        """
        if cmd not in _HEAT_MS:
            raise OSError("sht4x: unknown heater command")
        raw_t, raw_rh = self._command(cmd, _HEAT_MS[cmd], 2)
        self._t = _degc(raw_t)
        self._rh = _rh(raw_rh)
