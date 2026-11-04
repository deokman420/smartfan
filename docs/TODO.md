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

> Entries here describe the plan as written. **The resolved notes at the top
> of this file supersede them**, and the two had drifted apart — Phase 5 was
> recorded as resolved up there and as not-yet-flashed down here at the same
> time. Where they disagree, check the device.

### Phase 3 — the real fan

The measured spec: **pull the room from 91 % RH back under 56 % within 30
minutes.** The 40 mm proof-of-concept fan cannot. It plateaus the room at
68–69 % and only reaches +2.1 % above baseline eventually, which is why
`MAX_RUN_S` went to 45 minutes rather than `RH_FALL_OFF` being loosened.

Needs a relay, a real ceiling vent fan, and permanent wiring. **Still open
as of 2026-11-03** — the hardware is unchanged, still the Noctua NF-A4x10,
which is why `MAX_RUN_S` is 2700 rather than `RH_FALL_OFF` being loosened.

### 4d, 4e — the app and its update surface

In progress against the work order in `docs/phase-4-app-plan.md`, which is
the authority on what is left. What is observably shipping as of 2026-11-03:
the app builds, is pushed to flash by `tools/build_www.py --push`, is served
by the device, backfills a week of stored history, and carries the WiFi
panel. The update surface for the app is USB only, by design — `www/` is
versioned apart from the firmware and does not travel over the air.

### Phase 5 — the device serves the app — **done**

> **Reconciled 2026-11-03.** This said "built and tested on the host; not yet
> flashed to the board", which contradicted the resolved note at the top of
> this very file and had done for weeks. Verified against the live device
> rather than against either note:
>
> ```
> GET http://fan.local/  ->  200, gzip, 3571 bytes decompressed, <!doctype html
> /api/info              ->  app 5076fe72
> ```
>
> The board serves the app off its own flash, answers the JSON API and keeps
> a week of history. `tests/test_app.py` drives it in a real browser against
> the real fan and passes. See `docs/phase-5-plan.md` for the design.

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

### Watchdog restarts every few hours — watching, cause not proven

Seen 2026-11-03 at 13:18, 16:51 and 22:10 on build 4b4e29f1, reported as
`watchdog or reset`, with plenty of memory free and a strong signal. Network
steps could wait longer than the 8 s watchdog: a 10 s MQTT connect timeout, and
5 s for a PINGRESP plus 5 s for the close. That fits the evidence but was not
caught happening.

Build 40a26ae8 keeps every network wait inside the watchdog (`tests/test_net_budget.py`)
and records the step in progress in a watchdog scratch register. After a restart
that step is sent retained on `fan/<room>/last_stall`:

* a network step name means the budget still has a hole;
* `idle` means the stall was outside `net.py`, so look at the sensors, flash and
  the web step, which will need `main.py` and may be too big to push over the air;
* no message at all after a restart means the record itself does not work. It
  could not be tested on the board when it shipped, as there is no remote restart.

Close this after a few quiet days. Note that the OTA trial commits on "no FAULT
for 60 s" and does not check the network, so a bad `net.py` would be kept.

### An OTA installs, then loses a race and is rolled back — needs USB

