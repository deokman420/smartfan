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
from machine import Pin, ADC, WDT, reset_cause
import dht

import config as cfg
from settings import Settings
import ota

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


def _boot_reason():
    """Why we started, read from the port's own constants.

    A hardcoded table is wrong here: on RP2350 WDT_RESET is 3, which a
    generic mapping happily labels "hard reset" and so hides the single
    most important thing the diagnostic exists to report.

    The port cannot fully separate the two, because machine.reset() is
    itself implemented via the watchdog. In the field that ambiguity does
    not matter -- nothing calls machine.reset() in normal operation, so a
    cause of WDT_RESET means the control loop stopped feeding.
    """
    try:
        import machine
        c = reset_cause()
        if c == getattr(machine, "PWRON_RESET", 1):
            return "power-on"
        if c == getattr(machine, "WDT_RESET", 3):
            return "watchdog or reset"
        return "cause %s" % c
    except Exception:
        return "unknown"


def safe_mode_requested():
    """True if safe mode was asked for, by button or by flag file.

    Two routes on purpose:

    BUTTON -- hold PIN_BUTTON through power-up. No extra hardware, no jumper
    to locate on a ladder. Sampled over 400 ms because a single read could
    catch a contact bounce and strand the controller at the REPL.

    FILE -- create the file named by SAFE_MODE_FILE. The button cannot be
    automated, and once the watchdog is armed any REPL session longer than
    its timeout gets reset out from under you. Tooling needs a way in that
    does not involve a finger.
    """
    if not cfg.SAFE_MODE_ENABLED:
        return False

    try:
        import os
        if cfg.SAFE_MODE_FILE in os.listdir():
            print("safe mode: %s present" % cfg.SAFE_MODE_FILE)
            return True
    except Exception:
        pass

    pin = Pin(cfg.PIN_BUTTON, Pin.IN, Pin.PULL_UP)
    held = 0
    for _ in range(20):
        if pin.value() == 0:
            held += 1
        time.sleep_ms(20)
    if held >= 18:
        print("safe mode: button held at boot")
        return True
    return False


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

        self.st = Settings(log=print)

        self.state = LEARN
        self.entered_at = time.ticks_ms()
        self.fan_on_at = None
        self.fan_off_at = time.ticks_ms()
        self.rpm_idle = 0          # tach while commanded OFF; should be zero
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
        self.maxed = False         # hit MAX_RUN_S with the trigger still true
        self.updater = None        # set while an OTA is in progress
        self.ota_state = "idle"

        self.link = None
        self.telem = None
        self._push = False         # force a publish after a command lands
        self.wdt = None
        self.boot_reason = _boot_reason()
        if _NET:
            try:
                cmd_topic = "fan/%s/cmd" % cfg.ROOM
                set_topic = "fan/%s/set/+" % cfg.ROOM
                self.link = Link(secrets, "fan/%s/status" % cfg.ROOM, log=print,
                                 subscribe=(cmd_topic, set_topic),
                                 on_msg=self.on_command)
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

        if raw >= self.st.LIGHT_ON:
            self.light_on = True
        elif raw <= self.st.LIGHT_OFF:
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
        self.fan_off_at = None if on else time.ticks_ms()
        self.tach.rpm()          # discard the stale window
        if self.telem:
            self.telem.note_fan(on)

    def fan_seconds(self):
        return 0 if self.fan_on_at is None else ms_since(self.fan_on_at) / 1000.0

    def fan_off_seconds(self):
        return 0 if self.fan_off_at is None else ms_since(self.fan_off_at) / 1000.0

    # -- the decision -------------------------------------------------

    def decide(self):
        now = time.ticks_ms()

        if self.light_on:
            self.dark_since = None
        elif self.dark_since is None:
            self.dark_since = now

        # Mode sits above the automatic logic, including LEARN. Force off is
        # how you stop the fan during a long bath without pulling power, or
        # silence a misbehaving sensor from away; force on is how you air the
        # room out. An override that waits out a 60 s baseline pass before
        # taking effect is not an override.
        #
        # FAULT still wins: forcing a stalled fan on would just re-stall it.
        mode = self.st.mode
        if mode != "Auto" and self.state != FAULT:
            if mode == "Force off":
                if self.state != IDLE:
                    self.set_state(IDLE, "forced off")
            elif self.state != RUN:
                self.set_state(RUN, "forced on")
            return

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

            if self.fault_kind == "stuck":
                # Clears itself the moment the fan actually stops, so fixing
                # the wiring needs no button press and no reboot.
                if self.rpm_idle < cfg.MIN_RPM:
                    self.fault_kind = None
                    self.set_state(IDLE, "fan stopped, stuck fault cleared")
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

            # A new occupancy event clears the latch: someone is back in the
            # room, which is a fresh reason to run regardless of what the
            # last event managed.
            if self.light_on:
                self.maxed = False
                self.set_state(RUN, "light on")
            elif self.maxed:
                # Still latched. Release once the humidity has actually come
                # down -- the same condition that would have ended the run
                # properly. Until then, running again would achieve nothing
                # the last thirty minutes did not.
                if self.excess_rh <= self.st.RH_FALL_OFF:
                    self.maxed = False
            elif self.excess_rh >= self.st.RH_RISE_ON:
                self.set_state(RUN, "humidity +%.1f%%" % self.excess_rh)
            return

        # state == RUN
        ran = self.fan_seconds()
        if self.rpm >= cfg.MIN_RPM:
            self.fan_faults = 0          # a healthy run resets the tally
        if ran >= self.st.MAX_RUN_S:
            # Latch. Without this the cap protects nothing: IDLE immediately
            # re-reads the same humidity, finds it still above RH_RISE_ON,
            # and starts another full-length run two seconds later -- for
            # ever, on any room the fan cannot dry. Measured doing exactly
            # that: RUN 23:17:26 -> IDLE -> RUN 23:17:28.
            #
            # Hitting the cap means "I have done what I can for this event",
            # not "brief pause".
            self.maxed = True
            self.set_state(IDLE, "max run time - latched until the room "
                                 "dries or the light comes back on")
            return
        if ran < self.st.MIN_RUN_S:
            return
        if self.light_on:
            return
        if self.dark_since and ms_since(self.dark_since) / 1000.0 < self.st.POST_LIGHT_S:
            return
        if self.excess_rh > self.st.RH_FALL_OFF:
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
        # Still turning long after being told to stop: the switch is not in
        # control. Latching, because it cannot be fixed from here -- the fan
        # has a path to ground that does not go through the transistor, and
        # no amount of driving GP17 low will change that.
        if (not self.fan.value() and self.rpm_idle >= cfg.MIN_RPM
                and self.fan_off_seconds() > cfg.STUCK_S):
            if self.fault_kind != "stuck":
                self.fault_kind = "stuck"
                self.fault_reason = (
                    "fan still running %d rpm %ds after being switched off "
                    "- check the fan's black lead is in row 38, not a rail"
                    % (self.rpm_idle, int(self.fan_off_seconds())))
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

    def on_command(self, topic, payload):
        """Inbound MQTT command. Runs on the main thread via link.poll().

        Must be fast and must never raise: it is called from inside the MQTT
        reader, and an exception here would tear down the link. Unknown
        commands are logged and ignored rather than guessed at.
        """
        try:
            raw = payload.decode().strip()
        except Exception:
            return
        cmd = raw if "/set/" in topic else raw.lower()
        print("cmd: %s -> %s" % (topic.rsplit("/", 2)[-1], cmd))

        # fan/<room>/set/<PARAM> carries a value; the command topic carries
        # a verb. Split on the topic so a value can never be read as a verb.
        if "/set/" in topic:
            name = topic.rsplit("/", 1)[-1].upper()
            ok, result = self.st.set(name, cmd)
            if ok:
                print("set: %s = %s" % (name, result))
                self._push = True
            else:
                print("set: rejected %s=%r (%s)" % (name, cmd, result))
            return

        if cmd.startswith("update"):
            if self.updater is not None:
                print("ota: already running")
                return
            parts = raw.split(None, 1)
            if len(parts) < 2:
                print("ota: need a base url, e.g. 'update http://192.168.0.9:8000'")
                return
            self.updater = ota.Updater(parts[1].strip(), log=print)
            self.ota_state = "starting"
            self._push = True
            return

        if cmd == "reset-settings":
            self.st.reset()
            self._push = True
            return

        if cmd == "boost":
            self.maxed = False
            self.boost_until = time.ticks_add(time.ticks_ms(),
                                              self.st.BOOST_S * 1000)
            self.set_state(BOOST, "boost via mqtt")
        elif cmd == "cancel":
            if self.state == BOOST:
                self.boost_until = None
                self.set_state(IDLE, "boost cancelled via mqtt")
        elif cmd == "relearn":
            self.maxed = False
            self.baseline = self.rh
            self.set_state(LEARN, "relearn via mqtt")
            self.entered_at = time.ticks_ms() - 59000
        elif cmd == "clear":
            self.fault_kind = None
            self.fault_until = None
            self.fan_faults = 0
            self.dht_fails = 0
            if self.state == FAULT:
                self.set_state(IDLE, "fault cleared via mqtt")
        else:
            print("cmd: ignoring unknown %r" % cmd)
            return

        # Drive the outputs now rather than waiting for the loop, so the fan
        # responds to the press immediately, then republish so the dashboard
        # reflects it without waiting for the 30 s tick.
        self.drive_outputs()
        self._push = True

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
                                                  self.st.BOOST_S * 1000)
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
        print("boot: %s" % self.boot_reason)
        next_sample = time.ticks_ms()
        started = time.ticks_ms()
        sampled = False
        raw_light = 0
        self._ota_pending = ota.pending()
        if self._ota_pending:
            print("ota: update on trial, committing after %ds healthy"
                  % ota.COMMIT_S)

        while True:
            press = self.button.poll()
            if press:
                self.handle_button(press)

            if time.ticks_diff(time.ticks_ms(), next_sample) >= 0:
                next_sample = time.ticks_add(time.ticks_ms(),
                                             cfg.SAMPLE_S * 1000)
                raw_light = self.read_light()
                self.read_climate()
                # Sample the tach whichever way the fan is commanded. Reading
                # it only while driven is what made a stuck-on fan invisible.
                measured = self.tach.rpm()
                driven = bool(self.fan.value())
                self.rpm = measured if driven else 0
                self.rpm_idle = 0 if driven else measured

                if self.state == LEARN and self.baseline is None and self.rh:
                    self.baseline = self.rh

                self.check_fan_health()
                self.decide()
                sampled = True

            # Outputs first, then the network. If a connect attempt blocks for
            # a few seconds the fan is already in the state this pass decided,
            # and it simply holds there.
            self.drive_outputs()

            # Publish AFTER the outputs are driven. Reporting before meant a
            # state change and the pin change landed in different messages,
            # so Home Assistant briefly showed "RUN" with the fan still off.
            # Never allowed to affect control: telemetry cannot raise here.
            if sampled:
                # Log after the outputs too, for the same reason: printing
                # before them showed lines like "IDLE ... fan=1", which reads
                # as a fault and is only a one-tick lag.
                if cfg.LOG:
                    self.log(raw_light)
            if sampled and self.telem:
                sampled = False
                try:
                    self.telem.publish(self, raw_light, self._rssi(),
                                       force=self._push)
                    self._push = False
                except Exception as e:
                    print("telemetry: %s" % e)
            sampled = False

            # The single feed point. Deliberately after the outputs are
            # driven, so a loop that reaches here has done a full useful
            # pass -- not merely survived one.
            if self.wdt:
                self.wdt.feed()
            elif cfg.WDT_TIMEOUT_MS and ms_since(started) > 3000:
                # Armed only after a few clean passes. Enabling it on the
                # first line would make any startup hiccup a reboot loop,
                # and a watchdog cannot be switched off once started.
                self.wdt = WDT(timeout=cfg.WDT_TIMEOUT_MS)
                print("wdt: armed at %d ms" % cfg.WDT_TIMEOUT_MS)

            # One OTA step per pass. A download that hangs stops the loop and
            # the watchdog resets us, which for an update is the right
            # outcome -- the staged files are discarded and the old code
            # boots again untouched.
            if self.updater is not None:
                self.updater.step()
                self.ota_state = self.updater.status
                if self.updater.done:
                    ok = self.updater.ok
                    reset_needed = ok and "installed" in self.updater.status
                    self.updater = None
                    self._push = True
                    if reset_needed:
                        self.set_fan(False)
                        self.drive_outputs()
                        time.sleep_ms(400)      # let the last publish leave
                        import machine
                        machine.reset()

            # An update is only kept once the new code has run healthily for
            # a while. Until then boot.py is still holding a rollback ready.
            if (self._ota_pending and ms_since(started) > ota.COMMIT_S * 1000
                    and self.state != FAULT):
                try:
                    ota.commit()
                except Exception as e:
                    print("ota: commit failed (%s)" % e)
                self._ota_pending = False
                self._push = True

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
    if safe_mode_requested():
        # No loop, no watchdog, fan off. The board sits at the REPL so a bad
        # config can be fixed without a ladder.
        Pin(cfg.PIN_FAN, Pin.OUT, value=0)
        g = Pin(cfg.PIN_LED_OK, Pin.OUT)
        r = Pin(cfg.PIN_LED_RUN, Pin.OUT)
        for _ in range(6):
            g.value(1); r.value(1); time.sleep_ms(80)
            g.value(0); r.value(0); time.sleep_ms(80)
        print()
        print("=" * 52)
        print(" SAFE MODE")
        print(" main loop not started, watchdog not armed, fan off")
        print(" release the button, or remove %s, then reset" % cfg.SAFE_MODE_FILE)
        print("=" * 52)
    else:
        # Provisioning, before anything else drives a pin. Wrapped like every
        # other additive subsystem: a failure here must still leave a working
        # fan, and an un-provisioned controller runs offline perfectly well.
        # It only triggers when there is nothing usable to connect with, so
        # the normal boot path never touches it.
        try:
            import provision
            if provision.needs_provisioning():
                provision.run()          # resets on success
        except Exception as e:
            print("provision: skipped (%s)" % e)

        controller = Controller()
        try:
            controller.run()
        finally:
            # Never leave the fan energised if the loop dies or is stopped.
            Pin(cfg.PIN_FAN, Pin.OUT, value=0)
