/**
 * A rolling week of samples, kept in the browser and topped up by the fan.
 *
 * Two sources, one buffer. Live readings arrive every 30 s (broker) or 2 s
 * (device) and are recorded as they come. On top of that, a device-mode
 * connection asks the fan for its own stored history -- five-minute means
 * kept on flash for a week -- and backfills whatever this browser missed.
 *
 * That backfill is what closed the one honest complaint about the old
 * design: history used to cover only the time this browser had personally
 * had the app open, so a new phone opened on an empty chart and clearing
 * site data threw the week away. Home Assistant's recorder was the obvious
 * alternative and is still rejected for the same reason as before -- it
 * would add a second credential, a second service to be up, and a
 * dependency on the very thing the app is meant to work without. The fan
 * keeping its own record adds none of those.
 *
 * Resolutions are mixed on purpose, and the charts already handle it: gaps
 * longer than GAP_MS are drawn as breaks rather than as a confident straight
 * line, which is exactly the right treatment for the seam between a coarse
 * backfill and a fine live stream.
 *
 * Samples are stored as plain arrays, not objects. 2880 objects with ten
 * named keys each is roughly six times the JSON, and this goes through
 * localStorage on every write.
 */
import { writable, get } from 'svelte/store';

const KEY = 'smartfan.history';
// A week of five-minute means is 2,016 rows and a day of 30 s samples is
// 2,880. 6,000 holds both with room to spare. The cost is the serialise on
// persist, which is why that is throttled to once a minute.
const MAX = 6000;
const WINDOW_H = 7 * 24;
const MIN_GAP_MS = 15_000; // the device also publishes on change

/**
 * Longer than this is a break, not a line.
 *
 * It has to sit between one stored interval and two. Consecutive stored rows
 * are exactly one interval apart, so anything at or below that must join up
 * or a perfectly complete week draws as 2,016 separate dots; a single bucket
 * the device never wrote leaves a two-interval step, and that must break or
 * the chart invents a straight line across an outage.
 *
 * It used to be exactly one interval, which is the lower bound rather than a
 * value inside the range -- and the seam where the device's stored rows meet
 * this browser's live stream lands a little past it, because a stored row
 * carries its bucket's start while the live sample that follows arrives
 * somewhere inside that bucket. So every connect drew a break it had no
 * business drawing. Half an interval of margin is enough for that and still
 * nowhere near two.
 */
export const GAP_MS = 5.5 * 60_000;

// Index into a stored sample. Append only -- changing the order silently
// reinterprets every sample already in a user's browser.
export const F = {
  t: 0,
  rh: 1,
  base: 2,
  exc: 3,
  temp: 4,
  dp: 5,
  light: 6,
  rpm: 7,
  fan: 8,
  lit: 9,
  st: 10, // controller state, appended so older samples stay valid
  amb: 11, // learned light ambient, appended for the same reason
  voc: 12, // odor raw ticks, appended for the same reason again
  vocBase: 13, // ...its learned baseline
  vocExc: 14, // ...and the deviation the control loop acts on
  // The sensor's second pixel, appended for the same reason once more. The
  // VOC pixel sees reducing gases and the NOx pixel sees oxidizing ones, so
  // the two together separate bathroom odor from a gas flame in the next
  // room -- a distinction a single channel cannot draw, and the reason this
  // is recorded well before anything acts on it.
  nox: 15,
  noxBase: 16,
  noxExc: 17
};

/** Stored in F.st. Older samples simply omit it. */
export const STATE_CODE = { LEARN: 0, IDLE: 1, RUN: 2, BOOST: 3, FAULT: 4 };
export const STATE_NAME = ['LEARN', 'IDLE', 'RUN', 'BOOST', 'FAULT'];

function load() {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return [];
    const rows = JSON.parse(raw);
    if (!Array.isArray(rows)) return [];
    // Drop anything older than the window, so a browser left closed for a
    // month does not open on a chart of last month.
    const cutoff = Date.now() - WINDOW_H * 3600_000;
    return rows.filter((r) => Array.isArray(r) && r[F.t] > cutoff);
  } catch {
    return [];
  }
}

export const history = writable(load());

let lastWrite = 0;

