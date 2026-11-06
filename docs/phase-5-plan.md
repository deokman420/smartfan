# Phase 5 — the device serves its own app

**Status: done and running on hardware.** Built and tested on the host
2026-09-27, then flashed to a bench board the same day and verified over real
WiFi: `tests/test_device_http.py`, 18 checks, all passing. See "What the
hardware taught us" at the end for what that changed.

## The question that started it

> If a user doesn't have Home Assistant and only wants to use the app — right
> now the device writes to HA and the app would stop working if we lost the
> connection. Is there a way to be local/privacy first this way?

The premise turned out to be half wrong in a way worth writing down, because
the wrong half was never the problem and the right half was hiding under it.

**The device never wrote to Home Assistant.** It published to MQTT; HA was
one subscriber and the app was another, independent one. Control was already
entirely on-device — `main.py` reads, decides and drives the outputs before
telemetry is even attempted, and the publish is wrapped so it cannot raise.
Switch HA off and the fan and the app both carry on.

**But the broker was a single point of failure, and a broker in practice
means Home Assistant.** Every document here pointed at the Mosquitto add-on,
down to the default `192.168.0.5:1884` and "a username and password are
required" — which was only true because *that* add-on forbids anonymous
connections. So: no HA → no broker → no app. "Local first" quietly meant
"as long as you run Home Assistant".

## What was considered

**1. Document a standalone broker.** Mosquitto in Docker with a WebSocket
listener. An hour of work, no code. Rejected as the whole answer, not as a
step: it replaces a dependency on Home Assistant with a dependency on a
container someone has to keep running, and the honest version of "local
first" for a device like this is that the device is enough.

**2. The device serves the app and an API.** Chosen. Nothing else has to be
up, the address is on the label, and the fan and its interface become one
thing you plug in rather than two services to keep alive.

**3. A WebSocket server on the device.** Rejected. It would hold a connection
open on the side of a single-core board whose first duty is a fan, to save
polling a 300-byte endpoint every two seconds on a LAN. The device only
samples every two seconds anyway.

**4. Home Assistant's recorder for history.** Rejected again, for the reason
it was rejected the first time: a second credential, a second service that
has to be up, and a dependency on the thing the app must work without.

## What was built

| | |
|---|---|
| `httpd.py` | Non-blocking HTTP/1.1, stepped from the control loop with a byte and time budget. Keep-alive, gzip from flash, SPA fallback. |
| `webapi.py` | `/api/state`, `/api/info`, `/api/history`, `/api/cmd`, `/api/set/<PARAM>`. |
| `store.py` | Five-minute means, seven days, two alternating append-only files. |
| `tools/build_www.py` | Gzips `app/build` into `www/` and pushes it over USB. |
| `app/src/lib/link.js` | Was `broker.js`. The stores, with a transport behind them. |
| `app/src/lib/transport/` | `device.js` (HTTP) and `mqtt.js` (as before, now lazy). |

Plus: `telemetry.Telemetry` split into `Snapshot` (what the device is) and
`Telemetry` (publishing it), because the payload could previously only be
built by the object that owned an MQTT connection; `net.py` gained a
WiFi-only state for an empty `MQTT_HOST`; `history.js` gained `backfill()`.

## The rules it was built to

1. **Control comes first.** `web_step()` runs last in the loop pass, after
   the outputs are driven and after the watchdog has been fed by a pass that
   did its real work. Budget is 8 ms against a 20 ms loop. Every socket is
   non-blocking; every exception is swallowed.
2. **One command path, two doors.** Every HTTP write ends in
   `controller.on_command()` — the same function MQTT calls, reached by
   synthesising the topic. A parallel implementation would drift, and the
   first symptom would be a threshold the app accepts and the broker rejects.
3. **Additive, like the network layer before it.** Missing modules, no
   `secrets.py`, `HTTP_ENABLED = False` — each leaves a controller that runs
   the fan exactly as it would offline.
4. **Nothing is optimistic.** Unchanged, and it survived the new transport:
   a command posts and then waits for the next reading to confirm it.

## Decisions worth keeping

**Gzip only, no uncompressed copy.** 172 kB instead of 544 kB, and flash is
the scarce resource. `httpd` serves `X.gz` with `Content-Encoding` for a
request for `X`. Cost: `curl` needs `--compressed`.

**The MQTT client is a lazy chunk.** 106 kB of the 172 kB is `mqtt.js`, and
a device-mode user never fetches it. It stays on flash so that broker mode
still works from a device-served page.

**No authentication, said out loud.** A LAN device whose physical control is
a button on the wall. What protects it: writes need POST; CORS is reflected
only for local origins; `update` is refused over HTTP and stays on the MQTT
path, which has credentials. `HTTP_ENABLED = False` is the opt-out.

**Five-minute means, not raw samples.** Keeping the 30 s stream for a week
would be 20,000 rows and a flash write every half minute. 2,016 rows and 288
writes a day instead.

**Two files, alternating, append-only.** Losing power mid-append costs one
short line, which the parser skips. A single file rewritten in place would
put every record at risk on every write.

