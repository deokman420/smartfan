/* The browser's sample buffer, and the seam where two sources meet.
 *
 *     node tests/test_history.mjs
 *
 * Node rather than a browser, for the same reason as test_dryrate.mjs: the
 * thing under test is arithmetic over an array. history.js touches
 * localStorage only inside try/catch and registers its listeners behind a
 * `document` check, so it imports cleanly with neither present.
 *
 * What this exists to catch is one bug with two halves, both of them at the
 * join between the device's stored five-minute means and the live stream
 * this browser records itself:
 *
 *   - the device only ever served buckets that had CLOSED, so the newest row
 *     a client could get was up to two intervals old;
 *   - and the buffer dropped any stored mean whose bucket already held a
 *     live sample, so the one row that would have covered the join was
 *     thrown away by the very reading that triggered the request for it.
 *
 * Together they put a hole of several minutes at the end of every session,
 * which is the end anyone opening the app is looking at.
 */
import { fileURLToPath, pathToFileURL } from 'node:url';
import { dirname, join } from 'node:path';
import { readFile } from 'node:fs/promises';

const here = dirname(fileURLToPath(import.meta.url));
const root = dirname(here);
// pathToFileURL, not the bare path: a Windows absolute path begins with a
// drive letter, which the ESM loader reads as a URL scheme it does not know.
const { history, backfill, record, clearHistory, segments, flush, F, GAP_MS } =
  await import(pathToFileURL(join(root, 'app', 'src', 'lib', 'history.js')).href);

const results = [];
const check = (name, ok, detail = '') => {
  results.push(ok);
  console.log('  %s %s %s', name.padEnd(52), ok ? 'PASS' : 'FAIL', detail);
};

const INTERVAL = 300;
const BUCKET = INTERVAL * 1000;
const MIN = 60_000;

/** A stored row as the device sends it: unix SECONDS, bucket-aligned. */
const stored = (ms, rh) => [ms / 1000, rh, 60, rh - 60, 21, 14, 5, 0, 0, 0, 1];

/** Fold a live reading in at a chosen moment, which record() reads off the
 *  clock -- so the clock is what gets moved.
 *
 *  The odd offset is not decoration. A real reading is stamped with
 *  Date.now() and never lands exactly on a five-minute boundary, which is
 *  what lets backfill tell its own earlier rows from live ones. Round
 *  numbers here would test a buffer no browser ever holds. */
const SKEW = 137;
let clock = 0;
const realNow = Date.now;
Date.now = () => clock;
const live = (ms, rh) => {
  clock = ms + SKEW;
  record({ rh, base: 60, exc: rh - 60, temp: 21, dp: 14, light_pc: 5, rpm: 0,
           fan_on: false, light_on: false, state: 'IDLE' });
};

let _rows = [];
history.subscribe((r) => { _rows = r; });
const read = () => _rows;
/** Rows that came from the device: only those sit on a bucket boundary. */
const fromDevice = () => read().filter((r) => r[F.t] % BUCKET === 0);

/** The largest step between consecutive rows, which is what the charts
 *  measure against GAP_MS to decide between a line and a break. */
const widestGap = (rows) => {
  let worst = 0;
  for (let i = 1; i < rows.length; i++) {
    worst = Math.max(worst, rows[i][F.t] - rows[i - 1][F.t]);
  }
  return worst;
};

console.log('\nthe seam between stored means and the live stream\n');

// The reported case, to the minute. A laptop had the app open and closed it
// seven minutes ago, mid-bucket; it is opened again now; the fan answers
// with its stored week plus the bucket it is still filling.
{
  clearHistory();
  const base = 1_790_000_000_000 - (1_790_000_000_000 % BUCKET);
  const open = base;                        // the bucket the device is in
  const now = open + 2 * MIN;               // opened two minutes into it

  // Yesterday's session: live samples every 30 s, stopping 7 minutes back.
  for (let t = open - 30 * MIN; t <= open - 7 * MIN; t += 30_000) {
    live(t, 60 + (t % 7) / 10);
  }
  const ended = read()[read().length - 1][F.t];

  // Reopened. The first live reading lands before the backfill answers,
  // which is the ordering both transports actually use.
  clock = now;
  live(now, 61);

  // The fan's reply: every closed bucket, then the open one.
  const rows = [];
  for (let t = open - 60 * MIN; t < open; t += BUCKET) rows.push(stored(t, 60.5));
  rows.push(stored(open, 61));              // History.partial()
  const added = backfill(rows, INTERVAL);

  check('the open bucket survives the reading that asked for it',
        read().some((r) => r[F.t] === open), `${added} rows added`);
  check('the bucket after the session ended is kept',
        read().some((r) => r[F.t] === open - 5 * MIN));
  // The session ran from 30 minutes back to 7, so it covers the five buckets
  // from -30 to -10 and nothing else. Those means are redundant and must
  // still be dropped; the six older ones, the half-empty -5 and the open one
  // are the eight that are not.
  check('buckets the session covered are still dropped',
        added === 8 && !fromDevice().some(
          (r) => r[F.t] >= open - 30 * MIN && r[F.t] <= open - 10 * MIN),
        `${added} rows added, ${fromDevice().length} from the device`);

  const seam = read().filter((r) => r[F.t] >= ended);
  check('nothing between the old session and the new is wider than a break',
        widestGap(seam) <= GAP_MS,
        `widest ${(widestGap(seam) / MIN).toFixed(1)} min, break at ${(GAP_MS / MIN).toFixed(1)}`);
  check('and so the charts draw it as one line',
        segments(seam, F.rh).length === 1,
        `${segments(seam, F.rh).length} segments`);
}

