# How it works

## States

| State | LEDs | |
|---|---|---|
| LEARN | green blinking | First 60 s, building the *humidity* baseline. Fast, even blink |
| IDLE, VOC still settling | green, one slow flash | Humidity is done. The odor pixel has not gone flat yet: one 400 ms flash every 2.4 s. Light and humidity still run the fan |
| IDLE | green solid | Fan off, and the odor pixel has settled — or there is no odor sensor |
| RUN | red solid | Fan on — light, humidity and/or odor |
| BOOST | red blinking | 15-minute manual override |
| FAULT | alternating | Sensor silent, or fan commanded on but not turning |
| *button held 2 s* | both solid | Let go now and it relearns |
| *button held 8 s* | both blinking together | Let go now and the WiFi window opens |
| *WiFi window open* | green double-blink | Waiting for a password — see “Setting up a unit” |

Short button press boosts for 15 minutes, or clears a fault.

**Hold two seconds to relearn every baseline** — humidity, odor and NOx — then
let go. Both LEDs come on solid the moment the gesture is long enough, so you
can feel for it rather than count; keep holding and they start flashing
together instead, which is the WiFi window and not what you wanted. The green
blink that follows is LEARN, reseeding from the air the room has now.

This is the one control that needs no app and no network, and it is the right
one to reach for after moving the unit, after a power cut that reseeded a
baseline mid-event, or whenever the fan is running on air that smells of
nothing.

## Control logic

- Baselines update **only** while IDLE, dark, dry and with the odor path
  quiet, so neither a shower nor a smell can teach the controller that it is
  normal. The odor baseline additionally waits for its own pixel to settle,
  and a refusal to learn expires after `VOC_HOLD_MAX_S` — otherwise a baseline
  that is simply *wrong* could never correct itself.
- Fan starts on light-on, at `RH_RISE_ON` (+8 %) above baseline, or at
  `VOC_RISE_ON` ticks of odor deviation — the last of which is `0`, meaning
  off, until you set it.
- After the light goes out it keeps running until humidity falls within
  `RH_FALL_OFF` (+3 %) of baseline and the odor has cleared to
  `VOC_FALL_OFF`.
- A humid spell suppresses odor triggering for `VOC_RECOVER_S` afterwards:
  drying a wet room moves the gas signal on its own, and that is not a second
  reason to run.
- Bounded by a 5-minute minimum (`MIN_RUN_S`), a 2-minute post-light minimum
  and a 45-minute hard stop.
- A stall fault latches, counts to 3, and backs off 5 minutes between retries —
  it does not clear just because the humidity sensor is healthy.
- Hitting `MAX_RUN_S` **latches**. Without that the cap protects nothing: idle
  re-reads the same humidity, finds it still above the trigger, and starts
  another full run seconds later. The latch clears when the room actually
  dries, when the light comes back on, or on a boost or relearn — and, if
  odor alone is holding it, after `VOC_REST_S`, because an odor source can
  stop at any moment and sitting out the event helps nobody.
- `why` on the dashboard names every reason that currently applies and is
  recomputed each pass, not frozen at whatever started the run.

## What a real shower looks like

Measured in the room, 40 mm fan:

| | |
|---|---|
| Baseline | 53.1 % |
| Peak | **91.6 % RH, +38.5 % above baseline**, four minutes in |
| Fast phase | −5 %/min while airborne steam clears |
| Slow phase | −1 %/min, then −0.5 %/min as surfaces evaporate |
| Plateau | **68–69 %, about +15 %**, and it stops falling |

The plateau is the finding — but it is not permanent. Left alone the room
does come back: it was measured at **+2.1 %** later the same evening, inside
`RH_FALL_OFF`. So the drying logic works; it simply needs longer than half an
hour, which is why `MAX_RUN_S` moved from 30 to 45 minutes rather than the
stop condition being loosened.

That is not a tuning problem. A 0.25 W, 40 mm fan stirs air; it does not
extract it. The number to design phase 3 against: **pull the room from 91 %
back under 56 % within 30 minutes.**

## Commissioned values

Measured on the built hardware:

| | |
|---|---|
| Fan | 4,080–4,206 rpm (rated 4,500 at a true 5 V) |
| DHT22 | 25.0 °C, 52.3 %RH, 3/3 reads |
| Stall margin | 5.1× over `MIN_RPM` |
| Fan pin at boot | 0 — the base pulldown holds it off before code runs |
| Light, bathroom dark | 4,800–6,225 raw |
| Light, bathroom lit | 30,000–32,500 raw |
| Autonomous run | dark → LEARN → IDLE → light → RUN → fan at 4,013 rpm |

The last row is the whole thing working unattended: it learned a baseline in
the dark, idled, saw the light come on, started the fan, and the tachometer
confirmed it. No button, no command.

## A note on the transistor

If the fan runs when the base is **low** and stops when it's driven **high**, the
part is a PNP. S8050 (NPN) and S8550 (PNP) are complementary parts in identical
TO-92 packages with near-identical markings, and kits mix them. Diode-test it
before fitting: red probe on the middle leg should read ~0.7 V to both outer
legs. Black-on-middle reading 0.7 instead means PNP, and it will never work here
in any orientation.

A PNP in this socket also sits with its base-emitter junction reverse-biased
near 5 V against a 6 V limit, which destroys it. That cost two parts and most of
an evening. `troubleshoot.html` covers the full test.