function persist(rows) {
  try {
    localStorage.setItem(KEY, JSON.stringify(rows));
  } catch {
    // Quota, private window, blocked site data. The charts still work for
    // this session; only the carry-over across reloads is lost.
  }
}

/** Fold one telemetry payload into the buffer. */
export function record(p) {
  if (!p || p.rh === null || p.rh === undefined) return;
  const now = Date.now();
  const rows = get(history);
  const last = rows[rows.length - 1];
  if (last && now - last[F.t] < MIN_GAP_MS) return;

  const row = [
    now,
    num(p.rh),
    num(p.base),
    num(p.exc),
    num(p.temp),
    num(p.dp),
    num(p.light_pc),
    num(p.rpm),
    p.fan_on ? 1 : 0,
    p.light_on ? 1 : 0,
    STATE_CODE[p.state] ?? null,
    num(p.light_amb_pc),
    // null, not 0, when the sensor is absent or still conditioning. A
    // MOX reading of zero would draw as the worst air ever recorded,
    // and the chart has to show a gap where there was no sensor.
    num(p.voc),
    num(p.voc_base),
    num(p.voc_exc),
    num(p.nox),
    num(p.nox_base),
    num(p.nox_exc)
  ];

  const next = rows.length >= MAX ? rows.slice(rows.length - MAX + 1) : rows.slice();
  next.push(row);
  history.set(next);

  // Persist at most once a minute. Serialising 2880 rows on every sample
  // is wasted work on a page that may be open all day.
  if (now - lastWrite > 60_000) {
    lastWrite = now;
    persist(next);
  }
}

/**
 * Write the buffer out now, whatever the throttle says.
 *
 * The throttle means up to a minute of samples exists only in memory, and a
 * page that is closed loses them -- a gap right at the end of every session,
 * which is the end a user is looking at when they next open the app. The
 * device has those minutes on flash, but the backfill cannot be relied on to
 * carry them: a stored row is a five-minute mean and the hole is finer than
 * that. Cheaper to not lose them.
 */
export function flush() {
  const rows = get(history);
  if (!rows.length) return;
  lastWrite = Date.now();
  persist(rows);
}

// `pagehide` and a hidden `visibilitychange`, which is the pair that actually
// fires. `beforeunload` is not dispatched when a phone discards a background
// tab -- the single most likely way this app stops running -- and `unload` is
// worse. Both of these can fire for a page that comes back, and a redundant
// write of a few hundred kB once in a while is the cheaper mistake.
if (typeof document !== 'undefined') {
  addEventListener('pagehide', flush);
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'hidden') flush();
  });
}

/**
 * Fold the fan's own stored rows in, keeping whatever we already have.
 *
 * The device sends `[unix_seconds, ...]` in the same field order as F, which
 * is not an accident: store.py documents that the order is append-only for
 * exactly this reason. Timestamps are converted to ms here, at the one place
 * that knows both conventions.
 *
 * Live samples win over stored ones where the two genuinely describe the same
 * moment. A backfilled row is a five-minute mean; a live row is a real
 * reading, and where both exist the reading is the truer answer.
 *
 * "The same moment" used to mean "the same bucket", and that was too coarse
 * in the one place it mattered. A stored row is a single point stamped at its
 * bucket's start, so one live sample anywhere in a bucket claimed the whole
 * five minutes of it -- which is exactly what a session that ended mid-bucket
 * leaves behind. The mean covering the rest of that bucket was then dropped
 * as a duplicate of a reading taken minutes earlier, and the tail of the
 * bucket simply went missing. Worse, the first live reading of a new session
 * claimed the bucket the device is still filling, so the open-bucket row that
 * store.py now serves would have been thrown away by the very sample that
 * triggered the request for it.
 *
 * So coverage, not bucket identity: a stored row is redundant only when live
 * samples actually span the window it summarizes -- one in each half of it.
 * Where the app was running, both halves are full and the mean is dropped
 * exactly as before, so nothing interleaves. Where a session stopped partway
 * through a bucket, or started partway through one, the following window is
 * half empty and its mean is kept.
 *
 * Two halves rather than a distance, because distance cannot tell the two
 * apart: a live sample two minutes before a bucket begins is just as close to
 * it as one two minutes inside, and only the second says anything about what
 * the bucket contains.
 */
