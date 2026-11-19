/* Dismissing a bottom sheet by dragging it.
 *
 *     node tests/test_sheetdrag.mjs
 *
 * This suite exists because of a bug reported from an iPhone: the settings
 * sheet drew a grabber -- the universal "drag me down" pill -- and nothing
 * listened to it, so the sheet opened and could not be swiped away. The
 * decision logic is tested here; the DOM wiring is not, because the part
 * that was actually wrong was the absence of any logic at all.
 *
 * The thresholds are the interesting content. A sheet that only dismisses on
 * distance feels broken under a flick, and one that only dismisses on
 * velocity cannot be dragged deliberately, so both paths are pinned.
 */
import { register } from 'node:module';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { dirname, join } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const root = dirname(here);
const libRoot = pathToFileURL(join(root, 'app', 'src', 'lib') + '/').href;
register('./lib_alias_hook.mjs', import.meta.url, { data: { libRoot } });

const { DISMISS_PX, DISMISS_VPM, SLOP_PX, dragOffset, shouldDismiss, sheetDrag } =
  await import('$lib/sheetdrag.js');

let pass = 0;
let fail = 0;
function check(name, cond, extra = '') {
  if (cond) {
    pass += 1;
    console.log(`  ${name.padEnd(56)} PASS ${extra}`);
  } else {
    fail += 1;
    console.log(`  ${name.padEnd(56)} FAIL ${extra}`);
  }
}

console.log('\nthresholds');
check('DISMISS_PX is a sane fraction of a sheet', DISMISS_PX > 40 && DISMISS_PX < 200,
  `${DISMISS_PX}px`);
check('DISMISS_VPM is a flick, not a crawl', DISMISS_VPM > 0.1 && DISMISS_VPM < 2,
  `${DISMISS_VPM}px/ms`);
check('SLOP_PX leaves taps alone', SLOP_PX >= 3 && SLOP_PX <= 12, `${SLOP_PX}px`);

console.log('\ndistance dismisses');
check('a long slow drag dismisses', shouldDismiss(DISMISS_PX + 1, 2000) === true);
check('exactly the threshold dismisses', shouldDismiss(DISMISS_PX, 2000) === true);
check('one px short of it does not', shouldDismiss(DISMISS_PX - 1, 2000) === false);

console.log('\nvelocity dismisses');
// The reported gesture: a short fast flick. 40px in 60ms is 0.67px/ms, well
// clear of the threshold but nowhere near DISMISS_PX.
check('a 40px flick in 60ms dismisses', shouldDismiss(40, 60) === true, '0.67px/ms');
check('the same 40px taken slowly springs back', shouldDismiss(40, 2000) === false,
  '0.02px/ms');
check('exactly the velocity threshold dismisses',
  shouldDismiss(DISMISS_VPM * 100, 100) === true);
check('just under it does not',
  shouldDismiss(DISMISS_VPM * 100 - 0.01, 100) === false);

console.log('\nwhat must never dismiss');
check('an upward drag does not', shouldDismiss(-200, 100) === false);
check('no movement does not', shouldDismiss(0, 100) === false);
check('zero elapsed time is not infinite velocity',
  shouldDismiss(1, 0) === false, 'guards a divide by zero');
check('a huge upward flick does not', shouldDismiss(-500, 10) === false);
// A real pointer sequence can report the same timestamp twice.
check('negative duration does not dismiss a short drag',
  shouldDismiss(10, -5) === false);

console.log('\noverridable thresholds');
check('px can be overridden', shouldDismiss(50, 9999, { px: 40 }) === true);
check('vpm can be overridden', shouldDismiss(10, 100, { vpm: 0.05 }) === true);

console.log('\ndragOffset: down follows, up resists');
check('downward is one to one', dragOffset(60) === 60);
check('zero is zero', dragOffset(0) === 0);
check('upward is resisted, not blocked', dragOffset(-100) === -20, '100px pull -> 20px');
check('upward never exceeds the pull',
  [-1, -4, -25, -100, -400].every((d) => Math.abs(dragOffset(d)) <= Math.abs(d)));
