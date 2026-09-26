"""Bench helper. Run this instead of main.py to pick your thresholds.

    import calibrate; calibrate.light()      # LDR numbers
    import calibrate; calibrate.fan()        # fan + tach check
    import calibrate; calibrate.sensor()     # DHT22 check
    import calibrate; calibrate.leds()       # LED and button check

Nothing here is used by the controller at runtime.
"""

import time
from machine import Pin, ADC
import dht

import config as cfg


def light(seconds=60):
    """Print LDR readings so you can pick LIGHT_ON / LIGHT_OFF.

    Run it in the actual bathroom, in the actual mounting position.
    Turn the fixture on for half the time and off for the other half,
    then set LIGHT_ON just under the bright floor and LIGHT_OFF just
    over the dark ceiling. A wide gap between them is what stops an
    LED bulb's flicker from chattering the fan on and off.
    """
    adc = ADC(Pin(cfg.PIN_LDR))
    lo, hi = 65535, 0
    end = time.ticks_add(time.ticks_ms(), seconds * 1000)
    while time.ticks_diff(end, time.ticks_ms()) > 0:
        raw = adc.read_u16()
        lo = min(lo, raw)
        hi = max(hi, raw)
        bar = "#" * (raw * 40 // 65536)
        print("%6d  min=%-6d max=%-6d %s" % (raw, lo, hi, bar))
        time.sleep_ms(250)
    span = hi - lo
    print("\nobserved %d .. %d" % (lo, hi))
    if span < 5000:
        print("span is small -- the LDR may be shaded, or the divider")
        print("resistor is a poor match for this cell")
    else:
        print("suggested LIGHT_ON  = %d" % (lo + span * 2 // 3))
        print("suggested LIGHT_OFF = %d" % (lo + span // 3))


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
