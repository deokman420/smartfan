# Phase 2 — WiFi, Home Assistant, remote configuration, logging

## The governing constraint

**The fan must work with the network completely down.**

A bathroom extractor that stops working when the WiFi drops is worse than one
that was never connected — it fails silently, in a room nobody monitors, and the
first symptom is a mouldy ceiling. So the network layer is strictly *additive*:

- The control loop never blocks on network I/O
- Local `settings.json` is the source of truth, not MQTT retained topics
- Losing the broker changes nothing about how the fan behaves
- A hung network stack cannot stall the loop — watchdog enforced

Everything below is shaped by that. It is the difference between this being a
robust appliance and an IoT gadget.

## What already exists and gets reused

| From | Reused as | Change needed |
|---|---|---|
| `thb1s-bridge/mqtt.py` | MQTT 3.1.1 client | **Add SUBSCRIBE** — currently publish-only |
| `thb1s-bridge/main.py` | HA discovery pattern | Adapt topics and entity types |
| `dhy22-wifi/firmware/netlink.py` | WiFi connect / reconnect | Review for non-blocking use |
| `dhy22-wifi/firmware/ringbuf.py` | Offline sample buffer | Likely as-is |
| Mosquitto on the HA host | Broker | Reuse the `pico` account |

Adding SUBSCRIBE to `mqtt.py` is the only substantial new protocol work. That
client is publish-only by design; remote configuration needs the other half —
SUBSCRIBE, SUBACK, and inbound PUBLISH handling, still QoS 0.

## Module layout

```
main.py         control loop — core logic unchanged
config.py       compiled-in defaults, versioned
settings.json   runtime overrides, written by MQTT or button   (on device)
secrets.py      WiFi + MQTT credentials                        (gitignored)
netlink.py      non-blocking WiFi state machine
mqtt.py         MQTT client, publish + subscribe
telemetry.py    state publishing and HA discovery
ringbuf.py      buffers samples while the broker is unreachable
```

Settings resolve in one direction: `config.py` defaults ← overridden by
`settings.json` ← written by MQTT. A corrupt or missing `settings.json` falls
back to defaults rather than refusing to boot.

## Entities exposed to Home Assistant

### Sensors

| Entity | Unit | Device class |
|---|---|---|
| Temperature | °C | temperature |
| Humidity | % | humidity |
| Dew point | °C | temperature |
| Baseline humidity | % | humidity |
| Excess above baseline | % | — |
| Light level | % | illuminance |
| Fan speed | rpm | — |
| State | — | enum |
| Runtime today | min | duration |

`Excess above baseline` is the one worth graphing. It is what the controller
actually decides on, and seeing it rise and fall across a shower is the fastest
way to tune `RH_RISE_ON` for a particular room.

### Binary sensors

Fan running · Light detected · Fault · Sensor healthy

### Controls — writable from HA

| Entity | Type | Range |
|---|---|---|
| Humidity rise to start | number | 3–20 % |
| Humidity fall to stop | number | 1–10 % |
| Light on threshold | number | 0–65535 |
| Light off threshold | number | 0–65535 |
| Minimum run | number | 60–900 s |
| Maximum run | number | 300–3600 s |
| Post-light run | number | 0–600 s |
| Boost duration | number | 300–3600 s |
| Mode | select | Auto / Force off / Force on |
| Boost now | button | — |
| Relearn baseline | button | — |
| Clear fault | button | — |

**Force off** matters more than it looks: it is how you stop the fan during a
long bath without pulling power, and how you silence it if a sensor starts
misbehaving while you are away.

### Topics

```
fan/bathroom/status              online | offline        (LWT, retained)
fan/bathroom/state               JSON, all sensors        (retained)
fan/bathroom/set/<param>         HA writes here
fan/bathroom/cmd                 boost | relearn | clear
homeassistant/<type>/fan_bathroom/<key>/config           (discovery, retained)
```

One JSON state topic rather than a topic per sensor — fewer publishes, one
atomic snapshot, and HA's `value_template` pulls the fields out. This matches
the `thb1s` bridge.

