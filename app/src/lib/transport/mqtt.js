/**
 * The broker link: the page talks straight to Mosquitto over a WebSocket.
 *
 * This was the app's only transport, and it is now one of two. Nothing about
 * it has changed except that it is loaded on demand -- mqtt.js is 107 kB
 * gzipped, two thirds of the whole bundle, and a device-mode user never
 * touches a line of it. `import('mqtt')` keeps it in its own chunk, so it
 * costs nothing until someone chooses a broker.
 *
 * A browser has no API for opening a raw TCP socket, so port 1883, which the
 * device and Home Assistant use, is unreachable from a page no matter what is
 * configured. Hence 1884 and WebSockets. See broker-setup.html.
 *
 * The app is a client of the BROKER, not of the device. If the broker is down
 * this goes stale while the fan keeps running on its own logic. The app is
 * never in the control path, and nothing here should ever make it so.
 */
import { record } from '$lib/history.js';
import { loadHistory, canReachDevice } from './backfill.js';

function topics(room) {
  return {
    state: `fan/${room}/state`,
    status: `fan/${room}/status`,
    cmd: `fan/${room}/cmd`,
    set: `fan/${room}/set`
  };
}

export function create(getSettings, sink) {
  let client = null;
  let t = topics('bathroom');
  let backfilled = false;
  // The newest sample this browser held BEFORE connecting. Captured up
  // front because the first reading is recorded immediately, and asking
  // "what is newer than my newest row" a moment later asks for the last
  // half minute rather than for the week that is actually missing.
  let knewUpTo = 0;

  /**
   * Ask the fan for its stored week, once, using the address it just
   * reported.
   *
   * The broker carries readings, not records: the fan publishes the
   * current sample every 30 s and nothing retained goes further back. So
   * a broker-mode browser used to chart only what it had personally
   * watched -- a new phone opened empty, which is the complaint store.py
   * exists to answer. The rows are on the fan's flash either way; this
   * just fetches them over the one route that serves them.
   *
   * Deliberately not routed through the broker. Publishing a week of CSV
   * in MQTT chunks means buffering payloads on a device whose heap could
   * not hold 512 parsed rows, with an 8 s watchdog and a control loop
   * that must not stall, to serve a case plain HTTP already covers.
   *
   * Best effort, and never a banner. It needs the fan's own address and
   * an http page (see canReachDevice), so it is skipped rather than
   * attempted when either is missing -- the live stream is unaffected
   * and the charts fill from now on exactly as they did before.
   */
  function tryBackfill(payload) {
    if (backfilled) return;
    const ip = payload?.ip;
    if (!ip || !canReachDevice()) {
      backfilled = true;      // nothing will change on a later reading
      return;
    }
    backfilled = true;
    loadHistory(`http://${ip}`, knewUpTo);
  }

  function publish(topic, message) {
    if (!client || sink.state() !== 'online') return false;
    try {
      client.publish(topic, String(message), { qos: 0, retain: false });
      return true;
    } catch {
      return false;
    }
  }

  function teardown() {
    if (client) {
      try {
        client.end(true);
      } catch {
        /* already gone */
      }
      client = null;
    }
  }

  return {
    kind: 'broker',

    async connect() {
      teardown();
      const s = getSettings();
      t = topics(s.room);
      backfilled = false;
      knewUpTo = sink.newestSample();

      sink.link.set('connecting');
      sink.linkError.set('');
      sink.authFailed.set(false);

      let mqtt;
      try {
        ({ default: mqtt } = await import('mqtt'));
      } catch (err) {
        // The chunk is served from the same place as the page, so this means
        // the bundle is incomplete -- most likely a device-mode build that
        // was pushed without it. Say that rather than "connection failed".
        sink.link.set('error');
        sink.linkError.set('This build does not include the MQTT client.');
        return;
      }

      // A client id must be unique on the broker. Two clients sharing one
      // disconnect each other in a loop that looks exactly like a flaky
      // network, which is a genuinely miserable thing to debug.
      const clientId = `smartfan-app-${Math.random().toString(16).slice(2, 10)}`;

      client = mqtt.connect(`ws://${s.host}:${s.port}/`, {
        clientId,
        username: s.username || undefined,
        password: s.password || undefined,
        reconnectPeriod: 4000,
        connectTimeout: 8000,
        clean: true
      });

      client.on('connect', () => {
        sink.link.set('online');
        sink.linkError.set('');
        client.subscribe([t.state, t.status], { qos: 0 });
      });

      client.on('message', (topic, payload) => {
        const body = payload.toString();
        if (topic === t.status) {
          sink.deviceOnline.set(body === 'online');
          return;
        }
        if (topic !== t.state) return;
        try {
          const parsed = JSON.parse(body);
          sink.telemetry.set(parsed);
          sink.lastSeen.set(Date.now());
          // Charts are fed from the same stream that drives the readings, so
          // there is no second source that can disagree with the tiles.
          record(parsed);
          tryBackfill(parsed);
        } catch {
          /* a malformed payload is not a reason to tear the link down */
        }
      });

      client.on('error', (err) => {
        // Mosquitto accepts the socket and then rejects CONNECT on bad
        // credentials, so this arrives looking like a network fault. Say what
        // it probably is rather than echoing the raw message.
        const msg = String(err?.message || err);
        const auth = /not authorized|bad user|unauthor/i.test(msg);
        sink.linkError.set(auth ? 'The broker rejected these credentials.' : msg);
        sink.authFailed.set(auth);
        sink.link.set('error');

        // Stop retrying a credential the broker has already refused. The
        // default is to reconnect every few seconds forever, which hammers
        // the broker with a login that cannot start working on its own and
        // buries the real reason in a reconnect loop.
        if (auth) teardown();
      });

      client.on('close', () => {
        if (sink.state() !== 'error') sink.link.set('connecting');
      });

      client.on('offline', () => {
        if (sink.state() !== 'error') sink.link.set('connecting');
      });
    },

    disconnect() {
      teardown();
      sink.link.set('idle');
      sink.deviceOnline.set(false);
    },

    command(name) {
      return publish(t.cmd, name);
    },

    setParam(name, value) {
      return publish(`${t.set}/${String(name).toLowerCase()}`, value);
    }
  };
}
