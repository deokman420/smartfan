# Smartfan — Home Assistant integration

A local-only Home Assistant integration for the Pico 2 W bathroom fan
controller in this repository. It talks to the board over its own HTTP API on
your LAN: no broker, no cloud, no account.

## Do you need this?

Probably not, and that is worth saying first.

The firmware already publishes **MQTT discovery** of its own accord. If you
run Mosquitto, the controller appears in Home Assistant with its whole entity
set and you install nothing at all. That path is not deprecated and is not
going away — it is the zero-install option, and the firmware is built on the
principle that it works with no broker anywhere on the network.

Use this integration instead when:

- **You do not run an MQTT broker**, and would rather not add one to see a fan.
- **You want UI setup.** Add the device by host name, with the connection
  tested before anything is created.
- **You want one entity-id prefix.** Home Assistant freezes an entity_id at
  the device name it first saw and never rewrites it, so renaming this device
  left one board with entities under both `bathroom_fan_controller_*` and
  `bathroom_smartfan_*`. This integration assigns its own unique ids from the
  firmware's `DEVICE_ID`, so that cannot happen.
- **You want the two fields MQTT never announced**: the app bundle's build,
  and `why` — the controller's own words for the state it is in, instead of a
  dashboard guessing. It once said "Clearing humidity" over a room 0.9% above
  baseline while the fan was running on odor.

Running both at once works but gives you two sets of entities for one device.
Pick one; if you pick this, delete the MQTT device from Home Assistant.

## Install

Via HACS, as a **custom repository**:

1. HACS → three-dot menu → **Custom repositories**
2. Repository: `https://github.com/phbeks/pico2w-fan-controller`, category
   **Integration**
3. Install **Smartfan bathroom controller**, then restart Home Assistant

Or by hand: copy `custom_components/smartfan/` into your Home Assistant
`config/custom_components/` directory and restart.

## Set up

**Settings → Devices & services → Add integration → Smartfan.**

Enter the controller's address. The default is `fan.local`, and that is the
one to prefer — the DHCP lease moves, and the addresses written down in this
project's own docs have gone stale twice. The firmware sets its hostname
before bringing the interface up, which is what makes the name resolve; an IP
works too if your network does not carry mDNS.

There is **no automatic discovery**, and that is a property of the firmware
rather than a gap here: `network.hostname()` gives an mDNS responder for the
host's own address and nothing more. No `_http._tcp` service is advertised,
so Home Assistant has nothing to find. Teaching the firmware to advertise a
service is the prerequisite, not a change to this integration.

## Remove

**Settings → Devices & services → Smartfan → three-dot menu → Delete.** That
removes the device and its entities. The controller itself is untouched and
keeps running on its own logic, which is the point of it; it also keeps
publishing MQTT discovery if a broker is configured on the device. To uninstall
the code, remove it from HACS, or delete
`config/custom_components/smartfan/` and restart.

## What you get

Every sensor, threshold and command the firmware exposes. A few are disabled
by default — the raw NOx pair, the Wi-Fi and link diagnostics — and can be
switched on from the device page. The ones worth knowing about:

| Entity | Notes |
| --- | --- |
| Controller state | `LEARN` / `IDLE` / `RUN` / `BOOST` / `FAULT` |
| Reason | Why it is in that state, in the firmware's own words |
| Excess humidity | Degrees above the *learned* baseline, not an absolute |
| Excess odor | Sensor ticks away from learned clean air. The number the fan acts on |
| Excess NOx | The second sensor pixel. **Nothing acts on this** — see below |
| Mode | `Auto`, `Force off`, `Force on`. A forced run does not spend the run-time cap |
| Relearn baselines | Drops every learned baseline and reseeds from the next quiet sample |

Thresholds are exposed as `number` entities. **The device is the authority on
their ranges**, not this integration: it clamps what it cannot honour, and it
will move a paired setting out of the way rather than refuse — writing
*humidity rise to start* below *humidity fall to stop* pushes the second one
down. So the value you see after a write is the value the device actually
holds, which is not always the one you sent.

### About the odor and NOx numbers

They are raw tick counts from a metal-oxide element and a learned baseline,
not concentrations. There is no absolute level that means "bad air" — the
count depends on the element, its age, the room and how long it has been
powered — so the fan acts on the *gap*, and so should any automation you
write. The signal falls as the air gets worse, so a dip below the baseline is
the event.

**Nothing acts on NOx.** The two pixels respond to opposite chemistry: the
VOC pixel to reducing gases, which is what human-origin bathroom odor is made
of, and the NOx pixel to oxidizing gases, principally the NO₂ a gas flame
makes. Odor alone is the bathroom; odor and NOx together came from a kitchen.
The firmware records it so that discrimination can be measured from real
data, but the VOC sign was measured on this part and the NOx sign has not
been, so there is no threshold to go with it yet. Two caveats if you plot it:
the sensor's settle gate is the VOC pixel's 60-second figure and the NOx
pixel takes far longer, so the first stretch after every reboot is warm-up
drift rather than air.

## Relearn after moving it

Baselines live in RAM, so every power cut drops them and the next quiet
sample reseeds them. If the unit is moved to a different room, or a baseline
was seeded while the element was still coming to temperature, the deviation is
measured against the wrong place and the fan can start on nothing. Press
**Relearn baselines** once it is sitting where it will live and has been
powered for a few minutes.

## Polling

One request every ten seconds for the whole device, not one per entity. The
board serves HTTP cooperatively between control-loop passes, so forty
independent pollers would be forty chances to queue behind a sensor read.

## Reporting a problem

Attach **diagnostics** from the device page — it carries the whole snapshot,
the fault reason, the boot cause, the learned baselines and the reconnect
count, with the network addresses redacted.

## Why `api.py` is separate

It holds no `homeassistant` import. Home Assistant core's quality scale
requires an integration's dependencies to be open source, on PyPI and built
in public CI, so submitting this to core means that file becoming its own
package with the integration as a thin wrapper. Keeping the boundary clean
makes that a move rather than a rewrite.