// The case the old rule existed to protect: a browser that was running the
// whole time must not get coarse means interleaved into its own readings.
{
  clearHistory();
  const base = 1_790_000_000_000 - (1_790_000_000_000 % BUCKET);
  for (let t = base - 60 * MIN; t <= base; t += 30_000) live(t, 62);
  const before = read().length;

  const rows = [];
  for (let t = base - 60 * MIN; t < base; t += BUCKET) rows.push(stored(t, 55));
  const added = backfill(rows, INTERVAL);

  check('means are not interleaved into dense live coverage',
        added === 0 && read().length === before, `${added} rows added`);
}

// A first run on a new phone: nothing held, so everything is taken.
{
  clearHistory();
  const base = 1_790_000_000_000 - (1_790_000_000_000 % BUCKET);
  clock = base + MIN;
  const rows = [];
  for (let t = base - 24 * 60 * MIN; t < base; t += BUCKET) rows.push(stored(t, 60));
  const added = backfill(rows, INTERVAL);
  check('an empty buffer takes the whole week', added === rows.length,
        `${added}/${rows.length}`);
  check('a complete stored series is drawn as one line',
        segments(read(), F.rh).length === 1,
        `${segments(read(), F.rh).length} segments`);
}

// One bucket the device never wrote still has to read as an outage, or the
// threshold has been widened into dishonesty.
{
  clearHistory();
  const base = 1_790_000_000_000 - (1_790_000_000_000 % BUCKET);
  clock = base + MIN;
  const rows = [];
  for (let t = base - 60 * MIN; t < base; t += BUCKET) {
    if (t === base - 30 * MIN) continue;    // the fan was off, or unsynced
    rows.push(stored(t, 60));
  }
  backfill(rows, INTERVAL);
  check('a bucket the device never wrote is still a break',
        segments(read(), F.rh).length === 2,
        `${segments(read(), F.rh).length} segments`);
}

// The open bucket is fetched again on every reconnect, a little more
// complete each time. The later copy has to win, or the first partial mean
// of a bucket is the one that stays for a week.
{
  clearHistory();
  const base = 1_790_000_000_000 - (1_790_000_000_000 % BUCKET);
  clock = base + MIN;
  backfill([stored(base, 61)], INTERVAL);
  clock = base + 4 * MIN;
  const added = backfill([stored(base, 67)], INTERVAL);
  const row = read().find((r) => r[F.t] === base);
  check('a re-fetched bucket replaces the partial one, not duplicates it',
        read().filter((r) => r[F.t] === base).length === 1 && row[F.rh] === 67,
        `${read().length} rows, rh ${row && row[F.rh]}, ${added} added`);
}

// The throttle in record() leaves up to a minute in memory only, and a page
// that is closed takes it with it.
{
  clearHistory();
  check('the buffer can be written out on demand',
        typeof flush === 'function' && flush() === undefined);
}

// The CSV parser, which is where a schema change does its damage. It lives
// in backfill.js, which imports history.js through SvelteKit's `$lib` alias
// -- meaningless to node -- so the source is rewritten to a real file URL
// and imported from memory. Cheaper than a bundler, and it tests the file
// that ships rather than a copy of it.
{
  const src = (await readFile(join(root, 'app', 'src', 'lib', 'transport',
                                   'backfill.js'), 'utf8'))
    .replace("'$lib/history.js'",
             JSON.stringify(pathToFileURL(join(root, 'app', 'src', 'lib',
                                               'history.js')).href));
  const { parseRows } = await import(
    'data:text/javascript;base64,' + Buffer.from(src).toString('base64'));

  const WIDE = '1790000000,60,58,2,21,14,40,0,0,1,2,30.5';
  const OLD = '1790000000,60,58,2,21,14,40,0,0,1,2';

  const wide = parseRows(WIDE).rows;
  check('a row carrying the ambient keeps it',
        wide.length === 1 && wide[0][F.amb] === 30.5,
        JSON.stringify(wide[0]?.[F.amb]));

  // The one that matters. Every row already on a device's flash is a column
  // short of what this app now expects, and the parser used to require an
  // exact width -- so adding a column silently threw the whole stored week
  // away. Short is old, not corrupt.
  const old = parseRows(OLD).rows;
  check('a row from before the column still parses',
        old.length === 1 && old[0].length === wide[0].length,
        '%s fields'.replace('%s', String(old[0]?.length)));
  check('...with the missing ambient read as absent, not zero',
        old.length === 1 && old[0][F.amb] === null,
        JSON.stringify(old[0]?.[F.amb]));
  check('...and the columns before it land where they did',
        old.length === 1 &&
        old[0][F.rh] === 60 && old[0][F.light] === 40 && old[0][F.st] === 2);

  // A genuinely malformed row is still refused. The tolerance is for rows a
  // firmware behind, not for anything that happens to split on commas.
  check('a row narrower than the oldest schema is refused',
        parseRows('1790000000,60,58,2,21,14,40,0,0,1').rows.length === 0);
  check('an empty ambient field reads as absent too',
        parseRows(OLD + ',').rows[0][F.amb] === null);
}