## Work order

Each stage leaves the device in a working state. Stop after any of them.

### 2a — Telemetry only *(low risk)*

WiFi connect, MQTT publish, HA discovery, LWT. **No inbound control.** The
controller gains a reporting path and nothing can change its behavior remotely.

Delivers: live entities in HA, automatic history and graphing, and a week of
real data to tune thresholds against before anything can write to the device.

This is the stage that pays for itself. Tuning `RH_RISE_ON` by watching a real
shower beats guessing.

### 2b — Remote configuration

**2b.1 — commands. Done.**

SUBSCRIBE, Remaining-Length parsing and `check_msg()` added to `mqtt.py`. All
socket reads now route through one `_recv()`, so an inbound PUBLISH arriving
between a PINGREQ and its PINGRESP is dispatched rather than mistaken for a
malformed reply — a bug that only surfaces once the broker is busy.

`net.py` resubscribes on every reconnect and services inbound traffic each
poll. It also syncs NTP once per association, which is what makes the daily
runtime rollover work.

Commands on `fan/<room>/cmd`: `boost`, `cancel`, `relearn`, `clear`. Three HA
button entities. The handler runs on the main thread inside `link.poll()`, so
there is no concurrency to reason about; it drives the outputs immediately and
forces a republish rather than waiting for the 30 s tick.

Measured: press to fan-on in under 5 s. The boost countdown publishes on
whole-minute changes, so it reads 15, 14, 13 instead of jumping on the
interval.

**2b.2 — parameters. Done.**

`settings.py` layers runtime overrides over `config.py`:

    config.py defaults  <-  settings.json  <-  MQTT writes

Reads fall through, so a missing or corrupt `settings.json` costs the tuning
and nothing else. A controller that refuses to boot because of a bad settings
write is a ladder trip; one that boots on defaults is an inconvenience. The
file is re-validated on load, so a hand-edited or half-written file cannot put
the controller somewhere `set()` could not have reached.

Eight `number` entities and a `select`, all `entity_category: config`. Every
write is clamped in firmware, not merely by the slider — Home Assistant's
advertised min/max only shapes the control, and anything on the broker can
publish to `fan/<room>/set/+`.

Range clamping alone is not enough, so paired settings stay ordered:
`RH_RISE_ON` above `RH_FALL_OFF`, `LIGHT_ON` above `LIGHT_OFF`, `MAX_RUN_S`
above `MIN_RUN_S`. Whichever was just written wins and the other moves out of
its way. Rejecting instead would tell the user no without saying which other
value was blocking them.

**Mode** — Auto / Force off / Force on — sits above the automatic logic
including LEARN, because an override that waits out a 60 s baseline pass is
not an override. FAULT still wins: forcing a stalled fan on would re-stall it.

Verified on hardware: writes round-trip, out-of-range values clamp, bad types
and unknown names are rejected, Force on interrupted a LEARN pass within 6 s,
and two overrides survived a reboot.

### 2c — Robustness. Done.

**Watchdog**, 8 s, fed at exactly one place: after the outputs are driven, so
a loop reaching the feed has completed a useful pass rather than merely
survived one. Nothing in `net.py` or `telemetry.py` may feed it, or it stops
proving the thing it exists to prove. Armed 3 s after boot, because enabling
it on the first line would turn any startup hiccup into a reboot loop and a
watchdog cannot be switched off once started.

Proven by hanging the loop deliberately: the board reset and recovered on its
own with no intervention.

**Safe mode**, by two routes. Hold the existing button through power-up — no
extra hardware, no jumper to find on a ladder. Or create a `safemode` file,
because the button cannot be automated and, once the watchdog is armed, any
REPL session outlasting 8 s is reset out from under you. The test suite found
that the hard way: it dropped from 26/26 to 16/26 the moment the watchdog
landed, and now enters safe mode first.

**Backoff** was already in `net.py` from 2a.

