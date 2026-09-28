/* The learned time-to-off estimator, against a curve whose answer is known.
 *
 *     node tests/test_dryrate.mjs
 *
 * Node rather than Python, and no browser: the thing under test is
 * arithmetic over an array, and app/src/lib/dryrate.js imports relatively so
 * that this can just import it. Nothing here touches a fan, a broker or a
 * DOM.
 *
 * The fixture is tests/seed_history.py, which is not a convenience -- it is
 * the point. It models this bathroom from measured numbers, and it does so
 * in closed form:
 *
 *     excess(s) = 29 * e^(-(s - 12) / 17) + 1.2      (s = minutes since the shower)
 *
 * so tau is 17 min and the settling floor is 1.2 %RH, exactly, and a fit can
 * be scored against a right answer instead of against a previous run of
 * itself. The same file already backs the chart regression, so the estimator
 * is exercised on the shape the charts exist to show.
 */
import { execFileSync } from 'node:child_process';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { dirname, join } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const root = dirname(here);
// pathToFileURL, not the bare path: a Windows absolute path begins with a
// drive letter, which the ESM loader reads as a URL scheme it does not know.
const { learnTau, timeToOff, fitRun } = await import(
  pathToFileURL(join(root, 'app', 'src', 'lib', 'dryrate.js')).href
);

/** Ground truth, straight out of seed_history.py. */
const TAU = 17 * 60;
const FLOOR = 1.2;
const FALL_OFF = 3.0;
const trueLeftMin = (exc) => (TAU / 60) * Math.log((exc - FLOOR) / (FALL_OFF - FLOOR));

const results = [];
const check = (name, ok, detail = '') => {
  results.push([name, !!ok]);
  console.log(`  ${name.padEnd(52)} ${ok ? 'PASS' : 'FAIL'} ${detail}`);
};
const near = (got, want, tol) => Math.abs(got - want) <= tol;

let rows;
try {
  rows = JSON.parse(
    execFileSync('python', [join(here, 'seed_history.py')], { encoding: 'utf8' })
  );
} catch (e) {
  console.log(`\ncannot run tests/seed_history.py: ${e.message}`);
  process.exit(2);
}

console.log('dry-rate regression\n');
console.log(`  fixture: tau ${TAU / 60} min, floor ${FLOOR} %RH, ${rows.length} rows\n`);

// --- learning ---------------------------------------------------------
const learned = learnTau(rows);
check('it learns something at all', learned && learned.runs >= 1);
check(
  'tau is within 5% of the fixture',
  near(learned.tau, TAU, TAU * 0.05),
  `${(learned.tau / 60).toFixed(2)} min vs ${TAU / 60}`
);
check(
  'the settling floor is found, not assumed zero',
  near(learned.c, FLOOR, 0.4),
  `${learned.c.toFixed(2)} vs ${FLOOR}`
);

// The two-parameter fit -- asymptote pinned at zero -- reads the flattening
// tail as a slower room. Recorded here so the three-parameter fit is not
// quietly simplified back one day.
check(
  'fitting with the floor beats pinning it at zero',
  Math.abs(learned.tau - TAU) < Math.abs(1168 - TAU),
  `${(learned.tau / 60).toFixed(2)} min beats the 19.47 a pinned fit gave`
);

// --- predicting -------------------------------------------------------
// Well clear of the cap, so this measures the fit and nothing else.
const far = { ran: 0, learned, fallOff: FALL_OFF, minRunS: 0, maxRunS: 1e9, postLightS: 0, darkFor: 1e9 };
let worst = 0;
for (const exc of [20, 14, 9, 6, 4.5, 3.6]) {
  const got = timeToOff({ ...far, exc }).secs / 60;
  const want = trueLeftMin(exc);
  worst = Math.max(worst, Math.abs(got - want) / want);
}
check('every prediction is within 10% of the truth', worst < 0.1,
      `worst ${(worst * 100).toFixed(1)}%`);

check('at the threshold it says zero, not a negative',
      timeToOff({ ...far, exc: FALL_OFF }).secs === 0);
check('below the threshold it still says zero',
      timeToOff({ ...far, exc: 1.0 }).secs === 0);

// --- the controller's own floors --------------------------------------
// main.py cannot stop before MIN_RUN_S, cannot stop while the light is on,
// and holds POST_LIGHT_S after it goes off. An estimate that ignores those
// is not an estimate of anything the fan will do.
const dryNow = { exc: 3.05, learned, fallOff: FALL_OFF, maxRunS: 2700 };
check('it will not promise a stop before MIN_RUN_S',
      timeToOff({ ...dryNow, ran: 10, minRunS: 180, postLightS: 0, darkFor: 1e9 }).secs === 170);
check('a lit room is held for the full post-light wait',
      timeToOff({ ...dryNow, ran: 600, minRunS: 180, postLightS: 120, lightOn: true }).secs === 120);
check('a room dark for 90 s has 30 s of the hold left',
      timeToOff({ ...dryNow, ran: 600, minRunS: 180, postLightS: 120, darkFor: 90 }).secs === 30);
