/* Where the app keeps the broker password.
 *
 *     node tests/test_linkstore.mjs
 *
 * Audit NV-7: the PWA saved the broker password in localStorage on the
 * plain-http fan.local origin and kept it after the user switched back to
 * device mode, which needs no account at all. Now the password is stored
 * only while the app is in broker mode, and a copy left by an older build
 * is cleared the next time the app starts.
 */
import { register } from 'node:module';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { dirname, join } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const root = dirname(here);
const libRoot = pathToFileURL(join(root, 'app', 'src', 'lib') + '/').href;
register('./lib_alias_hook.mjs', import.meta.url, { data: { libRoot } });

const KEY = 'smartfan.settings';
const store = new Map();
globalThis.localStorage = {
  getItem: (k) => (store.has(k) ? store.get(k) : null),
  setItem: (k, v) => store.set(k, String(v)),
  removeItem: (k) => store.delete(k)
};
globalThis.location = { hostname: 'fan.local', port: '', protocol: 'http:' };

let passed = 0, failed = 0;
function check(name, ok, detail = '') {
  ok ? passed++ : failed++;
  console.log(`  ${name.padEnd(58)} ${ok ? 'PASS' : 'FAIL'} ${detail}`);
}

console.log('\nthe broker password in localStorage\n');

// A copy saved by an older build: device mode, password still in it.
store.set(KEY, JSON.stringify({ mode: 'device', host: 'fan.local',
                                username: 'pwa', password: 'fixture-pass' }));

const { saveSettings, settings, persistable } = await import(
  pathToFileURL(join(root, 'app', 'src', 'lib', 'link.js')).href);
// svelte/store's get(), without resolving svelte from tests/.
const get = (s) => { let v; s.subscribe((x) => (v = x))(); return v; };

const stored = () => JSON.parse(store.get(KEY) || '{}');

check('a password left by an older build is cleared at startup',
  stored().password === '' && !store.get(KEY).includes('fixture-pass'),
  store.get(KEY));
check('...and is not in the live settings either', get(settings).password === '');
check('...while the rest of the saved settings survive',
  stored().username === 'pwa' && stored().host === 'fan.local');

saveSettings({ ...get(settings), mode: 'broker', host: '192.168.0.24',
               username: 'pwa', password: 'fixture-pass' });
check('broker mode keeps the password, as it must to reconnect',
  stored().password === 'fixture-pass' && get(settings).password === 'fixture-pass');

saveSettings({ ...get(settings), mode: 'device', host: 'fan.local' });
check('switching back to device mode drops it from storage',
  stored().password === '' && !store.get(KEY).includes('fixture-pass'),
  store.get(KEY));
check('...and from the live settings',
  get(settings).password === '');
check('...keeping the username, so broker mode only asks for the password',
  stored().username === 'pwa');

check('persistable leaves a broker-mode object untouched',
  persistable({ mode: 'broker', password: 'x' }).password === 'x');

const key = Object.keys(Object.fromEntries(store)).join(',');
check('the settings key the test watches is the one the app uses',
  key === KEY, key);

console.log(`\n${passed}/${passed + failed} passed\n`);
process.exit(failed ? 1 : 0);
