# Bathroom fan controller — Pico 2 W

A humidity- and occupancy-driven extractor fan controller. The room light is the
occupancy signal; humidity decides how long the fan keeps running after the room
empties.

Absolute humidity thresholds don't work for this — a muggy August day sits at
65 %RH by itself and the fan never stops. So everything is expressed as a **rise
above a slowly learned baseline** for that particular room.

**Status:** phase 1 complete and commissioned on hardware. Fan switching, both
sensors, both status LEDs, the button and the tachometer all verified.

## Hardware

| | |
|---|---|
| MCU | Raspberry Pi Pico 2 W (RP2350), MicroPython 1.29.0 |
| Temperature / humidity | DHT22 3-pin breakout, silkscreen `VCC │ S │ GND` |
| Occupancy | LDR in a divider against 10 kΩ |
| Fan | Noctua NF-A4x10 5V, 3-wire, 50 mA |
| Switch | S8050 NPN, low-side, 1 kΩ base resistor |
| Status | Green + red LED, 330 Ω each |
| Input | Tactile switch, internal pull-up |

### Pin map

Physical pins run 1–20 down the left side and 40–21 down the right, so a
right-side pin is `41 − row`. Every signal is on the right.

| Signal | GPIO | Pin |
|---|---|---|
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

## Files

| File | |
|---|---|
| `main.py` | The controller. Autoruns at power-up. |
| `config.py` | Every tunable: pins, thresholds, timings. Versioned. |
| `calibrate.py` | Bench helpers — `light()`, `sensor()`, `fan()`, `leds()`. |
| `net.py` | WiFi + MQTT supervision, polled from the loop. Never blocks it. |
| `telemetry.py` | Home Assistant discovery and state publishing. |
| `mqtt.py` | MQTT 3.1.1 client. Publish-only until phase 2b. |
| `ringbuf.py` | Bounded FIFO for samples taken while the broker is unreachable. |
| `secrets.example.py` | Credentials template. Copy to `secrets.py` (gitignored). |
| `build-sheet.html` | Wiring: 34 steps, a tappable breadboard diagram and a schematic. |
| `troubleshoot.html` | Diagnostics: serial-log decoder, fault tree, expected voltages. |

Both HTML files are standalone — open them directly in a browser.

## States

| State | LEDs | |
|---|---|---|
| LEARN | green blinking | First 60 s, building the baseline |
| IDLE | green solid | Fan off, tracking the baseline |
| RUN | red solid | Fan on — light and/or humidity |
| BOOST | red blinking | 15-minute manual override |
| FAULT | alternating | Sensor silent, or fan commanded on but not turning |

Short button press boosts for 15 minutes, or clears a fault. Hold two seconds to
relearn the baseline.

## Control logic

- Baseline updates **only** while IDLE and dark, so a shower can never teach the
  controller that humid is normal.
- Fan starts on light-on, or at `RH_RISE_ON` (+8 %) above baseline.
- After the light goes out it keeps running until humidity falls within
  `RH_FALL_OFF` (+3 %) of baseline.
- Bounded by a 3-minute minimum, a 2-minute post-light minimum and a 30-minute
  hard stop.
- A stall fault latches, counts to 3, and backs off 5 minutes between retries —
  it does not clear just because the humidity sensor is healthy.
- Hitting `MAX_RUN_S` **latches**. Without that the cap protects nothing: idle
  re-reads the same humidity, finds it still above the trigger, and starts
  another full run seconds later. The latch clears when the room actually
  dries, when the light comes back on, or on a boost or relearn.

## Getting it running

```bash
# MicroPython for Pico 2 W — the ARM build, not RISC-V
# https://micropython.org/download/RPI_PICO2_W/
# hold BOOTSEL, plug in, drag the .uf2 onto the RP2350 drive

pip install mpremote
mpremote connect COM5 fs cp config.py :config.py
mpremote connect COM5 fs cp main.py :main.py
mpremote connect COM5 fs cp calibrate.py :calibrate.py
mpremote connect COM5 exec "import machine; machine.reset()"
```

Watch it run with `mpremote connect COM5`. Ctrl-C gets a REPL while the loop
is running.

### Calibration — required before install

`LIGHT_ON` / `LIGHT_OFF` ship as placeholders. Run this **in the bathroom, in
the final mounting position**, fixture on for half the window and off for the
other half:

```bash
mpremote connect COM5 exec "import calibrate; calibrate.light()"
```

It prints suggested thresholds. Put them in `config.py`.

The values in `config.py` are now **measured in the bathroom**, in the
mounting position: dark 4,800–6,225, lit 30,000–32,500, giving `LIGHT_OFF`
14,000 and `LIGHT_ON` 22,000.

The bench values they replaced (6,000 / 11,500) looked fine and were wrong in
a way that only shows up in the room: a bathroom is never truly dark, and
`LIGHT_OFF` at 6,000 sat *inside* the dark-state noise. The controller never
reliably saw the light go out, so the fan ran indefinitely.

**A threshold has to clear the quiet band, not sit in it.** Calibrate where it
will live, with the fixture it will actually see.

## What a real shower looks like

Measured in the room, 40 mm fan:

| | |
|---|---|
| Baseline | 53.1 % |
| Peak | **91.6 % RH, +38.5 % above baseline**, four minutes in |
| Fast phase | −5 %/min while airborne steam clears |
| Slow phase | −1 %/min, then −0.5 %/min as surfaces evaporate |
| Plateau | **68–69 %, about +15 %**, and it stops falling |

The plateau is the finding. This fan removes moisture at roughly the rate wet
surfaces release it — it holds the line but cannot win, so `RH_FALL_OFF` of
3 % is unreachable and `MAX_RUN_S` becomes the only thing that ends a run.

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
- **Phase 3** — mains relay for a real ceiling extractor, in a proper enclosure.
- **Phase 4** — multi-room.

## Networking

Optional and strictly additive. **Without `secrets.py` the controller runs
exactly as it does offline** — it prints `net: disabled` and carries on. An
extractor that stops working when the WiFi drops fails silently, in a room
nobody watches, and the first symptom is a mouldy ceiling.

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

Entities appear in Home Assistant automatically via MQTT discovery. State is
published every 30 s, and immediately on any change of state, fan, light or
fault — so a 20-second run is never invisible between two samples.

### Topics

```
fan/<room>/status                            online | offline  (LWT, retained)
fan/<room>/state                             JSON snapshot     (retained)
homeassistant/<kind>/<device_id>/<key>/config  discovery       (retained)
```

`ROOM`, `ROOM_NAME` and `DEVICE_ID` in `config.py` make this multi-room from
the start. Retrofitting that later means renaming every entity in Home
Assistant and losing its history.

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
