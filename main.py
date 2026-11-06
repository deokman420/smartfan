"""Bathroom fan controller for the Pico 2 W.

Occupancy comes from the room light via an LDR; the decision to keep
running after the room empties comes from humidity measured against a
slowly learned baseline. Absolute humidity thresholds do not work --
a muggy summer day sits at 65 %RH all by itself -- so everything here
is expressed as a rise above what this room normally sits at.

This file is the largest thing shipped to the device -- about 87 kB, more
than twice any other module -- which used to mean it could not be replaced
over the air. ota.py buffered each body whole before writing it, so a file
needed one CONTIGUOUS allocation its own size, and updates stopped landing
somewhere above 60 kB while flash and heap both sat largely free. ota.py now
streams to flash in CHUNK-sized reads, and this file travelling over the air
is the test that says so. Keep it that way: a controller in a ceiling that
cannot replace its own main.py is a controller that needs a ladder.

States:
    LEARN   first minute after boot, building a baseline
    IDLE    fan off, tracking the baseline
    RUN     fan on because of light and/or humidity
    BOOST   fan on because someone pushed the button
    FAULT   sensor is not answering, or the fan is not spinning
"""

import time
from machine import Pin, ADC, I2C, WDT, reset_cause
import dht

import config as cfg
from settings import Settings
import light
import voc
import ota

# Snapshot needs no network: it is the device's view of itself, and the HTTP
# API reads it whether or not a broker exists. Telemetry, beside it, is the
# MQTT publisher and does need a link.
from telemetry import Snapshot, Telemetry

# The network layer is strictly additive. No secrets.py, or any failure while
# bringing it up, leaves a controller that still runs the fan exactly as it
# would have done offline. A bathroom extractor that stops working when the
# WiFi drops fails silently in a room nobody watches.
try:
    import secrets
    import net
    from net import Link
    _NET = True
except ImportError:
    secrets = None
    _NET = False

# The on-device server is additive in exactly the same way, and guarded the
# same way: a board that does not have these files yet -- mid-OTA, or rolled
# back to an older set -- still boots and still runs the fan.
try:
    import httpd
    import webapi
    _WEB = True
except ImportError:
    _WEB = False

try:
    import store
    _STORE = True
except ImportError:
    _STORE = False

# Changing the WiFi from the app. Additive in exactly the same way as the
# rest: a board without this file still runs the fan, still serves the app,
# and simply has no provisioning window to open.
try:
    import wifi
    _WIFI = True
except ImportError:
    _WIFI = False


LEARN, IDLE, RUN, BOOST, FAULT = "LEARN", "IDLE", "RUN", "BOOST", "FAULT"


def ms_since(t):
    return time.ticks_diff(time.ticks_ms(), t)


