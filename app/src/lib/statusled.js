/**
 * The board's two status LEDs, as one bicolor LED in the tab icon.
 *
 * The enclosure has a green LED (PIN_LED_OK) and a red one (PIN_LED_RUN),
 * and main.py's drive_outputs() blinks them in a small vocabulary: steady
 * green is idle, steady red is running, alternating is a fault, and so on.
 * The favicon speaks the same vocabulary, so a code learned from the wall
 * means the same thing in a tab strip. That is the whole design rule here:
 * red and green belong to the board and say only what the board would say.
 *
 * The board has no third color, so neither does this. "Look at the app" is
 * a red double-blink -- on, off, on, then a pause -- a rhythm the board only
 * ever uses on green (the WiFi window), so it is free on red and cannot be
 * mistaken for BOOST's even blink. It covers two cases: the app cannot
 * currently see the board at all (its reading would be a guess, so it does
 * not show one), or the board is fine but something on it wants a person.
 * A FAULT still wins over the second kind, as FAULT wins everything on the
 * board. Under reduced motion the double-blink has no steady equivalent
 * that is not already taken -- steady red is RUN -- so it holds unlit.
 *
 * Patterns are lists of beats, cycled. A beat is 400 ms -- the board's own
 * phase -- while the tab is in front of you, and whatever the browser allows
 * behind it (Chrome holds background timers to one a second). Counting beats
 * rather than reading the clock is what keeps a throttled tab blinking
 * instead of aliasing a 400 ms alternation into a steady color.
 *
 * Not mirrored, because telemetry does not carry them: the button-hold
 * feedback and the WiFi provisioning double-blink. Both are things you only
 * see while standing at the box with a thumb on it.
 */

export const BEAT_MS = 400;

/** LED colors. The favicon plate is dark in both schemes, so these are the
 *  dark-scheme --good / --bad from tokens.css. */
export const LED_COLORS = {
  green: '#10b981',
  red: '#ef4444'
};

const G = 'green';
const R = 'red';
const _ = null;

/** On, off, on, then three dark: main.py's WiFi rhythm, on the red LED. */
const LOOK = [R, _, R, _, _, _];

/**
 * Each code: the beats, the one color to hold steady when motion is off,
 * and the words for it (the tab title and the tests use these).
 */
export const CODES = {
  idle: { beats: [G], steady: G, label: 'Idle' },
  // One flash then five dark: main.py's 200 ms x 12 slot settle pulse.
  settling: { beats: [G, _, _, _, _, _], steady: G, label: 'Odor sensor settling' },
  learn: { beats: [G, _], steady: G, label: 'Learning baseline' },
  run: { beats: [R], steady: R, label: 'Running' },
  boost: { beats: [R, _], steady: R, label: 'Boost' },
  fault: { beats: [G, R], steady: R, label: 'Fault' },
  attention: { beats: LOOK, steady: _, label: 'Needs attention' },
  unseen: { beats: LOOK, steady: _, label: 'No current reading' }
};

/**
 * Why a healthy, non-faulted board still wants someone to look at the app.
 * Kept to things the page itself already marks in its warning tone, so the
 * tab never raises a flag the page does not explain.
 */
export function attentionReason(t) {
  if (!t) return null;
  if (t.voc_ok === false) return 'Odor sensor failed';
  if (t.selftest === 'deferred') return 'Self-test never run';
  return null;
}

/**
 * Pick the code for the current reading.
 *
 * `link` is link.js's transport state, `online` whether the fan itself is
 * reporting, `stale` whether the last reading is too old to vouch for.
 * Returns null when there is nothing to say yet (no reading and no error),
 * which leaves the plain icon in place.
 */
export function ledCode({ t, link, online, stale }) {
  const blind = link === 'error' || (link === 'online' && (!online || stale));
  if (blind) return 'unseen';
  if (!t || link !== 'online') return null;

  if (t.fault || t.state === 'FAULT') return 'fault';
  if (attentionReason(t)) return 'attention';
  switch (t.state) {
    case 'LEARN':
      return 'learn';
    case 'BOOST':
      return 'boost';
    case 'RUN':
      return 'run';
    case 'IDLE':
      // main.py's _voc_settling(): a working sensor whose pixel has not
      // latched. voc_left_s goes null at the latch; a firmware too old to
      // publish it gets the plain idle green, which is what it would have
      // shown before the settle pulse existed.
      return t.voc_ok === true && t.voc_left_s != null ? 'settling' : 'idle';
    default:
      return null;
  }
}
