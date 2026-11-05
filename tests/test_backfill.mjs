/* Parsing the fan's stored week, when the fan and the app disagree.
 *
 *     node tests/test_backfill.mjs
 *
 * parseRows had no test at all, which is how the same bug survived three
 * times in the same expression: a `12` written as a literal while F had
 * fifteen keys, a strict equality that dropped every narrow row, and a
 * `> FIELD_COUNT` that dropped every wide one. Each discarded the entire
 * stored week, and each did it in silence.
 *
 * The wide case is not hypothetical and not rare. www/ only travels by
 * USB while firmware goes over the air, so a device a firmware ahead of
 * the browser bundle is this project's ORDINARY state -- and today's
 * device writes 18 columns.
 */
import { register } from 'node:module';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { dirname, join } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const root = dirname(here);
const libRoot = pathToFileURL(join(root, 'app', 'src', 'lib') + '/').href;

register('./lib_alias_hook.mjs', import.meta.url, { data: { libRoot } });

// localStorage must exist before history.js is imported: its module body
// calls load() at once, and a throwing accessor there would be swallowed
// into an empty buffer and quietly weaken every check below.
globalThis.localStorage = {
  _v: null,
  getItem() { return this._v; },
  setItem(k, v) { this._v = v; },
  removeItem() { this._v = null; }
};

const { parseRows, loadHistory, shouldTryBackfill, BACKFILL_TRIES,
        BACKFILL_RETRY_MS } = await import(
  pathToFileURL(join(root, 'app', 'src', 'lib', 'transport', 'backfill.js')).href
);
const { F } = await import(
  pathToFileURL(join(root, 'app', 'src', 'lib', 'history.js')).href
);

const WIDTH = Object.keys(F).length;
const results = [];
const check = (name, ok, detail = '') => {
  results.push(ok);
  console.log('  %s %s %s', name.padEnd(54), ok ? 'PASS' : 'FAIL', detail);
};

const HEAD = '#interval=300 fields=t,rh,base,exc,temp,dp,light,rpm,fan,lit,st';
/** One CSV line n columns wide, counting up from `from` so position shows. */
const line = (n, from = 1) =>
  Array.from({ length: n }, (_, i) => String(from + i)).join(',');

// The row this build was built for.
let got = parseRows(`${HEAD}\n${line(WIDTH)}`);
check('a row of exactly the expected width is kept',
  got.rows.length === 1 && got.rows[0].length === WIDTH,
  `${got.rows.length} rows`);
check('...with its values in their own columns',
  got.rows[0][0] === 1 && got.rows[0][WIDTH - 1] === WIDTH);

// Older firmware. Already worked; pinned so it keeps working.
got = parseRows(`${HEAD}\n${line(11)}`);
check('a narrower row from an older firmware is padded, not dropped',
  got.rows.length === 1 && got.rows[0][10] === 11 && got.rows[0][11] === null,
  `${got.rows.length} rows`);

// The bug. A device three columns ahead of this bundle, which is exactly
// what a USB-only app bundle plus over-the-air firmware produces.
const AHEAD = WIDTH + 3;
got = parseRows(`${HEAD}\n${line(AHEAD)}\n${line(AHEAD)}\n${line(AHEAD)}`);
check('rows from a NEWER firmware are kept, not silently discarded',
  got.rows.length === 3, `${got.rows.length} of 3`);
check('...truncated to the columns this build understands',
  got.rows[0].length === WIDTH && got.rows[0][WIDTH - 1] === WIDTH);
check('...and the known columns still read correctly',
  got.rows[0][0] === 1 && got.rows[0][5] === 6);
check('...with the skew counted, so an old bundle is visible',
  got.ahead === 3 && got.widest === AHEAD,
  `ahead=${got.ahead} widest=${got.widest}`);

// Genuinely unusable rows are still refused -- but counted now, not
// swallowed. A torn last line is the device losing power mid-append.
got = parseRows(`${HEAD}\n${line(WIDTH)}\n1,2,3`);
check('a torn short line is still refused',
  got.rows.length === 1 && got.short === 1,
  `rows=${got.rows.length} short=${got.short}`);

got = parseRows(`${HEAD}\n${line(WIDTH)}\n${Array(WIDTH).fill('x').join(',')}`);
check('a row of nonsense is still refused',
  got.rows.length === 1 && got.unparseable === 1,
  `rows=${got.rows.length} unparseable=${got.unparseable}`);

