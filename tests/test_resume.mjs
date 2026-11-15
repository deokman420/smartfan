/* History after a page comes back from the background.
 *
 *     node tests/test_resume.mjs
 *
 * A home-screen app on iOS is frozen in the background and resumed, not
 * reloaded, so connect() -- the only place the backfill used to be reset --
 * never runs again. The charts then showed "not enough data" and filled
 * from the moment of return, while a fresh tab showed the whole week.
 *
 * Drives device.js with a fake fetch and a fake clock: a long silence
 * followed by an answer must ask the fan for the gap again, a short one
 * must not, and a page becoming visible must poll at once.
 */
import { register } from 'node:module';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { dirname, join } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const root = dirname(here);
const libRoot = pathToFileURL(join(root, 'app', 'src', 'lib') + '/').href;
register('./lib_alias_hook.mjs', import.meta.url, { data: { libRoot } });

globalThis.localStorage = {
  _v: null,
  getItem() { return this._v; },
  setItem(k, v) { this._v = v; },
  removeItem() { this._v = null; }
};

// A clock the test moves. Date.now is what both device.js and history.js
// read, so this is the whole of "time passing".
let clock = Date.UTC(2026, 10, 15, 12, 0, 0);
Date.now = () => clock;

// No real interval: the test decides when a poll happens.
globalThis.setInterval = () => 1;
globalThis.clearInterval = () => {};

const listeners = {};
globalThis.document = {
  visibilityState: 'visible',
  addEventListener(type, fn) { (listeners[type] ||= new Set()).add(fn); },
  removeEventListener(type, fn) { listeners[type]?.delete(fn); }
};
globalThis.addEventListener = (type, fn) => (listeners['w:' + type] ||= new Set()).add(fn);
globalThis.removeEventListener = (type, fn) => listeners['w:' + type]?.delete(fn);
globalThis.location = { hostname: 'fan.local', port: '', protocol: 'http:' };

const asked = [];
globalThis.fetch = async (url) => {
  if (url.includes('/api/history')) {
    asked.push(Number(new URL(url, 'http://fan.local').searchParams.get('since')));
    return { ok: true, text: async () => '#interval=300\n' };
  }
  if (url.includes('/api/state')) {
    return { ok: true, json: async () => ({ rh: 50, state: 'IDLE' }) };
  }
  return { ok: true, json: async () => ({}) };
};

const { history, F } = await import(new URL('history.js', libRoot).href);
// svelte/store's get(), without importing svelte from outside app/.
const get = (st) => { let v; st.subscribe((x) => { v = x; })(); return v; };
const { create } = await import(new URL('transport/device.js', libRoot).href);

const store = () => ({ set() {} });
const sink = {
  link: store(), linkError: store(), authFailed: store(), telemetry: store(),
  deviceOnline: store(), lastSeen: store(),
  state: () => 'online',
  newestSample: () => {
    const rows = get(history);
    return rows.length ? rows[rows.length - 1][F.t] : 0;
  }
};

const results = [];
const check = (name, ok, detail = '') => {
  results.push(ok);
  console.log('  %s %s %s', name.padEnd(58), ok ? 'PASS' : 'FAIL', detail);
};
const settle = () => new Promise((r) => setTimeout(r, 20));

// history.js keeps its own visibilitychange listener (it flushes on hide),
// so the transport's are counted against this baseline, not against zero.
const baseVis = listeners.visibilitychange?.size || 0;
const basePs = listeners['w:pageshow']?.size || 0;

const dev = create(() => ({ host: 'fan.local', httpPort: 80 }), sink);
dev.connect();
await settle();
check('connect asks for the whole stored week', asked.length === 1 && asked[0] === 0,
  JSON.stringify(asked));

// Ordinary polling: 30 s later, nothing new to ask for.
clock += 30_000;
await dev.command('noop');
await settle();
check('a short gap does not re-ask', asked.length === 1, JSON.stringify(asked));

// The phone pocketed for an hour, then the page is shown again.
const before = sink.newestSample();
clock += 3600_000;
for (const fn of listeners.visibilitychange || []) fn();
await settle();
check('becoming visible polls at once and re-asks after a long gap',
  asked.length === 2, JSON.stringify(asked));
check('...from the newest row held before the gap, less the overlap',
  asked[1] === Math.floor(before / 1000) - 600, `${asked[1]} vs ${Math.floor(before / 1000) - 600}`);

// And straight after, back to normal.
clock += 2_000;
await dev.command('noop');
await settle();
check('the next poll after that does not re-ask', asked.length === 2, JSON.stringify(asked));

// Two minutes (a throttled hidden desktop tab) is under the threshold.
clock += 120_000;
await dev.command('noop');
await settle();
check('a two-minute gap (hidden desktop tab) does not re-ask', asked.length === 2,
  JSON.stringify(asked));

// pageshow from the back/forward cache.
clock += 600_000;
for (const fn of listeners['w:pageshow'] || []) fn({ persisted: true });
await settle();
check('a pageshow from the bfcache polls and re-asks', asked.length === 3, JSON.stringify(asked));

dev.disconnect();
check('disconnect removes both listeners',
  (listeners.visibilitychange?.size || 0) === baseVis &&
  (listeners['w:pageshow']?.size || 0) === basePs);

const failed = results.filter((ok) => !ok).length;
console.log(failed ? `\n${failed} FAILED` : `\nall ${results.length} passed`);
process.exit(failed ? 1 : 0);
