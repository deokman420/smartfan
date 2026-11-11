/* The tab icon's LED, as a function of the reading.
 *
 *     node tests/test_statusled.mjs
 *
 * The rule under test is that red and green say only what the board's own
 * LEDs would say (main.py drive_outputs), and the red double-blink only what the page
 * already flags. So the cases are the board's states, then the two ways the
 * app can be blind, then the ordering between them. There is no yellow:
 * the board has a green LED and a red one.
 */
import { register } from 'node:module';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { dirname, join } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const root = dirname(here);
const libRoot = pathToFileURL(join(root, 'app', 'src', 'lib') + '/').href;
register('./lib_alias_hook.mjs', import.meta.url, { data: { libRoot } });

const { ledCode, CODES } = await import('$lib/statusled.js');

let pass = 0;
let fail = 0;
function check(name, cond, extra = '') {
  if (cond) pass += 1;
  else fail += 1;
  console.log(`  ${name.padEnd(52)} ${cond ? 'PASS' : 'FAIL'} ${extra}`);
}

const LIVE = { link: 'online', online: true, stale: false };
const reading = (over = {}) => ({
  state: 'IDLE',
  fault: false,
  voc_ok: true,
  voc_left_s: null,
  selftest: 'passed',
  ...over
});
const code = (t, over = {}) => ledCode({ t, ...LIVE, ...over });

// --- the board's vocabulary ------------------------------------------------
check('IDLE is steady green', code(reading()) === 'idle');
check('RUN is steady red', code(reading({ state: 'RUN' })) === 'run');
check('BOOST blinks red', code(reading({ state: 'BOOST' })) === 'boost');
check('LEARN blinks green', code(reading({ state: 'LEARN' })) === 'learn');
check('FAULT alternates', code(reading({ state: 'FAULT', fault: true })) === 'fault');
check('IDLE while the VOC pixel settles pulses', code(reading({ voc_left_s: 3600 })) === 'settling');
check('a firmware without voc_left_s is plain idle', code(reading({ voc_left_s: undefined })) === 'idle');

// The patterns themselves, against main.py's timing.
check('fault is green then red', CODES.fault.beats.join() === 'green,red');
check('settle is one beat lit in six', CODES.settling.beats.filter(Boolean).length === 1 &&
  CODES.settling.beats.length === 6);
// The board has a green LED and a red one, and nothing else.
check('every code is green, red or dark', Object.values(CODES)
  .every((c) => [...c.beats, c.steady].every((b) => b === null || b === 'green' || b === 'red')));
check('look-at-the-app is a red double-blink', CODES.attention.beats.join() === 'red,,red,,,' &&
  CODES.unseen.beats.join() === CODES.attention.beats.join());
check('no board code double-blinks red', Object.entries(CODES)
  .filter(([k]) => k !== 'attention' && k !== 'unseen')
  .every(([, c]) => c.beats.filter((b) => b === 'red').length < 2));
check('reduced motion does not fake RUN for attention', CODES.attention.steady !== 'red');

// --- blind: the app cannot vouch for the board ------------------------------
check('a stale reading asks for a look, not its old color', code(reading({ state: 'RUN' }), { stale: true }) === 'unseen');
check('fan offline behind a live broker asks for a look', code(reading(), { online: false }) === 'unseen');
check('a link error asks for a look with no reading', ledCode({ t: null, link: 'error' }) === 'unseen');
check('still connecting says nothing', ledCode({ t: null, link: 'connecting' }) === null);
check('not set up says nothing', ledCode({ t: null, link: 'idle' }) === null);

// --- attention, and what outranks it ---------------------------------------
check('a failed odor sensor wants attention', code(reading({ voc_ok: false })) === 'attention');
check('a self-test never run wants attention', code(reading({ selftest: 'deferred' })) === 'attention');
check('FAULT outranks attention', code(reading({ state: 'FAULT', fault: true, voc_ok: false })) === 'fault');
check('blind outranks FAULT: an old fault is not a current one',
  code(reading({ state: 'FAULT', fault: true }), { stale: true }) === 'unseen');
check('an unknown state says nothing', code(reading({ state: 'WHAT' })) === null);

console.log(`\n${pass}/${pass + fail} passed\n`);
process.exit(fail ? 1 : 0);
