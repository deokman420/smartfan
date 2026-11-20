/**
 * The link to the fan, whichever way it is reached.
 *
 * One store per thing that changes, which is the whole reason this is Svelte:
 * a reading maps onto a store and the UI follows it with no diffing in
 * between. The stores are the app's public surface and they do not know or
 * care which transport filled them.
 *
 * Two transports, chosen in Settings:
 *
 *   device   HTTP to the fan itself. Nothing else has to be running --
 *            no broker, no Home Assistant, no server. This is the default.
 *   broker   MQTT over a WebSocket, as before. Adds Home Assistant to the
 *            picture and lets one page watch a fan it was not served by.
 *
 * This file was broker.js, and the rename is the point: the app is a client
 * of the FAN, and a broker is one way of reaching it rather than the
 * definition of the thing. Nothing here is ever in the control path -- the
 * fan runs its own logic and every value on screen is the last one it
 * actually reported.
 */
import { writable, derived, get } from 'svelte/store';
import { history, F } from '$lib/history.js';
import { ledCode } from '$lib/statusled.js';

const SETTINGS_KEY = 'smartfan.settings';

/**
 * The public demo build (npm run build:demo). A compile-time constant, so in
 * the ordinary build the demo branch below is dead code and the demo
 * transport and its fixture never reach the fan's flash.
 */
// typeof guard: the node tests import this file directly, outside Vite,
// where import.meta.env does not exist.
export const DEMO =
  typeof import.meta.env !== 'undefined' && import.meta.env.VITE_DEMO === '1';

/** Where the page itself came from, which is the best guess we have. */
function servedByDevice() {
  if (typeof location === 'undefined') return false;
  if (location.protocol === 'file:') return false;
  // The dev server and a preview build are the two cases that are NOT a
  // device, and both announce themselves by port.
  const devPorts = ['5173', '4173'];
  if (devPorts.includes(location.port)) return false;
  return location.hostname !== 'localhost' && location.hostname !== '127.0.0.1';
}

export const DEFAULTS = {
  mode: 'device',
  // Device mode: the address the fan answers on. Pre-filled from wherever
  // this page was served, which for the ordinary case -- the bundle off the
  // fan's own flash -- means there is nothing at all to fill in.
  host: servedByDevice() ? location.hostname : 'fan.local',
  httpPort: servedByDevice() && location.port ? Number(location.port) : 80,
  // Broker mode.
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
      const loaded = { ...DEFAULTS, ...JSON.parse(raw) };
      const clean = persistable(loaded);
      // A copy saved before the rule above may still hold a password while
      // in device mode. Clear it from storage on the way in.
      if (clean.password !== loaded.password) {
        localStorage.setItem(SETTINGS_KEY, JSON.stringify(clean));
      }
      return clean;
    }
  } catch {
    /* fall through to defaults */
  }
  return { ...DEFAULTS };
}

export const settings = writable(loadSettings());

/**
 * Whether the app has enough to connect with.
 *
 * Deliberately NOT a subscription that writes on every change: an
 * auto-persisting store saves the defaults during module init, so the very
 * first load would look configured and a broker-mode user would be greeted
 * with a rejection instead of being asked for credentials.
 *
 * Served by the device, this is true on the first load and the setup sheet
 * never appears -- because there is genuinely nothing to ask. The address is
 * the one in the address bar, and there is no account. That is the whole
 * benefit of the device serving its own app, and asking anyway would throw
 * it away.
 */
export const configured = writable(DEMO || hadSaved || servedByDevice());

/**
 * What may be written to localStorage. The broker password is kept only
 * while the app is in broker mode, where it is needed on every launch; once
 * the app is back on the device, which has no account, it is dropped
 * instead of sitting in storage on a plain-http origin indefinitely
 * (audit NV-7). Switching back to broker mode asks for it again.
 */
export function persistable(next) {
  return next.mode === 'broker' ? next : { ...next, password: '' };
}