check('upward is monotonic',
  dragOffset(-10) > dragOffset(-40) && dragOffset(-40) > dragOffset(-90));
check('offset is finite for a large drag', Number.isFinite(dragOffset(100000)));

/* ---------------------------------------------------------------- wiring --
 * A deliberately small fake DOM. This is not a browser; it checks the three
 * things the action promises that pure functions cannot: that it is inert
 * when the handle is hidden, that a touch drag on the handle calls back, and
 * that destroy() detaches.
 */
console.log('\naction wiring');

function fakeSheet({ handleDisplay = 'block', scrollTop = 0 } = {}) {
  const listeners = {};
  const handle = { display: handleDisplay };
  const node = {
    scrollTop,
    style: {
      props: {},
      transition: '',
      setProperty(k, v) {
        this.props[k] = v;
      }
    },
    querySelector: (sel) => (sel === '[data-sheet-handle]' ? handle : null),
    addEventListener: (t, fn) => ((listeners[t] = fn), undefined),
    removeEventListener: (t) => (delete listeners[t], undefined),
    captures: 0,
    setPointerCapture() { this.captures += 1; },
    releasePointerCapture() {}
  };
  return { node, listeners, handle };
}

globalThis.getComputedStyle = (el) => ({ display: el?.display ?? 'block' });

function target(onHandle, control = false) {
  return {
    closest: (sel) => {
      if (sel === '[data-sheet-handle]') return onHandle ? {} : null;
      return control ? {} : null;
    }
  };
}

function drag(listeners, { from = 0, to = 120, dt = 300, pointerType = 'touch',
  onHandle = true, control = false } = {}) {
  const t = target(onHandle, control);
  listeners.pointerdown?.({
    pointerId: 1, isPrimary: true, clientY: from, timeStamp: 0, pointerType, target: t
  });
  listeners.pointermove?.({
    pointerId: 1, clientY: to, timeStamp: dt, cancelable: true,
    preventDefault() {}, target: t
  });
  listeners.pointerup?.({ pointerId: 1, clientY: to, timeStamp: dt, target: t });
}

{
  let closed = 0;
  const { node, listeners } = fakeSheet();
  sheetDrag(node, () => (closed += 1));
  drag(listeners);
  check('a touch drag down the handle dismisses', closed === 1, `calls=${closed}`);
  check('the offset is reset after the gesture',
    node.style.props['--sheet-dy'] === '0px');
}

{
  let closed = 0;
  const { node, listeners } = fakeSheet({ handleDisplay: 'none' });
  sheetDrag(node, () => (closed += 1));
  drag(listeners);
  check('inert where the grabber is hidden', closed === 0,
    'the 560px dialog, read from CSS not duplicated');
}

{
  let closed = 0;
  const { node, listeners } = fakeSheet();
  sheetDrag(node, () => (closed += 1));
  drag(listeners, { to: 20, dt: 3000 });
  check('a short slow drag springs back', closed === 0);
}

{
  let closed = 0;
  const { node, listeners } = fakeSheet();
  sheetDrag(node, () => (closed += 1));
  drag(listeners, { to: from0(), dt: 300 });
  function from0() {
    return 3;
  }
  check('travel under the slop never becomes a drag', closed === 0, `<${SLOP_PX}px`);
}

{
  let closed = 0;
  const { node, listeners } = fakeSheet({ scrollTop: 240 });
  sheetDrag(node, () => (closed += 1));
  drag(listeners, { onHandle: false });
  check('a scrolled sheet drags its content, not itself', closed === 0);
}

{
  let closed = 0;
  const { node, listeners } = fakeSheet({ scrollTop: 0 });
  sheetDrag(node, () => (closed += 1));
  drag(listeners, { onHandle: false });
  check('at the top of the scroll it drags the sheet', closed === 1);
}

{
  let closed = 0;
  const { node, listeners } = fakeSheet();
  sheetDrag(node, () => (closed += 1));
  drag(listeners, { onHandle: false, control: true });
  check('a drag starting on a control is left to the control', closed === 0);
}

