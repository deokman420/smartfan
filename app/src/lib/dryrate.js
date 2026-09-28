/**
 * How long until the fan stops, learned from this room.
 *
 * While the fan runs, humidity above baseline decays exponentially towards a
 * floor that is not zero:
 *
 *     excess(t) = A * e^(-t/tau) + c
 *
 * tau is a property of this room and this fan -- the volume, the duct, the
 * gap under the door -- not of anything a datasheet would tell you, and it
 * is already in the history: every past run is a measured decay curve. So
 * the time left is
 *
 *     t = tau * ln((excess_now - c) / (RH_FALL_OFF - c))
 *
 * plus whatever the controller's own floors add.
 *
 * That `c` is not pedantry, and it was measured rather than assumed. Fitting
 * the textbook two-parameter version -- a straight line through ln(excess),
 * asymptote pinned at zero -- against tests/seed_history.py, whose curve has
 * a known tau of 17 min and a known floor of 1.2 %RH, returned 19.5 min. The
 * curve flattens as it nears its floor, a straight line through the logs
 * reads that flattening as a slower room, and the error lands hardest
 * exactly where the estimate is being read most: the last few minutes.
 *
 * A room really does settle slightly above its own learned baseline, so the
 * floor is physical. Fitting it also buys the most useful prediction here
 * for free: if c is at or above RH_FALL_OFF, the room will NEVER reach the
 * stop threshold, the run will end on MAX_RUN_S instead, and the estimator
 * can say so half an hour in advance.
 *
 * Why this lives in the app and not in main.py
 * --------------------------------------------
 * The history is already here, the regression is free here, and the RP2350
 * has neither the heap nor the reason. More importantly the estimate must
 * never be able to affect the fan: the controller stops on a measured
 * reading crossing a threshold, and that is the behaviour worth having. A
 * prediction that is merely on screen can only ever be a wrong label. A
 * prediction that had a vote could stop a fan in a wet room.
 *
 * So nothing here is imported by anything that decides. It reads history and
 * returns a number of seconds to print.
 */
// Relative rather than $lib, unlike the rest of the app, and deliberately:
// it makes this file importable by plain `node`, which is what lets the
// arithmetic be tested against a known curve without a browser in the way.
// history.js guards its own localStorage read, so it imports cleanly there.
import { F, GAP_MS, STATE_CODE } from './history.js';

/** Below this the reading is noise, and ln() of noise is a fitted fantasy. */
const FLOOR_RH = 0.6;

/** A decay has to actually decay before it tells us anything about tau. */
const MIN_FALL = 1.6;

/**
 * How far above baseline a run must have PEAKED to teach us anything.
 *
 * The fan runs for reasons other than humidity. Someone turns the light on
 * to find a towel and the fan runs its minimum plus the post-light hold; a
 * boost gets pressed in a dry room. Those are genuine runs and genuinely
 * useless here: the humidity never went anywhere, so the "decay" fitted from
 * them is a line through sensor noise, and averaging it in drags tau towards
 * whatever the DHT22's jitter looks like that day.
 *
 * Only a run where the humidity actually pumped says anything about how fast
 * this room clears, and that is also the only kind of run where anyone is
 * looking at the estimate. So the gate is the controller's own RH_RISE_ON --
 * the rise that would have started the fan on humidity alone. Below that it
 * was not a shower, whatever else it was.
 *
 * Passed in from the device's settings rather than hardcoded, so retuning the
 * threshold retunes this with it. The constant is only the fallback for a
 * caller that does not know it yet.
 */
const MIN_PEAK = 8.0;

/** Fewer points than this is a line through nothing. */
const MIN_POINTS = 6;

/** A room with the door opened halfway through is not one exponential. */
const MIN_R2 = 0.8;

/** Runs older than this stopped describing the current season. */
const MAX_RUNS = 12;

/** Sanity rails on tau itself, in seconds. A bathroom is minutes, not days. */
const TAU_MIN = 120;
const TAU_MAX = 3 * 3600;

/**
 * Split history into the stretches where the fan was actually running.
 *
 * Breaks on a gap, because a straight line across six missing hours is not a
 * measurement. Uses F.fan -- what the fan was doing -- rather than F.st,
 * since a BOOST dries the room exactly as well as a RUN does and there is no
 * reason to throw that curve away.
 */
function runs(rows) {
  const out = [];
  let cur = null;
  for (let i = 0; i < rows.length; i++) {
    const r = rows[i];
    const broke = cur && r[F.t] - rows[i - 1][F.t] > GAP_MS;
    if (r[F.fan] === 1 && !broke) {
      (cur ??= []).push(r);
    } else {
      if (cur && cur.length) out.push(cur);
      cur = r[F.fan] === 1 ? [r] : null;
    }
  }
  if (cur && cur.length) out.push(cur);
  return out;
}

/**
 * Least squares of ln(y - c) against x, with the R^2 that says whether to
 * believe it. Returns null when the shift puts anything at or below zero.
 */