export function backfill(rows, intervalS = 300) {
  if (!Array.isArray(rows) || !rows.length) return 0;
  const have = get(history);
  const bucket = Math.max(1000, intervalS * 1000);
  const half = bucket / 2;
  // Ascending, because record appends and this re-sorts, so the lookups
  // below are binary searches rather than a scan per incoming row. A first
  // backfill offers 2,016 rows against a buffer holding thousands.
  const held = have.map((r) => r[F.t]);
  // Where a bucket we have already backfilled sits, so a later fetch of the
  // same bucket replaces it instead of being discarded as a duplicate. The
  // open bucket is re-fetched on every reconnect -- OVERLAP_S guarantees it
  // -- and it is more complete each time. A stored row lands exactly on a
  // bucket boundary in ms and a live sample never does, so an exact hit here
  // is always our own earlier copy and never a reading.
  const ours = new Map();
  for (let i = 0; i < have.length; i++) ours.set(have[i][F.t], i);

  const cutoff = Date.now() - WINDOW_H * 3600_000;
  const add = [];
  const swap = new Map();
  const taken = new Set();
  for (const row of rows) {
    if (!Array.isArray(row) || row.length < 2) continue;
    const t = Number(row[0]) * 1000;
    if (!Number.isFinite(t) || t < cutoff || t > Date.now() + 60_000) continue;
    // The two stored files overlap after a rotation, so one response can
    // carry the same bucket twice.
    if (taken.has(t)) continue;
    taken.add(t);
    const next = row.slice();
    next[F.t] = t;
    const at = ours.get(t);
    if (at !== undefined) {
      swap.set(at, next);
      continue;
    }
    if (between(held, t, t + half) && between(held, t + half, t + bucket)) continue;
    add.push(next);
  }
  if (!add.length && !swap.size) return 0;

  const kept = swap.size ? have.map((r, i) => swap.get(i) || r) : have;
  const merged = kept.concat(add).sort((a, b) => a[F.t] - b[F.t]);
  const trimmed = merged.length > MAX ? merged.slice(merged.length - MAX) : merged;
  history.set(trimmed);
  persist(trimmed);
  return add.length;
}

/** Whether an ascending array holds anything in [lo, hi). */
function between(sorted, lo, hi) {
  let a = 0;
  let b = sorted.length;
  while (a < b) {
    const mid = (a + b) >> 1;
    if (sorted[mid] < lo) a = mid + 1;
    else b = mid;
  }
  return a < sorted.length && sorted[a] < hi;
}

function num(v) {
  // null and '' must survive as null. Number(null) is 0 and 0 is finite,
  // so this used to turn "the device has no reading" into a reading of
  // zero -- which for an odor tick count plots as the worst air ever
  // recorded, and for an excess is indistinguishable from air exactly as
  // clean as normal. Only undefined was being caught, via NaN.
  if (v == null || v === '') return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

export function clearHistory() {
  history.set([]);
  try {
    localStorage.removeItem(KEY);
  } catch {
    /* nothing to do */
  }
}

/** Rows inside the last `hours`, oldest first. */
export function since(rows, hours) {
  const cutoff = Date.now() - hours * 3600_000;
  return rows.filter((r) => r[F.t] >= cutoff);
}

/** min / max / mean / latest for one field, ignoring nulls. */
export function stats(rows, field) {
  const vals = rows.map((r) => r[field]).filter((v) => v !== null && v !== undefined);
  if (!vals.length) return null;
  let min = vals[0];
  let max = vals[0];
  let sum = 0;
  for (const v of vals) {
    if (v < min) min = v;
    if (v > max) max = v;
    sum += v;
  }
  return { min, max, mean: sum / vals.length, last: vals[vals.length - 1], n: vals.length };
}

/**
 * Split a series at real gaps so a period nobody recorded is drawn as a
 * break rather than a confident straight line between two distant points.
 */
export function segments(rows, field) {
  const out = [];
  let run = [];
  for (const r of rows) {
    const v = r[field];
    const broke = run.length && r[F.t] - run[run.length - 1][0] > GAP_MS;
    if (v === null || v === undefined || broke) {
      if (run.length > 1) out.push(run);
      run = [];
      if (v === null || v === undefined) continue;
    }
    run.push([r[F.t], v]);
  }
  if (run.length > 1) out.push(run);
  return out;
}
