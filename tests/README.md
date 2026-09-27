# Regression suites

Three layers, each catching what the one below cannot. Every suite exits
non-zero on failure, so any of them can gate a flash or a deploy.

```bash
python tests/test_device.py [COM5]   # hardware, needs the Pico attached
python tests/test_ha.py              # integration, needs HA + device online
python tests/test_ui.py [--headed]   # browser, needs HA reachable
```

Run them in that order. `test_device.py` stops `main.py` to drive the pins, so
it resets the board on the way out; `test_ha.py` waits for the device to come
back before asserting anything.

## What each layer is for

**`test_device.py` — 33 checks.** Firmware version, module presence, free RAM,
every sensor reading inside a plausible range, and the full fan on/off/on cycle
measured by tachometer. Most of these encode faults that actually happened
during the build.

The one worth keeping forever is **"transistor actually switches"**. A PNP
fitted where an NPN belongs conducts regardless of its base, so it reads
identically in both states. That cost most of an evening and two destroyed
parts. It is now one assertion.

**`test_ha.py` — 37 checks.** Discovery still produces the entities the
dashboard references, values are numeric and in range rather than merely
present, every card points at something real, and a command round-trips from
Home Assistant to the fan and back.

It also checks that the gauge's severity bands still equal `RH_RISE_ON` and
`RH_FALL_OFF` in `config.py`. Retune the firmware and forget the dashboard, and
the gauge quietly lies about when the fan runs — which is worse than no gauge.

**`test_ui.py` — 20 checks.** That all eight sections render, that a tile
click reaches the fan, that the countdown and cancel appear only while
boosting, and that nothing scrolls sideways at 400 px.

The section-heading checks exist because a dashboard rebuild that drops a
section still screenshots beautifully — it is just missing a third of the
controls. Rename a heading in `tools/build_dashboard.py` and this suite fails
until the rename is deliberate. Screenshots land in `tests/screenshots/`
(gitignored) for eyeballing after a change.

## Never verify after the final reset

`test_device.py` ends by leaving safe mode and restarting the controller.
Every `mpremote exec` **stops `main.py`** — so a check placed *after* that
restart kills the controller it just started, and then reports `PASS` for
having done so.

This suite left the device dead at the REPL after every run until it was
spotted from the outside: no LEDs lit, entities unavailable in Home
Assistant, filesystem perfectly clean, USB reachable. Nothing in the 33
passing checks pointed at it, because the damage happened after the last
assertion.

**The reset must be the last thing the suite says to the device.** Clear and
verify the flag in one `exec` beforehand, then restart and leave it alone.

## Two things that will catch you

**Home Assistant renders into shadow DOM.** `body.innerText` is empty.
Playwright locators pierce shadow roots; raw DOM text does not. Every
assertion has to go through a locator.

**`get_by_text(..., exact=False)` is case-insensitive substring matching.** A
state value of `BOOST` also matches the "Boost" heading and the "Boost 15
minutes" tile. Assertions about a state need `exact=True`. This produced a
false failure that looked convincingly like a real one.

## Auth

`test_ha.py` and `test_ui.py` read `HASS_LLAT` from `.env`. The UI suite seeds
`localStorage.hassTokens` before the app boots, so the token never reaches a
command line or any output.

Override the host with `HA_HOST=host:port` or `HA_URL=http://host:port`.

## Not Lighthouse

Lighthouse would audit Home Assistant's frontend — their bundle, their
accessibility, their render timing — and score identically with every entity in
the dashboard renamed out from under it. The regressions that matter here are
broken references, drifted thresholds and a command path that stopped working.
Those are assertions, not audits.
