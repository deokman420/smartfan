# Regression suites

Three layers, each catching what the one below cannot. Every suite exits
non-zero on failure, so any of them can gate a flash or a deploy.

```bash
python tests/test_manifest.py        # host only, no board needed
python tests/test_sht4x.py           # host only, the SHT4x driver and its CRC
python tests/test_httpd.py           # host only, no board needed
python tests/test_ota_publish.py     # host only, what an OTA serve hands out
python tests/test_settings.py        # host only, how often settings.json is rewritten
node   tests/test_linkstore.mjs      # host only, where the app keeps the broker password
python tests/test_device_app.py      # host only, the app with no broker
python tests/test_appicon.py         # host only, the tab icon and status bar
node   tests/test_statusled.mjs      # host only, the tab icon's LED code
python tests/test_theme.py           # host only, light / dark / system
python tests/test_units.py           # host only, celsius / fahrenheit
node   tests/test_dryrate.mjs        # host only, no browser at all
node   tests/test_tabswipe.mjs      # host only, swiping between sections
python tests/test_app.py             # the app, needs broker + device
python tests/test_improv.py          # host only, no board needed
python tests/test_visit.py           # host only, odor judged per visit
python tests/test_provision.py       # host only, no board needed
python tests/test_wifi.py            # host only, changing the wifi in-app
python tests/test_device.py [COM5]   # hardware, needs the Pico attached
python tests/test_device_http.py     # hardware, over WiFi, needs it on the network
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

**`test_httpd.py` — 54 checks.** The on-device server, its API and its
stored history, with no board and no browser. `httpd.py`, `webapi.py` and
`store.py` are portable Python on purpose, because the alternative is testing
a web server by flashing a fan controller and refreshing a phone.

The server under test is stepped the way the control loop steps it — short,
bounded `step()` calls from one thread — so a handler that blocked would hang
the suite rather than passing it. It also asserts the things a browser must
not be able to do: escape the bundle, boost the fan from the wrong website,
or push firmware through a door with no credentials.

Two real bugs came out of writing it, both invisible on hardware until they
bit. The CORS check accepted `http://192.168.0.9.evil.example`, a domain
anyone can register that merely *starts* like a private address. And closing
a socket with unread bytes sends a RST that discards the response just
written — which hit precisely the two answers that most need to be read, the
503 at the connection cap and the 413 for an oversized body.

**`test_device_http.py` — 18 checks.** The server on the board, over real
WiFi, which is the only place the things that actually broke could be seen.
It checks that `fan.local` resolves, that a browser loads the whole bundle
off flash, that Boost reaches the fan, that eight simultaneous requests are
all served, and — after every phase — that `up_min` has not gone backwards,
because the watchdog reboots the board if the control loop ever stops feeding
it and that is the loudest failure available.

It found that the history endpoint could not have worked at all on hardware:
a week of rows costs ~890 bytes of heap each and raised `MemoryError` at 512
of them, on code the host suite passed instantly. See `docs/phase-5-plan.md`.

One trap for anyone debugging with a cable: **`mpremote exec` reboots the
board** — entering the raw REPL sends a soft reset — so `up_min` dropping to
zero right after a USB command is the tooling, not a fault.

**`test_device_app.py` — 14 checks.** The built app against the real
`httpd.py` and `webapi.py`, running on the host with a stand-in controller.
It proves the whole no-broker path end to end — a tile shows the number the
API returned, Boost reaches the fan, and a browser that has never seen this
fan draws a week of history off the fan's own flash. The only fiction is the
fan itself.

Boost is verified on the controller object rather than on the app's screen.
The screen is the thing under test and would otherwise be marking its own
homework.

The bundle is served by the real `httpd.py` here too, not by a convenient
Python web server. That hole hid a genuine bug: a browser opens six
connections at once, the server capped them at three and answered 503, and
**a browser does not retry a 503** — it dropped the stylesheet and half the
scripts and rendered a blank page.

It also asserts that **nothing leaves the network**, which is the app's
central claim and was quietly untrue: the typefaces came from
`fonts.googleapis.com`, so opening the fan told Google about it. They are
served by the device now, and this check is what keeps the claim honest.