**The app defaults to device mode, and asks nothing when served by the fan.**
The address is the one in the address bar and there is no account, so the
setup sheet would be a question with no content. That is the whole payoff of
the device serving its own app.

## What the host tests found

Two of these were found by tests written for this phase, before any of it
reached a board.

**The CORS check was fooled by `http://192.168.0.9.evil.example`.** It
started with `192.168.` and was a domain anyone could register. A "looks
private" test a registrar can satisfy is not a test. Now the host must parse
as an IPv4 literal.

**Closing a socket with unread bytes sends a RST, which discards the response
that was just written.** That hit exactly the two answers that most need to
be understood — 503 at the connection cap and 413 for an oversized body —
because those are the two that answer without reading the whole request. Both
arrived at the client as a dropped connection. Fixed with a lingering close:
shut the write side, drain, then let go.

A third, in the app rather than the firmware: the backfill asked for
everything newer than its newest row, but the first live sample was recorded
*before* it asked — so it requested the last ten minutes rather than the week
that was missing, and did it silently. The connect-time snapshot is now taken
before the first poll.

## What the hardware taught us

Four things, and three of them were wrong on the host.

**The history endpoint could not have worked.** Building a week of rows in
memory and serialising it to JSON costs about **890 bytes of heap per row**,
measured on the board — 2,016 rows would need roughly 1.8 MB against the
430 kB that exists. It raised `MemoryError` at 512 rows. On a laptop the same
code was instant and correct, which is precisely why it survived the host
suite. `/api/history` now streams the stored files straight off flash, one
line at a time, and `webapi.Rows` is that stream; peak cost measured at
108 kB of collectable garbage for a 102 kB response, with free memory never
below 308 kB. `store.read()` is kept for small windows and its `limit` is now
a real bound — it used to allocate everything and trim afterwards, so the
argument that existed to protect memory protected nothing.

**A browser does not retry a 503.** The server capped connections at three
and refused the rest, on the reasoning that refusing is more honest than
queueing. A browser opens six at once: three got their 503, Chrome reported
`net::ERR_ABORTED`, and the page rendered as a blank screen with no
stylesheet. Connections over the cap now wait in the listener's backlog,
which is what a backlog is for, and an idle keep-alive connection is closed
to make room so one visitor cannot hold the door. Verified in a browser
against the board: 36 requests, no failures.

**`mdns.py` was 200 lines that could never run, and deleting it fixed the
feature.** MicroPython on this port already has an mDNS responder — which is
why ours failed to bind port 5353 with `EADDRINUSE`. The reason `fan.local`
did not resolve was one line of ordering in `net.py`: the built-in responder
registers the hostname when the interface comes up, and we were setting it
just afterwards. Moved before `active(True)`, the same board answers a ping
in 1–3 ms. The module is gone; git has it if a build without lwIP's responder
ever turns up.

**Flash was never tight.** The budget in `build_www.py` was a guess of
1.4 MB. The board reports 2.5 MB free, and the bundle is 7 % of it. The tool
now asks the board with `--port` instead of trusting the constant.

One thing worth knowing for anyone debugging this: **`mpremote exec` reboots
the board.** Entering the raw REPL sends a soft reset, so `up_min` going back
to zero right after a USB command is the tooling, not a watchdog reset.

## What the first phone install found

Two more, and both were in the surface that only a real phone exercises.

**No `apple-touch-icon`, so iOS used a screenshot of the page as the icon.**
iOS does not read the web manifest's icons for the home screen and does not
accept an SVG, and the app shipped only `icon.svg`. `tools/make_icons.py`
now renders the PNGs from it — full-bleed for iOS, which applies its own
rounded mask, and pre-rounded for the manifest.

**Three requests left the network on every load.** Inter and JetBrains Mono
came from `fonts.googleapis.com`, in an app that says in its own settings
sheet that nothing leaves the network. Both are now on the fan: 78 kB for the
pair as variable latin subsets, OFL 1.1 with the license beside them.
`tests/test_device_app.py` now fails if anything at all is requested from
another origin, because the next CDN link will look just as harmless.

A third, found while checking the first two: the service worker was
cache-first for navigations, so a push to the fan's flash showed up on an
installed phone one launch late. Navigations go to the network first now,
with a 2.5 s timeout and the cache behind it, and `/api/*` bypasses the
worker entirely.

## What is still unproven

* **A phone on the actual bathroom wifi**, which is the only test that counts
  for a thing operated one-handed in a steamy room. It has now been installed
  to an iOS home screen from `http://fan.local/` and that worked, but the
  icon, the fonts and the update path were all fixed afterwards and have not
  been re-installed since.
* **Long-run behavior.** The store has written its first rows, but nothing
  here has watched a full seven-day window roll over and rotate.
* **Sustained load.** Eight simultaneous requests are served, and eight
  minutes of polling every ten seconds left `up_min` climbing smoothly from
  0.9 to 7.5 with no reset — but nobody has left a page open for a day with
  the fan cycling.
