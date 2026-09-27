# Phase 4 — Shipping it: provisioning, the app, update delivery

## The governing constraint

**The app is optional. Everything the device does must be reachable without
installing anything.**

This is not a nice-to-have, it is the constraint that decides the
architecture. Phase 2's rule was that the fan works with the network down;
this one is its sibling: the fan works with *the app absent*. A device that
becomes useless because its vendor app left the store, or because the owner
declined the install, is a device that failed.

Everything below falls out of that sentence.

## Decision: SoftAP, not Bluetooth

**Web Bluetooth does not exist in iOS Safari.** It never has. Third-party
iOS browsers are required to use WebKit, so they inherit the same gap.

That single fact settles the question. BLE provisioning on an iPhone
*requires* a native app, and requiring the app to complete setup contradicts
the governing constraint. So BLE cannot be the primary path.

| | SoftAP portal | BLE |
|---|---|---|
| iPhone, no app | works | impossible |
| Android, no app | works | Chrome only, via Web Bluetooth |
| Inside the app | works | works |
| Firmware cost | HTTP server + DNS responder | BLE stack, GATT service, app |
| Radio | reuses the WiFi path already present | second stack, shared antenna |

The flow:

1. No credentials on flash, or association fails repeatedly, so the device
   raises its own AP: `Smartfan-XXXX`, where XXXX derives from the machine
   ID and is printed on the label.
2. Phone joins it. Both iOS and Android fire a captive-portal probe; the
   device answers every DNS query with its own address, so the setup sheet
   opens by itself.
3. User picks an SSID from a scan and types the password.
4. Device writes `secrets.py`, drops the AP, associates, and publishes
   discovery. The AP never returns unless provisioning is cleared.

**Improv Serial** is worth adding alongside — the Nabu Casa standard that
ESPHome uses, so Home Assistant owners already recognise it. It provisions
over USB from a browser using Web Serial, which makes it the right tool at
flash time and the right recovery path when someone forgets which network a
device is on. Small protocol, transport already present.

**Improv BLE is deferred, not rejected.** If a native app ever happens it
becomes a pleasant accelerator on Android. It buys nothing on iOS.

## Two UIs, with opposite requirements

Conflating these is the classic way this goes wrong.

### The on-device setup portal

Lives in the Pico's flash. RP2350 has 520 KB of SRAM, and the 4 MB of flash
is already largely MicroPython. This UI runs **once per device lifetime**.

It must be **plain, tiny, dependency-free HTML, CSS and one small script**,
inlined into a single response. No framework, no build step, no CDN — during
setup the device is an access point with no route to the internet, so every
external asset resolves to a blank page. This is the one place in the
project where plain HTML is not a shortcut but the engineering answer.

Budget: under 20 KB served, zero external requests, renders correctly in the
iOS Captive Network Assistant, which is a restricted WebView.

### The app

The ongoing monitoring and control surface. This is where "robust" applies.

**SvelteKit with the static adapter, built by Vite, shipped as an
installable PWA.**

- **Svelte, not React.** Smallest runtime of the serious options, which
  matters for something served over a LAN and opened on a phone. Compile-
  time reactivity maps directly onto streaming telemetry: one store per
  topic, no virtual-DOM diffing for a screen that repaints every 30 seconds
  and on change.
- **PWA, not native.** Installs to the iPhone and iPad home screen with no
  App Store, no developer programme fee, and no review latency on a release
  coupled to firmware. For a local-first appliance, store distribution is
  friction with no return. The usual argument for going native is BLE, and
  BLE is already out.
- **Static, no backend.** See transport below.

## Transport: MQTT over WebSockets, direct

Browsers cannot open raw TCP, so the app speaks MQTT over WebSockets to the
same broker Home Assistant uses, subscribing to the topics the device
already publishes. There is no application server in the middle. That is
what makes "local first" literal rather than a slogan.

**No broker change is required.** This plan originally called for adding a
`listener 9001` / `protocol websockets` block. That was wrong: the Home
Assistant Mosquitto add-on already exposes MQTT over WebSocket on **1884**,
verified on 2026-09-27 with a handshake that returned `101 Switching
Protocols` and negotiated `Sec-WebSocket-Protocol: mqtt`.

The app connects to `ws://192.168.0.5:1884/` — root path, not `/mqtt`, which
is where several clients default and then report the failure as a refused
connection. Full reference in `broker-setup.html`.

Per-device MQTT credentials were already decided in phase 2, so the app
authenticates as the owner rather than as the device.

**Consequence worth stating plainly:** the app is a client of the *broker*,
not of the device. If the broker is down the app shows stale data while the
fan keeps running on its own logic, exactly as phase 2 guarantees. The app
is never in the control path.

## Update notification reuses what exists

Nothing new is needed on the device side:

| Exists | Role here |
|---|---|
| `build` fingerprint sensor | what version is actually running |
| `tools/ota_publish.py` | builds the manifest, serves it, triggers |
| `ota.py` + `boot.py` | staged install, sha256, boot-loop rollback |

