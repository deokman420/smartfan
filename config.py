"""Tunables for the bathroom fan controller.

Everything you are likely to want to change lives here. Pin numbers are
GPIO numbers, not physical pin numbers.
"""

# --- Pins -----------------------------------------------------------
PIN_DHT     = 22   # DHT22 data, 10k pull-up to 3V3
PIN_LDR     = 26   # ADC0, top of the LDR divider
PIN_BUTTON  = 16   # to GND, internal pull-up
PIN_FAN     = 17   # 1k to S8050 base
PIN_TACH    = 18   # fan yellow, 10k pull-up to 3V3
PIN_LED_OK  = 19   # green
PIN_LED_RUN = 20   # red

# --- Light sensing --------------------------------------------------
# Raw ADC, 0-65535. Bright reads high.
#
# Occupancy is a STEP above the ambient light, never an absolute level.
# Absolute thresholds cannot work here for exactly the reason absolute
# humidity thresholds cannot: a sunny afternoon sits high all by itself.
# Measured in this bathroom over the four days to 2026-10-29:
#
#   truly dark, night            1600-3300   ( 2.5-5.0 %)
#   fixture on, room dark            53000   (81 %)
#   direct sun, no fixture       up to 36300 (55 %) and still climbing
#   brightest ambient seen           55800   (85 %)
#
# Sun and fixture share one band, and the old LIGHT_ON = 22000 sat less than
# halfway up the daylight ramp. The fan ran 75 min on 10-27 and 175 min on
# 10-28 in a bone-dry room, on sunlight alone. No choice of threshold fixes
# that, because the brightest ambient is above the fixture, not below it.
#
# What separates them is the RATE, and it is not close. Measured, per 2 s
# sample, over those same four days:
#
#   the fixture switching on     +51511 counts in ONE sample
#   the fastest ambient drift       +51 counts per sample (10-26 13:25)
#
# A factor of a thousand. So: track the ambient, and call it the fixture
# only when the reading jumps away from that ambient faster than any sky
# can move. The thresholds below sit two orders of magnitude clear of the
# sun and one order clear of the fixture.
LIGHT_STEP_ON  = 10000   # one-sample jump that means somebody hit a switch
LIGHT_RISE_ON  = 8000    # ...and it must land at least this far above ambient,
                         # so a jump out of a deep shadow is not a light
LIGHT_RISE_OFF = 4000    # back within this of ambient -> the fixture is off

# How the ambient estimate is allowed to move. Asymmetric, because the two
# directions mean different things: a reading that FALLS cannot be a fixture
# switching on, so it is ambient and is believed quickly, while a reading
# that RISES might be, so it is absorbed only as fast as weather can
# plausibly account for.
#
# LIGHT_AMB_UP is a hard ceiling in counts per sample rather than an EMA
# weight, and that is the point: anything slower than this is absorbed
# COMPLETELY and can never accumulate into a false trigger. An EMA cannot
# promise that -- a cloud edge crossing the sun shifts indoor light by tens
# of percent in twenty seconds, which an EMA lags and a slew limit eats. At
# 2500 it sits 49x above the fastest drift ever measured here and 20x below
# the fixture's own edge.
LIGHT_AMB_UP     = 2500  # max counts per sample the ambient may rise
LIGHT_AMB_DOWN_A = 0.25  # EMA weight for a falling reading; tau ~8 s

# The backstop. The ambient is frozen while the fixture is believed on --
# otherwise it would climb to meet the fixture and the light would read off
# with the bulb still burning. A frozen ambient can go stale, so a light_on
# that outlives this is treated as a lost edge: re-anchor the ambient to
# whatever the room reads now and start again. Every light misreading is
# therefore bounded, and bounded without anyone on a ladder.
#
# Nothing resolves the fixture once the ambient is near the rail: at 85 %
# the divider is compressed enough that the bulb adds a few hundred counts,
# under LIGHT_STEP_ON, so a blazing afternoon simply does not see the light.
# That fails safe -- a missed occupancy, not a fan running for three hours --
# and no threshold can do better from this sensor. A shade or snoot aimed at
# the fixture is the fix for that one, and it is a hardware fix.
LIGHT_MAX_ON_S = 1800

# Light alone is occupancy, not a standing instruction to ventilate. Past
# this much run time the fixture stops holding the fan on by itself and
# humidity has to justify the rest -- which both matches how a bathroom is
# used and bounds the cost of any future bad light reading to ten minutes,
# whatever its cause.
LIGHT_ONLY_MAX_S = 600

