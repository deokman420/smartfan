# Smartfan app

Local-first control and monitoring. A static bundle that talks straight to
your MQTT broker over a WebSocket — no application server, no cloud, no
account.

```bash
npm install
npm run dev      # http://localhost:5173
npm run build    # -> build/, static files
npm run preview  # serve the built bundle
```

## How it connects

The app is a client of the **broker**, not of the device. Nothing sits in
between, which is what makes local-first literal. It also means that if the
broker is down the app goes stale while the fan keeps running on its own
logic. **The app is never in the control path.**

Browsers cannot open raw TCP, so port 1883 — the one the device and Home
Assistant use — is unreachable from a page no matter what is configured.
The app uses MQTT over WebSocket on **1884**, which the Home Assistant
Mosquitto add-on already serves. Full reference in `../broker-setup.html`.

Connection settings live in the app, under Settings, and persist in that
browser. Defaults are `192.168.0.5:1884`, room `bathroom`.

**A username and password are required.** The Home Assistant Mosquitto
add-on does not allow anonymous connections, so the app asks for them on
first run rather than dialling out with nothing and reporting the refusal
as a fault. A credential the broker has already refused is not retried —
the default reconnect loop would hammer the broker with a login that
cannot start working on its own, and bury the reason.

> Credentials entered here are readable by anyone who opens the page and its
> dev tools. That is true of every browser MQTT client and cannot be
> engineered away. Give the app account the narrowest access that works and
> treat it as a LAN credential, not a secret.

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
hour ranges and min / average / max.

History is a rolling 24 hours kept **in this browser**, fed from the same
MQTT stream that drives the tiles — so the charts can never disagree with
the readings. Home Assistant's recorder already holds 30 days and was the
obvious alternative; it was rejected because it would add a second
credential, a second service that has to be up, and a dependency on the
very thing the app is meant to work without.

The honest cost: history only covers time this browser has had the app
open. It survives reloads, and gaps are drawn as **breaks rather than a
confident straight line** across a period nobody recorded.

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

## Why Svelte, and why a PWA

One store per topic, and the UI follows it with no diffing in between —
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
python ../tests/test_app.py [--headed]
```

Serves `build/` and drives it against the live broker and the live fan, so
it tests the artefact that would actually be deployed. It checks that the
values on screen are the ones the device published, and that pressing Boost
reaches the fan — verified by an independent MQTT subscriber rather than by
the app's own screen, which would be marking its own homework.

It drives the real fan for a few seconds and puts it back.
