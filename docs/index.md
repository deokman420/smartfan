# Smartfan

A bathroom extractor fan controller on a Raspberry Pi Pico 2 W. It turns the
fan on for light, humidity and odor, runs it until the room is dry, and talks
to Home Assistant, or to nothing at all.

!!! warning "Mains voltage"
    **The V2 build in [`hardware/v2/`](https://github.com/deokman420/smartfan/tree/main/hardware/v2/) connects to mains voltage.**
    Mains wiring can kill you and can start a fire. Build it only if you are
    competent to work on mains, keep it unplugged while it is open, and follow
    your local electrical code, which may require a licensed electrician for a
    fixed ceiling installation. The V2 board has **not been fabricated or tested
    yet**. It is a design to check, not a proven build. The V1 build runs
    on 5 V USB and has no mains anywhere. This project comes with **no warranty**
    (see the [license](https://github.com/deokman420/smartfan/blob/main/LICENSE)).

**New here?** Try the [live demo](/demo/) of the app,
then [build your own](build.md).

| Page | What's in it |
|---|---|
| [Build your own](build.md) | Parts, wiring, pin map, first install |
| [Set up a unit](setup.md) | Wi-Fi provisioning, naming, calibration |
| [Networking and Home Assistant](networking.md) | The fan's own server, MQTT, the dashboard |
| [Updating over the air](ota.md) | Signed OTA, and why it cannot brick the board |
| [How it works](how-it-works.md) | States, control logic, a real shower |
| [Odor sensing](sgp41-odor-sensing.md) | The SGP41 and its sign |

## Why baselines, not thresholds

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
hold as of 2026-11-03. Parked work is on the [roadmap](roadmap.md).
