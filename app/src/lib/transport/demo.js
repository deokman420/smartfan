/**
 * A fan that lives in the browser, for the public demo at
 * smartfan.phbeks.com/demo/.
 *
 * Built only with `npm run build:demo` (VITE_DEMO=1). In the ordinary build
 * `import.meta.env.VITE_DEMO` is a constant false, so link.js never imports
 * this file and none of it -- including the 100 kB fixture -- reaches the
 * fan's flash.
 *
 * The starting point is real: a snapshot of a live fan's /api/state and its
 * stored history, with the one network-identifying field (ip) swapped for a
 * documentation address. The history is shifted so its newest row is "now".
 *
 * From there it is a small simulation, not a replay. Every two seconds the
 * readings drift a little, and every seven minutes somebody "takes a shower":
 * the light comes on, the fan starts, humidity climbs, the light goes off
 * and the fan runs the room dry again. Boost, cancel and the thresholds all
 * work, against this simulation and nothing else.
 */
import { record, backfill, clearHistory } from '$lib/history.js';
import { parseRows } from './backfill.js';
import snapshot from '$lib/demo/state.json';
import historyCsv from '$lib/demo/history.csv?raw';

const POLL_MS = 2000;
const RUN_RPM = 4155;
// One shower every CYCLE_S, starting SHOWER_AT_S into the cycle. Faster
// than life on purpose: a visitor should see the whole story in a few minutes.
const CYCLE_S = 420;
const SHOWER_AT_S = 10;
const LIGHT_ON_S = 150;

const round = (n, d = 1) => Math.round(n * 10 ** d) / 10 ** d;
const jitter = (n) => (Math.random() - 0.5) * n;

