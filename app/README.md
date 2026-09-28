# Smartfan app

Local-first control and monitoring. A static bundle with no application
server, no cloud and no account — served by the fan itself, off its own
flash.

```bash
npm install
npm run dev      # http://localhost:5173
npm run build    # -> build/, static files
npm run preview  # serve the built bundle
```

## How it connects

Two transports, chosen under Settings. **The app is never in the control
path** either way: the fan runs its own logic, and every value on screen is
the last one it actually reported.

### The fan itself — the default

HTTP straight to the device, which serves this bundle off its flash and
answers a small JSON API on the same port. Nothing else has to be running:
no broker, no Home Assistant, no server. Open `http://fan.local/` and there
is nothing to set up — the address is the one in the address bar and there
is no account, so the app connects on first load rather than opening a
settings sheet with no content in it.

Polling, not pushing, at two seconds. A browser cannot hold a raw socket,
the device has one core and a watchdog, and a WebSocket server on a control
loop is a connection to keep alive on the side of something whose first duty
is a fan. The device samples every two seconds anyway, so this is as fresh
as the data gets.

One hop instead of two, which shows up in the failure modes: there is no
state where "the broker is up but the fan is not reporting". Either the fan
answers or nothing does.

### An MQTT broker

As before, and unchanged. Adds Home Assistant, its recorder and its
automations, and lets a page watch a fan it was not served by.

Browsers cannot open raw TCP, so port 1883 — the one the device and Home
Assistant use — is unreachable from a page no matter what is configured. The
app uses MQTT over WebSocket on **1884**, which the Home Assistant Mosquitto
add-on already serves. Full reference in `../broker-setup.html`.

**A username and password are required** by that add-on, which does not
allow anonymous connections, so the app asks for them rather than dialling
out with nothing and reporting the refusal as a fault. A credential the
broker has already refused is not retried — the default reconnect loop would
hammer the broker with a login that cannot start working on its own, and
bury the reason.

> Credentials entered here are readable by anyone who opens the page and its
> dev tools. That is true of every browser MQTT client and cannot be
> engineered away. Give the app account the narrowest access that works and
> treat it as a LAN credential, not a secret.

The MQTT client is **107 kB gzipped, two thirds of the whole bundle**, so it
is a lazy chunk: `import('mqtt')` runs when someone chooses broker mode and
never otherwise. A device-mode first load is around 65 kB.

## Sections, not one long scroll

Four views — Status, Readings, Thresholds, Health — with a sidebar on
desktop and a sticky tab strip on phones. The Status view on a phone went
from 3502 px of scrolling to 937 px.

**Deliberately not a hamburger.** A hamburger earns its place when there
are many destinations, or when navigation is secondary to the content.
Here there are four, they are the whole app, and it is operated one-handed
in a steamy room. Hiding four buttons behind a tap costs an interaction
every time and hides the fault badge, which is the one thing you want to
see without going looking for it.

Every entity the device publishes now has its own card. Several used to be
sub-labels — raw light under Light level, last restart under Uptime, last
outage under Reconnects, baseline under Humidity — and boost remaining was
rendered only while boosting, so the one time you wanted to check it was
the only time it existed.

## Charts

Five charts on their own view, each expanding to a detail with 1 / 6 / 24
hour and 7 day ranges, and min / average / max.

Live samples are recorded from the same stream that drives the tiles, so the
charts can never disagree with the readings. On top of that, a device-mode
connection **backfills from the fan's own flash** — five-minute means kept
for a week — which is what makes the 7 day range worth offering, and what
lets a phone opening the app for the first time see what happened rather
than an empty chart.

That backfill arrives as CSV and is parsed in `transport/device.js`. The
device sends its stored lines as they are because it has no choice: parsing
a week into objects and serialising it costs about 890 bytes of heap per row
on an RP2350, roughly four times the memory the whole board has.

That closed the one honest complaint about the old design: history used to
cover only the time this browser had personally had the app open, so a new
phone started from nothing and clearing site data threw the week away. Home
Assistant's recorder was the obvious alternative and is still rejected for
the same reason as before — it would add a second credential, a second
service that has to be up, and a dependency on the very thing the app is
meant to work without. The fan keeping its own record adds none of those.

Resolutions are mixed on purpose. Gaps longer than five minutes are drawn as
**breaks rather than a confident straight line**, which is exactly the right
treatment for the seam between a coarse backfill and a fine live stream.

Excess has its own chart rather than sharing the humidity one. Humidity
sits at 62–92 % and excess at 0–30 %, so one shared axis stretched the
scale from −12 to 104 and flattened both lines into nothing. On its own
scale, with `RH_RISE_ON` and `RH_FALL_OFF` drawn as reference lines, it
answers the question the reader actually has: was the fan about to run.

Drawn by hand in SVG. The app is served over a LAN with no route out, so a
CDN chart library is a blank chart, and bundling one to draw a few lines is
hundreds of kilobytes for something `Chart.svelte` does in eighty.

## Design

The palette, type pairing and card idioms come from phbeks.com. The `:root`
block in `src/app.css` mirrors that site's canonical tokens — keep it in
sync rather than inventing values.

