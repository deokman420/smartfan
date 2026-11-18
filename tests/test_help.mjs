/* The help center's content, checked against the firmware it describes.
 *
 *     node tests/test_help.mjs
 *
 * Help that is wrong is worse than none: somebody holds the button for the
 * number of seconds it says, or waits the number of minutes it says, and
 * concludes the fan is broken. So the defaults the topics quote are read
 * back out of config.py and wifi.py here, and a firmware change that moves
 * one fails this suite until the sentence moves with it.
 *
 * Also: every cross-link resolves, every fault sentence the firmware can
 * publish lands on a topic about that fault, search finds the obvious
 * things, and the text keeps to American spelling (see the memory note on
 * that; the frozen HA entity ids are not in here).
 */
import { register } from 'node:module';
import { readFileSync } from 'node:fs';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { dirname, join } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const root = dirname(here);
const libRoot = pathToFileURL(join(root, 'app', 'src', 'lib') + '/').href;
register('./lib_alias_hook.mjs', import.meta.url, { data: { libRoot } });

const { SECTIONS, TOPICS, BY_ID, PLACES, search, runs, faultTopic, TUNE_TOPIC } = await import(
  '$lib/help.js'
);

let pass = 0;
let fail = 0;
function check(name, cond, extra = '') {
  if (cond) {
    pass += 1;
  } else {
    fail += 1;
    console.log(`  ${name.padEnd(56)} FAIL ${extra}`);
    return;
  }
  console.log(`  ${name.padEnd(56)} PASS ${extra}`);
}

/** All of a topic's text, emphasis markers removed. */
function text(t) {
  const out = [t.title, t.summary];
  for (const b of t.blocks) {
    if (b.p) out.push(b.p);
    if (b.note) out.push(b.note);
    out.push(...(b.steps ?? []), ...(b.list ?? []));
    for (const r of b.rows ?? []) out.push(...r);
  }
  return out.join(' ').replace(/\*\*/g, '');
}

function constant(file, name) {
  const src = readFileSync(join(root, file), 'utf8');
  const m = src.match(new RegExp(`^${name}\\s*=\\s*([0-9.]+)`, 'm'));
  if (!m) throw new Error(`${name} not found in ${file}`);
  return Number(m[1]);
}
const cfg = (n) => constant('config.py', n);
const minutes = (s) => `${s / 60} minutes`;

// --- structure ------------------------------------------------------------
const ids = TOPICS.map((t) => t.id);
check('topic ids are unique', new Set(ids).size === ids.length);
check(
  'every topic belongs to a section',
  TOPICS.every((t) => SECTIONS.some((s) => s.id === t.section))
);
check(
  'every section has topics',
  SECTIONS.every((s) => TOPICS.some((t) => t.section === s.id))
);
const dangling = [];
for (const t of TOPICS)
  for (const b of t.blocks) for (const id of b.see ?? []) if (!BY_ID[id]) dangling.push(`${t.id}→${id}`);
check('every related link resolves', dangling.length === 0, dangling.join(' '));
check(
  'no topic links to itself',
  TOPICS.every((t) => !t.blocks.some((b) => (b.see ?? []).includes(t.id)))
);
check(
  'every Thresholds group has a topic',
  ['Humidity', 'Light', 'Odor', 'Timing'].every((g) => BY_ID[TUNE_TOPIC[g]])
);
// An odd number of markers leaves the rest of a sentence bold.
const unbalanced = TOPICS.filter((t) => JSON.stringify(t.blocks).split('**').length % 2 === 0);
check('every **strong** marker is closed', unbalanced.length === 0, unbalanced.map((t) => t.id).join(' '));