{
  let closed = 0;
  const { node, listeners } = fakeSheet();
  sheetDrag(node, () => (closed += 1));
  drag(listeners, { onHandle: false, pointerType: 'mouse' });
  check('a mouse drag off the handle is not a dismiss', closed === 0,
    'text stays selectable');
}

{
  let closed = 0;
  const { node, listeners } = fakeSheet();
  sheetDrag(node, () => (closed += 1));
  drag(listeners, { pointerType: 'mouse', onHandle: true });
  check('a mouse drag ON the handle is', closed === 1);
  check('a handle drag captures the pointer on down',
    node.captures >= 1, `captures=${node.captures}`);
}

{
  let closed = 0;
  const { node, listeners } = fakeSheet({ handleDisplay: 'block' });
  // A chart sheet without a grabber used to attach sheetDrag and then
  // do nothing: enabled() bails when querySelector finds no handle.
  node.querySelector = () => null;
  sheetDrag(node, () => (closed += 1));
  drag(listeners, { pointerType: 'mouse', onHandle: true });
  check('a sheet with no grabber does not drag-dismiss', closed === 0);
}

{
  let closed = 0;
  const { node, listeners } = fakeSheet();
  const h = sheetDrag(node, () => (closed += 1));
  h.update(() => (closed += 10));
  drag(listeners);
  check('update() swaps the callback', closed === 10, `got ${closed}`);
}

{
  const { node, listeners } = fakeSheet();
  const h = sheetDrag(node, () => {});
  h.destroy();
  check('destroy() detaches every listener',
    Object.keys(listeners).length === 0, `left ${Object.keys(listeners).length}`);
}

{
  let closed = 0;
  const { node, listeners } = fakeSheet();
  sheetDrag(node, () => (closed += 1));
  // A cancelled gesture is a system interruption, not a dismissal.
  listeners.pointerdown({
    pointerId: 1, isPrimary: true, clientY: 0, timeStamp: 0,
    pointerType: 'touch', target: target(true)
  });
  listeners.pointermove({
    pointerId: 1, clientY: 200, timeStamp: 100, cancelable: true,
    preventDefault() {}, target: target(true)
  });
  listeners.pointercancel({ pointerId: 1 });
  listeners.pointerup({ pointerId: 1, clientY: 200, timeStamp: 100 });
  check('pointercancel abandons the gesture', closed === 0);
  check('and resets the offset', node.style.props['--sheet-dy'] === '0px');
}

{
  let closed = 0;
  const { node, listeners } = fakeSheet();
  sheetDrag(node, () => (closed += 1));
  // A second finger must not hijack a gesture already in progress.
  listeners.pointerdown({
    pointerId: 1, isPrimary: true, clientY: 0, timeStamp: 0,
    pointerType: 'touch', target: target(true)
  });
  listeners.pointerdown({
    pointerId: 2, isPrimary: true, clientY: 500, timeStamp: 10,
    pointerType: 'touch', target: target(true)
  });
  listeners.pointermove({
    pointerId: 1, clientY: 200, timeStamp: 100, cancelable: true,
    preventDefault() {}, target: target(true)
  });
  listeners.pointerup({ pointerId: 2, clientY: 500, timeStamp: 110 });
  check('a second pointer cannot hijack the gesture', closed === 0);
  listeners.pointerup({ pointerId: 1, clientY: 200, timeStamp: 120 });
  check('and the first pointer still finishes it', closed === 1);
}

{
  let closed = 0;
  const { node, listeners } = fakeSheet();
  sheetDrag(node, () => (closed += 1));
  listeners.pointerdown({
    pointerId: 1, isPrimary: false, clientY: 0, timeStamp: 0,
    pointerType: 'touch', target: target(true)
  });
  listeners.pointermove?.({
    pointerId: 1, clientY: 200, timeStamp: 100, cancelable: true,
    preventDefault() {}, target: target(true)
  });
  listeners.pointerup?.({ pointerId: 1, clientY: 200, timeStamp: 100 });
  check('a non-primary pointer is ignored', closed === 0);
}

console.log(`\n${pass}/${pass + fail} passed`);
process.exit(fail ? 1 : 0);