**Two text tones are deliberately altered**, and the brand values are
recorded in the file so the divergence is visible rather than accidental:

| Token | Brand | On `--bg-card` |
|---|---|---|
| `--text-dim` | `#8492a6` | 5.62:1 — AA, not AAA |
| `--text-muted` | `#4a5568` | **2.36:1 — fails AA** |

On phbeks.com those carry decorative card tags. Here they were carrying the
field name and the qualifier on every stat card — content, in a room that
is often steamy and badly lit. `--text-dim` is raised to `#9faab9`: the same
hue and saturation, lightness only, at the first value clearing 7:1 against
all three surfaces.

Every text element in the app now passes **WCAG AAA**, asserted on the
rendered page by `tests/test_app.py`.

## Typefaces, served by the fan

Inter and JetBrains Mono are on the device, in `static/fonts`. They used to
come from `fonts.googleapis.com`, which meant **three requests left the
network every time the app was opened** — the stylesheet and one woff2 per
family — from an app whose own copy says nothing does. On a phone's home
screen that handed Google an address and a User-Agent every time someone
checked their bathroom fan.

Both are variable fonts, so one file per family covers every weight the
design system asks for: 78 kB for the pair, latin subset, against 2 MB of
free flash. SIL Open Font License 1.1, with the licence redistributed
alongside them in `static/fonts/LICENSE.txt`.

It also fixes a quieter failure. The app is served over a LAN that may have
no route out at all, and there the old arrangement silently fell back to
system fonts — the app stopped looking like the design system and nothing
said so.

`tests/test_device_app.py` asserts that **nothing leaves the network**. The
next CDN link will look exactly as harmless as that one did.

## Installing it on a phone

`static/apple-touch-icon.png` is not optional decoration. iOS ignores the web
manifest's icons for the home screen and will not take an SVG, so without it
iOS screenshots the page and uses that as the icon — which is exactly what
happened on the first real install. It is a **full-bleed square** because iOS
applies its own rounded mask, and a pre-rounded icon gets clipped twice.
Regenerate the PNGs from `icon.svg` with `python ../tools/make_icons.py`.

### There is no service worker, deliberately

There was one. It never ran.

Served from `http://fan.local/`, the app is not a [secure
context](https://developer.mozilla.org/en-US/docs/Web/Security/Secure_Contexts)
— only `localhost` and `127.0.0.1` get that exemption — so
`navigator.serviceWorker` does not exist and neither does `caches`. Measured
against the real device, not assumed:

```
isSecureContext      : False
serviceWorker in nav : False
caches available     : False
```

It registered fine from the dev server, which is exactly why this went
unnoticed: the one environment where it worked was the one nobody ships.

Deleting it costs almost nothing, because the cache was never buying much
here. The worker was already network-first for navigations for the reason
that still holds — the fan is metres away on the same LAN, so the network is
the better answer, and cache-first meant a push to the fan's flash reached
the phone a launch late. A shell cache only helps when the app is open and
the fan is unreachable, and then there is no data to show anyway.

What it did cost was real: a file on the fan's flash that did nothing, and a
claim in this README that was not true.

The one genuine loss is Android's install prompt, which requires HTTPS plus
a service worker. iOS Add-to-Home-Screen needs neither and still works. If
the Android install is wanted, terminate TLS off-device — a proxy on a real
hostname with a real certificate — rather than putting a private key and an
mbedtls handshake on an RP2350 that already caps at three connections.

## Why Svelte, and why a PWA

One store per reading, and the UI follows it with no diffing in between —
which is the whole shape of a telemetry screen. Svelte also has the
smallest runtime of the serious options, and this is served over a LAN and
opened on a phone.

It installs to an iPhone or iPad home screen with no App Store, no developer
programme fee, and no review latency on a release coupled to firmware. The
usual reason to go native is Bluetooth, and provisioning deliberately does
not use it — see `../docs/phase-4-app-plan.md`.

## Nothing is optimistic

Every value on screen is the last one the device actually published. A
control publishes a command and then waits for the next reading to confirm
it. That is deliberate: a command which silently failed should look like a
command which silently failed, not like a success that quietly reverts 30
seconds later.

Tunables are the same. The device validates every write against its own
ranges and rejects anything outside them, so an edit is a request, not an
assignment.

## Tests

```bash
python ../tests/test_device_app.py [--headed]   # no broker, no board
python ../tests/test_app.py [--headed]          # needs broker + device
```

`test_device_app.py` runs the **real** `httpd.py` and `webapi.py` on the
host, bound to a stand-in controller and stepped exactly as the control loop
steps them, then drives the built app against them. It proves the whole
no-broker path — tiles, commands, and a week of backfilled history — with no
hardware at all. The only fiction is the fan.

`test_app.py` serves `build/` and drives it against the live broker and the
live fan, so it tests the artefact that would actually be deployed. It checks that the
values on screen are the ones the device published, and that pressing Boost
reaches the fan — verified by an independent MQTT subscriber rather than by
the app's own screen, which would be marking its own homework.

It drives the real fan for a few seconds and puts it back.
