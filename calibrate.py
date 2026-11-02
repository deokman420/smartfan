"""Bench helper. Run this instead of main.py to pick your thresholds.

    import calibrate; calibrate.light()      # LDR numbers
    import calibrate; calibrate.fan()        # fan + tach check
    import calibrate; calibrate.sensor()     # DHT22 check
    import calibrate; calibrate.leds()       # LED and button check
    import calibrate; calibrate.voc()        # SGP41: sign of the signal

Nothing here is used by the controller at runtime.
"""

import time
from machine import Pin, ADC, I2C
import dht

import config as cfg


def light(seconds=60):
    """Print LDR readings, and measure the fixture's own step.

    Run it in the actual bathroom, in the actual mounting position, and
    flick the fixture on and off a few times while it runs.

    What matters is no longer the bright and dark LEVELS -- daylight in a
    room with a window reaches higher than the bulb does, so there is no
    level that separates them. What matters is the biggest one-sample JUMP,
    because that is the fixture and nothing in the sky can do it. Set
    LIGHT_STEP_ON to roughly a fifth of the jump printed below, which leaves
    room for the jump shrinking on a bright day as the divider compresses.
    """
    adc = ADC(Pin(cfg.PIN_LDR))
    lo, hi = 65535, 0
    jump = 0
    prev = None
    end = time.ticks_add(time.ticks_ms(), seconds * 1000)
    while time.ticks_diff(end, time.ticks_ms()) > 0:
        raw = adc.read_u16()
        lo = min(lo, raw)
        hi = max(hi, raw)
        if prev is not None:
            jump = max(jump, abs(raw - prev))
        prev = raw
        bar = "#" * (raw * 40 // 65536)
        print("%6d  min=%-6d max=%-6d %s" % (raw, lo, hi, bar))
        time.sleep_ms(250)
    span = hi - lo
    print("\nobserved %d .. %d  (span %d)" % (lo, hi, span))
    print("biggest one-sample jump: %d" % jump)
    if span < 5000:
        print("span is small -- the LDR may be shaded, or the divider")
        print("resistor is a poor match for this cell")
    elif jump < 5000:
        print("no switch-sized jump seen. Did the fixture get flicked?")
        print("Without a jump the controller cannot tell it from daylight.")
    else:
        print("suggested LIGHT_STEP_ON  = %d" % (jump // 5))
        print("suggested LIGHT_RISE_ON  = %d" % (jump // 6))
        print("suggested LIGHT_RISE_OFF = %d" % (jump // 12))
    if hi > 56000:
        print("")
        print("NOTE: %d is close to the 65535 rail. Up there the bulb adds"
              % hi)
        print("only a few hundred counts and cannot be detected at all.")
        print("Shade the LDR or aim it at the fixture, not the window.")


def sensor(n=10):
    """Read the DHT22 a few times. Occasional checksum errors are normal."""
    d = dht.DHT22(Pin(cfg.PIN_DHT))
    good = 0
    for i in range(n):
        try:
            d.measure()
            good += 1
            print("%2d  %.1f C  %.1f %%RH" % (i + 1, d.temperature(), d.humidity()))
        except OSError as e:
            print("%2d  FAILED: %s" % (i + 1, e))
        time.sleep(2)
    print("\n%d/%d good" % (good, n))
    if good == 0:
        print("3-pin module: silkscreen VCC | S | GND, VCC to 3V3 (never 5V).")
        print("The pull-up is on the module - do not add a second one.")


class _Pulses:
    """Tach counter.

    CPython lists expose __setitem__ as an attribute; MicroPython's do not, so
    the obvious one-line lambda raises inside the interrupt handler -- silently
    while the fan is stopped, then once per pulse the moment it spins. Rebinding
    a small int on an instance allocates nothing, which is what an IRQ needs.
    """

    def __init__(self):
        self.n = 0

    def tick(self, _pin):
        self.n += 1


def fan(seconds=10):
    """Switch the fan on, report RPM, switch it off again."""
    f = Pin(cfg.PIN_FAN, Pin.OUT, value=0)
    count = _Pulses()
    t = Pin(cfg.PIN_TACH, Pin.IN, Pin.PULL_UP)
    t.irq(trigger=Pin.IRQ_FALLING, handler=count.tick)

    print("fan ON")
    f.value(1)
    try:
        time.sleep(3)                     # spin-up
        for i in range(seconds):
            count.n = 0
            time.sleep(1)
            print("  %d rpm" % (count.n * 60 // cfg.TACH_PPR))
    finally:
        f.value(0)
        print("fan OFF")
    print("\nExpect roughly 4000-5000 rpm on an NF-A4x10; rated 4500 at 5 V.")
    print("0 rpm but the fan is spinning -> tach wiring or pull-up.")
    print("Not spinning at all -> swap the two outer transistor legs.")


def voc(seconds=180):
    """Print SGP41 ticks, and settle the one thing a datasheet cannot.

    THE SIGN. For SGP4x parts the raw signal is supposed to fall as
    reducing-gas concentration rises, so "worse air" should be
    baseline-minus-current. Getting that backwards gives a fan that
    ventilates whenever the air is clean, which would take a long time to
    notice. So this prints the direction in words rather than asking you to
    work it out from a column of numbers.

    Run it, let the baseline line settle, then give it something: uncap a
    marker pen next to the sensor, or a puff of hair spray from across the
    room. The DIRECTION line is the answer. `down` is what voc.py assumes.

    It also prints the quiet-room spread, which is the noise any threshold
    has to clear -- measured at 619 ticks over 20 s on this build's part.
    """
    import sgp41
    bus = I2C(0, sda=Pin(cfg.PIN_SDA), scl=Pin(cfg.PIN_SCL), freq=100000)
    s = sgp41.SGP41(bus, max_fails=cfg.VOC_MAX_FAILS)
    sn = s.serial()
    if sn is None:
        print("no valid reply from 0x59.")
        print("Nothing there, or TWO SGP4x on one bus ANDing each other --")
        print("they share a fixed address. See sgp41.py's docstring.")
        return
    print("SGP41 %s, self test %s" % (sn, "PASS" if s.self_test() else "FAIL"))

    d = dht.DHT22(Pin(cfg.PIN_DHT))
    rh = t = None
    s.start()
    print("conditioning 10 s, then %d s of readings\n" % seconds)

    base = None
    quiet = []
    end = time.ticks_add(time.ticks_ms(), (seconds + 10) * 1000)
    while time.ticks_diff(end, time.ticks_ms()) > 0:
        try:
            d.measure()
            rh, t = d.humidity(), d.temperature()
        except Exception:
            pass
        got = s.read(rh, t)
        if got is None:
            time.sleep_ms(900)
            continue
        raw, nox = got
        if base is None:
            base = float(raw)
            print("baseline seeded at %d ticks\n" % raw)
        delta = int(base - raw)
        # The first 30 s of settled readings are the noise floor.
        if len(quiet) < 15:
            quiet.append(raw)
        arrow = "down" if delta > 0 else ("up" if delta < 0 else "flat")
        print("voc=%6d  nox=%6d  base=%6d  DIRECTION %-4s  excess=%+d"
              % (raw, nox, int(base), arrow, delta))
        # Track the baseline slowly, the way voc.py does, so a long run does
        # not report drift as a permanent event.
        base = (1 - cfg.VOC_BASELINE_A) * base + cfg.VOC_BASELINE_A * raw
        time.sleep_ms(900)

    s.heater_off()
    if len(quiet) > 1:
        print("\nquiet-room spread over the first %d samples: %d ticks"
              % (len(quiet), max(quiet) - min(quiet)))
        print("VOC_RISE_ON has to clear that comfortably. Pick it from a real")
        print("event in the recorder, not from this bench run.")
    print("\nDIRECTION should read `down` while the air is bad.")
    print("If it reads `up`, the sign in voc.py is backwards -- fix it there,")
    print("not by negating a threshold.")


def leds(seconds=20):
    """Blink both LEDs and echo the button so you can verify wiring."""
    ok = Pin(cfg.PIN_LED_OK, Pin.OUT)
    run = Pin(cfg.PIN_LED_RUN, Pin.OUT)
    btn = Pin(cfg.PIN_BUTTON, Pin.IN, Pin.PULL_UP)
    print("green and red alternate; press the button to see it register")
    end = time.ticks_add(time.ticks_ms(), seconds * 1000)
    last = 1
    while time.ticks_diff(end, time.ticks_ms()) > 0:
        phase = (time.ticks_ms() // 500) % 2
        ok.value(phase)
        run.value(1 - phase)
        v = btn.value()
        if v != last:
            print("button %s" % ("PRESSED" if v == 0 else "released"))
            last = v
        time.sleep_ms(20)
    ok.value(0)
    run.value(0)

# OTA marker: 19:54:17 (pushed with the device in the bathroom)
