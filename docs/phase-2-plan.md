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
controller gains a reporting path and nothing can change its behaviour remotely.

Delivers: live entities in HA, automatic history and graphing, and a week of
real data to tune thresholds against before anything can write to the device.

This is the stage that pays for itself. Tuning `RH_RISE_ON` by watching a real
shower beats guessing.

### 2b — Remote configuration

Add SUBSCRIBE to `mqtt.py`, `settings.json` persistence, HA number/select/button
entities, validation and clamping on every inbound value.

Every writable parameter gets a hard range in firmware. A bad MQTT payload must
not be able to set `MAX_RUN_S` to zero or write a threshold outside the ADC's
range. Clamp, log the rejection, carry on.

### 2c — Robustness

- Watchdog timer, fed only by the control loop, never by the network code
- Exponential backoff on reconnect — no tight retry loop hammering a dead AP
- `ringbuf.py` buffering samples while offline, flushed on reconnect
- **Safe-mode pin** — hold a GPIO low at boot to skip `main.py` and reach the
  REPL. `thb1s-bridge` uses GP14 for this. With autorun plus WiFi, a bad config
  can otherwise lock you out of a device in a ceiling.

Safe mode is not optional once this thing is mounted.

### 2d — Dashboard

**Recommendation: use Home Assistant's own dashboard first.**

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

## Open questions

1. **Broker address** — reuse `192.168.0.5` and the `pico` account, or a
   separate credential per device?
2. **Sample rate to MQTT** — every 2 s matches the control loop but is noisy for
   history. Every 30 s with immediate publish on state change is probably right.
3. **Retention** — HA's default recorder keeps 10 days. Longer needs InfluxDB or
   the long-term statistics table. Worth deciding before a year of data exists.
4. **Multi-room** — phase 4 implies several of these. Device naming and topic
   structure should assume more than one now, while it is cheap. `fan/bathroom/`
   already does.

## Not in scope

Mains switching stays in phase 3, in a proper enclosure, and the safest route
remains driving a listed relay module (Shelly 1 / Sonoff Mini) over WiFi rather
than putting 120 VAC on a board you assembled. That decision is unchanged by
anything here.