**`test_appicon.py` — 27 checks.** The spinning favicon and the status-bar
tint, which follow `fanRunning` out of `lib/link.js`. Needs only a build:
the thing under test is a function of one boolean, so a real fan would add
nothing but new ways to fail.

It loads `lib/appicon.js` verbatim as a blob module, against the built
`index.html` with its `<script>` tags stripped. The stripping is not
squeamishness — `+layout.svelte` starts a watcher of its own and two of them
on one page fight over `<head>`, which is how the suite first failed.

Half the checks are about stopping rather than starting: a backgrounded tab
must drop the timer, and teardown must put the icon, the theme color and
`data-fan` back exactly as `app.html` shipped them. A favicon is decoration,
and decoration that leaks a 13 fps repaint into a tab nobody is looking at is
worse than no decoration.

The status LED in the icon's corner is the one exception to that, and the
checks say so: it mirrors the board's green/red blink codes (a red double-blink when the
app needs a look), and unlike the spin it keeps showing in a background tab,
because the tab strip is where you see it. The LED checks read the corner
pixel back off the favicon, so they test the color the tab would show. With
`$lib/statusled.js` swapped for a blob of the real file, since a blob module
cannot resolve the alias.

**`test_statusled.mjs` — 23 checks.** Which code the LED shows for a
reading: the board's five states and the VOC settle pulse, the two ways the
app can be blind, and what outranks what. Pure Node, no browser.

**`test_theme.py` — 18 checks.** Light, dark and system. Needs only a
build; the scheme is a function of one saved string and one OS preference,
and neither involves the fan.

Not "does light mode look light" — the screenshots show that, and
`check-contrast.mjs` proves the palette clears AAA without a browser at all.
What only a browser proves is the wiring: that `system` keeps following the
OS while the app is open rather than reading it once at boot, that an
explicit choice then ignores the OS, and that Cancel in the settings sheet
does **not** revert the scheme — the theme is deliberately outside the
connection draft, and snapping back on Cancel is the bug that arrangement
exists to prevent.

One check is worth more than the rest: that `data-theme` is already set when
the document reaches `readyState === 'interactive'`. The inline script in
`app.html` is the only thing between a light-mode user and a dark frame on
every single load, it duplicates three lines of `theme.js`, and duplicated
code that is never exercised is how a flash comes back six months later.
Asking after `load` would only prove `theme.js` eventually ran.

**`test_dryrate.mjs` — 27 checks.** The learned time-to-off estimator.
Node, not Python, and no browser: the thing under test is arithmetic over an
array, and `app/src/lib/dryrate.js` imports relatively so this can just
import it.

The fixture is `seed_history.py`, and that is the point rather than a
convenience. It models this bathroom in closed form — `29·e^(-(s-12)/17) +
1.2` — so tau is 17 min and the settling floor is 1.2 %RH *exactly*, and the
fit can be scored against a right answer instead of against a previous run
of itself. It currently lands at 17.16 min and 1.12 %RH, with every
prediction inside 2.8 %.

One check exists purely to stop a regression that already happened once:
fitting the textbook two-parameter decay, with the asymptote pinned at zero,
returned 19.5 min. A room settles slightly *above* its own baseline, the
curve flattens as it nears that floor, and a straight line through the logs
reads the flattening as a slower room. The floor is fitted now, and the test
asserts the three-parameter fit beats the number the pinned one gave.

It also asserts that only runs where the humidity **actually pumped** are
learned from. The fan runs far more often than anyone showers — a trip to
find a towel runs the minimum plus the post-light hold, a boost gets pressed
in a dry room — and those teach nothing, because the humidity never went
anywhere. Against a synthetic week of 36 runs, 6 of them showers:

```
with the gate     tau 17.16 min from 6 showers   spread 17.2-17.2
without the gate  tau 10.82 min from 12 runs     spread  4.5-17.2
```

A boost's 1.8 %RH decay clears a 1.6 %RH fall gate perfectly well, so before
the peak gate those runs were admitted and dragged the estimate 36 %
optimistic. The gate is the controller's own `RH_RISE_ON`, passed in from the
device's settings rather than hardcoded, so retuning the threshold retunes
this with it.

The rest is the controller's own stop conditions, mirrored from `main.py`'s
RUN branch — `MIN_RUN_S`, the post-light hold, and the `MAX_RUN_S` cap. An
estimate that ignores those is not an estimate of anything the fan will do.

