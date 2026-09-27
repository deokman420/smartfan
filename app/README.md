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