# Vestigial, and deliberately so. ota.py installs a whole batch or nothing,
# and a batch much past 60 kB does not install at all (see docs/TODO.md), so
# a change this wide has to go over in stages -- which means config.py can
# land one push AHEAD of the main.py that stops reading these. Without them
# the old main.py in the ceiling loses cfg.LIGHT_ON mid-flight, raises on its
# next sample, and rides the watchdog down to a rollback. Nothing reads them
# once the matching main.py is installed; they cost two lines, and staged
# updates are the normal case here rather than the exception.
LIGHT_ON  = 22000
LIGHT_OFF = 14000

# --- Humidity control -----------------------------------------------
# Thresholds are relative to a learned baseline, not absolute, so the
# controller behaves the same in a humid August and a dry January.
RH_RISE_ON  = 8.0    # %RH above baseline that starts the fan
RH_FALL_OFF = 3.0    # %RH above baseline at which drying is done
BASELINE_A  = 0.002  # EMA weight; ~15 min to settle at 2 s samples

# --- Timing, seconds --------------------------------------------------
SAMPLE_S     = 2     # DHT22 cannot be read faster than every 2 s
MIN_RUN_S    = 180   # never short-cycle the fan
MAX_RUN_S    = 2700  # hard stop after 45 min no matter what
                     # Was 30. A real shower measured here peaked at +38.5 %RH
                     # and the room took longer than half an hour to come back
                     # within RH_FALL_OFF -- so the cap, not the drying logic,
                     # was ending every run. It does reach +2.1 % eventually,
                     # which is what makes a longer window the right fix rather
                     # than a looser stop condition.
POST_LIGHT_S = 120   # keep running at least this long after lights out
BOOST_S      = 900   # manual button boost
LONG_PRESS_S = 2.0   # hold this long to relearn the baseline
HOLD_PRESS_S = 8.0   # ...and this long to open the WiFi provisioning window.
                     # Both LEDs flash together once you are past it, so the
                     # gesture is discoverable by holding rather than by
                     # reading this file.

# --- Fan health -------------------------------------------------------
TACH_PPR = 2     # pulses per revolution, standard for 3-wire fans
MIN_RPM  = 800   # below this while commanded on -> stall fault.
                 # Measured 4200 rpm healthy, so 800 is a wide margin that
                 # still catches a seized bearing or a severed lead. This is
                 # the only thing that notices a fan that has died in the
                 # ceiling. Set to 0 to disable.
SPINUP_S = 3     # ignore the tach while the fan gets going

# A fan still turning well after it was told to stop is not a slow coast --
# it means the switch is not in control of it. The usual cause is the fan's
# ground lead landing somewhere other than the collector, which bypasses the
# transistor entirely. Without this the controller cannot see that at all:
# the tach is otherwise only sampled while the fan is driven, so a fan stuck
# running is invisible to it and simply runs forever.
#
# An NF-A4x10 coasts for about 2-3 s, so 12 s is well clear of a normal stop.
STUCK_S = 12

# A stalled fan must not clear itself just because the humidity sensor is
# healthy. Back off, retry a few times, then latch and wait for a human.
FAULT_RETRY_S  = 300   # wait this long before retrying a stalled fan
MAX_FAN_FAULTS = 3     # consecutive stalls before latching for good

# Power-on self-test. MIN_RPM above is the only thing that notices a fan
# which has died in the ceiling, and it can only notice while the fan is
# being driven -- so a dead fan stayed invisible until the next shower asked
# for it, which could be days. This drives the fan briefly at boot so the
# question is asked at a moment somebody is present to hear the answer.
#
# Long enough to clear SPINUP_S and then measure a real tach window, which
# is one SAMPLE_S. Set to 0 to disable.
SELFTEST_S = 6

# Only on a genuine power-on, not on every restart. The alternative is a
# burst of fan every OTA push and every watchdog reset, and the port cannot
# tell those two apart anyway -- machine.reset() is implemented via the
# watchdog, so both report as "watchdog or reset" (see _boot_reason). Set
# False to test on every boot.
SELFTEST_PWRON_ONLY = True

# --- Robustness -------------------------------------------------------
# The watchdog is fed ONLY by the control loop. If the loop stops -- a hung
# network stack, a wedged driver, a bug -- the board resets rather than
# leaving a fan stuck on or a bathroom unventilated. Nothing in net.py or
# telemetry.py may feed it, or it stops proving the thing it exists to prove.
#
# 8000 ms against a 20 ms loop is a factor of 400. The only operation that
# blocks for long is the MQTT socket connect, bounded at 4 s, and MQTT_HOST
# is an IP literal so there is no DNS lookup to stall on.
WDT_TIMEOUT_MS = 8000