The app fetches a manifest, compares it against the published `build`
fingerprint, and raises a banner. Applying an update is the existing OTA
path; the app only has to ask for it. App updates are a service worker
refresh, which the PWA gives for free.

## Blocked: Tower

Probed 192.168.0.16 on 2026-09-27:

| Port | Result |
|---|---|
| 80 | connects then empty reply, not clean HTTP |
| 443 | TLS handshake fails |
| 5000 | Express + MongoDB, healthy, CORS on. Only `/health` resolves |
| 2375, 2376 | closed, no Docker socket |
| `/graphql` | absent, Unraid's own API not exposed |

**No reachable container-management API.** Deploying there would need an
Unraid API key (Settings, Management Access, API keys) or an explanation of
what the `:5000` service is.

**Superseded:** serving from the dev machine instead, so 4f no longer waits
on any of this. Revisit only if the app should outlive that machine.
Tracked in `docs/TODO.md`.

## Work order

### 4a — Provisioning firmware. Done.

AP mode, DNS responder, HTTP server, scan and join, credential write. Must
cooperate with the existing watchdog and must never run while the control
loop is live.

### 4b — The setup portal. Done.

Single inlined page, `provision.py`. Scan list, password field, progress,
and failure states that say what to do next. 2.9 KB against a 20 KB budget,
no external requests, one screen at 393 px with no scrolling.

The SSID list is rendered with event delegation reading `textContent`, not
an inline `onclick` carrying the name. Nearby SSIDs are chosen by other
people: one named `a\` breaks out of a quoted JS string argument, and one
named `'><script>` out of the attribute. `tests/test_provision.py` holds
both as regressions.

Still untested in the iOS Captive Network Assistant specifically -- that
needs the firmware on a board and a phone.

### 4c — Improv Serial. Done.

`improv.py`, over USB, at flash time and for recovery. Transport-agnostic
by design: the codec and state machine take bytes in and hand bytes back,
so the protocol is fully testable on a host.

Both provisioning routes land on the same `_attempt()`, so there is one
join path and one place that writes credentials.

No `dupterm` surgery is needed — while `main.py` is executing the REPL is
not reading stdin, so the bytes arrive directly. Ctrl-C interception does
have to be disabled for the window: `0x03` occurs freely inside packet
data, and a `KeyboardInterrupt` raised out of a credential would drop the
board to the REPL mid-setup.

Untested against the real improv-wifi.com Web Serial client; that needs a
board.

### 4d — The app. Done.

`app/`. SvelteKit built static by Vite, MQTT over WebSockets on 1884,
installable as a PWA. 442 KB of JS, one screen, no horizontal scroll at
393 px.

Nothing on screen is optimistic: every value is the last one the device
published, and a control publishes then waits for confirmation. A command
that silently failed should look like one, not like a success that reverts
30 seconds later.

`tests/test_app.py` — 16 checks against the live broker and the live fan,
serving `app/build` so it tests the deployable artefact. Boost round-trip
is confirmed by an independent subscriber rather than by the app's own
screen.

### 4e — Update surface

Manifest, fingerprint comparison, banner, trigger.

### 4f — Deploy

Served from here. Tower revisited only if the app should outlive this
machine — see `docs/TODO.md`.

## Things that will bite

**The iOS captive sheet is a restricted WebView.** It is not Safari. The OS
can dismiss it mid-flow, and navigating away from the AP kills it. The
portal must survive being reopened and must not depend on anything it cannot
inline.

**Android probes for internet and will silently leave.** Answer the
connectivity check wrongly and Android decides the network is useless, then
switches back to mobile data in the middle of setup.

**A wrong password is indistinguishable from a distant router.** MicroPython
surfaces little detail. The portal must retry, time out honestly, and return
to the form with the SSID still filled in rather than claiming a fault.

**Credentials sit in plaintext on the flash.** Fine for one device on a
personal network. If this ships to other people it is a disclosed property
of the product, not something to be discovered later.

**The AP is open during setup.** A window in which anyone in range can post
credentials to the device. A per-device PSK printed on the label closes it
and costs one label change.

**Shared antenna.** The CYW43439 has one. AP and station modes must be
sequential, never concurrent. Firmware discipline, not a capability gap.

## Decisions taken

| Decision | Why |
|---|---|
| SoftAP primary | Only path that works on iOS with no app |
| Improv Serial secondary | USB and recovery, a standard HA owners know |
| BLE deferred | Nothing on iOS without native, the thing being avoided |
| Svelte over React | Smallest runtime, reactivity suits streaming telemetry |
| PWA over native | No store, no fee, no review latency, BLE not needed |
| No backend | App talks to the broker; local-first has to be literal |
| Reuse OTA | Fingerprint, manifest and rollback already exist |

## Not in scope

Cloud accounts, remote access, telemetry collection, multi-user permissions,
an App Store presence. Each contradicts local-first, and none is needed for
the device to do its job.
