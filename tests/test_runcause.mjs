/* Why the fan was running, attributed per sample.
 *
 *     node tests/test_runcause.mjs
 *
 * This suite exists because of a specific shipped bug: the activity chart
 * reported "Clearing humidity" for every RUN, unconditionally. That was
 * true when humidity was the only sensed reason and silently became false
 * when odour arrived -- it once said it over a room 0.9% above baseline
 * while the fan ran on odour. The first check below is that exact case.
 *
 * Rows are built to the real recorded width so the index arithmetic is the
 * production arithmetic, not a convenient subset.
 */
import { register } from 'node:module';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { dirname, join } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const root = dirname(here);
const libRoot = pathToFileURL(join(root, 'app', 'src', 'lib') + '/').href;
register('./lib_alias_hook.mjs', import.meta.url, { data: { libRoot } });

const { F, GAP_MS } = await import('$lib/history.js');
const { CAUSES, burning, odourAt, runCause, runDuration } = await import(
  '$lib/runcause.js'
);

let pass = 0;
let fail = 0;
function check(name, cond, extra = '') {
  if (cond) {
    pass += 1;
  } else {
    fail += 1;
    console.log(`  ${name.padEnd(52)} FAIL ${extra}`);
    return;
  }
  console.log(`  ${name.padEnd(52)} PASS ${extra}`);
}

const CFG = { RH_FALL_OFF: 3, VOC_FALL_OFF: 70, VOC_NOX_VETO: 300 };
const WIDTH = F.noxExc + 1;

/* One sample. `st` 2 is RUN. Everything defaults to a quiet, dark, clean
   room so each test states only the thing it is about. */
function row({ t = 0, exc = 0, lit = 0, st = 2, vocExc = 0, noxExc = 0 } = {}) {
  const r = new Array(WIDTH).fill(null);
  r[F.t] = t;
  r[F.exc] = exc;
  r[F.lit] = lit;
  r[F.st] = st;
  r[F.vocExc] = vocExc;
  r[F.noxExc] = noxExc;
  return r;
}

// --- the bug this module was extracted for -----------------------------
check(
  'a run on odour is NOT reported as humidity',
  runCause(row({ exc: 0.9, vocExc: 400 }), CFG) === 'odour',
  runCause(row({ exc: 0.9, vocExc: 400 }), CFG)
);
check(
  'a run on humidity still is',
  runCause(row({ exc: 9 }), CFG) === 'humidity'
);

// --- priority, and that the parts do not overlap -----------------------
check(
  'a shower with the light on is a shower, not a lit room',
  runCause(row({ exc: 9, lit: 1 }), CFG) === 'humidity'
);
check(
  'the light only takes the credit when neither sensor would run',
  runCause(row({ lit: 1 }), CFG) === 'light'
);
check(
  'a run with no cause left is finishing, not humidity',
  runCause(row({}), CFG) === 'timer'
);

// --- the combustion veto ------------------------------------------------
// A big NOx RISE is a NEGATIVE excess, the same sign flip main.py makes.
check(
  'cooking is not attributed to odour',
  runCause(row({ vocExc: 400, noxExc: -1498 }), CFG) === 'timer',
  runCause(row({ vocExc: 400, noxExc: -1498 }), CFG)
);
check('...and burning() agrees', burning(row({ noxExc: -1498 }), 300) === true);
check(
  'a NOx FALL is not combustion',
  burning(row({ noxExc: 1498 }), 300) === false
);
check('no veto configured, no veto applied', burning(row({ noxExc: -1498 }), 0) === false);

// --- a threshold of 0 disables its term, it does not match everything ---
check(
  'odour off means no run is ever blamed on odour',
  runCause(row({ vocExc: 9999 }), { RH_FALL_OFF: 3, VOC_FALL_OFF: 0 }) === 'timer'
);

// --- not a run at all ---------------------------------------------------
check('IDLE has no cause', runCause(row({ st: 1, exc: 9 }), CFG) === null);
check('BOOST has no cause', runCause(row({ st: 3 }), CFG) === null);

// --- rows older than the odour columns ---------------------------------
const old = new Array(11).fill(null);
old[F.t] = 0;
old[F.exc] = 9;
old[F.st] = 2;
old[F.lit] = 0;
check('an 11-wide row still attributes humidity', runCause(old, CFG) === 'humidity');
check('...and reports no odour rather than "clear"', odourAt(old, CFG) === null);

// --- durations sum to the run ------------------------------------------
const M = 60_000;
const rows = [
  row({ t: 0, exc: 9 }),
  row({ t: 5 * M, exc: 9 }),
  row({ t: 10 * M, exc: 0.5, vocExc: 400 }),
  row({ t: 15 * M, exc: 0.5, vocExc: 400 }),
  row({ t: 20 * M, lit: 1 }),
  row({ t: 25 * M })
];
const each = CAUSES.map(([c]) => runDuration(rows, c, CFG));
const total = each.reduce((a, b) => a + b, 0);
check(
  'each cause gets its own minutes',
  each[0] === 10 * M && each[1] === 10 * M && each[2] === 5 * M,
  each.map((m) => m / M).join('/')
);
check('and they sum to the run, not more', total === 25 * M, `${total / M} min`);

// A gap in history must not be counted as runtime.
const gapped = [row({ t: 0, exc: 9 }), row({ t: GAP_MS * 3, exc: 9 })];
check('a gap in history is not runtime', runDuration(gapped, 'humidity', CFG) === 0);

// --- the tooltip text ---------------------------------------------------
check('clean air says so', odourAt(row({ vocExc: 10 }), CFG) === 'Air clear');
check('an event is named with its number', odourAt(row({ vocExc: 400 }), CFG) === 'Odour +400');
check(
  'cooking is named as ignored',
  odourAt(row({ vocExc: 400, noxExc: -1498 }), CFG) === 'Combustion, +400 ignored'
);

console.log(`\n${pass}/${pass + fail} passed\n`);
process.exit(fail ? 1 : 0);
