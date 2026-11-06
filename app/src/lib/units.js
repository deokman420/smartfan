/**
 * How readings are displayed: units of temperature, and of time.
 *
 * A viewer preference, like the color scheme and unlike a threshold: the
 * fan measures, stores and decides in Celsius whatever this says, and two
 * phones looking at the same fan can disagree about the units without either
 * of them being wrong. Nothing here is ever sent to the device.
 *
 * Everything on the wire and in history stays Celsius. Converting at the
 * edge, at the moment a number becomes text, is what keeps `RH_FALL_OFF`
 * comparisons, the dew-point maths and a week of stored rows from having to
 * know that this setting exists.
 *
 * The one trap, and it is the reason this file exists rather than a pair of
 * inline `* 9 / 5 + 32`s: a temperature and a temperature DIFFERENCE do not
 * convert the same way. The gap between the room and its dew point is 1.8 °F
 * per °C with no offset at all, and running it through the absolute
 * conversion turns a 4 °C margin into 39 °F. Hence `delta()` beside `abs()`,
 * and the two are never interchangeable.
 */
import { writable, derived, get } from 'svelte/store';

const KEY = 'smartfan.units';

export const CHOICES = ['C', 'F'];

function load() {
  // Same defensiveness as link.js and theme.js: a private window, cleared
  // site data or a thumbnailer can all make this throw, and none of them is
  // a reason to fail to draw the app.
  try {
    const saved = localStorage.getItem(KEY);
    if (CHOICES.includes(saved)) return saved;
  } catch {
    /* fall through */
  }
  return 'C';
}

/** 'C' | 'F'. The device is never told. */
export const units = writable(load());

export function setUnits(next) {
  if (!CHOICES.includes(next)) return;
  units.set(next);
  try {
    localStorage.setItem(KEY, next);
  } catch {
    /* applies to this session; it just will not survive it */
  }
}

/** The suffix to print, including the degree sign. */
export const tempUnit = derived(units, ($u) => ($u === 'F' ? '°F' : '°C'));

/** An absolute temperature, Celsius in, display units out. */
export function abs(c, u) {
  if (c === null || c === undefined || !Number.isFinite(Number(c))) return c;
  return u === 'F' ? Number(c) * 9 / 5 + 32 : Number(c);
}

/**
 * A temperature DIFFERENCE. Scale only, never the 32 degree offset.
 *
 * Used for the room-to-dew-point margin, which is the number that says
 * whether the mirror is about to fog. Converting that as an absolute would
 * report a 4 degree margin as 39, which is not a rounding error but a
 * different claim about the room.
 */
export function delta(c, u) {
  if (c === null || c === undefined || !Number.isFinite(Number(c))) return c;
  return u === 'F' ? Number(c) * 9 / 5 : Number(c);
}

/** The current units, for code that needs them once rather than as a store. */
export function current() {
  return get(units);
}

/* ── The clock ─────────────────────────────────────────────────────────── */

const CLOCK_KEY = 'smartfan.clock';

export const CLOCK_CHOICES = ['24', '12'];

function loadClock() {
  try {
    const saved = localStorage.getItem(CLOCK_KEY);
    if (CLOCK_CHOICES.includes(saved)) return saved;
  } catch {
    /* fall through */
  }
  return '24';
}

/** '24' | '12'. */
export const clock = writable(loadClock());

export function setClock(next) {
  if (!CLOCK_CHOICES.includes(next)) return;
  clock.set(next);
  try {
    localStorage.setItem(CLOCK_KEY, next);
  } catch {
    /* applies to this session; it just will not survive it */
  }
}

/**
 * A timestamp as a wall clock.
 *
 * Deliberately not toLocaleTimeString: these are axis ticks and tooltip
 * labels sitting in a monospace column, and the locale form adds seconds in
 * some locales, a narrow no-break space before the meridiem in others, and
 * changes width as the day goes on. The chart needs a stable width far more
 * than it needs the reader's regional comma.
 *
 * Midnight and noon are the cases that catch a naive `h % 12`: both are 12,
 * not 0.
 */
export function fmtClock(t, pref = '24') {
  const d = new Date(t);
  const h = d.getHours();
  const m = String(d.getMinutes()).padStart(2, '0');
  if (pref !== '12') return `${String(h).padStart(2, '0')}:${m}`;
  const suffix = h < 12 ? 'am' : 'pm';
  const twelve = h % 12 === 0 ? 12 : h % 12;
  return `${twelve}:${m}${suffix}`;
}
