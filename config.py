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

# --- Odor sensing (SGP41) -------------------------------------------
# The second sensed reason to run. Humidity covers showers; this covers the
# rest, which produces no humidity signal at all. See voc.py for the sign of
# the signal and logistics/sgp41-odor-sensing.md for the whole argument.
#
# Both thresholds ship at 0, which _voc_armed() reads as "never trigger":
# the sensor publishes and the recorder keeps it, while the fan behaves
# exactly as it did before. The right numbers are the ones a week of this
# room's own data gives up, not ones picked off a datasheet.
PIN_SDA = 0          # I2C0 SDA, physical pin 1
PIN_SCL = 1          # I2C0 SCL, physical pin 2
VOC_ENABLED     = True   # False disables the whole path, no rewiring
# This room's data has now given them up, and the device runs RISE_ON 200,
# FALL_OFF 70 from settings.json. Both ship at 0 regardless, so a reflash
# is never louder than the unit it replaced.
#
# 200 is a measurement, not a margin picked for comfort. From the 4 Nov
# overnight series (2085 samples, 6 h, first one taken in the bathroom on a
# baseline the settle gate actually allowed):
#
#   quiet air      sd 35.5 ticks, peak-to-peak 212, highest sample +125
#   a real event   peak +899, above +120 for 8.3 minutes
#
# So the two populations are three-quarters of an order of magnitude apart
# and the threshold can sit almost anywhere between them. 120 could not:
# quiet air reached +125 unaided, and it did so twice that night, starting
# the fan at 04:25 on nothing at all (humidity, dew point and temperature
# every one of them flat to 0.1 over the preceding ten minutes -- so not
# the room cooling, which is the other thing that moves this channel).
#
# The second reason is hysteresis. RISE_ON minus FALL_OFF was 50 ticks
# against a quiet sd of 35.5 -- a 1.4 sd gap, which noise crosses in both
# directions, so the decaying tail of the real event restarted the fan
# twice more with 0.6 and 1.1 minutes off in between. MIN_RUN_S bounds the
# on-time and nothing bounded the off-time; VOC_REST_S only applies after a
# MAX_RUN_S latch. At 200 the gap is 130 ticks, or 3.7 sd, and the tail no
# longer reaches back over the line.
#
# The cost is 2.8 minutes of the event's 8.3 spent below the trigger, which
# buys nothing back: one crossing is already a MIN_RUN_S run. Sampling was
# 10 s and the device triggered on a peak my log never saw, so true peaks
# sit somewhat above the recorded ones -- which is the argument for 200
# over the 150 that would also have been clean on this night's numbers.
VOC_RISE_ON     = 0      # ticks below baseline that start the fan; 0 = off
VOC_FALL_OFF    = 0      # ticks below baseline at which the air is clear
# Ticks the NOx pixel may RISE above its baseline before the odor path is
# treated as smelling combustion rather than a bathroom, and stands down.
#
# This unit is in the bathroom and the hob is not, so cooking reaching the
# sensor is a false positive: the fan running for it is exactly what is not
# wanted. Separating the two needs a signal that distinguishes them, and the
# NOx pixel turns out to be almost a pure one. Measured 4 Nov, both events on
# the same baseline a few hours apart:
#
#                       VOC excess peak   NOx RISE peak
#   bathroom odor            +899              +64
#   gas hob, kitchen         +504            +1498
#
# NOx is the stronger signal of the two by nine to one on the event it
# responds to, and it barely moves on the one it does not -- a flame makes
# NO2 and a bathroom does not. So the discriminating variable is NOx, not
# VOC: on VOC alone the cooking event is the SMALLER of the two and no
# threshold can separate them without also losing the real one.
#
# 300 sits 4.7x above the bathroom event's NOx and 5.0x below cooking's. The
# ratio NOx/VOC separates them too (0.48 against 1.85) but less widely, and
# it coupled the test to a second noisy number for nothing.
#
# This vetoes ONLY the odor path, through _voc_active(), which is also what
# stops it holding a run open. Humidity and the light are untouched: a shower
# taken while the hob is going still runs the fan, on the sensor that has no
# opinion about NO2. 0 disables the veto.
#
# One event of each kind, so the line is drawn from a single example either
# side. The margins are wide enough to be worth having now, and docs/TODO.md
# records that a second cooking event should be checked against it.
VOC_NOX_VETO    = 300
VOC_BASELINE_A  = 0.0005 # slower than BASELINE_A: odor events are shorter
                         # than showers and must not be learned as normal
