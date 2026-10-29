# Deferred work

> **Resolved 2026-09-27 — the broker is no longer required.** The app used to
> reach the fan only through MQTT, and a broker in practice means Home
> Assistant's Mosquitto add-on — so "local first" quietly meant "as long as
> you run Home Assistant". The device now serves the app off its own flash,
> answers a JSON API, and keeps a week of history, so a user with neither has
> a working interface. Home Assistant lost nothing: set `MQTT_HOST` and the
> whole MQTT surface works exactly as before. See
> [`docs/phase-5-plan.md`](phase-5-plan.md).

> **Resolved 2026-09-27 — the Mosquitto WebSocket listener.** It was on this
> list as work for you to do. It turned out to need nothing: the Home
> Assistant Mosquitto add-on already serves MQTT over WebSocket on port
> **1884**, confirmed with a real handshake that returned `101 Switching
> Protocols` and negotiated `Sec-WebSocket-Protocol: mqtt`. The
> `listener 9001` block in the phase 4 plan was never needed. See
> `broker-setup.html`.

> **Resolved 2026-10-26 — the device card reads "Bathroom Smartfan".** This
> was on the list as an OTA still owed, and it had already gone out. The fan
> reports `name: "Bathroom Smartfan"` on `/api/info`, and the OTA of
> 2026-10-26 pulled only `webapi.py` and `store.py` out of the fourteen
> shipped files — so `config.py`, which is where `DEVICE_NAME` lives, was
> already byte-identical to the repo. `DEVICE_ID` is still `fan_bathroom`, as
> the comment above it insists, so the entities and their history never moved.

> **Resolved 2026-10-26 — the history seam fix is fully on the fan.** It was
> on this list for an hour as the app half still owed, because the bundle can
> only go over USB. `fw 485b54cd` by OTA and `app bcb319ed` by
> `tools/build_www.py --push COM5`, and verified in a browser against the
> fan: a session that ended seven minutes earlier now joins the live stream
> as one line. See [`history-seam-fix.md`](history-seam-fix.md), which records
> why the two halves travel by different routes and why neither works alone.

Things consciously parked, with what unblocks each. Not a wish list — if it
is here it is because it was decided rather than forgotten.

Sorted by what is in the way, not by importance.

---

## Blocked on a change only you can make

### GitHub repo name

Still `deokman420/fan-controller`. Renaming is one click in settings and
GitHub redirects the old URL, so nothing breaks. Left alone because it is
your account, not a technical constraint.

---

## Needs the hardware in hand

Host tests cover everything that can be decided without a radio. These
cannot be, and no amount of stubbing substitutes.

### iOS Captive Network Assistant

**Needed by:** trusting 4b in the field.

The setup sheet on iOS is a restricted WebView, not Safari. The OS can
dismiss it mid-flow, and navigating away from the AP kills it. The portal
is built for this and unverified against it.

**Unblocks with:** a bench board flashed with no `secrets.py`, and a phone.

### The real improv-wifi.com Web Serial client

**Needed by:** trusting 4c in the field.

`tests/test_improv.py` asserts the protocol against hand-computed bytes, so
the codec is sound. What it cannot prove is that the actual browser client
is happy with the sequencing and timing.

**Unblocks with:** the same bench board, over USB.

### Per-device setup labels

`tools/device_label.py` prints the sticker for a board. Nothing physical
has been printed or affixed yet. The PSK is derived from the chip id, so it
is recoverable at any time — this is a packaging task, not a risk.

---

## Decided, and deliberately not done

### Entity IDs still say `bathroom_fan_controller_*`

Not a leftover. `DEVICE_ID` is the identity Home Assistant keys the device
off, so changing it would orphan all 31 entities and detach their history.
Renaming the `entity_id`s is a manual per-entity migration for no
functional gain. A device named "Bathroom Smartfan" with entity ids saying
`fan_controller` is normal in Home Assistant.

### Tower as the app host