// Empty fields are absence, not zero -- a zero would draw as a reading.
got = parseRows(`${HEAD}\n1,,3${','.repeat(WIDTH - 3)}`);
check('an empty field is null, never 0',
  got.rows[0][1] === null && got.rows[0][2] === 3,
  `${got.rows[0].slice(0, 4)}`);

// The interval comes off the header, because the device may change it.
got = parseRows(`#interval=60 fields=t,rh\n${line(WIDTH)}`);
check('the interval is read from the header', got.interval === 60,
  `${got.interval}`);
got = parseRows(`${HEAD}\n${line(WIDTH)}`);
check('...and defaults to 300 when absent or unparseable',
  got.interval === 300, `${got.interval}`);

// Nothing at all is not an error.
got = parseRows('');
check('empty input yields no rows and no complaints',
  got.rows.length === 0 && got.short === 0 && got.unparseable === 0);

/* ------------------------------------------------- failure vs emptiness --
 *
 * loadHistory returned 0 for "fetched nothing" AND for "could not fetch",
 * and both callers read 0 as done. One dropped connection therefore cost
 * the whole stored week for the life of the session, silently. Seen on a
 * phone on 5 Nov: charts starting at the moment the app was opened with
 * seven days sitting on the fan's flash.
 */
console.log('');

const NL = String.fromCharCode(10);
// Inside the 7-day window, or backfill() drops them on age and the
// check reads 0 -- which is the correct answer to a different question.
const T0 = Math.floor(Date.now() / 1000) - 900;
const ROWS = [HEAD, line(WIDTH, T0), line(WIDTH, T0 + 300)].join(NL);

function withFetch(impl, fn) {
  const was = globalThis.fetch;
  globalThis.fetch = impl;
  return fn().finally(() => { globalThis.fetch = was; });
}

const ok = (body) => async () => ({ ok: true, text: async () => body });
const status = (code) => async () => ({ ok: false, status: code, text: async () => '' });
const boom = () => async () => { throw new Error('connection reset'); };

let r = await withFetch(ok(ROWS), () => loadHistory('', 0));
check('a good fetch reports how many rows it added', r > 0, `added ${r}`);

r = await withFetch(ok(HEAD), () => loadHistory('', 0));
check('a fetch that worked but had nothing new is 0, not null', r === 0, `${r}`);

r = await withFetch(status(500), () => loadHistory('', 0));
check('a 500 is null, so the caller knows to ask again', r === null, `${r}`);

r = await withFetch(status(404), () => loadHistory('', 0));
check('...and so is a 404', r === null, `${r}`);

r = await withFetch(boom(), () => loadHistory('', 0));
check('a dropped connection is null, not a silent 0', r === null, `${r}`);

/* ------------------------------------------------------- the retry gate --
 *
 * The original bug was a scheduling decision, not a fetch: `backfilled`
 * was set to true BEFORE awaiting, so the single attempt counted as done
 * whether or not it had worked. This is that decision, on its own.
 */
console.log('');

check('a fresh session tries immediately',
  shouldTryBackfill(false, 0, 1000, 0) === true);
check('a succeeded backfill is never retried',
  shouldTryBackfill(true, 0, 1000, 0) === false);
check('...not even once the window reopens',
  shouldTryBackfill(true, 1, 9e9, 0) === false);
check('a failure inside the window waits',
  shouldTryBackfill(false, 1, 1000, 5000) === false);
check('...and goes again once the window opens',
  shouldTryBackfill(false, 1, 5000, 5000) === true);
check('exactly at the window boundary is allowed',
  shouldTryBackfill(false, 1, 5000, 5000) === true);
check('giving up is bounded, so a deaf fan is not hammered',
  shouldTryBackfill(false, BACKFILL_TRIES, 9e9, 0) === false);
check('...and the last permitted try is the Nth',
  shouldTryBackfill(false, BACKFILL_TRIES - 1, 9e9, 0) === true);
check('the retry spacing is a minute, not a tight loop',
  BACKFILL_RETRY_MS >= 10_000, `${BACKFILL_RETRY_MS}ms`);
check('the whole giving-up window outlasts a wifi association',
  (BACKFILL_TRIES - 1) * BACKFILL_RETRY_MS >= 120_000,
  `${((BACKFILL_TRIES - 1) * BACKFILL_RETRY_MS) / 1000}s`);

const failed = results.filter((r) => !r).length;
console.log('\n%d/%d passed\n', results.length - failed, results.length);
process.exit(failed ? 1 : 0);