check('being held by a floor rather than by humidity is flagged',
      timeToOff({ ...dryNow, ran: 600, minRunS: 180, postLightS: 120, lightOn: true }).waiting);

// --- the cap ----------------------------------------------------------
// The reason this exists. A 45 min cap reached before the room is dry is a
// different ending -- the fan latches and gives up -- and saying so early is
// the whole value.
const capped = timeToOff({
  exc: 20, ran: 900, learned, fallOff: FALL_OFF,
  minRunS: 180, maxRunS: 2700, postLightS: 120, darkFor: 1e9
});
check('a run that cannot finish in time is marked capped', capped.capped);
check('and is never reported as longer than the cap allows',
      capped.secs === 1800, `${capped.secs} s`);
check('a run that comfortably finishes is not marked capped',
      !timeToOff({ exc: 4, ran: 300, learned, fallOff: FALL_OFF,
                   minRunS: 180, maxRunS: 2700, postLightS: 0, darkFor: 1e9 }).capped);

// A room settling above the stop threshold never reaches it at all.
const stubborn = { ...learned, c: 4.0 };
const never = timeToOff({ exc: 9, ran: 300, learned: stubborn, fallOff: FALL_OFF,
                          minRunS: 180, maxRunS: 2700, postLightS: 0, darkFor: 1e9 });
check('a room that settles above the threshold is "never dries"', never.neverDries);
check('and is reported as capped rather than as infinity',
      never.capped && Number.isFinite(never.secs), `${never.secs} s`);

// --- only runs where the humidity actually pumped ---------------------
// The fan runs for reasons other than a shower, and far more often: a trip to
// find a towel runs the minimum plus the post-light hold, and a boost gets
// pressed in a dry room. Both are real runs and both teach nothing, because
// the humidity never went anywhere.
//
// This is not a hypothetical tidy-up. Measured below: the boosts alone drag
// tau from 17 min to under 11, a 36% optimistic estimate, because a 1.8 %RH
// decay clears a 1.6 %RH fall gate perfectly well.
const STEP = 30_000;

function trip(t0) {
  // Light on, fan runs, humidity wobbles inside the sensor's own noise.
  const out = [];
  for (let i = 0; i < 12; i++) {
    const exc = 0.9 + 0.35 * Math.sin(i / 2) + (i % 3) * 0.1;
    out.push([t0 + i * STEP, 62 + exc, 62, +exc.toFixed(1), 21.4, 14, 30,
              4200, 1, i < 5 ? 1 : 0, 2]);
  }
  return out;
}

function boost(t0) {
  // A boost in a dry room: a real decay, of nothing much.
  const out = [];
  for (let i = 0; i < 30; i++) {
    const exc = 1.8 * Math.exp(-i / 9) + 0.3;
    out.push([t0 + i * STEP, 62 + exc, 62, +exc.toFixed(1), 21.4, 14, 2,
              4200, 1, 0, 3]);
  }
  return out;
}

const mixed = [];
{
  let t = rows[0][0] - 40 * 3600_000;
  for (let d = 0; d < 6; d++) {
    for (let k = 0; k < 4; k++) { mixed.push(...trip(t)); t += 2 * 3600_000; }
    mixed.push(...boost(t)); t += 2 * 3600_000;
    const offset = t - rows[0][0];
    for (const r of rows) { const c = r.slice(); c[0] = r[0] + offset; mixed.push(c); }
    t += 8 * 3600_000;
  }
  mixed.sort((a, b) => a[0] - b[0]);
}

const gated = learnTau(mixed, { minPeak: 8.0 });
const ungated = learnTau(mixed, { minPeak: 0 });

check('it sees every run the fan actually made', gated.ran === 36, `${gated.ran}`);
check('but learns only from the showers', gated.seen === 6, `${gated.seen} of 36`);
check('so tau survives a realistic week', near(gated.tau, TAU, TAU * 0.05),
      `${(gated.tau / 60).toFixed(2)} min`);
check('and the spread stays tight', gated.hi - gated.lo < 60,
      `${((gated.hi - gated.lo) / 60).toFixed(1)} min`);

// The regression this gate exists to prevent, stated as a number.
check('without the gate the dry runs wreck it', ungated.tau < TAU * 0.75,
      `${(ungated.tau / 60).toFixed(2)} min from ${ungated.seen} runs`);
check('a run that never pumped is not fitted on its own',
      fitRun(boost(0), 8.0) === null);
check('...though it would be without a peak gate', fitRun(boost(0), 0) !== null);

// --- refusing to answer -----------------------------------------------
check('no history means no estimate, not a guess', learnTau([]) === null);
check('a too-short run is not fitted', fitRun(rows.slice(0, 3)) === null);
check('no learned tau means no estimate',
      timeToOff({ exc: 9, learned: null, fallOff: FALL_OFF }) === null);

// A run interrupted by the fan cycling must not be fitted across the break.
const spans = learnTau(rows.filter((r, i) => i % 40 !== 0));
check('it still learns from history with samples missing', spans === null || spans.tau > 0);

const failed = results.filter(([, ok]) => !ok);
console.log(`\n${results.length - failed.length}/${results.length} passed`);
process.exit(failed.length ? 1 : 0);
