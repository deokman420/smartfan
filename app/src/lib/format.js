/** Presentation helpers. Values arrive as numbers; people read units. */

export const MODES = ['Auto', 'Force on', 'Force off'];

/** Seconds -> "45 min" / "2 h 30 m". Durations here run to tens of minutes. */
export function dur(s) {
  if (s === null || s === undefined) return '—';
  const n = Number(s);
  if (!Number.isFinite(n)) return '—';
  if (n < 60) return `${Math.round(n)} s`;
  const m = Math.round(n / 60);
  if (m < 90) return `${m} min`;
  return `${Math.floor(m / 60)} h ${m % 60} m`;
}

/** Minutes -> the same, for up_min / run_min which arrive already in minutes. */
export function durMin(m) {
  return dur(m === null || m === undefined ? null : Number(m) * 60);
}

export function num(v, digits = 1, unit = '') {
  if (v === null || v === undefined) return '—';
  const n = Number(v);
  if (!Number.isFinite(n)) return '—';
  return `${n.toFixed(digits)}${unit}`;
}

export function int(v, unit = '') {
  if (v === null || v === undefined) return '—';
  const n = Number(v);
  if (!Number.isFinite(n)) return '—';
  return `${Math.round(n).toLocaleString()}${unit}`;
}

/**
 * Signal strength as words. -48 dBm means nothing to most people, and the
 * number is already shown next to it for anyone it does mean something to.
 */
export function rssiWord(dbm) {
  if (dbm === null || dbm === undefined) return '—';
  const n = Number(dbm);
  if (n >= -55) return 'Strong';
  if (n >= -67) return 'Good';
  if (n >= -75) return 'Weak';
  return 'Very weak';
}

/** The five controller states, with the colour each should read as. */
export const STATE_TONE = {
  LEARN: 'info',
  IDLE: 'calm',
  RUN: 'active',
  BOOST: 'active',
  FAULT: 'bad'
};

export const STATE_BLURB = {
  LEARN: 'Learning what this room normally sits at',
  IDLE: 'Watching, fan off',
  RUN: 'Clearing humidity',
  BOOST: 'Running on demand',
  FAULT: 'Needs attention'
};

/**
 * Where the excess sits against the thresholds that actually drive the fan.
 * Reading the gauge should answer "is it about to run", not "what number".
 */
export function excessTone(exc, riseOn, fallOff) {
  const n = Number(exc);
  if (!Number.isFinite(n)) return 'calm';
  if (n >= Number(riseOn)) return 'active';
  if (n > Number(fallOff)) return 'warn';
  return 'calm';
}
