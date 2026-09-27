# Deferred work

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