// --- links into the app ---------------------------------------------------
// A place whose anchor is gone would still "work" -- it would switch tab and
// land at the top -- so check the ids against the source they live in.
const page = readFileSync(join(root, 'app', 'src', 'routes', '+page.svelte'), 'utf8');
const wifi = readFileSync(join(root, 'app', 'src', 'lib', 'WifiPanel.svelte'), 'utf8');
const views = [...page.matchAll(/\{ id: '([a-z]+)', label: '[^']+', icon:/g)].map((m) => m[1]);
check('found the page tabs', views.length === 5, views.join(' '));
const dynamicTune = page.includes('id="place-tune-{group.title.toLowerCase()}"');
const anchorExists = (a) =>
  page.includes(`id="${a}"`) ||
  wifi.includes(`id="${a}"`) ||
  (dynamicTune && /^place-tune-(humidity|light|odor|timing)$/.test(a));
for (const [id, p] of Object.entries(PLACES)) {
  check(`place ${id} has a label`, typeof p.label === 'string' && p.label.length > 0);
  check(`place ${id} goes to a tab or Settings`, views.includes(p.view) || p.sheet === 'settings', p.view ?? p.sheet);
  if (p.anchor) check(`place ${id} anchor #${p.anchor} exists`, anchorExists(p.anchor));
}
const badGo = [];
for (const t of TOPICS) for (const g of t.go ?? []) if (!PLACES[g]) badGo.push(`${t.id}→${g}`);
check('every "open in the app" link resolves', badGo.length === 0, badGo.join(' '));
// A topic that sends you to a screen in words should also take you there.
const MENTIONS = [
  [/\bHealth screen\b/, 'health-'],
  [/\bReadings screen\b/, 'readings'],
  [/\bStatus screen\b/, 'status'],
  [/\bunder (Humidity|Timing)\b|\bin Thresholds\b/, 'tune-'],
  [/\bOpen \*\*Settings\*\*|\bIn Settings\b|\bthe Connection settings\b/, 'settings-']
];
const unlinked = [];
for (const t of TOPICS) {
  const body = JSON.stringify(t.blocks);
  for (const [re, prefix] of MENTIONS)
    if (re.test(body) && !(t.go ?? []).some((g) => g.startsWith(prefix))) unlinked.push(`${t.id}:${prefix}`);
}
check('every screen a topic names is linked', unlinked.length === 0, unlinked.join(' '));
check(
  'the Thresholds groups have their anchors',
  Object.values(TUNE_TOPIC).every((tid) => (BY_ID[tid].go ?? []).includes(tid))
);

// --- fault sentences, as check_fan_health() builds them -------------------
const MIN_RPM = cfg('MIN_RPM');
const faults = [
  [`fan stalled (0 rpm), stall 1/3`, 'fix-fault-stall'],
  [`fan stalled (120 rpm), stall 3/3 - LATCHED, press the button to retry`, 'fix-fault-stall'],
  [`fan still running 4100 rpm 13s after being switched off - check the fan's black lead is in row 38, not a rail`, 'fix-fault-stuck'],
  [`fan did not turn at power-on (0 rpm after 6s, needs ${MIN_RPM}) - check the fan, its supply and the tach lead`, 'fix-fault-selftest'],
  ['DHT22 not responding', 'fix-fault-sensor'],
  ['something new the firmware says one day', 'fix-fault'],
  [null, 'fix-fault']
];
for (const [reason, want] of faults)
  check(`fault "${String(reason).slice(0, 30)}…" → ${want}`, faultTopic(reason) === want, faultTopic(reason));

// The firmware's own wording, so a reworded fault fails here and not on a
// wall: every prefix faultTopic() keys on must still be in main.py.
const main = readFileSync(join(root, 'main.py'), 'utf8');
for (const p of ['"fan stalled (', '"fan still running ', '"fan did not turn at power-on', '"DHT22 not responding"'])
  check(`main.py still says ${p}`, main.includes(p));

// --- quoted defaults match the firmware ----------------------------------
function says(id, phrase) {
  check(`${id} says "${phrase}"`, text(BY_ID[id]).includes(phrase), '');
}
says('tune-humidity', `${cfg('RH_RISE_ON')} % by default`);
says('tune-humidity', `${cfg('RH_FALL_OFF')} % by default`);
says('tune-timing', `${minutes(cfg('MIN_RUN_S'))} by default`);
says('tune-timing', `${minutes(cfg('MAX_RUN_S'))} by default`);
says('tune-timing', `${minutes(cfg('POST_LIGHT_S'))} by default`);
says('tune-timing', `${minutes(cfg('BOOST_S'))} by default`);
says('boost', `${minutes(cfg('BOOST_S'))} by default`);
says('button', `${minutes(cfg('BOOST_S'))} by default`);
says('tune-light', `${minutes(cfg('LIGHT_ONLY_MAX_S'))} by default`);
says('button', `Hold ${cfg('LONG_PRESS_S')} seconds`);
says('button', `Hold ${cfg('HOLD_PRESS_S')} seconds`);
says('wifi-change', `${cfg('HOLD_PRESS_S')} seconds`);
says('button', `for ${constant('wifi.py', 'WINDOW_S') / 60} minutes`);
says('wifi-change', `for ${constant('wifi.py', 'WINDOW_S') / 60} minutes`);
says('fix-fault-stall', `waits ${minutes(cfg('FAULT_RETRY_S'))}`);
says('fix-fault-stall', `After ${cfg('MAX_FAN_FAULTS')} stalls`);
says('fix-fault-stuck', `more than ${cfg('STUCK_S')} seconds`);
says('fix-cant-connect', `for ${minutes(cfg('NET_STALL_S'))}`);
says('tune-odor', '0 means odor never starts the fan');
check('VOC_RISE_ON really ships as 0', cfg('VOC_RISE_ON') === 0);

// --- search ---------------------------------------------------------------
const first = (q) => search(q)[0]?.id;
check('empty search finds nothing', search('   ').length === 0);
check('"stalled" finds the stall fault first', first('stalled') === 'fix-fault-stall', first('stalled'));
check('"wifi" finds topics', search('wifi').length >= 3, String(search('wifi').length));
check('"boost" finds Boost first', first('boost') === 'boost', first('boost'));
check('every word must match', search('boost zzzz').length === 0);
check('search is case-blind', first('BOOST') === 'boost');
check('partial words match while typing', search('stal').some((t) => t.id === 'fix-fault-stall'));
check(
  'words match from their start, not inside others',
  !search('stalled').some((t) => t.id === 'fix-more' || t.id === 'updates'),
  search('stalled').map((t) => t.id).join(' ')
);
check('"0 rpm" finds the stall fault', search('0 rpm').some((t) => t.id === 'fix-fault-stall'));
check('"home assistant" finds the HA topic', first('home assistant') === 'ha', first('home assistant'));

// --- emphasis runs --------------------------------------------------------
const r = runs('a **b** c');
check(
  'runs() splits on **',
  r.length === 3 && r[1].strong && r[1].text === 'b' && !r[0].strong && !r[2].strong
);
check('runs() of plain text is one run', runs('plain').length === 1 && !runs('plain')[0].strong);

// --- spelling and rendering ----------------------------------------------
const BRITISH = /\b(colour|odour|behaviour|mould|grey|centre|metre|authoris|organis|recognis|realis|favour|neighbour|programme|licence|tyre)/i;
const brit = TOPICS.filter((t) => BRITISH.test(text(t))).map((t) => `${t.id}:${text(t).match(BRITISH)[0]}`);
check('American spelling throughout', brit.length === 0, brit.join(' '));
const sheet = readFileSync(join(root, 'app', 'src', 'lib', 'HelpSheet.svelte'), 'utf8');
check('HelpSheet renders no {@html}', !sheet.includes('{@html'));

console.log(`\n${pass}/${pass + fail} passed\n`);
process.exit(fail ? 1 : 0);