function line(pts, c) {
  let n = 0, sx = 0, sy = 0, sxx = 0, sxy = 0;
  const ys = [];
  for (const [x, y] of pts) {
    const v = y - c;
    if (!(v > 0)) return null;
    const ly = Math.log(v);
    ys.push([x, ly]);
    n++;
    sx += x;
    sy += ly;
    sxx += x * x;
    sxy += x * ly;
  }
  if (n < MIN_POINTS) return null;
  const denom = n * sxx - sx * sx;
  if (denom === 0) return null;
  const slope = (n * sxy - sx * sy) / denom;
  if (!(slope < 0)) return null; // humidity rose; not a decay
  const intercept = (sy - slope * sx) / n;

  const mean = sy / n;
  let ssRes = 0, ssTot = 0;
  for (const [x, ly] of ys) {
    ssRes += (ly - (slope * x + intercept)) ** 2;
    ssTot += (ly - mean) ** 2;
  }
  if (ssTot === 0) return null;
  return { slope, r2: 1 - ssRes / ssTot, n };
}

/**
 * Fit one run, returning tau in seconds, or null if it will not say.
 *
 * Fitting starts at the run's PEAK excess, not at its first sample. The fan
 * comes on while the shower is still running, so the first minutes of nearly
 * every run are humidity going UP with the fan already spinning. Fitting
 * through that produced a tau that was wrong in the worst direction -- it
 * flattened the curve and made every estimate optimistic.
 */
export function fitRun(run, minPeak = MIN_PEAK) {
  let peak = 0;
  for (let i = 1; i < run.length; i++) {
    if (run[i][F.exc] > run[peak][F.exc]) peak = i;
  }
  // Not a shower. See MIN_PEAK: this is the whole difference between
  // learning from four real events and averaging in forty trips to the
  // bathroom that never raised the humidity at all.
  if (!(run[peak][F.exc] >= minPeak)) return null;

  const decay = run.slice(peak);
  if (decay.length < MIN_POINTS) return null;

  const first = decay[0][F.exc];
  const last = decay[decay.length - 1][F.exc];
  if (!(first > FLOOR_RH) || !(last > 0) || first - last < MIN_FALL) return null;

  // Samples as (seconds since the peak, excess).
  const pts = [];
  for (const r of decay) {
    const y = r[F.exc];
    if (!(y > FLOOR_RH)) break; // once it is in the noise, stop believing it
    pts.push([(r[F.t] - decay[0][F.t]) / 1000, y]);
  }
  if (pts.length < MIN_POINTS) return null;

  // Grid-search the floor, least-squares the rest. Fitting all three of A,
  // tau and c at once wants a nonlinear solver; fixing c makes it an
  // ordinary line through ln(y - c), so a sweep over c and the best R^2
  // wins. Twenty-five candidates is nothing to compute and lands well
  // inside the noise of the reading itself.
  //
  // c is capped below the smallest excess in the window, since ln of a
  // negative is not a fit, and at RH_FALL_OFF's own scale above: a floor
  // higher than that means "never dries", which is a real answer here and
  // handled in timeToOff rather than by refusing to fit.
  const lowest = pts[pts.length - 1][1];
  let best = null;
  for (let i = 0; i <= 24; i++) {
    const c = (i / 25) * lowest;
    const fit = line(pts, c);
    if (fit && (!best || fit.r2 > best.r2)) best = { ...fit, c };
  }
  if (!best) return null;

  const { slope, r2, c, n } = best;
  if (r2 < MIN_R2) return null;

  const tau = -1 / slope;
  if (!(tau >= TAU_MIN && tau <= TAU_MAX)) return null;

  return { tau, c, r2, points: n, from: decay[0][F.t], fell: first - last };
}

function quantile(sorted, q) {
  if (!sorted.length) return null;
  const at = (sorted.length - 1) * q;
  const lo = Math.floor(at);
  const hi = Math.ceil(at);
  return lo === hi ? sorted[lo] : sorted[lo] + (sorted[hi] - sorted[lo]) * (hi - lo === 0 ? 0 : at - lo);
}

/**
 * What this room's drying time constant looks like, over recent runs.
 *
 * The MEDIAN, not the mean. One run with the door left open is a genuine
 * measurement of a different room, and a mean lets it drag every estimate
 * afterwards. The quartiles come back too, because the honest output here is
 * a range: tau varies with the door, the season and what the weather is
 * doing outside, and a single number would imply a precision this does not
 * have.
 */
