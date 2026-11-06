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
its own colors.

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
being on someone's network is not a license to point their fan at your
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

### A DHT22 fault appeared minutes after a USB push — cause not established

Seen once, 4 Nov, on build e3b61612 just after the three-file USB push:

    state  FAULT
    reason DHT22 not responding
    up_min 10.5

`clear` recovered it immediately and the sensor has read correctly since
(24.8 C, 60-63 %RH), so the hardware is fine and nothing is disconnected.

**The obvious explanation is wrong.** The first guess was the safemode dance,
which resets the board twice in quick succession, and a DHT22 read attempted
before the sensor settles. But `check_fan_health()` needs `dht_fails >= 5`,
and **any successful read puts the counter back to 0**, so this took five
CONSECUTIVE failures. A settling problem would have faulted in the first
seconds; instead the board was in `LEARN` with no fault at 0.9 min and
faulted somewhere before 10.5 min.

What is known:

* It was not faulted at boot. Something produced a run of five bad reads
  roughly 1-10 minutes in.
* `tests/test_ha.py` ran in that window and drives Force on / Force off
  cycles, so the fan was switching. A fan transient coupling into the DHT22
  line is a candidate and nothing more -- the two have never been correlated.
* The preceding 8-hour run did not fault once, so this is not a steady rate.

**The counters are published now.** `dht_fails` (consecutive, trips FAULT at
5, zeroed by any good read) and `dht_bad` (every bad read since boot, never
reset) are both in the snapshot, so a run of four no longer looks like a run
of none, and one good read can no longer hide the episode. The cause of the
4 Nov fault is still unknown. The next one is a measurement.

### `main.py` cannot be sent over the air — the downloader buffers whole files — **fixed 5 Nov**

**Measured 4 Nov, and it settles the open question about the size ceiling.** A
106 kB batch (`main.py` 74073 B, `config.py`, `settings.py`) was published to
the device. It fetched all three and then:

    ota: error: memory allocation failed, allocating 40449 bytes

with **330 kB of heap free and 581 kB of flash free**. So the ceiling is not
flash, and not total RAM either — it is **heap fragmentation against a single
contiguous buffer**, because `ota.py` holds each response body in memory before
writing it. A 74 kB file needs a 74 kB block, and after hours of uptime there
isn't one.

This is why the largest batch ever proven is 50 kB. It is a per-file limit, not
a per-batch one, and `main.py` is permanently over it.

**The failure was clean**: no reboot (uptime unbroken), no fault, `fw` unchanged,
flash unchanged, nothing staged, no rollback needed. It gave up before touching
anything, which is the right behavior.

Two things follow:

* **The streaming rewrite is required, not optional.** It was filed here as
  "possibly unnecessary". It is the only way `main.py` ever goes over the air,
  and `main.py` is where the control logic lives — so today every control change
  needs the cable.
* **The pre-flight check is half a check.** `MIN_FREE` and `free_bytes()` verify
  flash and never look at RAM, so a batch that cannot possibly be buffered is
  waved through and fails mid-download. It should refuse on the largest single
  file against `gc.mem_free()` and say so, the way it already does for flash.

**Resolved 5 Nov 2026.** `ota.py` streams the body in `CHUNK` (512 B) reads
straight to the staged `.new` file, so no contiguous buffer is needed at any
size, and a short body is now named as short rather than surfacing as a
checksum mismatch that sends you looking at the publisher.

Proven the only way that counts: an **88,284-byte `main.py`** went over the
air, `fw 043c589c` → `1dff8dc8`, with no leftovers. The first bullet above is
done.

The second bullet is now moot rather than done — with nothing buffered there
is no RAM pre-flight to add, and `MIN_FREE`/`free_bytes()` against flash is
the whole of the check that is needed.

What remains true: `boot.py` and `ota.py` are still excluded from `SHIP`
deliberately, because a recovery path you can break remotely is not one. Those
two still need the cable.

### The VOC settle gate never fires on flatness — it times out — **VOC limit 70, 6 Nov**

Measured 4 Nov on a clean boot (2085 samples, 6 h, the device in the bathroom):

| pixel | flattest 15-min window | outcome |
|---|---|---|
| NOx | 31 ticks | fired honestly at **75.6 min** |
| VOC | **51 ticks** | hit the **120 min ceiling** |

`_FLAT_TICKS` was 45 and shared by both pixels, so VOC could never satisfy it —
its own quiet-air noise over 15 minutes was wider than the threshold. The log
line read `VOC SETTLED at up_min=120.2`, which was `VOC_SETTLE_MAX_S` expiring,
not the gate deciding anything. **For VOC the gate was a 2-hour timer.**

