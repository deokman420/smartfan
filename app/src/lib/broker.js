/**
 * The broker link. One store per thing that changes, which is the whole
 * reason this is Svelte: a topic maps onto a store and the UI follows it
 * with no diffing in between.
 *
 * There is no server here. The page talks straight to Mosquitto over a
 * WebSocket, because a browser has no API for opening a raw TCP socket --
 * so port 1883, which the device and Home Assistant use, is unreachable
 * from a page no matter what is configured. See broker-setup.html.
 *
 * The app is a client of the BROKER, not of the device. If the broker is
 * down this goes stale while the fan keeps running on its own logic. The
 * app is never in the control path, and nothing here should ever make it
 * so.
 */
import mqtt from 'mqtt';
import { writable, derived, get } from 'svelte/store';

const SETTINGS_KEY = 'smartfan.settings';

export const DEFAULTS = {
  host: '192.168.0.5',
  port: 1884,
  room: 'bathroom',
  username: '',
  password: ''
};

let hadSaved = false;

function loadSettings() {
  // Any of this can throw or come back empty -- private windows, cleared
  // site data, a thumbnailer. Never let it stop the app booting.
  try {
    const raw = localStorage.getItem(SETTINGS_KEY);
    if (raw) {
      hadSaved = true;
      return { ...DEFAULTS, ...JSON.parse(raw) };
    }
  } catch {
    /* fall through to defaults */
  }
  return { ...DEFAULTS };
}

export const settings = writable(loadSettings());

/**
 * Whether anyone has ever saved connection settings on this browser.
 *
 * Deliberately NOT a subscription that writes on every change: an
 * auto-persisting store saves the defaults during module init, so the very
 * first load would look configured and the app would greet a new user with
 * a broker rejection instead of asking for credentials.
 */
export const configured = writable(hadSaved);

export function saveSettings(next) {
  settings.set(next);
  configured.set(true);
  try {
    localStorage.setItem(SETTINGS_KEY, JSON.stringify(next));
  } catch {
    /* nothing we can do, and nothing that should break the page */
  }
}

/** 'idle' | 'connecting' | 'online' | 'error' */
export const link = writable('idle');
export const linkError = writable('');

/** The error was an authentication refusal, not a network fault. */
export const authFailed = writable(false);

/** Last parsed telemetry payload, or null before the first one arrives. */
export const telemetry = writable(null);

/** The device's own retained availability, independent of our link. */
export const deviceOnline = writable(false);

/** Wall-clock ms of the last payload, for the staleness indicator. */
export const lastSeen = writable(0);

export const stale = derived([lastSeen, link], ([$seen, $link], set) => {
  // The device publishes every 30 s. Ninety is late enough that something
  // is wrong and early enough to be worth saying so.
  const tick = () => set($link === 'online' && $seen > 0 && Date.now() - $seen > 90_000);
  tick();
  const id = setInterval(tick, 5000);
  return () => clearInterval(id);
}, false);

let client = null;

function topics(room) {
  return {
    state: `fan/${room}/state`,
    status: `fan/${room}/status`,
    cmd: `fan/${room}/cmd`,
    set: `fan/${room}/set`
  };
}

export function connect() {
  disconnect();

  const s = get(settings);
  const t = topics(s.room);
  const url = `ws://${s.host}:${s.port}/`;

  link.set('connecting');
  linkError.set('');
  authFailed.set(false);

  // A client id must be unique on the broker. Two clients sharing one
  // disconnect each other in a loop that looks exactly like a flaky
  // network, which is a genuinely miserable thing to debug.
  const clientId = `smartfan-app-${Math.random().toString(16).slice(2, 10)}`;

  client = mqtt.connect(url, {
    clientId,
    username: s.username || undefined,
    password: s.password || undefined,
    reconnectPeriod: 4000,
    connectTimeout: 8000,
    clean: true
  });

  client.on('connect', () => {
    link.set('online');
    linkError.set('');
    client.subscribe([t.state, t.status], { qos: 0 });
  });

  client.on('message', (topic, payload) => {
    const body = payload.toString();
    if (topic === t.status) {
      deviceOnline.set(body === 'online');
      return;
    }
    if (topic !== t.state) return;
    try {
      telemetry.set(JSON.parse(body));
      lastSeen.set(Date.now());
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
    linkError.set(auth
      ? 'The broker rejected these credentials.'
      : msg);
    authFailed.set(auth);
    link.set('error');

    // Stop retrying a credential the broker has already refused. The
    // default is to reconnect every few seconds forever, which hammers the
    // broker with a login that cannot start working on its own and buries
    // the real reason in a reconnect loop.
    if (auth && client) {
      try {
        client.end(true);
      } catch {
        /* already gone */
      }
      client = null;
    }
  });

  client.on('close', () => {
    if (get(link) !== 'error') link.set('connecting');
  });

  client.on('offline', () => {
    if (get(link) !== 'error') link.set('connecting');
  });
}

export function disconnect() {
  if (client) {
    try {
      client.end(true);
    } catch {
      /* already gone */
    }
    client = null;
  }
  link.set('idle');
  deviceOnline.set(false);
}

function publish(topic, message) {
  if (!client || get(link) !== 'online') return false;
  try {
    client.publish(topic, String(message), { qos: 0, retain: false });
    return true;
  } catch {
    return false;
  }
}

/** boost | cancel | relearn | clear | reset-settings */
export function command(name) {
  return publish(topics(get(settings).room).cmd, name);
}

/**
 * Write one tunable. The device validates against its own ranges and
 * rejects anything outside them, so this is a request rather than an
 * assignment -- the value only counts once it comes back in telemetry.
 */
export function setParam(name, value) {
  const t = topics(get(settings).room);
  return publish(`${t.set}/${name.toLowerCase()}`, value);
}