Probed 2026-09-27: no Docker socket, no `/graphql`, and `:5000` is an
Express + MongoDB app whose only resolving route is `/health`.

**Superseded** — you said to serve from here, so 4f no longer waits on it.
Revisit only if the app should outlive the dev machine, which would need an
Unraid API key.

### Improv BLE

Deferred, not rejected. Buys nothing on iOS without a native app, which is
the thing being avoided. Worth revisiting only if a native app ever
happens. See `docs/phase-4-app-plan.md`.

---

## Quality passes not yet run

### Setup, end to end, by somebody else

**Needed by:** handing a unit to anyone who did not build it.

The flow is built and unit-tested (`tests/test_provision.py`, 62 checks) and
the pages have been rendered and read, but the whole path — power up a board
with no credentials, join the setup AP from a phone, complete the form, land
on the app — has never been walked in one go on hardware. The parts that can
only fail there:

* **The iOS Captive Network Assistant.** A restricted WebView, not Safari.
  It can be dismissed mid-flow by the OS, and navigating away from the AP
  kills it. The portal is built for this and unverified against it.
* **The country field actually changing the channel list**, which is only
  observable against a router on channel 12 or 13.
* **Two units at once**, which is what the room field exists for and what no
  test can prove with one board.

**Unblocks with:** a second board, flashed with no `secrets.py`, and a phone.

### The on-device server, on a phone

**Needed by:** trusting phase 5 in the field.

Done on hardware already: `tests/test_device_http.py` runs against the bench
board over real WiFi — 17 checks, all passing. `fan.local` resolves, a browser
loads the bundle off flash in 3.3 s, Boost reaches the fan, eight
simultaneous requests are all served, and the control loop never missed a
watchdog feed. Three real bugs came out of that session; see
[`docs/phase-5-plan.md`](phase-5-plan.md).

What a desktop browser on a bench cannot settle:

* **A phone, in the room**, one-handed and steamy. That is the whole point of
  the thing.
* ~~**The service worker across a bundle push.**~~ Settled by deleting it.
  It never ran when served by the fan: `http://fan.local` is not a secure
  context, so `navigator.serviceWorker` does not exist there. It worked only
  from the dev server. See `app/README.md`.
* **A seven-day window rolling over**, so the two history files actually
  rotate in anger.

### WCAG AAA contrast audit of the dashboard

You asked for the dashboard to be "AAA worthy". It was rebuilt for layout
and density; whether it clears **AAA contrast** against your active Home
Assistant theme has not been measured. AAA wants 7:1 for body text.

This needs your theme, since the dashboard inherits it rather than setting
its own colours.

---

## Phase backlog

### Phase 3 — the real fan

The measured spec: **pull the room from 91 % RH back under 56 % within 30
minutes.** The 40 mm proof-of-concept fan cannot. It plateaus the room at
68–69 % and only reaches +2.1 % above baseline eventually, which is why
`MAX_RUN_S` went to 45 minutes rather than `RH_FALL_OFF` being loosened.

Needs a relay, a real ceiling vent fan, and permanent wiring.

### 4d, 4e — the app and its update surface

In progress. See `docs/phase-4-app-plan.md` for the work order.

### Phase 5 — the device serves the app

Built and tested on the host; not yet flashed to the board in the ceiling.
See `docs/phase-5-plan.md`, and the hardware item above for what is left.

### Phase 6 — changing the WiFi from the app

**Done.** `wifi.py`, `GET/POST /api/wifi`, `GET /api/wifi/scan`, and the
Network section in the app's settings sheet. Hold the fan's button for
`HOLD_PRESS_S` (8 s, both LEDs flash together) and the door is open for three
minutes.

Nothing is written until the radio has actually associated. The candidate
credentials live in `Link._try` and never reach the filesystem, so a failed
attempt needs nothing undone — dropping the override *is* the rollback. A
typo costs a retry rather than a ladder.

The one thing that cannot be hidden: a successful change takes the fan off
the network the request came in on, so the reply cannot carry the result. The
POST returns 202 and the app says plainly that the fan has left and the phone
has to follow. Silence after a switch is what success looks like from the old
network.

