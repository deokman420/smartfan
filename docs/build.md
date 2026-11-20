# Build your own

There are two builds. Start with V1.

| | V1: the bench unit | V2: the ceiling unit |
|---|---|---|
| Status | Running and commissioned | Designed, not fabricated |
| Power | 5 V USB | 120 V mains in, 12 V and 5 V made on the board |
| Fan | Noctua NF-A4x10 5V (stirs air, does not extract it) | Noctua NF-F12 industrialPPC-2000 IP67 |
| Humidity | DHT22 | SHT40 |
| Wiring | Breadboard: [`build-sheet.html`](https://github.com/deokman420/smartfan/blob/main/build-sheet.html) | KiCad PCB: [`hardware/v2/`](https://github.com/deokman420/smartfan/tree/main/hardware/v2/) |
| Parts | The [Hardware](#hardware) table below | [`hardware/v2/shopping-list-v2.html`](https://github.com/deokman420/smartfan/blob/main/hardware/v2/shopping-list-v2.html) |
| Printed parts | None | [`hardware/v2/cad/`](https://github.com/deokman420/smartfan/tree/main/hardware/v2/cad/) (not test-fitted yet) |

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
   setup network. Follow [Setting up a unit](setup.md#setting-up-a-unit). Over USB,
   copy `secrets.example.py` to `secrets.py`, fill it in, and copy it across
   in safe mode instead.
5. **Calibrate the light** in the room it will live in. See
   Calibration.
6. **Optional: Home Assistant.** Set `MQTT_HOST` in `secrets.py`, then add the
   integration in [`custom_components/smartfan/`](https://github.com/deokman420/smartfan/tree/main/custom_components/smartfan/)
   through HACS.

After the first install, firmware updates go over the air. See
[Updating over the air](ota.md#updating-over-the-air).

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