**Link health** rather than buffering — reconnect count, last outage duration,
uptime and boot reason, all as HA diagnostic entities.

`ringbuf.py` is deliberately **not** used. It was written for the HTTP ingest
API, where every reading carries its own NTP timestamp and so backfills into
the right place in history. MQTT state topics have no timestamp; Home
Assistant stamps them on receipt. Replaying an hour of buffered samples would
jam sixty readings into one second and corrupt the history worse than the gap
did. An honest hole beats fabricated continuity.

A note on `reset_cause()`: a hardcoded table is wrong on this port. RP2350
defines `WDT_RESET = 3`, which a generic mapping labels "hard reset" — hiding
the one thing the diagnostic exists to report. It now reads `machine`'s own
constants.

### 2d — Dashboard. Done.

Built over the WebSocket API (Lovelace config cannot be written over REST).
Seven sections, 31 cards, 17 entities: current state, boost controls with a
countdown that only appears while boosting, the excess-above-baseline gauge
with its bands set to the real control thresholds, activity timeline, climate,
health, and a light-calibration graph.

The gauge is the one that matters — it shows whether the fan *should* be on
without the reader needing to know the logic.

**Original recommendation, which still stands: use Home Assistant's own
dashboard first.**

Once entities exist, HA gives you history, graphs, gauges, automations and phone
notifications for free. A Lovelace view with the humidity trace, the fan state
timeline and the control sliders is an afternoon of YAML.

Build the Next.js dashboard from `dhy22-wifi` only if you want something HA
genuinely cannot do — a public status page, a custom visualisation of baseline
tracking, or multi-room comparison in one view. It is real work, and duplicating
HA's built-in history is the most common way to spend a weekend for nothing.

If it does get built, it reads from HA's REST API or the MQTT broker over
websockets — not from the Pico directly. The Pico should have exactly one
network client.

## Things that will bite

**DHT22 timing.** The read is bit-banged and timing-sensitive. Network work
during a read corrupts it. Either wrap the read in
`machine.disable_irq()` / `enable_irq()`, or accept the occasional checksum
failure — which the code already tolerates, and which is the simpler answer
given it samples every 2 s and needs 5 consecutive failures to fault.

**Loop cadence.** The control loop runs every 20 ms. MQTT publishing every 2 s
is fine; MQTT *connecting* can block for seconds. Connection handling belongs in
a state machine polled from the loop, never a blocking call inside it.

**Credentials.** `secrets.py` is already gitignored. Commit a
`secrets.example.py` alongside it, matching the `thb1s-bridge` convention.

**Clock.** Runtime-today and any timestamping needs NTP. `ntptime` is in the
build. Handle the pre-sync case — do not let a 1970 timestamp corrupt a daily
counter.

**Retained discovery.** HA discovery configs must be published retained, or the
entities vanish when HA restarts before the Pico republishes.

## Decisions taken

1. **Broker** — `192.168.0.5:1883`, reusing the existing `pico` account for now
   (option A). Per-device accounts remain the intent; the topic namespace
   already separates the devices, so splitting later is two lines.
   `MQTT_CLIENT_ID` is already distinct per device — sharing one would have the
   two clients disconnecting each other in a loop that looks exactly like a
   flaky network.
2. **Sample rate** — 30 s, plus immediate publish on any change of state, fan,
   light, fault or boost-minute. Verified: a `LEARN -> IDLE` transition landed
   at 62.4 s rather than waiting for the 90 s tick.
3. **Retention** — 30 days. HA's recorder defaults to 10, so this needs a
   `recorder:` override in `configuration.yaml`. Still to do.
4. **Multi-room** — assumed from the start. `fan/<room>/...` topics, with
   `ROOM`, `ROOM_NAME` and `DEVICE_ID` in `config.py`.

## Not in scope

Mains switching stays in phase 3, in a proper enclosure, and the safest route
remains driving a listed relay module (Shelly 1 / Sonoff Mini) over WiFi rather
than putting 120 VAC on a board you assembled. That decision is unchanged by
anything here.