// segments() is what turns that null into a break rather than a line to
// zero, and it is the reason absent has to stay absent.
{
  const rows = [
    [1000, 60, 60, 0, 21, 14, 40, 0, 0, 0, 1, null],
    [2000, 60, 60, 0, 21, 14, 41, 0, 0, 0, 1, 30],
    [3000, 60, 60, 0, 21, 14, 42, 0, 0, 0, 1, 31]
  ];
  const segs = segments(rows, F.amb);
  check('rows without an ambient are skipped, not drawn as zero',
        segs.length === 1 && segs[0].length === 2 &&
        segs[0].every(([, v]) => v >= 30));
}

// A live reading carries the ambient into the buffer the same way.
{
  clearHistory();
  clock = 10 * MIN;
  record({ rh: 60, base: 58, exc: 2, temp: 21, dp: 14, light_pc: 40,
           light_amb_pc: 30.5, rpm: 0, fan_on: false, light_on: false,
           state: 'IDLE' });
  check('record() stores the ambient a live payload carries',
        read()[0]?.[F.amb] === 30.5, JSON.stringify(read()[0]?.[F.amb]));

  clearHistory();
  clock = 20 * MIN;
  record({ rh: 60, base: 58, exc: 2, temp: 21, dp: 14, light_pc: 40,
           rpm: 0, fan_on: false, light_on: false, state: 'IDLE' });
  check('...and a payload without one records absence',
        read()[0]?.[F.amb] === null, JSON.stringify(read()[0]?.[F.amb]));
}

Date.now = realNow;

// Holes, named. The shape of 2026-11-17: a power cut drops one stored bucket,
// and after the power-on the odor baseline stays null for a while.
{
  const { offline, learning, LEARNING } =
    await import(pathToFileURL(join(root, 'app', 'src', 'lib', 'history.js')).href);
  const B = 5 * MIN;
  const row = (t, voc, vocBase) => {
    const r = new Array(F.noxExc + 1).fill(1);
    r[F.t] = t;
    r[F.voc] = voc;
    r[F.vocBase] = vocBase;
    return r;
  };
  // Buckets 0..3 learned, bucket 4 missing (unplugged), 5..7 learning, 8.. learned.
  const rows = [0, 1, 2, 3, 5, 6, 7, 8, 9].map((i) =>
    row(i * B, 31000, i >= 5 && i <= 7 ? null : 31100));

  const offs = offline(rows);
  check('offline() finds the one missing bucket',
        offs.length === 1 && offs[0].start === 3 * B && offs[0].end === 5 * B,
        JSON.stringify(offs));
  check('...and nothing in a complete run',
        offline(rows.slice(0, 4)).length === 0);

  const learns = learning(rows, [F.voc, F.vocBase]);
  check('learning() finds the null-baseline stretch',
        learns.length === 1 && learns[0].reason === LEARNING[F.vocBase],
        JSON.stringify(learns));
  // Starts at the restart's first row (the row before it is across the
  // outage) and runs to the first row with a baseline again.
  check('...bounded by the outage and the first learned row',
        learns[0]?.start === 5 * B && learns[0]?.end === 8 * B,
        JSON.stringify(learns[0]));

  const absent = rows.map((r) => { const c = r.slice(); c[F.vocBase] = null; return c; });
  check('a column null in every row is an absent sensor, not learning',
        learning(absent, [F.vocBase]).length === 0);
  check('a field with no learning meaning is never labelled',
        learning(rows.map((r) => { const c = r.slice(); c[F.temp] = null; return c; }),
                 [F.temp]).length === 0);
}

const failed = results.filter((ok) => !ok).length;
console.log('\n%d/%d passed\n', results.length - failed, results.length);
process.exit(failed ? 1 : 0);
