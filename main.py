"""Bathroom fan controller for the Pico 2 W.

Occupancy comes from the room light via an LDR; the decision to keep
running after the room empties comes from humidity measured against a
slowly learned baseline. Absolute humidity thresholds do not work --
a muggy summer day sits at 65 %RH all by itself -- so everything here
is expressed as a rise above what this room normally sits at.

States:
    LEARN   first minute after boot, building a baseline
    IDLE    fan off, tracking the baseline
    RUN     fan on because of light and/or humidity
    BOOST   fan on because someone pushed the button
    FAULT   sensor is not answering, or the fan is not spinning
"""

import time
from machine import Pin, ADC
import dht

import config as cfg

# The network layer is strictly additive. No secrets.py, or any failure while
# bringing it up, leaves a controller that still runs the fan exactly as it
# would have done offline. A bathroom extractor that stops working when the
# WiFi drops fails silently in a room nobody watches.
try:
    import secrets
    from net import Link
    from telemetry import Telemetry
    _NET = True
except ImportError:
    secrets = None
    _NET = False

LEARN, IDLE, RUN, BOOST, FAULT = "LEARN", "IDLE", "RUN", "BOOST", "FAULT"


def ms_since(t):
    return time.ticks_diff(time.ticks_ms(), t)


class Tach:
    """Counts falling edges on the fan's open-collector tach line."""

    def __init__(self, pin_no):
        self._count = 0
        self._pin = Pin(pin_no, Pin.IN, Pin.PULL_UP)
        self._pin.irq(trigger=Pin.IRQ_FALLING, handler=self._tick)
        self._t0 = time.ticks_ms()

    def _tick(self, _pin):
        self._count += 1

    def rpm(self):
        """Pulses since the last call, converted to RPM. Resets the window."""
        n, self._count = self._count, 0
        elapsed = ms_since(self._t0) / 1000.0
        self._t0 = time.ticks_ms()
        if elapsed <= 0:
            return 0
        return int(n * 60.0 / (elapsed * cfg.TACH_PPR))


class Button:
    """Polled debounce. Reports short and long presses on release."""

    def __init__(self, pin_no):
        self._pin = Pin(pin_no, Pin.IN, Pin.PULL_UP)
        self._down_at = None
        self._stable = 1
        self._changed_at = time.ticks_ms()

    def poll(self):
        """Returns None, 'short', or 'long'."""
        raw = self._pin.value()
        if raw != self._stable:
            if ms_since(self._changed_at) < 25:
                return None
            self._stable = raw
            self._changed_at = time.ticks_ms()
            if raw == 0:                      # pressed, pin pulled to GND
                self._down_at = time.ticks_ms()
                return None
            if self._down_at is None:
                return None
            held = ms_since(self._down_at) / 1000.0
            self._down_at = None
            return "long" if held >= cfg.LONG_PRESS_S else "short"
        self._changed_at = time.ticks_ms()
        return None


