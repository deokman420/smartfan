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

/** The five controller states, with the color each should read as. */
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
  RUN: 'Clearing the air',
  BOOST: 'Running on demand',
  FAULT: 'Needs attention'
};

/**
 * What the controller is doing, and why it decided to.
 *
 * RUN used to read a fixed 'Clearing humidity', which was true when
 * humidity was the only reason a fan could start. It is now one of three,
 * and the page said "Clearing humidity" over a room 0.9 % above baseline
 * while the fan ran on odor. The controller publishes the test it actually
 * passed in `why`; this turns that into English and never invents one.
 *
 * Unknown reasons fall back to the plain state blurb rather than being
 * shown raw: a new reason added to the firmware should read as vague here,
 * not as debug output on someone's wall tablet.
 */
export function stateBlurb(state, why) {
  const blurb = STATE_BLURB[state] ?? '';
  if (state !== 'RUN' || !why) return blurb;
  const w = String(why).toLowerCase();
  if (w.startsWith('odor') || w.startsWith('odor')) return 'Clearing odor';
  if (w.startsWith('humidity')) return 'Clearing humidity';
  if (w.startsWith('light')) return 'Running with the light';
  return blurb;
}

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

/**
 * The VOC settle gate, as the page is allowed to describe it.
 *
 * Three five-minute blocks have to exist before the reading is allowed to
 * count as flat, and the ceiling in config.py seeds a baseline at two hours
 * even if it never does. ready is the first of those. left is the second.
 * Only left is an ETA: a ramp can fail every window until the ceiling.
 *
 * Firmware publishes both as voc_ready_s and voc_left_s. A build from before
 * that omits the keys, and uptime is the same clock — the heater starts at
 * boot — to within the second or two the sensor takes to come up.
 */
export const ODOR_WINDOW_S = 15 * 60;
export const ODOR_CEILING_S = 120 * 60;

export function odorSettle(t) {
  if (!t || t.voc_exc != null || t.voc_ok === false) return null;
  if (t.voc == null && t.voc_ok !== true) return null;

  if (!('voc_left_s' in t)) {
    const up = Number(t.up_min);
    if (!Number.isFinite(up)) return { kind: 'learning' };
    const left = Math.max(0, Math.round(ODOR_CEILING_S - up * 60));
    const ready = Math.max(0, Math.round(ODOR_WINDOW_S - up * 60));
    if (left <= 0) return { kind: 'quiet' };
    return { kind: 'eta', left, ready };
  }
  if (t.voc_left_s == null) return { kind: 'quiet' };
  const left = Math.round(Number(t.voc_left_s));
  const readyRaw = t.voc_ready_s == null ? 0 : Number(t.voc_ready_s);
  if (!Number.isFinite(left)) return { kind: 'learning' };
  return {
    kind: 'eta',
    left,
    ready: Number.isFinite(readyRaw) ? Math.max(0, Math.round(readyRaw)) : 0
  };
}

/** The status fact and the readings line. Null means keep the old "Learning". */
export function odorSettleLine(t) {
  const s = odorSettle(t);
  if (!s || s.kind === 'learning') return null;
  if (s.kind === 'quiet') {
    return {
      value: 'Waiting',
      sub: 'for a quiet room',
      line: 'waiting for a quiet room'
    };
  }
  const latest = dur(s.left);
  const early = s.ready > 30 ? dur(s.ready) : null;
  return {
    value: latest,
    sub: early ? `earliest ${early}` : 'at the latest',
    line: early
      ? `earliest ${early}, by ${latest}`
      : `by ${latest}, sooner if the reading goes flat`
  };
}