> **Re-diagnosed 2026-11-03, and the old heading was wrong.** This was filed as
> "a batch much past 60 kB does not install", attributed to flash space. It is
> not about size and it is not about space. The update **installs perfectly**;
> the *trial boot* then fails, twice, and the rollback correctly puts the old
> code back. Seen end to end over the cable, serial captured through all three
> boots:
>
> ```
> ota: 2 file(s), 50232 B to stage, 610304 B free: webapi.py, telemetry.py
> ota: staged webapi.py (22253 bytes)
> <<reset>>
> telemetry: build 22e12496   ota: update on trial, committing after 60s healthy
> net: offline -> wifi        <<reset>>        <- died before "wdt: armed"
> telemetry: build 22e12496   ota: update on trial, committing after 60s healthy
> net: offline -> wifi        <<reset>>        <- again
> telemetry: build 64bf4aa9                    <- MAX_TRIES spent, rolled back
> ```
>
> **The cause.** `boot_check()` arms an 8 s hardware watchdog whenever an
> update is pending — that is what catches `main.py` failing at import. But
> `run()` deliberately feeds nothing for its first 3 s:
>
> ```python
> if self.wdt:
>     self.wdt.feed()
> elif cfg.WDT_TIMEOUT_MS and ms_since(started) > 3000:
>     self.wdt = WDT(timeout=cfg.WDT_TIMEOUT_MS)
> ```
>
> "Arming on the first line would make any startup hiccup a reboot loop" is
> sound on an ordinary boot, where no watchdog exists yet. On a trial boot it
> is backwards: one is already running, declining to feed it protects nothing,
> and WiFi association has to finish inside whatever is left of eight seconds.
> It is a race, which is why the results looked erratic and size-correlated
> when the variable is really how long the radio takes to associate.
>
> **Size was never plausible.** Measured on the board the same day:
> **596 kB of flash free, 325 kB of heap free while running.** A 106 kB batch
> needing twice its own size is nowhere near either number.
>
> The original observations stand as observations — ~47, ~48 and ~62 kB
> installed; ~98 and ~106 kB did not — but the explanation attached to them
> did not survive being measured.
>
> **Proposed fix, not yet applied:** adopt the watchdog immediately when an
> update is on trial, since `boot_check` has already armed one —
> `elif cfg.WDT_TIMEOUT_MS and (self._ota_pending or ms_since(started) > 3000)`.
> Untouched so far because it is the recovery path, which is the one thing a
> bad change cannot fix remotely.

Two things to do, and the first matters more:

* ~~**Say something when it fails.**~~ **Done 2026-11-03.** `ota_state` is
  published as `ota`, beside `flash_free`, in the snapshot — so it reaches
  `/api/state`, MQTT discovery and a Home Assistant diagnostic entity — and
  both appear on `/api/info`. The updater also now does the arithmetic
  *before* spending the radio: it sums the batch from the manifest's `size`
  fields, compares against `os.statvfs`, and refuses a batch that would
  leave less than `MIN_FREE` with a message naming both numbers. A manifest
  from an older publisher with no sizes is planned anyway rather than
  refused. `tests/test_ota.py` covers the planning step; the module had no
  test before, which was uncomfortable for the one file deliberately
  excluded from its own update mechanism.
* **Stop needing the space.** Still owed, and still the real fix. Stream each
  download to its `.new` file instead of buffering the body in `r.content`,
  and stage/swap one file at a time so peak usage is one file rather than the
  batch.

  **The blocker on this is cleared.** Asking whether `boot_check`'s rollback
  could cover a partial swap turned up a worse bug than the one being
  investigated, now fixed: the pending marker was written *after* the rename
  loop, so a power cut inside it left a half-swapped set and no marker at
  all — `boot_check` returned False, armed no watchdog and rolled nothing
  back. In the instant between a file's two renames it does not exist, and
  if that file were `main.py` the board would drop to the REPL at import
  with nothing able to rescue it. The marker now goes down first, carrying
  `swapping: true`, and an interrupted swap is rolled back on sight rather
  than being given an attempt — validating it would be worse, because a
  mixture that happened to run would be committed after `COMMIT_S`.

  The rollback was already per-file and tolerant of absent `.bak`s, which is
  exactly what per-file staging needs, so that trade no longer costs
  atomicity. `tests/test_ota.py` covers it: breaking the rename loop
  partway leaves a real missing file, and the rollback puts the whole set
  back.

What unblocks it: `ota.py` is excluded from OTA on purpose, because it is the
recovery path and a recovery path you can break remotely is not one. So this is
a USB push, and it should go over the cable together with a test that pushes a
deliberately oversized batch and watches it install.

Until then, keep a push under ~60 kB, and remember that staging means a module
can ship one push ahead of the `main.py` it depends on — see the `getattr` in
`Snapshot.payload` and the vestigial `LIGHT_ON` in `config.py`. Written up in
[`daylight-is-brighter-than-the-bulb.md`](daylight-is-brighter-than-the-bulb.md).