# How long a pixel may refuse to settle before its baseline is seeded
# anyway. The gate asks the element whether it has stopped climbing, and a
# gate with no ceiling is a sensor that can decline to work for ever -- a
# draughty room, a part that wanders, a threshold set a little too tight.
#
# Two hours is the measured NOx warm-up (2-3 Nov, 9.2 h overnight series),
# so the ceiling should rarely be what fires. While a pixel is unsettled its
# baseline stays unseeded, which makes the odor path inert rather than
# wrong: humidity and the light still run the fan. That is the trade this
# number sets -- deaf for a bounded while against deaf for an hour on a
# baseline seeded mid-ramp, which is what the old 60 s timer bought.
VOC_SETTLE_MAX_S = 7200

VOC_RECOVER_S   = 900    # no VOC triggering this long after a steam event,
                         # because drying a wet room moves the MOX signal too
# How long the odor path may hold the baseline still before the baseline,
# not the air, becomes the likelier explanation.
#
# _voc_learning() refuses to learn while the deviation is above
# VOC_FALL_OFF, so an event cannot teach the baseline to ignore it. Left
# unbounded that is a trap: the maxed latch ALSO releases only when the
# deviation falls under VOC_FALL_OFF, so a baseline that is simply wrong --
# seeded during warm-up, or after the unit is moved to a different room --
# can neither correct itself nor let the latch go. Observed on 2026-11-02:
# a baseline seeded 60 s after boot, while the element was still coming to
# temperature, read +166 on air that had nothing wrong with it.
#
# Two hours because a real event in this room does not outlast it and a
# wrong baseline does. The gas-hob event measured here was above
# VOC_FALL_OFF for a bit over 90 minutes and decaying; a stale baseline
# does not decay at all. Past the cap the deviation is treated as the
# baseline's fault and learning resumes -- by which time MAX_RUN_S has
# long since latched, so this changes what the controller BELIEVES, not
# what the fan is doing. 0 disables the bound and restores the deadlock.
VOC_HOLD_MAX_S  = 7200
# How long the fan rests after an odor run hits MAX_RUN_S, before it may
# run again on odor that is still there.
#
# MAX_RUN_S plus the latch means "I have done what I can for this event",
# and for humidity that is true: a shower puts a finite amount of water in
# the room, the fan either clears it or cannot, and a room it cannot dry
# does not get drier for running longer. Odor is not like that. It comes
# from a source that can stop at any moment -- the hob goes off, the
# cooking ends -- and the air then clears quickly. Worse, the latch
# releases only when the deviation falls under VOC_FALL_OFF, which is
# exactly what cannot happen while the source is still going. So the fan
# gave up an hour in and then sat out the rest of the event, becoming
# available again only once there was nothing left to do. Observed
# 2026-11-02: a run started at 07:38 on gas-hob odor hit the cap at 08:40
# with the deviation at +229, seven times the stop point, and stayed
# latched with the air still bad.
#
# So an odor latch rests rather than gives up. Twenty minutes against
# MAX_RUN_S makes it roughly three parts running to one resting on a long
# event, which keeps clearing air while bounding the duty cycle -- and
# re-asks the question three times an hour instead of once. The original
# bug the cap was written for was an INSTANT restart (RUN 23:17:26 -> IDLE
# -> RUN 23:17:28); a rest this long is not that.
#
# Only the odor half rests. A humidity latch still waits for the room to
# dry, or the fan would cycle for ever on a room it cannot win. 0 restores
# the old give-up-for-good behaviour.
VOC_REST_S      = 1200
VOC_MAX_FAILS   = 10     # consecutive I2C failures before the path stands down

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