# Shortest network outage that may trigger a self-reset, whatever the
# setting says. Five times net.py's 60 s backoff ceiling, so no ordinary
# retry sequence can reach it. See Controller._net_stall_check.
NET_STALL_MIN_S = 300


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
    """Polled debounce. Reports short, long and hold presses on release."""

    def __init__(self, pin_no):
        self._pin = Pin(pin_no, Pin.IN, Pin.PULL_UP)
        self._down_at = None
        self._stable = 1
        self._changed_at = time.ticks_ms()

    def held_s(self):
        """How long the button has been down right now, 0 if it is not.

        The press is only reported on release, so without this an eight
        second hold is eight seconds of no feedback whatsoever. main.py uses
        it to flash both LEDs once the hold threshold is passed, which turns
        an undocumented gesture into one you find by leaning on the button.
        """
        if self._down_at is None or self._stable != 0:
            return 0.0
        return ms_since(self._down_at) / 1000.0

    def poll(self):
        """Returns None, 'short', 'long', or 'hold'."""
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
            if held >= cfg.HOLD_PRESS_S:
                return "hold"
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
        # Since when the RUN in progress has been the light's alone. Not
        # the same as when the light came on: a run started by odor or by a
        # shower and joined later by the light has already spent part of
        # its length on a reason that was not the light, and
        # LIGHT_ONLY_MAX_S is an allowance for the light. Measured 5 Nov:
        # an odor run was ten minutes old when the light came on, so the
        # allowance was gone before anyone was in the room and the fan cut
        # out the moment the air cleared, with the light still on.
        self.light_only_since = None

        # Daylight here reads brighter than the bulb does, so the fixture is
        # found by its step away from a learned ambient rather than by any
        # level. light.py carries the measurements and the reasoning.
        self.lightdet = light.Detector(self.st, log=print)
        self.light_prev = False    # light_on as decide() last saw it, for edges

        # The odor path is strictly an enhancement. A missing, dead or
        # disabled sensor must leave this controller behaving exactly as it
        # did without it -- so everything here is allowed to fail, nothing
        # here can raise FAULT, and self.voc of None means "no opinion, ever".
        # Stopping the humidity path over a secondary sensor would be a far
        # worse outcome than losing odor sensing.
        self.vocdet = voc.Detector(self.st, log=print, name="voc")
        # The NOx pixel, learned the same way and acted on by nothing. It is
        # here to answer a question one channel cannot: whether an odor event
        # is human-origin (reducing gases, VOC only) or combustion carried in
        # from the kitchen (NO2, which is what this pixel sees). Recorded and
        # published so the answer comes off a week of this room's own data,
        # which is how the VOC thresholds were set. voc.py carries why the
        # sign cannot be assumed and why there is no threshold to go with it.
        self.noxdet = voc.Detector(self.st, log=print, name="nox")
        self._comb = False     # last logged state of the combustion veto
        # When the VOC baseline seeded, which is the moment the odor path
        # would otherwise have gone live. The wait for the NOx baseline is
        # measured from here rather than from boot, because what is being
        # bounded is how long the fan stays deaf AFTER it could have heard.
        self.voc_ready_since = None
        self._nox_ready_logged = None  # last logged state of that gate
        # The NOx trend, for _combustion(). _nox_decaying starts False so
        # that a controller with no trend history yet treats a high NOx
        # reading as combustion: arriving at a high level IS a rise, and
        # the safe default for a veto is to apply it. Only an observed fall
        # turns it off. config.py carries the measurement.
        self._nox_ref = None       # the rise this trend is measured against
        self._nox_ref_at = None    # when that reference was taken
        self._nox_decaying = False
        self.voc = None            # sgp41.SGP41, or None if absent/disabled
        self.voc_recover_until = None   # VOC_RECOVER_S after a steam event
        # When the odor path first went non-quiet, so a hold that has gone
        # on too long can be told from one that is still plausible. None
        # means the path is quiet and nothing is being held.
        self.voc_hold_since = None
        if cfg.VOC_ENABLED:
            try:
                import sgp41
                bus = I2C(0, sda=Pin(cfg.PIN_SDA), scl=Pin(cfg.PIN_SCL),
                          freq=100000)
                s = sgp41.SGP41(bus, max_fails=cfg.VOC_MAX_FAILS, log=print,
                                settle_ceiling_s=getattr(
                                    cfg, "VOC_SETTLE_MAX_S", None))
                sn = s.serial()
                if sn is None:
                    # Either nothing is there, or two SGP4x are sharing the
                    # bus and ANDing each other's replies into garbage. Both
                    # read the same from here; sgp41.py's docstring has the
                    # difference and how to tell.
                    print("voc: no valid reply from 0x59, path disabled")
                else:
                    s.start()
                    self.voc = s
                    print("voc: SGP41 %s ready, conditioning" % sn)
            except Exception as e:
                print("voc: unavailable (%s)" % e)

        self.baseline = None
        self.rh = None
        self.temp = None
        self.rpm = 0
        self.dht_fails = 0
        # Total bad DHT22 reads since boot, and never reset -- not by a good
        # read, not by `clear`, not by leaving FAULT.
        #
        # dht_fails alone cannot answer "was that fault coming?", because it
        # is a CONSECUTIVE count that one good read puts back to zero.
        # Sampled every few seconds it reads 0 almost always, and a run of
        # four failures looks exactly like a quiet day. That is why the
        # 4 Nov fault has no cause: there was no record of the reads that
        # led to it. See docs/TODO.md.
        #
        # So both are published. This one says whether the sensor is
        # misbehaving at all; dht_fails says how close the next fault is.
        self.dht_bad = 0
        self.fault_reason = ""
        self.state_reason = ""
        self.fault_kind = None     # 'sensor' or 'fan'
        self.fault_until = None    # earliest retry for a fan fault
        self.fan_faults = 0        # consecutive stalls
        self.maxed = False         # hit MAX_RUN_S with the trigger still true
        self.maxed_at = None       # when, so an odor latch can rest and retry
        self.forced = False        # the current state was imposed by MODE
        self.selftest = None       # 'running' while the boot test drives the fan
        self.updater = None        # set while an OTA is in progress
        self.ota_state = "idle"
        self._ota_pending = False  # set in run(); see _wdt_due

        self.link = None
        self.prov = None           # wifi.Provisioner, once there is a Link
        self.telem = None
        self._push = False         # force a publish after a command lands
        self.wdt = None
        self.boot_reason = _boot_reason()

        # Last raw LDR reading. Kept on the controller rather than only in
        # run()'s locals so a web request can report it; read_light() is what
        # keeps it current.
        self.raw_light = 0
        self.ip = None
        self.mqtt_host = getattr(secrets, "MQTT_HOST", "") if secrets else ""

        # Everything the device knows about itself, with or without a broker.
        self.snap = Snapshot(cfg, log=print)

        # Arm the power-on self-test. Decided here rather than in the loop so
        # the reason is printed once, at the top of the log, where someone
        # reading a boot has it in front of them.
        #
        # Not while MODE says Force off. That is the one setting that means
        # "do not run this fan", chosen by somebody who may be in the bath or
        # nursing a sensor they do not trust, and a diagnostic is not a good
        # enough reason to overrule it.
        if cfg.SELFTEST_S <= 0:
            self.selftest = "disabled"
            print("selftest: disabled")
        elif self.st.mode == "Force off":
            # DEFERRED, not skipped. Force off is sticky -- it lives in
            # settings.json and survives power cuts -- so "skip the boot
            # diagnostic" used to mean "never prove this fan again", and
            # nothing anywhere said so. A unit that went back into a ceiling
            # having never turned its fan looked exactly like one that had.
            #
            # The objection to running it is that somebody said do not run
            # this fan. That objection expires the moment the mode returns
            # to Auto, and that is also the moment anyone wants to know the
            # fan still works, so decide() picks it up there.
            self.selftest = "deferred"
            print("selftest: deferred, mode is Force off - it will run "
                  "when the mode returns to Auto")
        elif cfg.SELFTEST_PWRON_ONLY and self.boot_reason != "power-on":
            self.selftest = "skipped"
            print("selftest: skipped, boot was %s" % self.boot_reason)
        else:
            self.selftest = "running"
            print("selftest: proving the fan turns, up to %ds" % cfg.SELFTEST_S)

        self.hist = None
        if _STORE and getattr(cfg, "HISTORY_ENABLED", True):
            try:
                self.hist = store.History(cfg, log=print)
                print("history: %ds buckets, %d days, %d rows on flash"
                      % (self.hist.interval_s, self.hist.days,
                         self.hist.stats()["rows"]))
            except Exception as e:
                print("history: disabled (%s)" % e)

        self.http = None
        self.api = None
        self._web_retry_at = 0     # backoff after a failed bind

        if _NET:
            try:
                cmd_topic = "fan/%s/cmd" % cfg.ROOM
                set_topic = "fan/%s/set/+" % cfg.ROOM
                self.link = Link(secrets, "fan/%s/status" % cfg.ROOM, log=print,
                                 subscribe=(cmd_topic, set_topic),
                                 on_msg=self.on_command,
                                 hostname=getattr(cfg, "HOSTNAME", None))
                # No broker configured is a supported setup, not a failure:
                # WiFi comes up, the device serves its own app, and nothing
                # publishes.
                #
                # Telemetry is built either way now. Its constructor does no
                # I/O and publish() returns immediately while the link is
                # offline, so an unused one costs a few topic strings -- and
                # building it only when a broker existed AT BOOT meant adding
                # one from the app brought MQTT up with nothing to publish
                # over it until the next reboot.
                self.telem = Telemetry(cfg, self.link, self.snap, log=print)
                if self.link.mqtt_enabled:
                    print("net: enabled, room=%s" % cfg.ROOM)
                else:
                    print("net: wifi only, no broker configured")
                if _WIFI:
                    self.prov = wifi.Provisioner(self.link, log=print)
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

        The reading is stored as well as returned, because the HTTP API has
        to report the same number the log line shows without going and
        reading the sensor from inside a web request. Storing it here, at the
        one place that takes the reading, is what stops the two diverging.
        """
        ok, run = self.led_ok.value(), self.led_run.value()
        self.led_ok.value(0)
        self.led_run.value(0)
        time.sleep_ms(5)
        raw = self.ldr.read_u16()
        self.led_ok.value(ok)
        self.led_run.value(run)

        self.update_light(raw)
        self.raw_light = raw
        return raw

    def update_light(self, raw):
        """Hand the sample to the detector and publish its verdict.

        light_on stays an attribute of the controller because decide(), the
        log, the HTTP API and Home Assistant all read it.
        """
        self.light_on = self.lightdet.update(raw)

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
            self.dht_bad += 1
            return False

    def read_voc(self):
        """One odor sample, fed through the detector. Never raises.

        The humidity reading is passed in as compensation: the SGP41 derives
        its own raw signal from element resistance, which moves with RH and
        temperature, and the fixed 50 %RH/25 C defaults would bias every
        reading in a bathroom. The DHT22 is good enough for that job, which
        is why this does not wait on an SHT40.
        """
        if self.voc is None:
            return
        try:
            # Any humid spell suppresses odor triggering for a while after
            # it. Drying a wet room moves the MOX signal on its own, so a
            # shower would otherwise hand the fan a second reason to run
            # just as the first one expired.
            if self.excess_rh > self.st.RH_FALL_OFF:
                self.voc_recover_until = time.ticks_add(
                    time.ticks_ms(), self.st.VOC_RECOVER_S * 1000)

            if not self.voc.ok:
                # Stood down for good. Drop the last reading so every
                # surface reports absence rather than a number frozen at
                # whatever the sensor saw on its way out. Both pixels, or
                # the NOx reading outlives the sensor it came from.
                self.vocdet.raw = None
                self.noxdet.raw = None
                return

            got = self.voc.read(self.rh, self.temp)
            if got is None:
                return          # still conditioning -- not an error
            # How long the odor path has been holding the baseline still.
            # Tracked here rather than inside _voc_learning() because this
            # runs exactly once per sample: a predicate that started its own
            # clock would restart it on every caller that asked, and the
            # tests ask.
            if self._voc_quiet():
                self.voc_hold_since = None
            elif self.voc_hold_since is None:
                self.voc_hold_since = time.ticks_ms()

            # One ROOM verdict for both pixels, two ELEMENT verdicts. The
            # room half is genuinely shared -- they see one sample of one
            # room, and two gates that could disagree about it would be two
            # baselines describing different places. Warm-up is not shared,
            # so each pixel still seeds on its own evidence.
            #
            # This used to carry a second claim -- that the fan must not
            # wait for "the pixel nothing acts on", which settles in two
            # hours against the VOC pixel's minutes. Both halves are now
            # wrong. The veto acts on NOx, and the measured medians are 88
            # minutes against 110 with NOx first in four of seven reboots.
            # _nox_ready() is where the fan waits; the seeding below is
            # unchanged, because what may be LEARNED and what may ACT are
            # different questions.
            learning = self._voc_learning()
            self.vocdet.update(got[0], learning and self.voc.settled_voc)
            # ...and the NOx pixel additionally refuses to learn while it is
            # itself reading combustion. Without that term the hob teaches
            # the NOx baseline that its own exhaust is normal -- measured
            # 5 Nov, the baseline walked 16300 to 16593 during one cooking
            # event -- which quietly raises the bar for every veto after it.
            # A sensor must not be allowed to calibrate itself deaf.
            self.noxdet.update(got[1], learning and self.voc.settled_nox
                               and self._nox_settled())

            if self.vocdet.base is not None and self.voc_ready_since is None:
                self.voc_ready_since = time.ticks_ms()

            self._update_nox_trend()

            ready = self._nox_ready()
            if ready != self._nox_ready_logged:
                self._nox_ready_logged = ready
                if not ready:
                    print("voc: odor path held until the NOx baseline seeds "
                          "(up to %d s)" % cfg.NOX_WAIT_MAX_S)
                elif self.noxdet.base is None:
                    print("voc: NOx baseline never seeded -- odor path live "
                          "WITHOUT a combustion veto")
                else:
                    print("voc: NOx baseline seeded -- odor path live, "
                          "combustion veto armed")

            # Say when the veto takes the odor path away and when it gives
            # it back. A suppression nobody can see is the fault that cost
            # two days here already: the fan sat still while the app showed
            # an odor excess, and nothing anywhere said why.
            comb = self._combustion()
            if comb != self._comb:
                self._comb = comb
                print("voc: %s (nox %+d, veto %d)"
                      % ("combustion - odor path standing down" if comb
                         else "combustion cleared - odor path live again",
                         -(self.excess_nox or 0),
                         getattr(self.st, "VOC_NOX_VETO", 0)))
        except Exception as e:
            # A sensor fault here is logged and dropped. It must never reach
            # set_state(FAULT): see __init__.
            print("voc: read failed (%s)" % e)

    def _voc_learning(self):
        """Is the room quiet enough, and the sensor warm enough, to learn?

        The policy lives here rather than in voc.py because it reads three
        other signals. Learning during an event would teach the baseline to
        treat the smell as normal, and then report clean air as a deviation.

        The sensor's own warm-up USED TO BE a fourth condition here, and is
        not any more -- not because it stopped mattering but because it is
        not one condition. It is two, one per pixel, and they differ by
        twentyfold: minutes for the VOC pixel against about two hours for
        the NOx pixel. Asking them as a single question meant either seeding
        the NOx baseline mid-ramp or holding the VOC baseline -- the one the
        fan acts on -- hostage to a pixel nothing acts on. So read_voc()
        asks each pixel for itself, as `settled_voc` and `settled_nox`, and
        this predicate answers only the question it can answer for both: is
        the ROOM quiet enough to learn from.

        It still matters, and for the reason it always did. The baseline is
        held in RAM, so every power cut drops it and the first quiet sample
        afterwards reseeds it; an element still climbing seeds a baseline
        that the EWMA then takes an hour to drag back. sgp41.py carries the
        measurement, and why the old 60 s timer was wrong about both pixels.

        AND NOT WHILE THE ODOR SIGNAL IS STILL UP. The other four terms stop
        this learning during a *run*, which is not the same as during an
        event. A run that ends on MAX_RUN_S latches and returns to IDLE with
        the air still bad, and every term above is then satisfied -- the
        state is IDLE, the light is off, the room is dry -- so the baseline
        walks onto the smell at cfg.VOC_BASELINE_A and the deviation decays
        without the air changing at all. Measured here on 2026-11-02: after
        an hour-long odor run hit the cap, the raw reading went 30394 to
        30399 over the following twenty minutes -- flat, no recovery -- while
        the baseline slid 30633 to 30571, so a reported excess of +240 became
        +172 purely by forgetting. The event teaches the baseline to ignore
        it, which is what voc.py's class docstring warns about and what the
        other four terms were meant to prevent.

        _voc_quiet() is the right test rather than a fresh comparison
        because it is already the condition every other caller uses for
        "the odor path has nothing to say", and it is inert when the path
        is: it returns True with no sensor, no baseline, a stood-down
        sensor, or VOC_RISE_ON at 0. So the first seed still happens, and
        an observe-only rollout learns exactly as it did before.

        BOUNDED, because unbounded it deadlocks. The first version of this
        said a stuck-high reading merely freezes the baseline, and that
        MAX_RUN_S and the latch bound the damage. They do not: the latch
        releases on `not light_on and excess_rh low and _voc_quiet()`, and
        _voc_quiet() is the same test being refused here. So a baseline
        that is wrong rather than an air that is bad -- seeded during
        warm-up, or after the unit is carried to another room -- can
        neither correct itself nor release the latch, and the odor path is
        stuck until someone reboots it or presses Relearn. Watched happening
        on 2026-11-02 on a baseline seeded 60 s after boot, reading +166 on
        air with nothing wrong with it.

        So the refusal expires. Past cfg.VOC_HOLD_MAX_S the deviation is
        taken to be the baseline's fault rather than the room's, and
        learning resumes. That is the honest reading of a deviation that
        has not decayed in two hours: real events decay and stale baselines
        do not. config.py carries the figure and why it is two hours.
        """
        return (self.state == IDLE
                and not self.light_on
                and self.excess_rh <= self.st.RH_FALL_OFF
                and (self._voc_settled() or self._voc_hold_expired())
                and self.voc is not None)

    def _odor_latch_rested(self):
        """May a latch held open only by odor let go and try again?

        Every term is load-bearing. The room must be dry and the light off,
        so humidity and occupancy have both finished with this latch and
        odor is demonstrably the only thing holding it -- releasing a
        humidity latch early is the for-ever cycle MAX_RUN_S exists to
        prevent. The path must be armed, so an absent, disabled or
        stood-down sensor can never spin the fan back up. And the rest has
        to have actually elapsed.

        A stuck-high reading cannot run the fan for ever on this: a frozen
        raw value is one the baseline learns toward, which brings the
        deviation to 0 and makes the first branch fire instead, and a
        baseline that is merely stale is corrected inside
        cfg.VOC_HOLD_MAX_S. The worst case is a bounded duty cycle for a
        couple of hours, not an unbounded one.
        """
        if not self.st.VOC_REST_S or self.maxed_at is None:
            return False
        if self.light_on or self.excess_rh > self.st.RH_FALL_OFF:
            return False
        if not self._voc_armed() or self._voc_quiet():
            return False
        return ms_since(self.maxed_at) / 1000.0 >= self.st.VOC_REST_S

    def _voc_hold_expired(self):
        """Has the odor path held the baseline still for longer than it may?

        False whenever nothing is being held, which includes every case
        where the path is inert -- voc_hold_since is only ever set by
        read_voc() on a sample the path was not quiet for.
        """
        cap = self.st.VOC_HOLD_MAX_S
        if not cap or self.voc_hold_since is None:
            return False
        return ms_since(self.voc_hold_since) / 1000.0 >= cap

    def _voc_active(self):
        """Is the odor path allowed to influence the fan at all?

        False while the sensor is absent, while it has stood down, while
        VOC_RISE_ON is 0 (the observe-only rollout setting), or before a
        baseline exists. Keeping this one predicate in front of every use is
        what makes the observe-only week genuinely observe-only -- an
        ungated hold or latch release reading a live sensor against a 0
        threshold would change the fan's behavior on day one.

        The `ok` term is not decoration. A stood-down sensor keeps its last
        reading, and read() returns None forever after, so without it the
        frozen value is compared against VOC_FALL_OFF on every pass. If the
        last thing the sensor ever saw was bad air, _voc_quiet() would be
        False for ever: the fan held open to MAX_RUN_S, latched, and the
        latch unable to release -- a dead sensor cycling the fan for good.
        """
        return (self._voc_usable()
                and self._nox_ready()
                and not self._combustion())

    def _voc_usable(self):
        """_voc_active without the veto: is there a signal here at all?

        Split out because the two questions had been conflated and the
        conflation cost a baseline. "The odor path may not act" and "there
        is nothing to read" are not the same state, and only the second is
        a reason to let the baseline learn. See _voc_settled().
        """
        return (self.voc is not None
                and self.voc.ok
                and bool(self.st.VOC_RISE_ON)
                and self.vocdet.base is not None)

    def _nox_ready(self):
        """Has the combustion veto got a baseline to work with yet?

        The odor path waits for this before it may move the fan. Without
        it, every boot opened a window in which a cooking smell could start
        the fan with nothing able to object -- the exact false positive the
        NOx pixel was fitted to reject.

        The old reasoning for not waiting was that VOC settles in minutes
        and NOx in two hours. Measured across the seven reboots on flash,
        that is wrong: median 88 minutes against 110, and NOx arrived FIRST
        in four of the seven. The wait usually costs nothing.

        BOUNDED, because "usually" is not "always" and a pixel that never
        seeds must not leave the fan deaf to odor for good. Past
        NOX_WAIT_MAX_S the path goes live without a veto, which is worse
        than having one and much better than having no odor path.

        True when there is no veto to wait for, so VOC_NOX_VETO = 0 behaves
        exactly as it did.
        """
        if not getattr(self.st, "VOC_NOX_VETO", 0):
            return True
        if self.noxdet.base is not None:
            return True
        if self.voc_ready_since is None:
            return False
        return ms_since(self.voc_ready_since) / 1000.0 >= cfg.NOX_WAIT_MAX_S

    def _voc_settled(self):
        """Is the VOC deviation small enough to learn a baseline from?

        _voc_quiet() answers a different question -- may the fan stop --
        and answers True whenever the path is inert, which is right for
        that question and wrong for this one. A combustion veto makes the
        path inert while the sensor is reading heavily polluted air, so
        _voc_learning() asking _voc_quiet() meant the veto quietly handed
        the baseline the whole cooking event to learn from.

        Measured 5 Nov: through one hob event the raw count fell 30427 to
        30160 -- the air getting worse -- while the baseline followed it
        down 30703 to 30593. The event was teaching the baseline to ignore
        it, which is the exact failure the four terms in _voc_learning()
        exist to prevent, entering by a door nobody had checked.

        Inert-because-absent still learns, so a missing or disabled sensor
        and an observe-only rollout behave exactly as they did.
        """
        if not self._voc_usable():
            return True
        exc = self.excess_voc
        if exc is None:
            return True
        return exc <= self.st.VOC_FALL_OFF

    def _combustion(self):
        """Is the NOx pixel saying this smell is a flame, not a bathroom?

        The odor path exists for a bathroom. This unit's hob is in another
        room, so cooking that reaches the sensor is a false positive, and on
        the VOC pixel it is not separable from the real thing -- measured
        4 Nov, the cooking event peaked at +504 and the genuine bathroom
        event at +899, so the unwanted one is the SMALLER signal and no VOC
        threshold can reject it without rejecting what it is for.

        NOx separates them almost cleanly: +1498 for the hob against +64 for
        the bathroom, because a flame makes NO2 and a bathroom does not. See
        VOC_NOX_VETO in config.py for both series and why the line is 300.

        Sign: the measured direction is that bad air moves the NOx raw count
        UP, which is opposite to VOC, and `excess()` is `base - raw` for
        both pixels. So a rise is a NEGATIVE excess_nox and the test needs
        the sign flipped here. voc.py's "THE NOx SIGN IS NOT CONFIRMED" is
        what this resolves; the published value keeps its old convention
        because changing it would change the recorded row's meaning.

        False whenever there is nothing to go on -- no veto, no baseline, no
        reading -- so a missing or unsettled NOx pixel leaves the odor path
        exactly as it was rather than silently disabling it.

        LEVEL AND DIRECTION. The level alone was not enough, and the way it
        failed is the reason this paragraph exists: NO2 outlives the flame
        that made it, so a hob that finished at 07:40 left a NOx rise above
        this threshold until past 08:10, and the whole of that half hour
        was a window in which the odor path was switched off. A real
        bathroom event landed in it, reading nearly three times
        VOC_RISE_ON, and the fan did not move.

        So a flame is a NOx rise that is still rising. The decay afterwards
        is the room clearing, which is the one time the odor path most
        needs to be awake. _nox_decaying is maintained by read_voc() and is
        False until a fall is actually observed, so this stays a veto by
        default and only steps aside on evidence.
        """
        veto = getattr(self.st, "VOC_NOX_VETO", 0)
        if not veto:
            return False
        exc = self.excess_nox
        if exc is None:
            return False
        if -exc < veto:
            return False
        return not self._nox_decaying

    def _nox_settled(self):
        """Is the NOx pixel quiet enough to learn a baseline from?

        The same question _voc_settled() asks of the other pixel, against
        the only threshold NOx has. Deliberately NOT _combustion(), which
        now steps aside during the decay: the decay is exactly when the
        reading is still far from normal and still must not be learned.
        """
        veto = getattr(self.st, "VOC_NOX_VETO", 0)
        if not veto:
            return True
        exc = self.excess_nox
        if exc is None:
            return True
        return -exc < veto

    def _update_nox_trend(self):
        """Track whether the NOx rise is climbing or clearing.

        Against a reference refreshed every cfg.NOX_TREND_S rather than
        against the previous sample: the loop reads this sensor far faster
        than NO2 moves, so consecutive readings differ mostly by noise and
        a sample-to-sample comparison would flap the veto on and off
        several times a minute. Over two minutes the measured hob decay
        moves 150-200 counts, three to four times NOX_TREND_EPS.

        Below the veto level there is no episode to have a direction, so
        the whole thing resets -- which is what makes a second pan going on
        half an hour later read as a fresh rise rather than as a
        continuation of the first one's tail.
        """
        veto = getattr(self.st, "VOC_NOX_VETO", 0)
        exc = self.excess_nox
        rise = None if exc is None else -exc
        if not veto or rise is None or rise < veto:
            self._nox_ref = None
            self._nox_ref_at = None
            self._nox_decaying = False
            return
        now = time.ticks_ms()
        if self._nox_ref_at is None:
            self._nox_ref = rise
            self._nox_ref_at = now
            return
        if ms_since(self._nox_ref_at) / 1000.0 < cfg.NOX_TREND_S:
            return
        was = self._nox_ref
        self._nox_ref = rise
        self._nox_ref_at = now
        if rise >= was + cfg.NOX_TREND_EPS:
            self._nox_decaying = False
        elif rise <= was - cfg.NOX_TREND_EPS:
            self._nox_decaying = True

    def _voc_armed(self):
        """As _voc_active, plus the post-steam recovery window."""
        if not self._voc_active():
            return False
        if self.voc_recover_until is not None and time.ticks_diff(
                self.voc_recover_until, time.ticks_ms()) > 0:
            return False
        return True

    def _voc_quiet(self):
        """True when the odor path has no objection to the fan stopping.

        Returns True when the path is inert, so an absent or disabled sensor
        can never hold a run open or keep the maxed latch closed.
        """
        if not self._voc_active():
            return True
        exc = self.excess_voc
        # No opinion is not an objection. _voc_active() already requires a
        # baseline, so this is the narrow case of a reading having gone
        # away under a baseline that stands -- and a path with nothing to
        # say must not be what holds the fan open.
        if exc is None:
            return True
        return exc <= self.st.VOC_FALL_OFF

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

    @property
    def excess_voc(self):
        """Ticks below the learned clean-air baseline, or None.

        Positive means worse air. None means the odor path has no opinion:
        no sensor, no reading, or no baseline yet. It is never 0 for those,
        because 0 is a real reading -- air exactly as clean as normal. See
        voc.Detector.excess().

        The SGP41's raw signal falls as reducing-gas concentration rises, so
        the deviation meaning "bad air" is baseline minus current. voc.py
        carries that reasoning and the bench check for it.
        """
        return self.vocdet.excess()

    @property
    def excess_nox(self):
        """The NOx pixel's deviation from its learned baseline, or None.

        Same `base - raw` convention as excess_voc, and deliberately not the
        same standing: the VOC sign was measured on this part and this one
        has not been, so no caller may turn this into a decision yet. It is
        reported so the sign and the scale can be read off recorded data.
        voc.py carries the argument, under "THE NOx SIGN IS NOT CONFIRMED".
        """
        return self.noxdet.excess()

    def set_state(self, new, reason=""):
        if new == self.state:
            return
        print("state: %s -> %s  %s" % (self.state, new, reason))
        # fault_reason is published on every reading. Leaving it set after
        # the controller has recovered makes a cleared fault look current.
        if self.state == FAULT and new != FAULT:
            self.fault_reason = ""
        # Published, because the app was guessing and guessing wrong. There
        # are three reasons to run now -- humidity, the light, and odor --
        # and the status page said "Clearing humidity" for all of them,
        # including a run started by odor in a room 0.9 % above baseline.
        # The controller is the only thing that knows which test it
        # actually passed, so it is the thing that says.
        self.state_reason = reason
        self.state = new
        self.entered_at = time.ticks_ms()

    def set_fan(self, on):
        if bool(on) == bool(self.fan.value()):
            return
        self.fan.value(1 if on else 0)
        self.fan_on_at = time.ticks_ms() if on else None
        self.fan_off_at = None if on else time.ticks_ms()
        self.tach.rpm()          # discard the stale window
        self.snap.note_fan(on)

    def fan_seconds(self):
        return 0 if self.fan_on_at is None else ms_since(self.fan_on_at) / 1000.0

    def fan_off_seconds(self):
        return 0 if self.fan_off_at is None else ms_since(self.fan_off_at) / 1000.0

    # The two numbers that explain a light verdict, for the log, the API and
    # the app. Read through to the detector so they cannot drift from it.
    @property
    def light_amb(self):
        return self.lightdet.amb

    def light_rise(self):
        return self.lightdet.rise(self.raw_light)

    # -- the decision -------------------------------------------------

    def decide(self):
        now = time.ticks_ms()

        # A RISING edge, not the level. IDLE used to clear the MAX_RUN_S latch
        # on `if self.light_on` -- which reads "a new occupancy event" but
        # tests a level, so a light merely still on cleared the latch on the
        # pass after it was set and RUN started again. Measured on 10-28: 175
        # minutes against a 60 minute cap. Latch release has to be an event.
        rising = self.light_on and not self.light_prev
        self.light_prev = self.light_on

        if self.light_on:
            self.dark_since = None
        else:
            if self.dark_since is None:
                self.dark_since = now

        # Since when has the light been the ONLY thing that could be asking
        # for a run? Maintained in every state, not just RUN, so the answer
        # does not depend on which pass happens to look at it -- the first
        # version was computed inside the RUN branch and cleared by
        # set_state, which meant the clock started on the second pass of a
        # run rather than the first. Harmless at a 2 s loop and wrong
        # anywhere else, which is the kind of thing that stays wrong.
        #
        # It is read against the run's own length (see LIGHT_ONLY_MAX_S
        # below), so time spent light-only in IDLE is not credited to a run
        # that has not started.
        if (self.light_on
                and self.excess_rh <= self.st.RH_FALL_OFF
                and self._voc_quiet()):
            if self.light_only_since is None:
                self.light_only_since = now
        else:
            self.light_only_since = None

        # Mode sits above the automatic logic, including LEARN. Force off is
        # how you stop the fan during a long bath without pulling power, or
        # silence a misbehaving sensor from away; force on is how you air the
        # room out. An override that waits out a 60 s baseline pass before
        # taking effect is not an override.
        #
        # FAULT still wins: forcing a stalled fan on would just re-stall it.
        mode = self.st.mode
        if mode != "Auto" and self.state != FAULT:
            self.forced = True
            if mode == "Force off":
                if self.state != IDLE:
                    self.set_state(IDLE, "forced off")
            elif self.state != RUN:
                self.set_state(RUN, "forced on")
            return

        # Releasing an override. The state we are sitting in was imposed by
        # MODE, not reached by any automatic rule, so it must not be
        # inherited: coming out of Force on used to leave the fan in RUN, and
        # RUN's own exit refuses to leave before MIN_RUN_S. The fan then ran
        # on for up to three more minutes in a dark, dry room with nothing
        # calling for it -- measured at 190 s. Forcing it off first worked
        # around it, because Force off sets IDLE outright.
        #
        # MIN_RUN_S is there so the automatic logic cannot short-cycle the
        # fan, and a forced run is not the automatic logic's run. Ending an
        # override is a deliberate act, in the same class as Force off, which
        # has always stopped the fan at once.
        #
        # Dropping to IDLE does not mean the fan stops: execution carries on
        # into the IDLE branch below on this same pass, so if the room does
        # want the fan it goes straight back to RUN with a reason of its own
        # and the fan never sees a transition.
        if self.forced:
            self.forced = False
            # A self-test that Force off deferred at boot gets its turn now.
            # Only when it was deferred: a test that already ran, failed, or
            # was disabled outright must not be restarted by toggling a
            # mode, and FAULT is not a state to start spinning a fan in.
            if self.selftest == "deferred" and self.state != FAULT:
                self.selftest = "running"
                print("selftest: mode left Force off, proving the fan now")
            if self.state != IDLE:
                self.set_state(IDLE, "override released")
                # And the forced run must not spend the automatic cap either.
                # Without this, airing the room out for an hour and then
                # returning to Auto with the room still humid would start an
                # automatic run already past MAX_RUN_S, which latches it
                # immediately.
                if self.fan.value():
                    self.fan_on_at = time.ticks_ms()

        if self.state == LEARN:
            if ms_since(self.entered_at) > 60000 and self.baseline is not None:
                self.set_state(IDLE, "baseline %.1f%%" % self.baseline)
            return

        if self.state == FAULT:
            # Clear only the kind of fault that actually recovered. A stalled
            # fan used to be cleared by a healthy DHT22, which turned a real
            # hardware fault into an endless RUN -> FAULT -> IDLE loop.
            if self.fault_kind == "selftest":
                # Nothing here can retest it. There is no run to retry, and
                # re-driving a motor that would not turn is how a seized
                # bearing gets cooked. It clears on the button, on `clear`,
                # or on the next power-on that passes -- all of which mean
                # somebody is present.
                return

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
            # last event managed. The light coming ON is that event; the
            # light merely BEING on is not.
            if rising:
                self.maxed = False
            if self.light_on and not self.maxed:
                self.set_state(RUN, "light on")
            elif self.maxed:
                # Still latched. Release once the humidity has actually come
                # down -- the same condition that would have ended the run
                # properly. Until then, running again would achieve nothing
                # the last thirty minutes did not.
                #
                # And not while the light still reads on: a dry room already
                # satisfies the humidity half, so that half alone cannot hold
                # a latch set by a light that will not go out.
                #
                # Every independent reason to run must be quiet before the
                # latch lets go. Each new sensor adds a term here: a
                # stuck-high odor reading would otherwise release the latch
                # on humidity alone and the fan would cycle for ever.
                if (not self.light_on
                        and self.excess_rh <= self.st.RH_FALL_OFF
                        and self._voc_quiet()):
                    self.maxed = False
                    self._say("room settled, free to run again")
                elif self._odor_latch_rested():
                    # Dry room, light off, and the only thing still holding
                    # the latch is odor -- which is the one reason whose
                    # source can stop at any moment, and the one whose
                    # release condition cannot come true while it has not.
                    # Resting and re-asking beats sitting out the event.
                    # config.py carries the asymmetry and the figure.
                    self.maxed = False
                    self._say("odor latch rested, free to run again")
                    print("odor: latch rested %d min, free to run again"
                          % (self.st.VOC_REST_S // 60))
            elif self.excess_rh >= self.st.RH_RISE_ON:
                self.set_state(RUN, "humidity +%.1f%%" % self.excess_rh)
            elif (self._voc_armed() and self.excess_voc is not None
                    and self.excess_voc >= self.st.VOC_RISE_ON):
                # Reached only with the light off and the room dry, which is
                # exactly the gap this sensor was bought to close: air that
                # needs clearing with nobody there to press Boost and no
                # humidity to notice.
                self.set_state(RUN, "odor +%d" % self.excess_voc)
            return

        # state == RUN
        ran = self.fan_seconds()
        if self.rpm >= cfg.MIN_RPM:
            self.fan_faults = 0          # a healthy run resets the tally
        self._refresh_run_reason(ran)
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
            self.maxed_at = time.ticks_ms()
            self.set_state(IDLE, "max run time - latched until the room "
                                 "dries or the light comes back on")
            return
        if ran < self.st.MIN_RUN_S:
            return
        # Light alone is occupancy, not a standing instruction to ventilate.
        # Past LIGHT_ONLY_MAX_S the humidity has to justify the rest, which it
        # does through the RH_FALL_OFF check below for as long as the room is
        # wet. However good the detector gets it still reads one LDR through a
        # window, and this is what bounds a bad reading to ten minutes rather
        # than an afternoon. It latches for the same reason MAX_RUN_S does.
        # ...and only when nothing else still justifies the run. Latching a
        # dry room while the odor signal is still high would stop the very
        # event the second sensor exists to catch.
        #
        # Measured from when the run BECAME the light's alone, not from
        # when it started. `ran` was the wrong clock and failed the obvious
        # way on 5 Nov: a run that began on odor was already past
        # LIGHT_ONLY_MAX_S when the light came on, so the allowance was
        # spent before anyone entered the room and the fan latched off the
        # instant the air cleared -- with the light on and someone
        # standing under it. The light then could not restart it either,
        # because the latch releases on a rising edge and the light was
        # already on, so the only way back was to flick the switch off and
        # on again. Which is what happened.
        # Never older than the run itself, so a light that was already on
        # over a dry room before anything started does not arrive with its
        # allowance spent -- and equally, a run that only became the
        # light's alone partway through is measured from that moment.
        light_only_s = 0.0
        if self.light_only_since is not None:
            light_only_s = min(ms_since(self.light_only_since) / 1000.0, ran)
        if (self.light_only_since is not None
                and light_only_s >= self.st.LIGHT_ONLY_MAX_S):
            self.maxed = True
            self.maxed_at = time.ticks_ms()
            self.set_state(IDLE, "light on %d min with a dry room - latched "
                                 "until the light goes off"
                                 % (self.st.LIGHT_ONLY_MAX_S // 60))
            return
        if self.light_on:
            return
        if self.dark_since and ms_since(self.dark_since) / 1000.0 < self.st.POST_LIGHT_S:
            return
        if self.excess_rh > self.st.RH_FALL_OFF:
            return
        # The room ends the run, not a timer -- which is the whole point of a
        # second sensed signal. Inert while the path is disabled or absent.
        if not self._voc_quiet():
            return
        self.set_state(IDLE, "dry, +%.1f%%" % self.excess_rh)

    def _refresh_run_reason(self, ran):
        """Say why the fan is running NOW, not why it started.

        set_state() writes the reason on a transition, and a run routinely
        outlives its own cause. A shower starts on the light, the light goes
        out, and humidity holds the fan open for another half hour under a
        label still reading "light on". Measured doing exactly that: 77.1
        %RH, 14.7 above baseline, light_on False, `why: "light on"`.

        This is the second half of a fix whose first half is recorded in
        set_state()'s own comment -- the status page used to guess which of
        three reasons was running the fan and guessed wrong, so the
        controller publishes the answer instead. Publishing it once, at the
        transition, left it to go stale exactly on the long runs where
        somebody actually wants to know what is holding the fan on.

        Every reason that currently applies is named, not just the first.
        During a shower more than one usually does, and picking a winner
        would invent a precedence the exit chain does not have: the light
        and the humidity each hold the run independently, and which one
        outlasts the other is not knowable from here.

        The timer-only terms appear alone or not at all. A run still inside
        MIN_RUN_S but already justified by a wet room is held by the room,
        and reporting the timer would hide the thing worth reporting.
        """
        why = []
        if self.light_on:
            why.append("light on")
        if self.excess_rh > self.st.RH_FALL_OFF:
            why.append("humidity +%.1f%%" % self.excess_rh)
        if not self._voc_quiet():
            exc = self.excess_voc
            why.append("odor" if exc is None else "odor +%d" % exc)
        if not why:
            # Nothing about the room justifies the run; a timer does. Said
            # plainly, because "the fan is on because it has not been on
            # long enough yet" is a real and perfectly good answer.
            if (self.dark_since is not None
                    and ms_since(self.dark_since) / 1000.0
                    < self.st.POST_LIGHT_S):
                why.append("running on after the light")
            elif ran < self.st.MIN_RUN_S:
                why.append("minimum run time")
        if not why:
            # The exit chain is about to end the run and will set its own
            # reason. Overwriting it here with "nothing" would replace a
            # real answer with a worse one.
            return
        self._say(", ".join(why))

    def _say(self, reason):
        """Publish a new reason without a state change.

        set_state() is the only other writer, which meant state_reason was
        only ever correct at the instant of a transition. _refresh_run_reason
        fixed that for RUN; IDLE kept whatever ended the last run, however
        long ago and however much had changed since.

        That is not cosmetic. The MAX_RUN_S latch leaves behind "latched
        until the room dries or the light comes back on", and when the latch
        did release the device went on reporting it -- to the app, to Home
        Assistant's Reason sensor, and to anyone debugging. Measured 5 Nov:
        the fan sat in IDLE reading that message for forty minutes after the
        latch had gone, which sent this session's own diagnosis down the
        wrong path until the predicates were read instead of the field.
        """
        if reason != self.state_reason:
            self.state_reason = reason
            self._push = True

    def _wdt_due(self, elapsed_ms):
        """May the loop take ownership of the watchdog on this pass?

        Normally only after a few clean passes. Arming on the first line
        would make any startup hiccup a reboot loop, and a watchdog cannot
        be switched off once it is started.

        AN OTA TRIAL BOOT IS THE EXCEPTION, and getting this wrong cost
        every update the device has ever been offered. boot_check() arms an
        8 s watchdog whenever an update is pending -- that is what catches
        main.py failing at import, where MicroPython would otherwise drop to
        a REPL nobody can reach. So on a trial boot one is ALREADY running,
        and waiting three seconds protects nothing: it spends three of the
        eight seconds the trial has, and WiFi association has to fit in what
        is left. Often it does not.

        Measured 2026-11-03 with the serial captured through every boot:
        build 22e12496 installed cleanly, then died twice at
        `net: offline -> wifi` before this line was ever reached, and the
        rollback correctly restored the old build. From the network that
        reads as a push that did nothing, which is how it sat in TODO.md for
        weeks as a size limit. It was never about size -- the board had
        596 kB of flash and 325 kB of heap free.
        """
        if not cfg.WDT_TIMEOUT_MS:
            return False
        return self._ota_pending or elapsed_ms > 3000

    def step_selftest(self):
        """Prove the fan turns, at boot. True while the test still owns it.

        The fan is driven by drive_outputs for as long as this returns True,
        so the tach gets a real window rather than being asked about a fan
        nobody switched on.

        Deliberately thin, because the hard part already existed:
        check_fan_health has always faulted a commanded fan that reports no
        RPM. What was missing was anything that commands the fan when a human
        is present. So this supplies the command and the verdict, and the
        verdict is its own fault kind -- "the fan did not turn at power-on"
        is a more useful sentence than "fan stalled", and it must not expire
        on a retry timer the way a mid-run stall does.
        """
        if self.selftest != "running":
            return False

        ran = self.fan_seconds()
        if ran >= cfg.SPINUP_S and self.rpm >= cfg.MIN_RPM:
            self.selftest = "passed"
            print("selftest: fan reached %d rpm after %.0fs" % (self.rpm, ran))
            return False

        if ran >= cfg.SELFTEST_S:
            self.selftest = "failed"
            self.fault_kind = "selftest"
            self.fault_reason = (
                "fan did not turn at power-on (%d rpm after %ds, needs %d) "
                "- check the fan, its supply and the tach lead"
                % (self.rpm, int(ran), cfg.MIN_RPM))
            self.set_fan(False)
            self.set_state(FAULT, self.fault_reason)
            return False

        return True

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
        # The self-test commands the fan before any state has an opinion
        # about it -- at boot the controller is in LEARN, which drives
        # nothing. See step_selftest.
        self.set_fan(self.selftest == "running"
                     or self.state in (RUN, BOOST))

        phase = (time.ticks_ms() // 400) % 2

        # Held past the WiFi threshold: both LEDs together, which is a
        # pattern no controller state uses. Checked before the state machine
        # so the feedback is immediate and unambiguous -- let go now and the
        # window opens.
        held = self.button.held_s()
        if held >= cfg.HOLD_PRESS_S:
            self.led_ok.value(phase)
            self.led_run.value(phase)
            return
        if held >= cfg.LONG_PRESS_S:
            # Past the relearn threshold, not yet past the WiFi one: both
            # LEDs STEADY, which no controller state uses either. That makes
            # the two hold gestures one progression a thumb can follow --
            # steady means "let go now and it relearns", flashing means "keep
            # going and the WiFi window opens" -- so the relearn is findable
            # by leaning on the button instead of by reading this file.
            #
            # Without it the first feedback of any kind arrived at 8 s, which
            # is already past relearn: holding on for want of a signal is how
            # you open the provisioning window while trying to reseed a
            # baseline. The confirmation afterwards is the LEARN blink the
            # relearn drops the controller into.
            self.led_ok.value(1)
            self.led_run.value(1)
            return
        if self.prov is not None and self.prov.is_open:
            # Window open: a double-blink on the OK led -- on, off, on, then
            # a pause five slots long.
            #
            # This was an even blink told apart from LEARN's only by rate,
            # 150 ms against 400 ms. That is not a distinction: it does not
            # survive a glance from the doorway, and it does not survive an
            # edit to either period, which could silently merge "building a
            # baseline" with "waiting for a password" and leave both tests
            # passing. A rhythm survives both. Nothing else here stutters
            # the green LED, so green-in-twos means one thing only.
            slot = (time.ticks_ms() // 150) % 8
            self.led_ok.value(1 if slot in (0, 2) else 0)
            self.led_run.value(0)
            return
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
                self.set_state(IDLE, "boost canceled via mqtt")
        elif cmd == "relearn":
            self.maxed = False
            self.baseline = self.rh
            # Every learned baseline, or Relearn half-answers the request.
            self.vocdet.relearn()
            self.noxdet.relearn()
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
        if press == "hold":
            # Opening the window is all this does. The credentials arrive
            # over the API afterwards, from a phone with a keyboard, because
            # a device with one button and two LEDs is not where anyone
            # should be typing a WPA2 passphrase.
            if self.prov is not None:
                self.prov.open("button")
            else:
                print("wifi: no provisioner (offline build)")
        elif press == "long":
            self.baseline = self.rh
            self.vocdet.relearn()
            self.noxdet.relearn()
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
                self.set_state(IDLE, "boost canceled")
            else:
                self.boost_until = time.ticks_add(time.ticks_ms(),
                                                  self.st.BOOST_S * 1000)
                self.set_state(BOOST, "button")

    def log(self, raw_light):
        """One line per sample, the only view of the controller over USB.

        Every sensed quantity appears here beside the state it produced, so
        a bench session can see why the fan did what it did without the
        dashboard. Each odor field is gated on its own value being present,
        so `--` means "nothing to say" and a number always means a real
        reading -- including `+0`, which is air exactly as clean as normal
        and no longer indistinguishable from an absent sensor.
        """
        dp = self.dew_point()
        raw_voc = self.vocdet.raw
        exc_voc = self.excess_voc
        raw_nox = self.noxdet.raw
        exc_nox = self.excess_nox
        print("%-5s rh=%s%% t=%sC dp=%s base=%s exc=%+.1f "
              "light=%d amb=%s rise=%+d(%s) voc=%s odr=%s nox=%s nxd=%s "
              "fan=%d rpm=%d %s" % (
                  self.state,
                  self.rh, self.temp,
                  "%.1f" % dp if dp is not None else "--",
                  "%.1f" % self.baseline if self.baseline is not None else "--",
                  self.excess_rh,
                  raw_light,
                  "%d" % self.light_amb if self.light_amb is not None else "--",
                  self.light_rise(), "ON" if self.light_on else "off",
                  "%d" % raw_voc if raw_voc is not None else "--",
                  "%+d" % exc_voc if exc_voc is not None else "--",
                  "%d" % raw_nox if raw_nox is not None else "--",
                  "%+d" % exc_nox if exc_nox is not None else "--",
                  self.fan.value(), self.rpm,
                  self.fault_reason if self.state == FAULT else ""))

    # -- the on-device server -----------------------------------------

    def web_step(self):
        """Serve the app and the API, a few milliseconds at a time.

        Started lazily: the socket cannot bind before WiFi is up, and a board
        with no secrets.py never gets here at all. Once bound it stays bound
        through any number of WiFi drops, because it listens on 0.0.0.0 and a
        new DHCP lease does not invalidate that.

        A failed bind backs off rather than retrying on the next pass. Port 80
        can be held briefly by the previous socket after a reset, and a retry
        every 20 ms would fill the log with the same line several hundred
        times before it succeeded.

        Never raises. The fan does not care whether anyone is watching.
        """
        if not _WEB or not getattr(cfg, "HTTP_ENABLED", True):
            return
        if self.link is None or not self.link.wifi_ok:
            return
        if self.http is None and time.ticks_diff(self._web_retry_at,
                                                 time.ticks_ms()) > 0:
            return
        try:
            if self.http is None:
                self.api = webapi.Api(self, cfg, self.snap, self.hist,
                                      log=print)
                self.http = httpd.Server(
                    handler=self.api.handle,
                    root=getattr(cfg, "WWW_ROOT", "www"),
                    port=getattr(cfg, "HTTP_PORT", 80),
                    log=print)
                if not self.http.start():
                    self.http = None
                    self._web_retry_at = time.ticks_add(time.ticks_ms(), 5000)
                    return
                name = getattr(cfg, "HOSTNAME", None)
                print("http: app at http://%s/%s"
                      % (self.link.ip or "?",
                         "  (http://%s.local/)" % name if name else ""))

            self.ip = self.link.ip
            self.http.step(getattr(cfg, "HTTP_BUDGET_MS", 8))
        except Exception as e:
            print("http: %s" % e)

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
                # After the climate read, which supplies its compensation.
                self.read_voc()
                # Sample the tach whichever way the fan is commanded. Reading
                # it only while driven is what made a stuck-on fan invisible.
                measured = self.tach.rpm()
                driven = bool(self.fan.value())
                self.rpm = measured if driven else 0
                self.rpm_idle = 0 if driven else measured

                if self.state == LEARN and self.baseline is None and self.rh:
                    self.baseline = self.rh

                # The self-test owns the fan and the verdict while it runs, so
                # the generic stall check does not race it to a less specific
                # fault, and decide() does not switch the fan off underneath
                # it. Both resume on the pass after it finishes.
                if not self.step_selftest():
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
            if sampled:
                # One payload per sample, read by everything that wants one:
                # the MQTT publish, and the five-minute history bucket. Built
                # once so the broker and the stored record can never disagree
                # about what this sample was.
                try:
                    p = self.snap.payload(self, raw_light, self.rssi(),
                                          self.link)
                except Exception as e:
                    p = None
                    print("snapshot: %s" % e)

                if p is not None and self.hist is not None:
                    self.hist.note(p)

                if p is not None and self.telem:
                    try:
                        self.telem.publish(self, raw_light, self.rssi(),
                                           force=self._push, payload=p)
                        self._push = False
                    except Exception as e:
                        print("telemetry: %s" % e)
            sampled = False

            # The single feed point. Deliberately after the outputs are
            # driven, so a loop that reaches here has done a full useful
            # pass -- not merely survived one.
            if self.wdt:
                self.wdt.feed()
            elif self._wdt_due(ms_since(started)):
                self.wdt = WDT(timeout=cfg.WDT_TIMEOUT_MS)
                print("wdt: armed at %d ms%s" % (
                    cfg.WDT_TIMEOUT_MS,
                    " (trial boot: boot_check already armed one)"
                    if self._ota_pending else ""))

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
                self._net_stall_check()

            # After the link, so an attempt is judged on the association the
            # poll above just made rather than on the one before it. Wrapped
            # like everything else out here: a provisioning bug must not be
            # able to stop a fan.
            if self.prov is not None:
                try:
                    self.prov.poll()
                except Exception as e:
                    print("wifi: %s" % e)

            # Last in the pass, after the watchdog has already been fed by a
            # loop that did its real work. A browser cannot delay the fan by
            # more than this step's budget, and if it ever did hang, the
            # watchdog resets us -- which is the correct outcome for a web
            # server that has wedged a fan controller.
            self.web_step()

            time.sleep_ms(20)

    def _net_stall_check(self):
        """Reboot if the network has been unreachable for too long.

        The one failure the rest of the system cannot see. The hardware
        watchdog asks "is the loop running", and on 2026-11-04 the answer
        was yes for the whole 23 minutes during which nothing could reach
        the device: it sampled, controlled the fan, seeded a gas baseline
        and wrote an unbroken history to flash, all while MQTT and HTTP
        were dead. net.poll() retries forever behind a 60 s backoff cap and
        never escalates, so the outage ended when a human pulled the plug.

        A reset is a blunt instrument and that is the point -- it is the
        only tool that reaches a wedged radio. The threshold must therefore
        sit well above every outage that fixes itself: a router reboot, a
        channel change, a few failed associations. 15 minutes is roughly
        fifteen times the backoff ceiling.

        The fan is left alone. A reset interrupts a run, so the check is
        skipped while the fan is on and takes effect once it stops: a
        stalled link is not a reason to stop extracting, and a few more
        minutes offline costs nothing next to cutting a run short. For the
        same reason it never fires during an OTA.

        0 disables it, which is also what happens off-device where there is
        no link to judge.
        """
        secs = getattr(self.st, "NET_STALL_S", 0)
        if not secs or self.link is None:
            return
        # A floor enforced here and not only in the spec, because this is
        # the one setting whose bad value is a reboot loop: at 5 s the
        # device would reset every 5 s for as long as the outage lasted,
        # and each reset costs the learned baselines. settings.json can be
        # hand-edited and carried between builds, so the consumer refuses
        # to act on a value it cannot defend rather than trusting that
        # whatever wrote it was sane. 0 still means off; anything else is
        # at least NET_STALL_MIN_S.
        if secs < NET_STALL_MIN_S:
            secs = NET_STALL_MIN_S
        # self.fan.value(), not a cached flag: the pin is the authority on
        # whether air is moving, and it is what telemetry derives "fan_on"
        # from. An earlier draft read a self.fan_on that does not exist,
        # which took the controller down on the first pass after boot.
        if self.fan.value() or self.updater is not None or self._ota_pending:
            return
        try:
            down = self.link.down_s
        except Exception:
            return
        if down < secs:
            return
        print("net: unreachable for %d s, resetting" % down)
        try:
            net.mark_netstall()       # survives the reset; published retained
        except Exception:
            pass
        try:
            import machine
            time.sleep_ms(50)
            machine.reset()
        except Exception as e:
            print("net: reset failed (%s)" % e)

    def rssi(self):
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