export function learnTau(rows, { minPeak = MIN_PEAK } = {}) {
  if (!Array.isArray(rows) || rows.length < MIN_POINTS) return null;

  const all = runs(rows);
  const fits = [];
  for (const run of all) {
    const fit = fitRun(run, minPeak);
    if (fit) fits.push(fit);
  }
  if (!fits.length) return null;

  const recent = fits.slice(-MAX_RUNS);
  const taus = recent.map((f) => f.tau).sort((a, b) => a - b);
  const floors = recent.map((f) => f.c).sort((a, b) => a - b);
  return {
    tau: quantile(taus, 0.5),
    lo: quantile(taus, 0.25),
    hi: quantile(taus, 0.75),
    // The floor is a median too, and for the same reason: one run that
    // ended early sits well above the room's real settling point.
    c: quantile(floors, 0.5),
    runs: recent.length,
    seen: fits.length,
    // Every stretch the fan ran, including the ones that taught us nothing.
    // The gap between this and `seen` is the point of MIN_PEAK, and it is
    // worth being able to look at rather than inferring.
    ran: all.length
  };
}

/**
 * Seconds until the controller would stop the fan, or null if unknowable.
 *
 * This mirrors the stop conditions in main.py's RUN branch rather than
 * guessing at them, because an estimate that ignores MIN_RUN_S or the
 * post-light hold is not an estimate of anything the fan will do:
 *
 *     ran >= MAX_RUN_S            -> stops, latched
 *     ran <  MIN_RUN_S            -> cannot stop yet
 *     light_on                    -> cannot stop
 *     dark for < POST_LIGHT_S     -> cannot stop
 *     excess > RH_FALL_OFF        -> cannot stop
 *
 * `capped` is the interesting one and the reason this is worth building: it
 * says the 45-minute cap will arrive before the room is dry, which is a
 * different event with a different meaning -- the fan gives up and latches
 * rather than finishing. Knowing that twenty minutes ahead is the whole
 * point.
 */
export function timeToOff({
  exc,
  ran = 0,
  lightOn = false,
  darkFor = null,
  learned,
  fallOff,
  minRunS = 0,
  maxRunS = Infinity,
  postLightS = 0
}) {
  if (!learned?.tau || !Number.isFinite(exc) || !Number.isFinite(fallOff)) return null;

  // The room settles at `c`. If that is at or above the threshold the
  // controller is waiting for, the humidity never gets there and the run
  // ends on MAX_RUN_S -- which is a different ending, and one worth naming
  // long before it arrives.
  const c = Math.min(learned.c ?? 0, exc);
  const reaches = fallOff > c;
  const dry = (tau) => {
    if (exc <= fallOff) return 0;
    if (!reaches) return Infinity;
    return tau * Math.log((exc - c) / (fallOff - c));
  };

  // The light is the one input that cannot be predicted: nobody knows when
  // somebody leaves. Measure from now and say so, rather than pretending.
  const holdFor = lightOn ? postLightS : Math.max(0, postLightS - (darkFor ?? postLightS));

  const floors = Math.max(minRunS - ran, holdFor, 0);
  const secs = Math.max(dry(learned.tau), floors);
  const left = maxRunS - ran;
  // What the room actually needs, before the cap truncates it. `secs` is
  // what will happen; this is why, and the gap between them is the story
  // when a run ends on the clock instead of on the humidity.
  const needs = secs;

  return {
    secs: Math.min(secs, Math.max(left, 0)),
    lo: Math.max(Math.min(dry(learned.lo), secs), floors),
    hi: Math.min(Math.max(dry(learned.hi), floors), Math.max(left, 0)),
    // The room's own settling point is above the stop threshold: this run
    // cannot end by drying, only by running out of clock.
    neverDries: !reaches,
    // Held open by something other than the humidity: worth saying, because
    // "3 min" that is really "3 min after you switch the light off" is a
    // worse answer than no answer.
    waiting: floors > dry(learned.tau),
    lightOn,
    capped: secs >= left,
    needs,
    capIn: Math.max(left, 0),
    runs: learned.runs
  };
}

/**
 * How far into the current run we are, and how long the room has been dark.
 *
 * Both read from the tail of history rather than from the payload, because
 * the payload does not carry either: `run_min` is the running total for the
 * day, not the age of this run. Deriving them here keeps the estimator a
 * pure app-side addition -- no new field, no firmware change, nothing new
 * for the device to compute or publish.
 *
 * The tail of history is the live stream, arriving every 2 s in device mode,
 * so the resolution is far finer than a minutes-scale estimate needs.
 */
export function runContext(rows) {
  if (!Array.isArray(rows) || !rows.length) return { ran: 0, darkFor: null };
  const end = rows[rows.length - 1];
  const now = end[F.t];

  let ran = 0;
  let darkFor = null;
  for (let i = rows.length - 1; i >= 0; i--) {
    const r = rows[i];
    if (i < rows.length - 1 && rows[i + 1][F.t] - r[F.t] > GAP_MS) break;
    if (r[F.fan] === 1) ran = (now - r[F.t]) / 1000;
    else break;
  }
  for (let i = rows.length - 1; i >= 0; i--) {
    const r = rows[i];
    if (i < rows.length - 1 && rows[i + 1][F.t] - r[F.t] > GAP_MS) break;
    if (r[F.lit] === 0) darkFor = (now - r[F.t]) / 1000;
    else break;
  }
  return { ran, darkFor };
}

export { STATE_CODE };
