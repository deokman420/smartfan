# Set up a unit

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

## Getting it running

MicroPython for the Pico 2 W, the ARM build, not RISC-V:
<https://micropython.org/download/RPI_PICO2_W/>. Hold BOOTSEL, plug in, and
drag the `.uf2` onto the RP2350 drive.

Firmware updates go over the air with `python tools/ota_publish.py` (see
[Updating over the air](ota.md#updating-over-the-air)). The app bundle goes over
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
[`docs/daylight-is-brighter-than-the-bulb.md`](daylight-is-brighter-than-the-bulb.md).