# Hold the existing button down through power-up to skip main.py and land at
# the REPL. No extra hardware: it reuses PIN_BUTTON.
#
# This is not optional once the controller is mounted. With autorun plus
# WiFi, a bad config or a boot loop otherwise means fetching a ladder.
SAFE_MODE_ENABLED = True

# Second route into safe mode, for tooling. The button cannot be automated,
# and once the watchdog is armed any REPL session outlasting its timeout is
# reset out from under you -- which makes the test suite, and any remote
# debugging, impossible without this.
#   mpremote connect COM5 fs touch :safemode   (then reset)
SAFE_MODE_FILE = "safemode"

# --- Identity ---------------------------------------------------------
# Topics and unique_ids assume there will eventually be more than one of
# these. Getting the shape right now costs nothing; retrofitting it later
# means renaming every entity in Home Assistant and losing their history.
ROOM       = "bathroom"        # topic segment: lowercase, no spaces
ROOM_NAME  = "Bathroom"        # display name in Home Assistant
DEVICE_ID  = "fan_bathroom"    # unique_id prefix; must be unique per device

# Display name only -- what Home Assistant shows on the device card and
# prefixes onto every friendly name. Safe to change at any time.
#
# DEVICE_ID above is the identity and must NOT follow it. Home Assistant
# keys the device off `identifiers`, so renaming here keeps all 31 entities,
# their entity_ids and their recorded history; changing DEVICE_ID would
# orphan the lot and start a second device beside the first.
DEVICE_NAME = "Bathroom Smartfan"

# --- The on-device server ---------------------------------------------
# The device serves the app itself, from flash, and answers a small JSON API
# on the same port. This is what makes a broker optional: with no Home
# Assistant and no Mosquitto anywhere on the network, http://fan.local/ is
# still a working interface to the fan.
#
# It is stepped from the control loop with the budget below, after the
# outputs have already been driven and after the watchdog has been fed by a
# pass that did its real work. Control is never waiting on a browser.
HTTP_ENABLED   = True
HTTP_PORT      = 80
HTTP_BUDGET_MS = 8      # work done per loop pass; 8 ms against a 20 ms loop
WWW_ROOT       = "www"  # the built app, gzipped, pushed by tools/build_www.py

# The name the app is reached by, so there is no IP to remember or re-learn
# after a DHCP lease changes. http://fan.local/ works on phones and laptops
# alike, answered by MicroPython's own mDNS responder -- there is no code of
# ours behind it, only the ordering in net.py that registers this name before
# the interface comes up. Verified on the bench: ping fan.local answers.
#
# It is the DHCP hostname too, so the router's client list is readable.
HOSTNAME = "fan"

# --- Stored history ---------------------------------------------------
# Without Home Assistant there is no recorder, and the app's own history is
# a rolling day inside one browser. This keeps five-minute means on flash so
# that any phone opening the app for the first time sees real history.
#
# 300 s x 7 days is 2,016 rows and 288 small writes a day, which flash will
# outlive the fan at. Raising the resolution costs writes, not just space.
HISTORY_ENABLED    = True
HISTORY_INTERVAL_S = 300
HISTORY_DAYS       = 7

# --- Telemetry --------------------------------------------------------
# 30 s keeps history readable without flooding the recorder. Any change of
# state, fan, light or fault publishes immediately regardless, so a 20 s
# run is never invisible between two samples.
PUBLISH_INTERVAL_S = 30
EXPIRE_AFTER_S     = 120   # HA marks entities unavailable after this silence

# MQTT itself is optional. Leave secrets.MQTT_HOST empty and the controller
# runs WiFi only: no broker, no Home Assistant, no discovery -- just the fan
# and its own server. See secrets.example.py.

# --- Misc -------------------------------------------------------------
LOG = True       # print a status line on every sample

# --- This particular unit ---------------------------------------------
# Everything above is the product. identity.py is this board: the room its
# owner named at setup, and the address that follows from it.
#
# It is a separate file because THIS one is versioned and shipped by OTA. A
# room name written in here would be overwritten by the next update, and the
# symptom would be a device that silently renames itself and moves house --
# new topics, new address, entities orphaned in Home Assistant.
#
# Absent on a board that was set up before this existed, or configured by
# hand, and the defaults above stand. See provision.py.
try:
    import identity as _id
    ROOM = getattr(_id, "ROOM", ROOM)
    ROOM_NAME = getattr(_id, "ROOM_NAME", ROOM_NAME)
    HOSTNAME = getattr(_id, "HOSTNAME", HOSTNAME)
except ImportError:
    pass
