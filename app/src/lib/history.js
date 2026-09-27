/**
 * A rolling 24 hours of samples, kept in the browser.
 *
 * Why not Home Assistant's recorder, which already holds 30 days: the app
 * is a client of the broker and nothing else. Pulling history would add a
 * second credential, a second service to be up, and a dependency on the
 * very thing the app is supposed to work without. See app/README.md.
 *
 * The honest cost of that choice: history only covers the time this
 * browser has had the app open. It survives reloads, it does not invent
 * the gaps, and the charts say so rather than drawing a straight line
 * across a period nobody watched.
 *
 * Samples are stored as plain arrays, not objects. 2880 objects with ten
 * named keys each is roughly six times the JSON, and this goes through
 * localStorage on every write.
 */
import { writable, get } from 'svelte/store';

const KEY = 'smartfan.history';
const MAX = 2880; // 24 h at one sample per 30 s
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
  lit: 9
};

function load() {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return [];
    const rows = JSON.parse(raw);
    if (!Array.isArray(rows)) return [];
    // Drop anything older than the window, so a browser left closed for a
    // week does not open on a chart of last week.
    const cutoff = Date.now() - 24 * 3600_000;
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
    p.light_on ? 1 : 0
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