export function saveSettings(next) {
  next = persistable(next);
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

/** Last reading, or null before the first one arrives. */
export const telemetry = writable(null);

/**
 * Whether the fan itself is reporting.
 *
 * In broker mode this is the device's own retained availability, which is a
 * second hop that can fail on its own: the broker can be reachable while the
 * fan is not. In device mode there is only one hop, so this follows whether
 * the fan answered.
 */
export const deviceOnline = writable(false);

/** Wall-clock ms of the last reading, for the staleness indicator. */
export const lastSeen = writable(0);

export const stale = derived([lastSeen, link], ([$seen, $link], set) => {
  // The device publishes every 30 s over MQTT and answers every 2 s over
  // HTTP. Ninety is late enough that something is wrong either way, and
  // early enough to be worth saying so.
  const tick = () => set($link === 'online' && $seen > 0 && Date.now() - $seen > 90_000);
  tick();
  const id = setInterval(tick, 5000);
  return () => clearInterval(id);
}, false);

/**
 * Whether the fan is running AND we have current grounds to say so.
 *
 * `telemetry.fan_on` alone is the last thing the fan said, which the page can
 * get away with showing because the link pill sits right beside the header
 * mark and qualifies it. The favicon and the status-bar tint have no pill
 * next to them: a tab spinning away while the fan has been unreachable for
 * ten minutes is not a stale reading, it is a false one. So this is the
 * reading and the standing to report it, together.
 */
export const fanRunning = derived(
  [telemetry, deviceOnline, stale],
  ([$t, $online, $stale]) => !!$t?.fan_on && $online && !$stale
);

/**
 * The board's LED code for the tab icon, or null for nothing to say yet.
 * See lib/statusled.js for the vocabulary.
 */
export const statusLed = derived(
  [telemetry, link, deviceOnline, stale],
  ([$t, $link, $online, $stale]) =>
    ledCode({ t: $t, link: $link, online: $online, stale: $stale })
);

/** What a transport writes into. Deliberately small. */
const sink = {
  link,
  linkError,
  authFailed,
  telemetry,
  deviceOnline,
  lastSeen,
  state: () => get(link),
  /** Newest sample already held, so a backfill asks only for the gap. */
  newestSample: () => {
    const rows = get(history);
    return rows.length ? rows[rows.length - 1][F.t] : 0;
  }
};

let active = null;

function getSettings() {
  return get(settings);
}

async function build(mode) {
  if (DEMO) {
    const { create } = await import('$lib/transport/demo.js');
    return create(getSettings, sink);
  }
  if (mode === 'broker') {
    const { create } = await import('$lib/transport/mqtt.js');
    return create(getSettings, sink);
  }
  const { create } = await import('$lib/transport/device.js');
  return create(getSettings, sink);
}

export async function connect() {
  disconnect();
  const mode = getSettings().mode === 'broker' ? 'broker' : 'device';
  active = await build(mode);
  await active.connect();
}

export function disconnect() {
  if (active) {
    active.disconnect();
    active = null;
  }
  link.set('idle');
  deviceOnline.set(false);
}

/** Which transport is live, for the UI to describe itself honestly. */
export const mode = derived(settings, ($s) => ($s.mode === 'broker' ? 'broker' : 'device'));

/** boost | cancel | relearn | clear | reset-settings */
export function command(name) {
  return active ? active.command(name) : false;
}

/**
 * Write one tunable. The device validates against its own ranges and rejects
 * anything outside them, so this is a request rather than an assignment --
 * the value only counts once it comes back in a reading.
 */
export function setParam(name, value) {
  return active ? active.setParam(name, value) : false;
}

/**
 * The fan's own WiFi settings, or null when the transport has no route to
 * them. Only device mode does: a broker reached over the network the fan is
 * about to leave is not the channel to change that network on, and the
 * device door is gated on a button press anyway.
 */
export function wifiApi() {
  return active?.wifi ?? null;
}
