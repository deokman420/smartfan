# Files in the repo

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
