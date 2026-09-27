# Regression suites

Three layers, each catching what the one below cannot. Every suite exits
non-zero on failure, so any of them can gate a flash or a deploy.

```bash
python tests/test_manifest.py        # host only, no board needed
python tests/test_app.py             # the app, needs broker + device
python tests/test_improv.py          # host only, no board needed
python tests/test_provision.py       # host only, no board needed
python tests/test_device.py [COM5]   # hardware, needs the Pico attached
python tests/test_ha.py              # integration, needs HA + device online
python tests/test_ui.py [--headed]   # browser, needs HA reachable
```

Run them in that order. `test_device.py` stops `main.py` to drive the pins, so
it resets the board on the way out; `test_ha.py` waits for the device to come
back before asserting anything.

## What each layer is for

**`test_manifest.py` — 12 checks.** That `telemetry.TRACKED` and
`ota_publish.SHIP` agree with each other and with the repo.

A module missing from `SHIP` is never delivered. A module missing from
`TRACKED` is worse: the fingerprint does not change when that file changes,
so `ota_publish` prints **MATCH** and the device goes on running old code.
Nothing breaks, nothing is reported, and it is invisible from outside.

Both lists have already drifted here — `provision.py` reached `SHIP` and not
`TRACKED`, because the edit meant to add it did not match and `str.replace`
does not raise when it changes nothing.

**`test_improv.py` — 34 checks.** The Improv Serial codec and state machine.
`improv.py` holds no transport on purpose, so all of it is reachable here.

The framing is anchored to a **hand-computed byte string**, not to whatever
`build()` emits. A test that encodes with the same function it decodes with
passes just as cheerfully when both are wrong.

The rest are protocol traps: a corrupt checksum must be dropped without
wedging the stream, a packet split across two reads must not answer early
(which is what a serial port actually does), and the network list must end
with an empty result or the browser waits for more until it times out.

**`test_provision.py` — 41 checks.** Host-only, with MicroPython stubbed,
so it needs no board and touches none. The radio cannot be exercised here,
but everything that decides whether provisioning is *correct* can be: the
derived AP credentials, form decoding, the `secrets.py` it writes, and the
escaping of network names it did not choose.

Two of those earn their keep on their own. **"PSK meets the WPA2
minimum"** — anything under 8 characters makes the AP come up *open*, which
is the exact hole the WPA2 setup AP exists to close, and it fails silently.
And **"hostile SSID is escaped, not executed"** — nearby network names are
attacker-controlled strings rendered into a captive sheet on the owner's
phone. An AP named `'><script>` is a one-line attack on setup.

It also writes into a temp directory, because `_write_secrets` writes
`secrets.py` into the working directory and the repo has a real one.

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

**`test_app.py` — 23 checks.** The app, served from `app/build` so it
tests the artefact that would actually be deployed, against the live broker
and the live fan.

What it proves that a screenshot cannot: the values on screen are the ones
the device published, and pressing Boost reaches the fan. That last one is
confirmed by **an independent MQTT subscriber**, not by the app's own
screen — otherwise the app marks its own homework.

It also opens the app in a **fresh browser context** with no
`localStorage`, because the first run is the one screen every new user sees
and the one no developer ever sees twice.

And it measures **contrast on the rendered page**, not against the token
list — a token that passes in isolation still fails on a different surface.
The audit walks up for the first opaque ancestor, because an element's own
background is usually transparent and scoring text against transparent
silently passes everything.

It drives the real fan for a few seconds and puts it back.

## Assertions must wait, not glance

`is_visible()` answers instantly about right now, and
`goto(wait_until="load")` returns before Svelte has hydrated. Used together
they report FAIL on an app that renders perfectly well a moment later.

Both of `test_app.py`'s first two checks failed that way on their first run
while every check after them passed — because those later ones called
`inner_text()` and `input_value()`, which auto-wait. **A failure at the top
and passes below it is the signature of this mistake, not of a broken app.**
Use `locator.wait_for(state="visible")`.

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
