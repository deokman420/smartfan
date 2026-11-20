// Build the public demo: the real app, talking to a fan simulated in the
// browser (src/lib/transport/demo.js) instead of a real one.
//
//   npm run build:demo   ->  build-demo/   (served at smartfan.phbeks.com/demo/)
//
// Then scan the output for anything that should not be on a public site,
// and fail the build if it finds any. The fixture was sanitized when it was
// captured; this is the check that it stayed that way.
import { spawnSync } from 'node:child_process';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join } from 'node:path';

const OUT = 'build-demo';
const env = { ...process.env, VITE_DEMO: '1', PUBLIC_DEMO: '1', SMARTFAN_BUILD_OUT: OUT };
const r = spawnSync('npx', ['vite', 'build'], { stdio: 'inherit', env, shell: true });
if (r.status !== 0) process.exit(r.status ?? 1);

// Private address ranges, mail addresses, and the words credentials travel
// under. The app's own source legitimately says "password" in UI copy and
// comments, so those words are checked only in the fixture-carrying chunk.
const EVERYWHERE = [/192\.168\.\d/, /\b10\.\d+\.\d+\.\d+\b/, /\b172\.(1[6-9]|2\d|3[01])\.\d+\.\d+\b/,
  /[\w.+-]+@[\w-]+\.(com|net|org|nl|io)\b/i];

function* files(dir) {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) yield* files(p);
    else yield p;
  }
}

const hits = [];
for (const f of files(OUT)) {
  if (!/\.(js|html|json|css|webmanifest|svg|txt)$/.test(f)) continue;
  const text = readFileSync(f, 'utf8');
  for (const re of EVERYWHERE) {
    const m = text.match(re);
    if (m) hits.push(`${f}: ${m[0]}`);
  }
}
if (hits.length) {
  console.error('\nbuild-demo: refusing, private-looking strings in the output:');
  for (const h of hits) console.error('  ' + h);
  process.exit(1);
}
console.log(`\nbuild-demo: ${OUT}/ is clean.`);