**`test_wifi.py` — 65 checks.** Changing the WiFi from the app, with a fake
radio. A real association cannot be exercised on the host, but everything
that decides whether this is *safe* can be.

The assertion the whole design exists for is **"nothing is written while the
radio is still trying"**, and its partner **"NOTHING is written when it
fails"**. Those are what make a typo cost a retry rather than a ladder, and
they are exactly the property a refactor would remove without noticing.

Also: that an in-flight attempt cannot be abandoned by an expiring window, that
a passphrase full of quotes and backslashes survives `repr()` into
`secrets.py`, and that changing the WiFi keeps the broker settings.

`fresh()` is worth reading before adding to this file. `provision._carry_over()`
does `import secrets`, and chdir alone leaves the *repo's* secrets.py first on
`sys.path` — so the carry-over check was reading this developer's real broker
credentials and comparing them against fixtures. It passed by coincidence,
because the fixture IP happened to match the real one. The fixtures are
deliberately implausible now.

The broker rides the same window, and its tests assert the same shape: that
nothing is written while it is still connecting, that a refused broker leaves
the file untouched, and that **changing the broker keeps the wifi credentials
and vice versa** — which is why there is now one `update_secrets()` writer
rather than two that each had to remember the other's keys.

Two of them are about a distinction that is easy to collapse: an *absent*
password means "keep the one you have" and an *empty* one means "this broker
wants none". Conflating them wipes the credential every time somebody
corrects a typo in the hostname, and the wipe only shows up later as a broker
that has stopped accepting the fan.

The HTTP contract lives in `test_httpd.py` instead: which status code a shut
window gives, that the provisioner is never even asked, and that a password
containing `:,# =` is not mangled by the newline-separated body.

**`test_units.py` — 28 checks.** Celsius and Fahrenheit, everywhere a
temperature is printed. A browser rather than a unit test of the arithmetic,
because the arithmetic is two lines and nobody gets it wrong twice — what
goes wrong is a temperature printed from a path the conversion does not
reach, and that only shows up rendered. Both bugs found while building it
were exactly that shape: the chart card's headline read `23.6 °F` beside an
axis reading 75, and `Chart.svelte` decided whether to narrate the
temperature/dew-point gap by testing `unit === '°C'`.

The check that matters most is that the dew-point margin is **not** converted
as an absolute. A temperature and a temperature difference convert
differently — 1.8 °F per °C with no offset — and the absolute conversion
turns a 3.9 degree margin into 39.0, which is a different claim about whether
the mirror is about to fog rather than a rounding error.

It also covers the clock, which belongs to the same module for the same
reason — a 12-hour clock is a display unit like a degree, and `Chart.svelte`
and `Activity.svelte` had byte-identical `clock()` functions, which is
exactly how one gets left behind on 24-hour. Midnight and noon are the cases
a naive `h % 12` gets wrong; both are 12, not 0.

And it asserts the fan is never told: changing units issues no POST.

**`test_provision.py` — 62 checks.** Host-only, with MicroPython stubbed,
so it needs no board and touches none. The radio cannot be exercised here,
but everything that decides whether provisioning is *correct* can be: the
derived AP credentials, form decoding, the `secrets.py` and `identity.py` it
writes, and the escaping of network names it did not choose.

It also covers what setup now asks for beyond the password. **The room is
free text that becomes a DNS label, an MQTT topic segment and a display
name**, so every shape a person might type — `Master Bath`, `WC #2`, sixty
characters, `!!!` — is asserted to come out as something all three can
accept. And the **country** is checked against a list rather than passed
through, because an invalid regulatory domain is not always an error: some
builds accept it and quietly restrict the channels, which presents as a
wrong password.

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

**`test_app.py` — 42 checks.** The app, served from `app/build` so it
tests the artifact that would actually be deployed, against the live broker
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

## inner_text() returns what the browser renders

`.k` labels are uppercased by `text-transform` in CSS. The DOM holds
"Boost remaining"; `inner_text()` reports **"BOOST REMAINING"**. A
case-sensitive substring check fails on a label that is plainly on screen.

Match labels case-insensitively. This is the same family as the
`get_by_text` note below — both are about asserting on rendered text rather
than on source text.

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
