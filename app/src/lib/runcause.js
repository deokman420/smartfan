/**
 * Why the fan was running, per recorded sample.
 *
 * This exists because the activity chart used to answer that question with
 * the string "Clearing humidity", unconditionally, for every RUN. That was
 * true when humidity was the only sensed reason and quietly became a lie
 * when it was not: it once reported it over a room 0.9% above baseline
 * while the fan was running on odor.
 *
 * It lives here, in a module a Node test can import, rather than inside
 * Activity.svelte where the wrong answer shipped and survived. Nothing in
 * here touches a DOM, a fan or a broker -- it is arithmetic over the rows
 * the device already records.
 */
import { F, GAP_MS } from './history.js';

/* Reported in this order, and each sample counted ONCE, so the parts sum to
 * the run rather than overlapping.
 *
 * Humidity first because it is the substantive reason -- a shower is why
 * this fan exists. Odor next. The light only takes the credit when neither
 * sensor would have run the fan on its own, because it is an occupancy
 * proxy and not a measurement of anything being wrong with the air; a
 * shower taken with the light on is a shower, not a lit room. "Finishing a
 * run" is MIN_RUN_S and POST_LIGHT_S -- the cause has gone and the fan has
 * not stopped yet, which is a real and common state that nothing used to
 * name.
 */
export const CAUSES = [
  ['humidity', 'Clearing humidity'],
  ['odor', 'Clearing odor'],
  ['light', 'Running with the light'],
  ['timer', 'Finishing a run']
];

const RUN = 2;

function num(r, i) {
  if (r.length <= i) return null;
  const v = r[i];
  return v === null || v === undefined ? null : v;
}

/**
 * Is the NOx pixel saying this smell is a flame rather than a bathroom?
 *
 * The firmware vetoes the odor path past VOC_NOX_VETO ticks of NOx RISE.
 * `excess()` is `base - raw` for both pixels and bad air moves the NOx raw
 * count UP, so a rise is a NEGATIVE excess -- the same sign flip that
 * _combustion() makes in main.py. Getting this backwards would paint
 * cooking as clean air and clean air as cooking.
 */
export function burning(row, veto) {
  if (!veto) return false;
  const n = num(row, F.noxExc);
  return n !== null && -n >= veto;
}

/**
 * One of CAUSES, or null when the sample is not a RUN at all.
 *
 * Thresholds that are 0 or missing disable their term rather than matching
 * everything: a device with odor switched off must never have a run
 * attributed to odor.
 */
export function runCause(row, cfg = {}) {
  if (num(row, F.st) !== RUN) return null;
  const rhStop = Number(cfg.RH_FALL_OFF) || 0;
  const vocStop = Number(cfg.VOC_FALL_OFF) || 0;
  const veto = Number(cfg.VOC_NOX_VETO) || 0;

  const rh = num(row, F.exc);
  if (rhStop && rh !== null && rh > rhStop) return 'humidity';

  const v = num(row, F.vocExc);
  if (vocStop && v !== null && v > vocStop && !burning(row, veto)) return 'odor';

  if (num(row, F.lit) === 1) return 'light';
  return 'timer';
}

/** Milliseconds of run attributed to one cause, gaps in history excluded. */
export function runDuration(rows, cause, cfg = {}) {
  let ms = 0;
  for (let i = 0; i < rows.length - 1; i++) {
    const dt = rows[i + 1][F.t] - rows[i][F.t];
    if (dt <= 0 || dt > GAP_MS) continue;
    if (runCause(rows[i], cfg) === cause) ms += dt;
  }
  return ms;
}

/**
 * The odor state of one sample, or null when the row predates the odor
 * columns -- so a caller leaves the line out rather than printing a
 * confident "Air clear" about a sensor that was not fitted.
 */
export function odorAt(row, cfg = {}) {
  const e = num(row, F.vocExc);
  if (e === null) return null;
  const vocStop = Number(cfg.VOC_FALL_OFF) || 0;
  const veto = Number(cfg.VOC_NOX_VETO) || 0;
  if (burning(row, veto)) return `Combustion, +${Math.round(e)} ignored`;
  if (vocStop && e > vocStop) return `Odor +${Math.round(e)}`;
  return 'Air clear';
}