class Controller:
    def __init__(self):
        self.fan = Pin(cfg.PIN_FAN, Pin.OUT, value=0)
        self.led_ok = Pin(cfg.PIN_LED_OK, Pin.OUT, value=0)
        self.led_run = Pin(cfg.PIN_LED_RUN, Pin.OUT, value=0)
        self.ldr = ADC(Pin(cfg.PIN_LDR))
        self.dht = dht.DHT22(Pin(cfg.PIN_DHT))
        self.tach = Tach(cfg.PIN_TACH)
        self.button = Button(cfg.PIN_BUTTON)

        self.state = LEARN
        self.entered_at = time.ticks_ms()
        self.fan_on_at = None
        self.boost_until = None
        self.light_on = False
        self.dark_since = None

        self.baseline = None
        self.rh = None
        self.temp = None
        self.rpm = 0
        self.dht_fails = 0
        self.fault_reason = ""
        self.fault_kind = None     # 'sensor' or 'fan'
        self.fault_until = None    # earliest retry for a fan fault
        self.fan_faults = 0        # consecutive stalls

        self.link = None
        self.telem = None
        if _NET:
            try:
                self.link = Link(secrets, "fan/%s/status" % cfg.ROOM, log=print)
                self.telem = Telemetry(cfg, self.link, log=print)
                print("net: enabled, room=%s" % cfg.ROOM)
            except Exception as e:
                print("net: disabled (%s)" % e)
                self.link = self.telem = None
        else:
            print("net: disabled (no secrets.py) - running offline")

    # -- sensing ------------------------------------------------------

    def read_light(self):
        """Read the LDR with both status LEDs blanked.

        The red LED sits near the sensor and will otherwise read as
        "the bathroom light is on", which latches the fan permanently.
        """
        ok, run = self.led_ok.value(), self.led_run.value()
        self.led_ok.value(0)
        self.led_run.value(0)
        time.sleep_ms(5)
        raw = self.ldr.read_u16()
        self.led_ok.value(ok)
        self.led_run.value(run)

        if raw >= cfg.LIGHT_ON:
            self.light_on = True
        elif raw <= cfg.LIGHT_OFF:
            self.light_on = False
        return raw

    def read_climate(self):
        """Returns True if the DHT22 answered with a good reading."""
        try:
            self.dht.measure()
            self.temp = self.dht.temperature()
            self.rh = self.dht.humidity()
            self.dht_fails = 0
            return True
        except OSError:
            self.dht_fails += 1
            return False

    # -- helpers ------------------------------------------------------

    def dew_point(self):
        """Magnus approximation, degrees C. Logged for interest."""
        if self.rh is None or self.temp is None or self.rh <= 0:
            return None
        import math
        a, b = 17.27, 237.7
        g = (a * self.temp) / (b + self.temp) + math.log(self.rh / 100.0)
        return (b * g) / (a - g)

    @property
    def excess_rh(self):
        """How far above the learned baseline we currently sit."""
        if self.baseline is None or self.rh is None:
            return 0.0
        return self.rh - self.baseline

    def set_state(self, new, reason=""):
        if new == self.state:
            return
        print("state: %s -> %s  %s" % (self.state, new, reason))
        self.state = new
        self.entered_at = time.ticks_ms()

    def set_fan(self, on):
        if bool(on) == bool(self.fan.value()):
            return
        self.fan.value(1 if on else 0)
        self.fan_on_at = time.ticks_ms() if on else None
        self.tach.rpm()          # discard the stale window
        if self.telem:
            self.telem.note_fan(on)

    def fan_seconds(self):
        return 0 if self.fan_on_at is None else ms_since(self.fan_on_at) / 1000.0

    # -- the decision -------------------------------------------------

    def decide(self):
        now = time.ticks_ms()

        if self.light_on:
            self.dark_since = None
        elif self.dark_since is None:
            self.dark_since = now

        if self.state == LEARN:
            if ms_since(self.entered_at) > 60000 and self.baseline is not None:
                self.set_state(IDLE, "baseline %.1f%%" % self.baseline)
            return

        if self.state == FAULT:
            # Clear only the kind of fault that actually recovered. A stalled
            # fan used to be cleared by a healthy DHT22, which turned a real
            # hardware fault into an endless RUN -> FAULT -> IDLE loop.
            if self.fault_kind == "sensor":
                if self.dht_fails == 0:
                    self.fault_kind = None
                    self.set_state(IDLE, "sensor recovered")
                return

            if self.fault_kind == "fan":
                if self.fan_faults >= cfg.MAX_FAN_FAULTS:
                    return                      # latched; button or reboot only
                if self.fault_until is None:
                    return
                if time.ticks_diff(self.fault_until, now) <= 0:
                    self.fault_kind = None
                    self.fault_until = None
                    self.set_state(IDLE, "retrying after stall %d/%d"
                                   % (self.fan_faults, cfg.MAX_FAN_FAULTS))
                return

            return

        if self.state == BOOST:
            if time.ticks_diff(self.boost_until, now) <= 0:
                self.boost_until = None
                self.set_state(IDLE, "boost expired")
            return

        if self.state == IDLE:
            # Only learn the baseline when the room is quiet and dry.
            if not self.light_on and self.rh is not None:
                if self.baseline is None:
                    self.baseline = self.rh
                else:
                    a = cfg.BASELINE_A
                    self.baseline = (1 - a) * self.baseline + a * self.rh

            if self.light_on:
                self.set_state(RUN, "light on")
            elif self.excess_rh >= cfg.RH_RISE_ON:
                self.set_state(RUN, "humidity +%.1f%%" % self.excess_rh)
            return

        # state == RUN
        ran = self.fan_seconds()
        if self.rpm >= cfg.MIN_RPM:
            self.fan_faults = 0          # a healthy run resets the tally
        if ran >= cfg.MAX_RUN_S:
            self.set_state(IDLE, "max run time")
            return
        if ran < cfg.MIN_RUN_S:
            return
        if self.light_on:
            return
        if self.dark_since and ms_since(self.dark_since) / 1000.0 < cfg.POST_LIGHT_S:
            return
        if self.excess_rh > cfg.RH_FALL_OFF:
            return
        self.set_state(IDLE, "dry, +%.1f%%" % self.excess_rh)

    def check_fan_health(self):
        """A commanded fan that reports no RPM is a real, silent failure."""
        if self.state == FAULT:
            return
        if self.dht_fails >= 5:
            self.fault_kind = "sensor"
            self.fault_reason = "DHT22 not responding"
            self.set_fan(False)
            self.set_state(FAULT, self.fault_reason)
            return
        if self.fan.value() and self.fan_seconds() > cfg.SPINUP_S:
            if self.rpm < cfg.MIN_RPM:
                self.fan_faults += 1
                self.fault_kind = "fan"
                self.fault_until = time.ticks_add(time.ticks_ms(),
                                                  cfg.FAULT_RETRY_S * 1000)
                latched = self.fan_faults >= cfg.MAX_FAN_FAULTS
                self.fault_reason = "fan stalled (%d rpm), stall %d/%d%s" % (
                    self.rpm, self.fan_faults, cfg.MAX_FAN_FAULTS,
                    " - LATCHED, press the button to retry" if latched else "")
                self.set_fan(False)
                self.set_state(FAULT, self.fault_reason)

    # -- output -------------------------------------------------------

    def drive_outputs(self):
        self.set_fan(self.state in (RUN, BOOST))

        phase = (time.ticks_ms() // 400) % 2
        if self.state == FAULT:
            self.led_ok.value(phase)
            self.led_run.value(1 - phase)
        elif self.state == LEARN:
            self.led_ok.value(phase)
            self.led_run.value(0)
        elif self.state == BOOST:
            self.led_ok.value(0)
            self.led_run.value(phase)
        elif self.state == RUN:
            self.led_ok.value(0)
            self.led_run.value(1)
        else:
            self.led_ok.value(1)
            self.led_run.value(0)

    def handle_button(self, press):
        if press == "long":
            self.baseline = self.rh
            print("baseline relearned: %s" % self.baseline)
            self.set_state(LEARN, "manual relearn")
            self.entered_at = time.ticks_ms() - 59000   # short LEARN pass
        elif press == "short":
            if self.state == FAULT:
                self.fault_kind = None
                self.fault_until = None
                self.fan_faults = 0
                self.dht_fails = 0
                self.set_state(IDLE, "fault cleared by button")
            elif self.state == BOOST:
                self.boost_until = None
                self.set_state(IDLE, "boost cancelled")
            else:
                self.boost_until = time.ticks_add(time.ticks_ms(),
                                                  cfg.BOOST_S * 1000)
                self.set_state(BOOST, "button")

    def log(self, raw_light):
        dp = self.dew_point()
        print("%-5s rh=%s%% t=%sC dp=%s base=%s exc=%+.1f light=%d(%s) "
              "fan=%d rpm=%d %s" % (
                  self.state,
                  self.rh, self.temp,
                  "%.1f" % dp if dp is not None else "--",
                  "%.1f" % self.baseline if self.baseline is not None else "--",
                  self.excess_rh,
                  raw_light, "ON" if self.light_on else "off",
                  self.fan.value(), self.rpm,
                  self.fault_reason if self.state == FAULT else ""))

    # -- main loop ----------------------------------------------------

    def run(self):
        print("fan controller starting; learning baseline for 60 s")
        next_sample = time.ticks_ms()

        while True:
            press = self.button.poll()
            if press:
                self.handle_button(press)

            if time.ticks_diff(time.ticks_ms(), next_sample) >= 0:
                next_sample = time.ticks_add(time.ticks_ms(),
                                             cfg.SAMPLE_S * 1000)
                raw_light = self.read_light()
                self.read_climate()
                self.rpm = self.tach.rpm() if self.fan.value() else 0

                if self.state == LEARN and self.baseline is None and self.rh:
                    self.baseline = self.rh

                self.check_fan_health()
                self.decide()
                if cfg.LOG:
                    self.log(raw_light)

                # Report after deciding, so what is published is what the
                # controller actually concluded this tick. Never allowed to
                # affect control: telemetry cannot raise past here.
                if self.telem:
                    try:
                        self.telem.publish(self, raw_light, self._rssi())
                    except Exception as e:
                        print("telemetry: %s" % e)

            # Outputs first, then the network. If a connect attempt blocks for
            # a few seconds the fan is already in the state this pass decided,
            # and it simply holds there.
            self.drive_outputs()

            if self.link:
                was = self.link.online
                try:
                    self.link.poll()
                except Exception as e:
                    print("net: %s" % e)
                if was and not self.link.online and self.telem:
                    self.telem.on_disconnect()

            time.sleep_ms(20)

    def _rssi(self):
        """WiFi signal, or None when offline. Cheap and never raises."""
        try:
            if self.link and self.link.online and self.link._wlan:
                return self.link._wlan.status("rssi")
        except Exception:
            pass
        return None


if __name__ == "__main__":
    controller = Controller()
    try:
        controller.run()
    finally:
        # Never leave the fan energised if the loop dies or is stopped.
        Pin(cfg.PIN_FAN, Pin.OUT, value=0)
