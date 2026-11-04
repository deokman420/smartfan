/* Swipe a bottom sheet away, because the grabber promised it.
 *
 * The settings sheet has always drawn a grabber -- a 36x4 pill at the top
 * edge, shown only below 560px, which is to say only where the sheet is a
 * bottom sheet. On every platform that pill means one thing: drag me down to
 * dismiss. Nothing was listening. Reported from an iPhone as "settings pulls
 * up from the bottom but I am not able to slide it away".
 *
 * The decision logic is split out as plain functions because the part worth
 * testing is the part with no DOM in it: how far is far enough, and how a
 * flick that travels 40px in 60ms must dismiss while a slow 40px drag must
 * spring back. The action below is the wiring.
 */

/* Travel that dismisses on distance alone. About a third of a short sheet,
 * far enough that resting a thumb on the grabber cannot trigger it. */
export const DISMISS_PX = 88;

/* Downward speed that dismisses regardless of distance, in px/ms. A flick
 * is short and fast; without this, flicking feels broken because the finger
 * leaves the glass long before it has travelled DISMISS_PX. */
export const DISMISS_VPM = 0.45;

/* Pointer travel before a gesture is treated as a drag rather than a tap.
 * Below this the sheet does not move at all, so a tap on the grabber is not
 * a 2px jump. */
export const SLOP_PX = 6;

/**
 * Should a finished gesture dismiss the sheet?
 *
 * @param dy  total downward travel in px; negative is upward
 * @param dt  duration in ms
 */
export function shouldDismiss(dy, dt, opts = {}) {
  const px = opts.px ?? DISMISS_PX;
  const vpm = opts.vpm ?? DISMISS_VPM;
  if (!(dy > 0)) return false; // upward, or nothing: keep it open
  if (dy >= px) return true;
  if (!(dt > 0)) return false; // no elapsed time means no velocity
  return dy / dt >= vpm;
}

/**
 * How far the sheet actually moves for a given finger travel.
 *
 * Downward is one-to-one. Upward is resisted rather than forbidden, because
 * a sheet that is rigid against an upward pull reads as broken, while one
 * that follows exactly reads as a sheet that could be dragged up and then
 * leaves a gap under itself. The square root gives the usual rubber band:
 * 100px of pull yields 20px of travel.
 *
 * The min() is not decoration. Below 4px of pull, 2*sqrt(x) is LARGER than
 * x, so the bare curve moves the sheet further than the finger did -- a
 * visible jump at the very start of every upward gesture, which is the one
 * place it would be noticed. Clamping to the pull makes the band start at
 * one-to-one and tighten from there.
 */
export function dragOffset(dy) {
  if (dy >= 0) return dy;
  const pull = -dy;
  return -Math.min(pull, 2 * Math.sqrt(pull));
}

/* A pointerdown on one of these is aimed at the control, not at the sheet. */
const CONTROLS = 'input, button, select, textarea, a, [role="radio"], [role="radiogroup"]';

/**
 * Svelte action: make `node` draggable downward to dismiss.
 *
 * Applies to the sheet element itself, which is also the scroll container.
 * A gesture starts when the pointer goes down either on the handle (the
 * element carrying `data-sheet-handle`) or anywhere non-interactive while
 * the sheet is scrolled to its top -- the second case is what makes a long
 * sheet feel right, since dragging content that cannot scroll up any
 * further should move the sheet instead.
 *
 * The handle's computed display gates the whole thing, so the action is
 * inert at the width where the sheet is a centred dialog and the grabber is
 * hidden. That keeps one source of truth -- the media query in the
 * stylesheet -- rather than repeating the breakpoint in JavaScript.
 *
 * @param node  the sheet element
 * @param onDismiss  called when the gesture asks for the sheet to close
 */
export function sheetDrag(node, onDismiss) {
  let dismiss = onDismiss;
  let id = null; // active pointerId, or null
  let y0 = 0;
  let t0 = 0;
  let moved = false;

  function enabled() {
    const handle = node.querySelector('[data-sheet-handle]');
    if (!handle) return false;
    try {
      return getComputedStyle(handle).display !== 'none';
    } catch {
      return true;
    }
  }

  function offset(px) {
    node.style.setProperty('--sheet-dy', `${px}px`);
  }

  function release() {
    if (id !== null) {
      try {
        node.releasePointerCapture(id);
      } catch {
        /* already gone */
      }
    }
    id = null;
    moved = false;
    node.style.transition = '';
    offset(0);
  }

  function down(e) {
    if (id !== null || !e.isPrimary || !enabled()) return;
    // Mouse and pen get the handle only. A click-drag across the body of a
    // dialog is not a dismiss gesture anywhere else, and treating it as one
    // makes text unselectable.
    const onHandle = !!e.target?.closest?.('[data-sheet-handle]');
    if (!onHandle) {
      if (e.pointerType !== 'touch') return;
      if (e.target?.closest?.(CONTROLS)) return;
      if (node.scrollTop > 0) return;
    }
    id = e.pointerId;
    y0 = e.clientY;
    t0 = e.timeStamp;
    moved = false;
  }

  function move(e) {
    if (e.pointerId !== id) return;
    const dy = e.clientY - y0;
    if (!moved) {
      if (Math.abs(dy) < SLOP_PX) return;
      moved = true;
      node.style.transition = 'none';
      try {
        node.setPointerCapture(id);
      } catch {
        /* capture is an optimisation, not a requirement */
      }
    }
    // Only once this is a real drag, so a tap that scrolls is left alone.
    if (e.cancelable) e.preventDefault();
    offset(dragOffset(dy));
  }

  function up(e) {
    if (e.pointerId !== id) return;
    const dy = e.clientY - y0;
    const dt = e.timeStamp - t0;
    const go = moved && shouldDismiss(dy, dt);
    release();
    if (go) dismiss?.();
  }

  function cancel(e) {
    if (e.pointerId === id) release();
  }

  node.addEventListener('pointerdown', down);
  node.addEventListener('pointermove', move);
  node.addEventListener('pointerup', up);
  node.addEventListener('pointercancel', cancel);

  return {
    update(next) {
      dismiss = next;
    },
    destroy() {
      release();
      node.removeEventListener('pointerdown', down);
      node.removeEventListener('pointermove', move);
      node.removeEventListener('pointerup', up);
      node.removeEventListener('pointercancel', cancel);
    }
  };
}
