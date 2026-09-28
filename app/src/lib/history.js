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
export const GAP_MS = 5 * 60_000; // longer than this is a break, not a line

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
  st: 10 // controller state, appended so older samples stay valid
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
    STATE_CODE[p.state] ?? null
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
 * Fold the fan's own stored rows in, keeping whatever we already have.
 *
 * The device sends `[unix_seconds, ...]` in the same field order as F, which
 * is not an accident: store.py documents that the order is append-only for
 * exactly this reason. Timestamps are converted to ms here, at the one place
 * that knows both conventions.
 *
 * Live samples win over stored ones for the same bucket. A backfilled row is
 * a five-minute mean; a live row is a real reading, and where both exist the
 * reading is the truer answer.
 */
export function backfill(rows, intervalS = 300) {
  if (!Array.isArray(rows) || !rows.length) return 0;
  const have = get(history);
  const bucket = Math.max(1000, intervalS * 1000);
  // Keys of what we hold, at the backfill's own resolution, so an incoming
  // mean that overlaps live samples is dropped rather than interleaved.
  const seen = new Set(have.map((r) => Math.floor(r[F.t] / bucket)));

  const cutoff = Date.now() - WINDOW_H * 3600_000;
  const add = [];
  for (const row of rows) {
    if (!Array.isArray(row) || row.length < 2) continue;
    const t = Number(row[0]) * 1000;
    if (!Number.isFinite(t) || t < cutoff || t > Date.now() + 60_000) continue;
    const key = Math.floor(t / bucket);
    if (seen.has(key)) continue;
    seen.add(key);
    const next = row.slice();
    next[F.t] = t;
    add.push(next);
  }
  if (!add.length) return 0;

  const merged = have.concat(add).sort((a, b) => a[F.t] - b[F.t]);
  const trimmed = merged.length > MAX ? merged.slice(merged.length - MAX) : merged;
  history.set(trimmed);
  persist(trimmed);
  return add.length;
}

function num(v) {
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
