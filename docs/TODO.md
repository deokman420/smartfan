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

Things consciously parked, with what unblocks each. Not a wish list — if it
is here it is because it was decided rather than forgotten.

Sorted by what is in the way, not by importance.

---

## Blocked on a change only you can make

### OTA push so the device card reads "Bathroom Smartfan"

**Needed by:** finishing the rename.

The dashboard title is host-side and already live. The device *card* name
lives in `config.DEVICE_NAME`, which is on the device, so it needs an OTA
before Home Assistant shows it.

Nothing is broken meanwhile: `DEVICE_ID` is unchanged, so all 31 entities,
their `entity_id`s and their history are intact. The device simply still
introduces itself by the old name.

**Unblocks with:** `python tools/ota_publish.py`

---

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

Decided, not built. Today a new network means USB and `provision.py`, which
for a fan in a ceiling means a ladder.

Most of the machinery already exists: `provision.py` writes credentials
atomically (`secrets.new` then rename, so a power cut cannot leave a
`secrets.py` that will not import), `_carry_over()` keeps the MQTT settings
through a re-provision, and there is already a SoftAP with a captive portal.

Two things have to be added, and neither is the API endpoint:

**Try before commit.** `needs_provisioning()` fires when credentials are
*absent*, never when they are *wrong*. Typing a password wrong from the app
would put the fan on no network with valid-looking secrets and no AP — USB
recovery only, which is the exact failure this feature exists to avoid. Join
the new network while still holding the old secrets and only write on
success. `ota.py` already works this way; this is the same shape.

Add the matching boot-side fallback too: N failed joins raises the AP. That
makes the whole class of problem recoverable without a ladder, including the
one where the router changes underneath a fan nobody has touched.

**A button press, not a password.** The API's protection today is that the
fan is only reachable from your network, which is proportionate for reading
humidity and pressing Boost. Writing WiFi credentials is not in that class:
anyone on the LAN, guests included, could take the fan off the network. A
long press opens a short provisioning window and the API only accepts
credentials inside it. Physical presence as authorisation, which fits the
rest of this device better than bolting on an account — and the button and
its long-press handler are already there.
