# Bathroom Smartfan — Pico 2 W

A humidity-, odor- and occupancy-driven extractor fan controller. The room
light is the occupancy signal; humidity decides how long the fan keeps running
after the room empties; a gas sensor catches the thing neither of those
notices.

Absolute humidity thresholds don't work for this — a muggy August day sits at
65 %RH by itself and the fan never stops. So everything is expressed as a **rise
above a slowly learned baseline** for that particular room.

Absolute *light* thresholds don't work either, for the same reason and more
sharply. Measured here, direct sun reaches 85 % of the ADC's range while the
ceiling fixture reads 81 %: daylight is **brighter than the bulb**, so no level
separates them, and the fan ran 175 minutes in a dry room on sunlight alone
before this was understood. What does separate them is speed — the fixture moves
the reading 51511 counts in a single 2 s sample, and the fastest the sky has ever
managed in this room is 51. So the light is found by the **step away from a
learned ambient**, never by a level, and light alone stops holding the fan on
after `LIGHT_ONLY_MAX_S`. Details and the measurements are in `config.py`.

Absolute *odor* thresholds don't work either, and this one is the hardest of
the three. The SGP41's two pixels report raw tick counts with no absolute
meaning — the number depends on the element, its age, the room and how long it
has been powered — so once again the fan acts on a **deviation from a learned
baseline**, and note the sign: the signal **falls** as the air gets worse. Two
measurements are worth carrying before trusting it. Both pixels track room
humidity hard enough that **0.3 °C of overnight cooling moves the odor channel
by its entire trigger threshold**. And the element settles far more slowly than
a timer suggests — about **30 minutes** for the VOC pixel and about **two
hours** for NOx, measured over a 9.2 h overnight series — so a baseline is
seeded only once that pixel's raw count has actually stopped moving, per pixel,
never on a stopwatch.

Nothing acts on the NOx pixel. It is recorded so that odor-with-NOx (a gas
flame in the kitchen) can one day be told from odor alone (the bathroom), but
its quiet-room wander is larger than any threshold worth setting, so it drives
nothing today.

**Odor triggering ships off** — `VOC_RISE_ON = 0`. The sensor reads, records
and publishes from the first boot; you set a threshold once you have watched
your own room for a few days. Humidity and light work regardless.

**Status:** running on hardware and commissioned. Three sensing paths —
humidity, light and odor — plus fan switching, both status LEDs, the button
and the tachometer, all verified on the board. Over-the-air updates install and
hold as of 2026-11-03. Parked work is in [`docs/TODO.md`](docs/TODO.md).