#### The automatic AP fallback was a bad idea, and is not built

The earlier note here said "N failed joins raises the AP". That was wrong,
and writing it down is cheaper than rediscovering why.

**It would break the rule the whole project rests on.** A router that is off
for an hour is an ordinary event, and the fan is supposed to keep running the
bathroom through it. Handing the device to a captive portal on WiFi failure
means a power cut at the router stops the fan controlling anything — trading
the failure this feature prevents for a worse one it would cause.

**Try-before-commit already removed the failure mode it was for.** Bad
credentials can no longer be written, so the "valid-looking secrets, no
network, no AP" state has no way to arise from the app any more.

A *deliberate* escape hatch — a second hold starting the AP on purpose,
never automatically — is still worth having for the case where the network
changes underneath a fan nobody has touched. It needs one thing first:
`provision.run()` blocks for minutes and feeds no watchdog, because it has
only ever run before the WDT is armed. Calling it from the live loop means
threading a feed through its portal loop. Not hard, but it is real work on
the recovery path, and it should not be bolted on in passing.

### The broker, from the app — done

`GET/POST /api/broker`, behind the same button window as the WiFi. The
argument is narrower than the WiFi's and still real: this cannot strand the
fan — wifi stays up and the app stays reachable whatever the answer — but
being on someone's network is not a licence to point their fan at your
broker, and these are the credentials that reach Home Assistant.

Built the same way anyway. The fan connects before it saves, so a rejected
broker leaves no trace and the failure is always "it did not work" rather
than "it did not work and something changed". An empty host is a legitimate
answer meaning "no broker", not a missing field, and commits at once because
there is nothing to connect to.

Two things fell out of it:

* **One `update_secrets()` writer.** Two rewriters of `secrets.py` would each
  have to remember to carry the other's keys through, which is the bug that
  loses a broker password the first time somebody changes networks.
* **`Telemetry` is always constructed.** It used to be built only when a
  broker existed *at boot*, so adding one from the app brought MQTT up with
  nothing to publish over it until the next reboot. Its constructor does no
  I/O and `publish()` returns immediately while offline, so an unused one
  costs a few topic strings.

### An OTA batch much past 60 kB does not install — needs USB

`ota.py` installs a whole batch or nothing. Past roughly 60 kB of staged bytes
the batch stages every file and then dies before the install step, and the
error goes only to a serial console nobody is holding. From the network the
symptom is silent: the device re-downloads the same files on every push, never
resets, and keeps running the old code.

Measured on this device: ~47 kB installed, ~48 kB installed, ~62 kB installed,
~98 kB did not, ~106 kB did not. Note it is the *batch total*, not any single
file — a 47 kB `main.py` pushed on its own installed fine. Every staged file is
written as `name.new` while the original and any `name.bak` are still on flash,
so a push needs roughly twice its own size free.

Two things to do, and the first matters more:

* **Say something when it fails.** The install failure is invisible from the
  network. `ota_state` is set on the Controller and then never published, so
  the one surface that could report it does not. Publishing it — and a free-
  space figure in `/api/info` — would have turned an afternoon of bisecting
  into one glance.
* **Stop needing the space.** Stream each download to its `.new` file instead
  of buffering the body, and stage/swap one file at a time so peak usage is one
  file rather than the batch.

What unblocks it: `ota.py` is excluded from OTA on purpose, because it is the
recovery path and a recovery path you can break remotely is not one. So this is
a USB push, and it should go over the cable together with a test that pushes a
deliberately oversized batch and watches it install.

Until then, keep a push under ~60 kB, and remember that staging means a module
can ship one push ahead of the `main.py` it depends on — see the `getattr` in
`Snapshot.payload` and the vestigial `LIGHT_ON` in `config.py`. Written up in
[`daylight-is-brighter-than-the-bulb.md`](daylight-is-brighter-than-the-bulb.md).