export function create(getSettings, sink) {
  let timer = null;
  let started = 0;
  const s = structuredClone(snapshot);
  s.cfg = { ...s.cfg };
  let boostUntil = 0;
  let runStart = 0;
  let runMin = 18;
  let lightRaw = s.light;

  function setState(name, why) {
    if (s.state !== name) {
      if (name === 'RUN' || name === 'BOOST') runStart = runStart || Date.now();
      else runStart = 0;
    }
    s.state = name;
    s.why = why;
  }

  function step() {
    const now = Date.now();
    const t = (now - started) / 1000;
    const inCycle = t % CYCLE_S;
    const lit = inCycle >= SHOWER_AT_S && inCycle < SHOWER_AT_S + LIGHT_ON_S;

    // Light: the room lamp against a little daylight.
    const target = lit ? 9800 : 2100;
    const prev = lightRaw;
    lightRaw += (target - lightRaw) * 0.6 + jitter(40);
    s.light = Math.round(lightRaw);
    s.light_pc = round((s.light * 100) / 65535);
    s.light_rise = Math.round(s.light - prev);
    s.light_on = lit;

    // Humidity: climbs while the shower runs, falls once the fan has the room.
    const showering = lit && inCycle > SHOWER_AT_S + 10;
    if (showering) s.rh = Math.min(s.base + 16, s.rh + 0.6 + jitter(0.15));
    else if (s.fan_on) s.rh = Math.max(s.base, s.rh - 0.25 + jitter(0.05));
    else s.rh = s.rh + (s.base - s.rh) * 0.02 + jitter(0.06);
    s.rh = round(s.rh);
    s.exc = round(s.rh - s.base);
    s.temp = round(21.4 + (showering ? 0.6 : 0) + jitter(0.08));
    s.dp = round(s.temp - (100 - s.rh) / 5);

    // Odor: a gentle wander around its baseline.
    s.voc = Math.round(s.voc_base - 20 + jitter(60));
    s.voc_exc = Math.round(s.voc_base - s.voc);
    s.nox = Math.round(15540 + jitter(40));

    // The control loop, in miniature. main.py is the real one.
    if (s.state === 'BOOST') {
      s.boost_left = Math.max(0, Math.round((boostUntil - now) / 1000));
      if (!s.boost_left) setState('IDLE', 'boost expired');
    } else if (s.state === 'IDLE') {
      if (lit && s.cfg.MODE === 'Auto') setState('RUN', 'light on');
      else if (s.exc >= s.cfg.RH_RISE_ON) setState('RUN', `humidity +${s.exc}%`);
    } else if (s.state === 'RUN') {
      const ranS = (now - runStart) / 1000;
      if (!lit && s.exc <= s.cfg.RH_FALL_OFF && ranS >= Math.min(s.cfg.MIN_RUN_S, 90)) {
        setState('IDLE', `dry, +${s.exc}%`);
      }
    }
    if (s.cfg.MODE === 'Force off' && s.state !== 'IDLE') setState('IDLE', 'forced off');
    if (s.cfg.MODE === 'Force on' && s.state !== 'RUN') setState('RUN', 'forced on');
    if (s.cfg.MODE === 'Auto' && s.why?.startsWith('forced')) setState('IDLE', 'override released');

    s.fan_on = s.state === 'RUN' || s.state === 'BOOST';
    s.rpm = s.fan_on ? Math.round(RUN_RPM + jitter(60)) : 0;
    if (s.fan_on) runMin += POLL_MS / 60000;
    s.run_min = round(runMin);
    s.up_min = round(s.up_min + POLL_MS / 60000);
    s.rssi = Math.round(-41 + jitter(4));

    const payload = structuredClone(s);
    sink.telemetry.set(payload);
    sink.lastSeen.set(now);
    record(payload);
  }

  function loadHistory() {
    // Start from the snapshot every visit, not from what an earlier visit's
    // simulation left in this browser's storage.
    clearHistory();
    const { rows, interval } = parseRows(historyCsv);
    if (!rows.length) return;
    // Slide the captured days forward so the newest row is the last bucket.
    const shift = Math.floor(Date.now() / 1000 / interval) * interval - rows[rows.length - 1][0];
    for (const r of rows) r[0] += shift;
    backfill(rows, interval);
  }

  return {
    kind: 'device',

    connect() {
      started = Date.now();
      sink.link.set('connecting');
      sink.linkError.set('');
      sink.authFailed.set(false);
      loadHistory();
      sink.link.set('online');
      sink.deviceOnline.set(true);
      step();
      timer = setInterval(step, POLL_MS);
    },

    disconnect() {
      if (timer) clearInterval(timer);
      timer = null;
      sink.link.set('idle');
      sink.deviceOnline.set(false);
    },

    command(name) {
      if (name === 'boost') {
        boostUntil = Date.now() + s.cfg.BOOST_S * 1000;
        setState('BOOST', 'boost via app');
      } else if (name === 'cancel' && s.state === 'BOOST') {
        boostUntil = 0;
        setState('IDLE', 'boost canceled');
      } else if (name === 'relearn') {
        s.base = s.rh;
        setState('IDLE', `baseline ${s.base}%`);
      } else if (name === 'reset-settings') {
        s.cfg = structuredClone(snapshot.cfg);
      }
      step();
      return true;
    },

    setParam(name, value) {
      const key = Object.keys(s.cfg).find((k) => k.toLowerCase() === String(name).toLowerCase());
      if (!key) return false;
      const n = Number(value);
      s.cfg[key] = typeof snapshot.cfg[key] === 'number' && Number.isFinite(n) ? n : String(value);
      step();
      return true;
    },

    /* Shown, never changed: the panel sits locked, as it does on a real fan
       until somebody holds its button. */
    wifi: {
      async status() {
        return { ssid: 'HomeNetwork', open: false, secs_left: 0, kind: 'wifi', state: 'ok' };
      },
      async scan() {
        return { ok: false, status: 403 };
      },
      async brokerStatus() {
        return { enabled: true, host: 'homeassistant.local', port: 1883, user: 'smartfan', state: 'online' };
      },
      async setBroker() {
        return { ok: false, settled: false, status: 403, error: 'Not in the demo.' };
      },
      async connect() {
        return { ok: false, status: 403, error: 'Not in the demo.' };
      }
    }
  };
}