> [!WARNING]
> **The V2 build in [`hardware/v2/`](hardware/v2/) connects to mains voltage.**
> Mains wiring can kill you and can start a fire. Build it only if you are
> competent to work on mains, keep it unplugged while it is open, and follow
> your local electrical code, which may require a licensed electrician for a
> fixed ceiling installation. The V2 board has **not been fabricated or tested
> yet**. It is a design to check, not a proven build. The V1 build below runs
> on 5 V USB and has no mains anywhere. This project comes with **no warranty**
> (see [License](#license)).

## Build your own

There are two builds. Start with V1.

| | V1: the bench unit | V2: the ceiling unit |
|---|---|---|
| Status | Running and commissioned | Designed, not fabricated |
| Power | 5 V USB | 120 V mains in, 12 V and 5 V made on the board |
| Fan | Noctua NF-A4x10 5V (stirs air, does not extract it) | Noctua NF-F12 industrialPPC-2000 IP67 |
| Humidity | DHT22 | SHT40 |
| Wiring | Breadboard: [`build-sheet.html`](build-sheet.html) | KiCad PCB: [`hardware/v2/`](hardware/v2/) |
| Parts | The [Hardware](#hardware) table below | [`hardware/v2/shopping-list-v2.html`](hardware/v2/shopping-list-v2.html) |
| Printed parts | None | [`hardware/v2/cad/`](hardware/v2/cad/) (not test-fitted yet) |

The firmware today drives V1: it reads a DHT22 and, if fitted, an SGP41. The
SHT40 driver (`sht4x.py`) exists and is tested, but `main.py` does not use it
yet, so V2 needs that change before it reads humidity.

### First install

You need Python 3 with `pip install mpremote`, Node.js 20+ for the app, and a
USB cable. The examples use `COM5`. On Linux or macOS the port is something
like `/dev/ttyACM0`.

1. **Flash MicroPython.** Use the Pico 2 W ARM build, not RISC-V, from
   <https://micropython.org/download/RPI_PICO2_W/>. Hold BOOTSEL, plug in,
   and drag the `.uf2` onto the RP2350 drive.
2. **Copy the firmware.** Do it in safe mode, so the controller (and its 8 s
   watchdog) is not running while files land:

   ```bash
   mpremote connect COM5 fs touch :safemode + reset
   mpremote connect COM5 fs cp boot.py ota.py main.py config.py settings.py \
     telemetry.py light.py sgp41.py sht4x.py voc.py net.py mqtt.py ringbuf.py \
     calibrate.py provision.py improv.py httpd.py webapi.py store.py wifi.py :
   ```

   That is `SHIP` in `tools/ota_publish.py` plus the two recovery files. Do
   **not** copy `secrets.py`, `settings.json` or `identity.py` from this repo.
   They belong to one device.
3. **Build and push the app.** This builds the app, copies it to flash, takes
   the board out of safe mode and restarts it:

   ```bash
   cd app && npm ci && cd ..
   python tools/build_www.py --push COM5
   ```

4. **Connect it to Wi-Fi.** With no `secrets.py` the board starts its own
   setup network. Follow [Setting up a unit](#setting-up-a-unit). Over USB,
   copy `secrets.example.py` to `secrets.py`, fill it in, and copy it across
   in safe mode instead.
5. **Calibrate the light** in the room it will live in. See
   [Calibration](#calibration--the-light-in-the-room).
6. **Optional: Home Assistant.** Set `MQTT_HOST` in `secrets.py`, then add the
   integration in [`custom_components/smartfan/`](custom_components/smartfan/)
   through HACS.

After the first install, firmware updates go over the air. See
[Updating over the air](#updating-over-the-air).

## Hardware

| | |
|---|---|
| MCU | Raspberry Pi Pico 2 W (RP2350), MicroPython 1.29.0 |
| Temperature / humidity | DHT22 3-pin breakout, silkscreen `VCC │ S │ GND` |
| Air quality | Sensirion SGP41, I²C `0x59` — VOC and NOx pixels |
| Occupancy | LDR in a divider against 10 kΩ |
| Fan | Noctua NF-A4x10 5V, 3-wire, 50 mA |
| Switch | S8050 NPN, low-side, 1 kΩ base resistor |
| Status | Green + red LED, 330 Ω each |
| Input | Tactile switch, internal pull-up |

### Pin map

Physical pins run 1–20 down the left side and 40–21 down the right, so a
right-side pin is `41 − row`. Every signal is on the right **except the I²C
pair**, which sits at the top of the left side on pins 1 and 2.

| Signal | GPIO | Pin |
|---|---|---|
| SGP41 SDA | GP0 | 1 |
| SGP41 SCL | GP1 | 2 |
| DHT22 data | GP22 | 29 |
| Light level | GP26 / ADC0 | 31 |
| Button | GP16 | 21 |
| Fan drive | GP17 | 22 |
| Fan tach | GP18 | 24 |
| Green LED | GP19 | 25 |
| Red LED | GP20 | 26 |

The fan is switched on the **low side**, on/off only. PWM would chop the
tachometer's reference and make the RPM reading meaningless — and a working
tach is the only thing that notices a fan that has died in a ceiling.

## Deferred work

Parked items, each with what unblocks it: [`docs/TODO.md`](docs/TODO.md).

## Files

| File | |
|---|---|
| `main.py` | The controller. Autoruns at power-up. |
| `config.py` | Every tunable: pins, thresholds, timings. Versioned. |
| `calibrate.py` | Bench helpers — `light()`, `sensor()`, `fan()`, `leds()`. |
| `ota.py` | Over-the-air updates with automatic rollback. |
| `boot.py` | Runs before `main.py`. The recovery path — never updated over the air. |
| `light.py` | Is the room light on? The ambient tracker that tells the fixture from daylight. |
| `sgp41.py` | The gas sensor driver. Owns conditioning, and the per-pixel settle gate. |
| `voc.py` | The learned odor baseline and the deviation arithmetic, one per pixel. |
| `settings.py` | Runtime overrides on flash, so a threshold survives a reboot. |
| `wifi.py` | Changing the network from the app, with the old credentials as the rollback. |
| `provision.py` | First-run setup: the captive portal and the join flow. |
| `improv.py` | Improv Serial, so a cable can provision it from a browser. |
| `net.py` | WiFi + MQTT supervision, polled from the loop. Never blocks it. |
| `telemetry.py` | The device's own snapshot, plus Home Assistant discovery and publishing. |
| `mqtt.py` | MQTT 3.1.1 client. Publishes telemetry and subscribes for commands. |
| `ringbuf.py` | Bounded FIFO for samples taken while the broker is unreachable. |
| `httpd.py` | Non-blocking HTTP server, stepped by the control loop. |
| `webapi.py` | The device's JSON API. A second transport onto the same commands. |
| `store.py` | A week of five-minute means on flash, so history is not one browser's. |
| `secrets.example.py` | Credentials template. Copy to `secrets.py` (gitignored). |
| `tools/build_dashboard.py` | Builds the Home Assistant dashboard over the WebSocket API. |
| `tools/ota_publish.py` | Builds a manifest, serves it, tells the device to update. |
| `tools/build_www.py` | Gzips the built app into `www/` and pushes it to flash. |
| `build-sheet.html` | Wiring: 34 steps, a tappable breadboard diagram and a schematic. |
| `troubleshoot.html` | Diagnostics: serial-log decoder, fault tree, expected voltages. |
| `hardware/v2/` | The V2 mains board (KiCad, Gerbers), its build docs, and the printed parts in `cad/`. |
| `custom_components/smartfan/` | The Home Assistant integration, installable through HACS. |

Both HTML files are standalone — open them directly in a browser.

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

## Getting it running

MicroPython for the Pico 2 W, the ARM build, not RISC-V:
<https://micropython.org/download/RPI_PICO2_W/>. Hold BOOTSEL, plug in, and
drag the `.uf2` onto the RP2350 drive.

Firmware updates go over the air with `python tools/ota_publish.py` (see
[Updating over the air](#updating-over-the-air)). The app bundle goes over
USB with `python tools/build_www.py --push COM5`. `boot.py` and `ota.py`
are the recovery path and go over USB when they themselves change.
`secrets.py` is per device and is copied on its own, further down.

Copying `config.py`, `main.py` and `calibrate.py` and resetting, which is
what this section used to say, is not an install. The controller imports a
dozen other modules, and those three files are not a bootable set.

Watch a running board with `mpremote connect COM5`. Ctrl-C gets a REPL while
the loop is running. `mpremote exec` soft-resets and stops `main.py`, so it
is the wrong tool for a check you wanted the loop to survive.

### Calibration — the light, in the room

Occupancy is a step, not a level. Daylight in this room gets brighter than
the bulb, so no pair of absolute thresholds can separate them, and
`LIGHT_ON` / `LIGHT_OFF` are vestigial names kept so an older `main.py`
still imports if `config.py` lands a push ahead of it. The live thresholds
are `LIGHT_STEP_ON`, `LIGHT_RISE_ON` and `LIGHT_RISE_OFF`.

Run this in the bathroom, in the mounting position, and flick the fixture
a few times during the minute:

```bash
mpremote connect COM5 exec "import calibrate; calibrate.light()"
```

It prints suggested values for those three, from the biggest one-sample
jump. The measurements and why a level cannot work are in `light.py` and
[`docs/daylight-is-brighter-than-the-bulb.md`](docs/daylight-is-brighter-than-the-bulb.md).

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

## Roadmap

- **Phase 1** — local control. ✅ Done.
- **Phase 2** — WiFi, MQTT, Home Assistant, remote configuration, logged
  history. Plan: [`docs/phase-2-plan.md`](docs/phase-2-plan.md).
  - **2a** — telemetry only, no inbound control. ✅ Done.
  - **2b** — remote configuration via MQTT subscribe.
  - **2c** — watchdog, backoff, offline buffering, safe-mode boot pin.
  - **2d** — dashboard (Home Assistant's own first).
- **Phase 3** — a real ceiling extractor on mains, in a proper enclosure.
  Designed, not built: [`hardware/v2/`](hardware/v2/).
- **Phase 4** — provisioning and the app.
- **Phase 5** — the device serves the app and its own history, so a broker
  and Home Assistant are both optional. ✅ Done. Plan:
  [`docs/phase-5-plan.md`](docs/phase-5-plan.md).
- **Phase 6** — multi-room.

## Setting up a unit

What somebody who is not you does, holding a board and a phone.

1. **Power it up.** With no `secrets.py` — or with the template's
   `your-ssid` still in it — the controller provisions before anything else
   runs, and never touches that path again once it has credentials.
2. **Join `Smartfan-XXXX`** from a phone. The network name and its password
   are on the label; `python tools/device_label.py COM5` prints one. Both are
   derived from the chip's unique id, so they are stable for the life of the
   board and recoverable without a serial cable. It is WPA2 rather than open,
   because an open setup network is a window in which anyone in range can
   provision your fan.
3. **The setup page opens by itself** — a DNS responder answers every query
   with the device, so the captive-portal probes iOS, Android and Windows
   fire all land on it. It asks four things:

   | | |
   |---|---|
   | Network, password | picked from a scan taken before the AP came up |
   | **Room** | free text, default `bathroom` |
   | **Country** | the Wi-Fi regulatory domain, guessed from the phone |

4. **It joins, and the setup network disappears** — one antenna, so the board
   cannot be an access point and a client at once. The page says so before it
   happens, because otherwise it reads as a failure.
5. **Solid green light, and the page has told you the address.** That last
   part matters more than it sounds: the device has no screen, and a light
   can say "connected" but not "open this".

Nothing is written until the join succeeds, so a wrong password costs you a
retry rather than a half-configured device.

### The room decides the name

The room is free text and everything follows from its slug: `Master Bath`
becomes `master-bath`, which is the MQTT topic segment, the display name, and
the address `http://master-bath-fan.local/`. A second unit is therefore
`ensuite-fan.local` rather than a second `fan.local` that answers at random.

It is written to **`identity.py`, not `config.py`**, and that distinction is
load-bearing: `config.py` is versioned and shipped by OTA, so a room name
written there would be silently overwritten by the next update — the device
would rename itself, change address, and orphan its Home Assistant entities.
`identity.py` is in `ota.PROTECTED` and is never sent.

A board with no `identity.py` — one set up before this existed, or configured
by hand — keeps the `config.py` defaults, which is why this repo's own unit is
still `fan.local`.

### Alternative: over USB

Improv Serial, driven from improv-wifi.com in Chrome. No app and no captive
portal, but it needs a cable, so it is the bench path rather than the ceiling
path. It carries no room field — the protocol has no place for one — so a unit
provisioned that way keeps whatever it is already called.

## Networking

Optional and strictly additive. **Without `secrets.py` the controller runs
exactly as it does offline** — it prints `net: disabled` and carries on. An
extractor that stops working when the WiFi drops fails silently, in a room
nobody watches, and the first symptom is a mouldy ceiling.

### Three setups, in order of how much has to be running

| | What is needed | What you get |
|---|---|---|
| **Offline** | nothing but power | The fan. All of the control logic is on the board. |
| **Device only** | WiFi | The fan, plus the app at `http://fan.local/` and a week of history — served by the board itself. Leave `MQTT_HOST` empty. |
| **With a broker** | WiFi + MQTT | All of the above, plus Home Assistant, its recorder and its automations. |

The middle row is the one worth being explicit about, because it did not
used to exist. The app talked to an MQTT broker, and a broker in practice
means Home Assistant's Mosquitto add-on — so "local first" quietly meant
"as long as you run Home Assistant". Now the device serves the app off its
own flash and answers a small JSON API, and a user with none of that has a
working interface to their fan.

**Home Assistant did not lose anything.** With `MQTT_HOST` set, discovery,
the 31 entities, the recorder and the dashboard all work exactly as before.
It went from being the only way in to being one of two.

### The device's own server

```
GET  /                       the app, gzipped, off flash
GET  /api/state              the same JSON snapshot MQTT publishes
GET  /api/info               identity, build, link and history status
GET  /api/history?since=<s>  stored rows as CSV text, oldest first
POST /api/cmd                boost | cancel | relearn | clear | reset-settings
POST /api/set/<PARAM>        a tunable's new value
```

`/api/history` is CSV rather than JSON, and that is the board's constraint
rather than a taste: a week of rows parsed into memory and serialised costs
about **890 bytes of heap each**, measured — roughly 1.8 MB against the
430 kB that exists, and it raised `MemoryError` at 512 rows. The stored files
are already line-oriented text, so they are streamed straight off flash one
line at a time and the app does the parsing, where there is room for it.

The last row is the bucket still being filled, which is on no file yet.
Without it the newest row a client could get was the last bucket to *close* —
between one and two intervals behind the clock, and stamped with its bucket's
start — so the join between the stored series and the browser's own live
stream was always wider than the gap the charts draw as a break. Every client
opened on a hole of several minutes ending at the moment it connected, and
the data was never missing: it was in the accumulator, unasked for.

Served by `httpd.py`, which is stepped from the control loop for a few
milliseconds per pass, after the outputs are already driven. **Control is
never waiting on a browser**, and a wedged request cannot stall the fan —
if one ever did, the watchdog resets the board, which for a web server that
has hung a fan controller is the right outcome.

Writes go through `controller.on_command()` — the same function the MQTT
subscription calls, reached by synthesising the topic the command would have
arrived on. One command path with two doors into it, so the validation and
clamping cannot drift between them.

There is **no authentication**, deliberately. This is a LAN device whose
physical control is a button on the wall, and a password on a thing with no
screen buys little. What does exist: writes need POST, so no link or
prefetch can boost the fan; CORS is reflected only for local origins, so a
page on the internet cannot drive it; and `update` — the one command that
changes what the device runs — is refused over HTTP and stays on the MQTT
path, which has credentials. It is also refused while the button window is
open, because the window is what lets someone change the broker, and one
change that works closes the window. If that trade is wrong for you, set
`HTTP_ENABLED = False`.

Push the app to a board with:

```bash
python tools/build_www.py --push COM5     # builds, gzips, copies to flash
python tools/build_www.py --port COM5     # just ask the board what is free
```

172 kB on flash for the whole bundle, of which the MQTT client is 106 kB and
loads only if someone chooses broker mode. A board reports 2.5 MB free, so
that is about 7 %.

### `http://fan.local/`

There is no responder of ours behind that name. MicroPython on this port
already has one — which is why an mDNS module of our own could never bind
port 5353 — and the only thing that makes it work is the ordering in
`net.py`: `network.hostname()` has to be called **before** the interface
comes up, because the built-in responder registers the name as the netif
appears. Set it afterwards and nothing resolves, which is precisely the bug
this had. Measured on the bench: ping `fan.local`, 1–3 ms.

### A week of history, on the fan

Without Home Assistant there is no recorder, and the app's own buffer covers
only the time that browser has had it open — so a new phone opened on an
empty chart. `store.py` keeps **five-minute means for seven days** in two
alternating append-only files, and the app backfills from them on connect.
That is 2,016 rows and 288 small writes a day.

Home Assistant's recorder was the obvious alternative and is still not used
for this: it would add a second credential, a second service that has to be
up, and a dependency on the very thing the app is meant to work without.

```bash
cp secrets.example.py secrets.py     # then fill in, it is gitignored
mpremote connect COM5 fs cp secrets.py :secrets.py
mpremote connect COM5 exec "import machine; machine.reset()"
```

Use a **dedicated broker account per device** so one can be revoked without
touching the others. On the Mosquitto add-on:

```bash
mosquitto_passwd -b /share/mosquitto/accesscontrol pico-fan-bathroom <password>
```

### Dashboard

```bash
python tools/build_dashboard.py      # idempotent; safe to re-run
```

Eight sections, 52 cards, every one of the 31 entities used. Lovelace config
cannot be written over REST, so this goes through the WebSocket API. Re-running
it reports any entity the dashboard references that no longer exists, and any
entity that exists but is not shown.

Entities appear in Home Assistant automatically via MQTT discovery. State is
published every 30 s, and immediately on any change of state, fan, light or
fault — so a 20-second run is never invisible between two samples.

### Topics

```
fan/<room>/status                            online | offline  (LWT, retained)
fan/<room>/state                             JSON snapshot     (retained)
fan/<room>/cmd                               a command verb
fan/<room>/set/<PARAM>                       a tunable's new value
homeassistant/<kind>/<device_id>/<key>/config  discovery       (retained)
```

`ROOM`, `ROOM_NAME` and `DEVICE_ID` in `config.py` make this multi-room from
the start. Retrofitting that later means renaming every entity in Home
Assistant and losing its history.

## Updating over the air

```bash
python tools/ota_publish.py --dry-run    # what would change
python tools/ota_publish.py              # serve, and tell the device to pull
```

Builds a manifest, serves this directory over HTTP, and publishes
`update <url>` to the device. It downloads each changed file, verifies its
sha256, and installs **only once every file has verified** — a half-applied
update is never a state the device can reach.

### It cannot brick the device

That is the whole design, and it is tested by deliberately pushing a
`main.py` that cannot be imported:

```
ota: installed 1 file(s), resetting
[board resets]   [board resets]   [board resets]
fan controller starting; learning baseline for 60 s
telemetry: announced 30 entities
```

Three resets, then healthy code running again — unattended.

The failure that needs catching is `main.py` raising at import: MicroPython
then drops to the REPL and sits there, unreachable over the network, with no
second boot to trigger anything. So `boot.py` arms a watchdog whenever an
update is pending. Healthy code takes over feeding it; broken code does not,
the board resets, and after two failed attempts `boot.py` restores the
`.bak` files and reboots into the old build.

An update is only *kept* once the new code has run for 60 seconds without
faulting. Until then the rollback is still armed.

### Knowing what is actually on it

```
build   local 8e669867   device 8e669867   MATCH
```

The device hashes its own tracked files at boot and publishes the result as
a **Build** entity. `ota_publish.py` computes the same hash locally and
compares. Without this, the only way to know what an update really put on a
device in a ceiling is to go and get it.

`TRACKED` lives in `telemetry.py` and the publisher reads it from there
rather than keeping its own copy — two lists like that drift, and a drifted
copy reports a mismatch that is not one.

**`boot.py` and `ota.py` are never sent over the air.** They are the recovery
path, and a recovery path you can break remotely is not one. Flash those over
USB. `secrets.py` and `settings.json` are excluded too — they belong to the
device, not the build.

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

## License

- **Firmware, app, Home Assistant integration and tools:** GNU GPL v3.0. See
  [`LICENSE`](LICENSE).
- **Hardware** (everything in [`hardware/`](hardware/): schematic, PCB,
  Gerbers, CAD scripts and STLs): CERN Open Hardware Licence v2, Strongly
  Reciprocal. See [`hardware/LICENSE`](hardware/LICENSE).
- **Bundled third-party files** keep their own licenses: Inter and JetBrains
  Mono (SIL Open Font License, [`app/LICENSES/`](app/LICENSES/)) and three.js
  (MIT, [`hardware/v2/cad/vendor/LICENSE-three.txt`](hardware/v2/cad/vendor/LICENSE-three.txt)).

Both licenses come with no warranty. You build and install this at your own
risk, and that matters most for anything connected to mains.