It was not doing harm: the ceiling seeded 30417 and the following six quiet
hours sat at a mean of +11 ticks, so the baseline was right. But the gate was
written to notice flatness and for half the sensor it did not. VOC's flattest
patch was 70–85 min and the raw count there is within 5 ticks of where it
settled, so a per-pixel threshold near 70 fires on that patch and lands on
the same number 35 minutes sooner.

**Set 2026-11-06, per pixel.** VOC's limit is 70. NOx stays at 45, because
its gate already fires on a 31-tick window and 70 would only make that slack.

70 is also above a 4 tick/min ramp, which is 60 ticks across the same
15-minute span and the slope the VOC pixel still had at 30 minutes. So the
gate may now seed VOC during the last of the warm-up, about 35 minutes
sooner, onto a count the two-hour ceiling was already landing on. That was
the trade. A second bathroom boot is what says whether 70 fired on the flat
or on the tail of the ramp.

### Nothing bounds the fan's off-time, only its on-time

Same series. The real odor event decayed through the trigger band and
restarted the fan twice, with **0.6 and 1.1 minutes off** in between.
`MIN_RUN_S` is 180 s and there is no counterpart; `VOC_REST_S` only applies
after a `MAX_RUN_S` latch, which is a different situation.

Raising `VOC_RISE_ON` to 200 widened the hysteresis gap to 130 ticks (3.7 sd of
quiet noise, up from 1.4) and both restarts fall below the new line, so this is
**masked rather than fixed**. A source that genuinely oscillates across 200
would still cycle. The honest fix is a minimum off-time on the odor path.

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
  the web step in `main.py`. Size is not what makes that hard: `main.py` goes
  over the air;
* no message at all after a restart means the record itself does not work. It
  could not be tested on the board when it shipped, as there is no remote restart.

Close this after a few quiet days. Note that the OTA trial commits on "no FAULT
for 60 s" and does not check the network, so a bad `net.py` would be kept.

### Eight parallel requests fail after a heavy page load — cause not proven

Seen 2026-11-06, on the board, in `tests/test_device_http.py`. The
"eight simultaneous requests are all served" check failed three runs out of
four and passed the fourth. Every failure was a connection error, not an HTTP
status. The same eight requests, run on their own with curl and with Python,
passed every time. It shows up only after that suite's browser load and the
history stream (1713 rows).

The likely mechanism is sockets the Pico's TCP stack has not finished with —
the browser's keep-alive connections plus the history response, which is
closed rather than kept alive because its length is not known — set against
a small connection table. `httpd.py` caps application connections at 4 and
evicts an idle keep-alive to make room; that does not free a PCB still in
TIME_WAIT. `httpd.py` was not touched by the commits under test, so this is
not a regression from that push.

Not "fixed" by closing harder. The last time a full socket was closed without
a drain, the client saw a RST and lost the response, which is how the 503
and the 413 used to arrive. Needs a capture of connection state on the board
at the moment the ninth handshake fails, not another guess at the timeout.

### An OTA installs, then loses a race and is rolled back — **fixed**

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
> **Applied.** `Controller._wdt_due` is that condition:
> `return self._ota_pending or elapsed_ms > 3000`. A trial boot takes the
> watchdog on the first pass; an ordinary boot still waits 3 s; and
> `WDT_TIMEOUT_MS = 0` still disables it. `tests/test_control.py` pins all
> three. It is on the device as of the USB deploy through `4e6890b`.

Two things were left under this heading, and both are done:

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
* **Stop needing the space.** **Done 2026-11-05.** `ota.py` streams each body
  to its `.new` file in `CHUNK` reads instead of `r.content`. An 88,284-byte
  `main.py` installed over the air. The full write-up is the entry above,
  "main.py cannot be sent over the air". The "keep a push under ~60 kB" rule
  that used to end this section is withdrawn.

  Asking whether `boot_check`'s rollback
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

What remains true, and is not a size limit: `boot.py` and `ota.py` are excluded
from OTA on purpose, because a recovery path you can break remotely is not one.
Those two still need the cable. Staging can still put a module one push ahead
of the `main.py` it depends on — see the `getattr` in `Snapshot.payload` and
the vestigial `LIGHT_ON` / `LIGHT_OFF` in `config.py`. The measurements this
was diagnosed from are in
[`daylight-is-brighter-than-the-bulb.md`](daylight-is-brighter-than-the-bulb.md),
whose size rule is withdrawn at the top of that section.
