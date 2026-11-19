/* Swipe left or right to change section, on phones.
 *
 * The bottom tab strip is in reach of a thumb, but a sideways swipe across
 * the content is the gesture a phone user tries first, and nothing answered
 * it. Desktop is left alone: the sidebar is right there and a trackpad's
 * horizontal swipe means back/forward to the browser.
 *
 * As with sheetdrag.js, the decision is plain functions so it can be tested
 * without a DOM, and the action below is the wiring.
 *
 * Touch events rather than pointer events, on purpose: with touch-action
 * left at auto the browser cancels a pointer as soon as it decides the
 * finger is panning, which is exactly the moment this needs to see.
 * Listeners are passive, so vertical scrolling is never delayed.
 */

/* Sideways travel that switches section on distance alone. */
export const SWIPE_PX = 60;

/* A quick flick switches with less travel, but never with less than this. */
export const FLICK_MIN_PX = 30;

/* Sideways speed that makes a short flick count, in px/ms. */
export const FLICK_VPM = 0.5;

/* How much more sideways than vertical a swipe must be. A scroll that
 * drifts a little sideways must not change section under the reader. */
export const DOMINANCE = 1.5;

/* Longer than this and it was a slow drag or a rest, not a swipe. */
export const MAX_MS = 800;

/* Touches that start this close to a screen edge belong to the OS (the
 * iOS back gesture, Android's edge swipe). */
export const EDGE_PX = 16;

/**
 * Which way did a finished gesture go?
 *
 * @param dx  sideways travel in px; negative is leftward
 * @param dy  vertical travel in px
 * @param dt  duration in ms
 * @returns   1 for the next section (swiped left), -1 for the previous
 *            (swiped right), 0 for neither
 */
export function swipeDirection(dx, dy, dt) {
  const ax = Math.abs(dx);
  const ay = Math.abs(dy);
  if (!(dt > 0) || dt > MAX_MS) return 0;
  if (ax < DOMINANCE * ay) return 0;
  const far = ax >= SWIPE_PX;
  const flick = ax >= FLICK_MIN_PX && ax / dt >= FLICK_VPM;
  if (!far && !flick) return 0;
  return dx < 0 ? 1 : -1;
}

/**
 * The section a swipe lands on, or null at either end. No wrap-around:
 * swiping past Health onto Status reads as a mistake, not a feature.
 */
export function neighbor(ids, current, dir) {
  const i = ids.indexOf(current);
  if (i < 0 || !dir) return null;
  const j = i + dir;
  return j >= 0 && j < ids.length ? ids[j] : null;
}

/* Things that own their own horizontal gestures: form controls, the chart
 * scrubbers, and anything marked data-no-swipe. */
const OWN_GESTURE = 'input, textarea, select, .scrub, [data-no-swipe]';

function scrollsSideways(el, stop) {
  for (let n = el; n && n !== stop; n = n.parentElement) {
    if (n.scrollWidth > n.clientWidth + 1) {
      const ox = getComputedStyle(n).overflowX;
      if (ox === 'auto' || ox === 'scroll') return true;
    }
  }
  return false;
}

/**
 * Svelte action.
 *
 *   <div use:tabSwipe={{ enabled, onSwipe }}>
 *
 * onSwipe(dir) is called with 1 or -1 and returns true if it moved, which
 * plays a short slide in from the side the new section came from.
 */
export function tabSwipe(node, params) {
  let opts = params ?? {};
  let start = null;
  const phone =
    typeof matchMedia === 'function' ? matchMedia('(max-width: 959px)') : { matches: true };

  function down(e) {
    start = null;
    if (opts.enabled === false || !phone.matches) return;
    if (e.touches.length !== 1) return;
    const t = e.touches[0];
    const w = typeof innerWidth === 'number' ? innerWidth : Infinity;
    if (t.clientX < EDGE_PX || t.clientX > w - EDGE_PX) return;
    const target = e.target;
    if (target?.closest?.(OWN_GESTURE)) return;
    if (target?.parentElement !== undefined && scrollsSideways(target, node)) return;
    start = { x: t.clientX, y: t.clientY, at: e.timeStamp };
  }

  function up(e) {
    if (!start) return;
    const t = e.changedTouches[0];
    const s = start;
    start = null;
    if (!t) return;
    const dir = swipeDirection(t.clientX - s.x, t.clientY - s.y, e.timeStamp - s.at);
    if (!dir || !opts.onSwipe?.(dir)) return;
    const cls = dir > 0 ? 'swipe-next' : 'swipe-prev';
    node.classList.remove('swipe-next', 'swipe-prev');
    void node.offsetWidth; // restart the animation on a quick second swipe
    node.classList.add(cls);
  }

  function multi(e) {
    if (e.touches.length > 1) start = null; // a pinch, not a swipe
  }
  function cancel() {
    start = null;
  }
  function done(e) {
    if (e.target === node) node.classList.remove('swipe-next', 'swipe-prev');
  }

  node.addEventListener('touchstart', down, { passive: true });
  node.addEventListener('touchmove', multi, { passive: true });
  node.addEventListener('touchend', up, { passive: true });
  node.addEventListener('touchcancel', cancel, { passive: true });
  node.addEventListener('animationend', done);

  return {
    update(p) {
      opts = p ?? {};
    },
    destroy() {
      node.removeEventListener('touchstart', down);
      node.removeEventListener('touchmove', multi);
      node.removeEventListener('touchend', up);
      node.removeEventListener('touchcancel', cancel);
      node.removeEventListener('animationend', done);
    }
  };
}
