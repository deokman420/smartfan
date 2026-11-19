/* Swiping between sections on a phone.
 *
 *     node tests/test_tabswipe.mjs
 *
 * The decision logic is what is tested: a scroll that drifts sideways must
 * never change section, a short quick flick must, and the ends of the tab
 * strip do not wrap. The wiring is exercised with a fake node.
 */
import { register } from 'node:module';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { dirname, join } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const root = dirname(here);
const libRoot = pathToFileURL(join(root, 'app', 'src', 'lib') + '/').href;
register('./lib_alias_hook.mjs', import.meta.url, { data: { libRoot } });

const { SWIPE_PX, FLICK_MIN_PX, FLICK_VPM, DOMINANCE, MAX_MS, EDGE_PX, swipeDirection, neighbor, tabSwipe } =
  await import('$lib/tabswipe.js');

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
check('SWIPE_PX is a deliberate distance', SWIPE_PX >= 40 && SWIPE_PX <= 120, `${SWIPE_PX}px`);
check('a flick still needs some travel', FLICK_MIN_PX >= 20 && FLICK_MIN_PX < SWIPE_PX);
check('sideways must clearly dominate', DOMINANCE >= 1.2);

console.log('\ndirection');
check('leftward swipe goes to the next section', swipeDirection(-SWIPE_PX, 0, 300) === 1);
check('rightward swipe goes to the previous one', swipeDirection(SWIPE_PX, 0, 300) === -1);
check('one px short and slow does nothing', swipeDirection(-(SWIPE_PX - 1), 0, 600) === 0);
check('a short fast flick counts', swipeDirection(-40, 5, 50) === 1, '0.8px/ms');
check('the same 40px slowly does not', swipeDirection(-40, 5, 400) === 0);
check('a flick under FLICK_MIN_PX does not', swipeDirection(-(FLICK_MIN_PX - 1), 0, 10) === 0);
check('exactly the flick speed counts',
  swipeDirection(-FLICK_MIN_PX, 0, FLICK_MIN_PX / FLICK_VPM) === 1);

console.log('\nscrolling is not swiping');
check('a vertical scroll with sideways drift is ignored', swipeDirection(-70, 200, 300) === 0);
check('a diagonal just under dominance is ignored',
  swipeDirection(-100, 100 / DOMINANCE + 1, 300) === 0);
check('a diagonal at dominance counts', swipeDirection(-100, 100 / DOMINANCE, 300) === 1);
check('a long rest then a drag is not a swipe', swipeDirection(-200, 0, MAX_MS + 1) === 0);
check('zero time is not a swipe', swipeDirection(-200, 0, 0) === 0);

console.log('\nneighbors');
const ids = ['status', 'readings', 'tuning', 'charts', 'health'];
check('next from status is readings', neighbor(ids, 'status', 1) === 'readings');
check('previous from charts is tuning', neighbor(ids, 'charts', -1) === 'tuning');
check('no wrap past the last', neighbor(ids, 'health', 1) === null);
check('no wrap before the first', neighbor(ids, 'status', -1) === null);
check('unknown section goes nowhere', neighbor(ids, 'nope', 1) === null);

console.log('\nwiring');
function fakeNode() {
  const listeners = {};
  const classes = new Set();
  return {
    listeners,
    classes,
    offsetWidth: 0,
    classList: {
      add: (c) => classes.add(c),
      remove: (...cs) => cs.forEach((c) => classes.delete(c))
    },
    addEventListener: (k, f) => (listeners[k] = f),
    removeEventListener: (k) => delete listeners[k]
  };
}
const target = (owns = false) => ({ closest: () => (owns ? {} : null) });
function swipe(node, x0, x1, { owns = false, fingers = 1 } = {}) {
  const t0 = { clientX: x0, clientY: 300 };
  node.listeners.touchstart({
    touches: Array(fingers).fill(t0), target: target(owns), timeStamp: 0
  });
  node.listeners.touchend({
    touches: [], changedTouches: [{ clientX: x1, clientY: 305 }], timeStamp: 200
  });
}
globalThis.innerWidth = 390;
{
  const node = fakeNode();
  const got = [];
  tabSwipe(node, { onSwipe: (d) => (got.push(d), true) });
  swipe(node, 300, 150);
  check('a swipe calls onSwipe with the direction', got.join() === '1');
  check('and plays the slide-in', node.classes.has('swipe-next'));
  swipe(node, 100, 250);
  check('the other way too', got.join() === '1,-1' && node.classes.has('swipe-prev'));
  swipe(node, EDGE_PX - 1, 250);
  check('a touch from the screen edge is left to the OS', got.length === 2);
  swipe(node, 300, 150, { owns: true });
  check('a chart scrubber or input keeps its gesture', got.length === 2);
  swipe(node, 300, 150, { fingers: 2 });
  check('a two-finger touch is not a swipe', got.length === 2);
}
{
  const node = fakeNode();
  let called = 0;
  const action = tabSwipe(node, { enabled: false, onSwipe: () => (called++, true) });
  swipe(node, 300, 150);
  check('disabled while a sheet is open', called === 0);
  action.update({ enabled: true, onSwipe: () => (called++, true) });
  swipe(node, 300, 150);
  check('update() re-enables it', called === 1);
}
{
  const node = fakeNode();
  tabSwipe(node, { onSwipe: () => false });
  swipe(node, 300, 150);
  check('no slide-in at the end of the strip', node.classes.size === 0);
}

console.log(`\n${pass}/${pass + fail} passed`);
process.exit(fail ? 1 : 0);
