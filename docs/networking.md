# Networking and Home Assistant

Optional and strictly additive. **Without `secrets.py` the controller runs
exactly as it does offline** — it prints `net: disabled` and carries on. An
extractor that stops working when the WiFi drops fails silently, in a room
nobody watches, and the first symptom is a mouldy ceiling.

## Three setups, in order of how much has to be running

| | What is needed | What you get |
|---|---|---|
| **Offline** | nothing but power | The fan. All of the control logic is on the board. |
| **Device only** | WiFi | The fan, plus the app at `http://fan.local/` and a week of history — served by the board itself. Leave `MQTT_HOST` empty. |
| **With a broker** | WiFi + MQTT | All of the above, plus Home Assistant, its recorder and its automations. |

The middle row is the one worth being explicit about, because it did not
used to exist. The app talked to an MQTT broker, and a broker in practice
means Home Assistant's Mosquitto add-on — so "local first" quietly meant
"as long as you run Home Assistant". Now the device serves the app off its
own flash and answers a small JSON API, and a user with none of that has a
working interface to their fan.

**Home Assistant did not lose anything.** With `MQTT_HOST` set, discovery,
the 31 entities, the recorder and the dashboard all work exactly as before.
It went from being the only way in to being one of two.

## The device's own server

```
GET  /                       the app, gzipped, off flash
GET  /api/state              the same JSON snapshot MQTT publishes
GET  /api/info               identity, build, link and history status
GET  /api/history?since=<s>  stored rows as CSV text, oldest first
POST /api/cmd                boost | cancel | relearn | clear | reset-settings
POST /api/set/<PARAM>        a tunable's new value
```

`/api/history` is CSV rather than JSON, and that is the board's constraint
rather than a taste: a week of rows parsed into memory and serialised costs
about **890 bytes of heap each**, measured — roughly 1.8 MB against the
430 kB that exists, and it raised `MemoryError` at 512 rows. The stored files
are already line-oriented text, so they are streamed straight off flash one
line at a time and the app does the parsing, where there is room for it.

The last row is the bucket still being filled, which is on no file yet.
Without it the newest row a client could get was the last bucket to *close* —
between one and two intervals behind the clock, and stamped with its bucket's
start — so the join between the stored series and the browser's own live
stream was always wider than the gap the charts draw as a break. Every client
opened on a hole of several minutes ending at the moment it connected, and
the data was never missing: it was in the accumulator, unasked for.

Served by `httpd.py`, which is stepped from the control loop for a few
milliseconds per pass, after the outputs are already driven. **Control is
never waiting on a browser**, and a wedged request cannot stall the fan —
if one ever did, the watchdog resets the board, which for a web server that
has hung a fan controller is the right outcome.

Writes go through `controller.on_command()` — the same function the MQTT
subscription calls, reached by synthesising the topic the command would have
arrived on. One command path with two doors into it, so the validation and
clamping cannot drift between them.

There is **no authentication**, deliberately. This is a LAN device whose
physical control is a button on the wall, and a password on a thing with no
screen buys little. What does exist: writes need POST, so no link or
prefetch can boost the fan; CORS is reflected only for local origins, so a
page on the internet cannot drive it; and `update` — the one command that
changes what the device runs — is refused over HTTP and stays on the MQTT
path, which has credentials. It is also refused while the button window is
open, because the window is what lets someone change the broker, and one
change that works closes the window. If that trade is wrong for you, set
`HTTP_ENABLED = False`.

Push the app to a board with:

```bash
python tools/build_www.py --push COM5     # builds, gzips, copies to flash
python tools/build_www.py --port COM5     # just ask the board what is free
```

172 kB on flash for the whole bundle, of which the MQTT client is 106 kB and
loads only if someone chooses broker mode. A board reports 2.5 MB free, so
that is about 7 %.

## `http://fan.local/`

There is no responder of ours behind that name. MicroPython on this port
already has one — which is why an mDNS module of our own could never bind
port 5353 — and the only thing that makes it work is the ordering in
`net.py`: `network.hostname()` has to be called **before** the interface
comes up, because the built-in responder registers the name as the netif
appears. Set it afterwards and nothing resolves, which is precisely the bug
this had. Measured on the bench: ping `fan.local`, 1–3 ms.

## A week of history, on the fan

Without Home Assistant there is no recorder, and the app's own buffer covers
only the time that browser has had it open — so a new phone opened on an
empty chart. `store.py` keeps **five-minute means for seven days** in two
alternating append-only files, and the app backfills from them on connect.
That is 2,016 rows and 288 small writes a day.

Home Assistant's recorder was the obvious alternative and is still not used
for this: it would add a second credential, a second service that has to be
up, and a dependency on the very thing the app is meant to work without.

```bash
cp secrets.example.py secrets.py     # then fill in, it is gitignored
mpremote connect COM5 fs cp secrets.py :secrets.py
mpremote connect COM5 exec "import machine; machine.reset()"
```

Use a **dedicated broker account per device** so one can be revoked without
touching the others. On the Mosquitto add-on:

```bash
mosquitto_passwd -b /share/mosquitto/accesscontrol pico-fan-bathroom <password>
```

## Dashboard

```bash
python tools/build_dashboard.py      # idempotent; safe to re-run
```

Eight sections, 52 cards, every one of the 31 entities used. Lovelace config
cannot be written over REST, so this goes through the WebSocket API. Re-running
it reports any entity the dashboard references that no longer exists, and any
entity that exists but is not shown.

Entities appear in Home Assistant automatically via MQTT discovery. State is
published every 30 s, and immediately on any change of state, fan, light or
fault — so a 20-second run is never invisible between two samples.

## Topics

```
fan/<room>/status                            online | offline  (LWT, retained)
fan/<room>/state                             JSON snapshot     (retained)
fan/<room>/cmd                               a command verb
fan/<room>/set/<PARAM>                       a tunable's new value
homeassistant/<kind>/<device_id>/<key>/config  discovery       (retained)
```

`ROOM`, `ROOM_NAME` and `DEVICE_ID` in `config.py` make this multi-room from
the start. Retrofitting that later means renaming every entity in Home
Assistant and losing its history.
