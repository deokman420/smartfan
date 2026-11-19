/* Placement of the peak-value label on a chart.

      node tests/test_peaklab.mjs

   A peak near a y-axis tick used to print "88.0" on top of "90". The
   helper is the whole of the decision, so it can be pinned without a
   browser or a fixture of history rows.
*/
import { fileURLToPath, pathToFileURL } from 'node:url';
import { dirname, join } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const root = dirname(here);
const { peakLabLayout } = await import(
  pathToFileURL(join(root, 'app', 'src', 'lib', 'peaklab.js')).href
);

const results = [];
const check = (name, ok, detail = '') => {
  results.push(ok);
  console.log('  %s %s %s', name.padEnd(56), ok ? 'PASS' : 'FAIL', detail);
};

console.log('\npeak label placement');

let p = peakLabLayout(50, 40, [10, 50, 90]);
check('a mid-plot peak stays centered above the dot',
  p.xAlign === 'center' && p.below === false, JSON.stringify(p));

p = peakLabLayout(50, 8, []);
check('a peak near the top of the plot drops below the dot',
  p.xAlign === 'center' && p.below === true, JSON.stringify(p));

p = peakLabLayout(4, 40, [8, 80]);
check('a peak against the left edge sits to the right of the dot',
  p.xAlign === 'end', JSON.stringify(p));

p = peakLabLayout(96, 40, [10, 40, 90]);
check('a peak against the right edge sits to the left of the dot',
  p.xAlign === 'start', JSON.stringify(p));

// The reported case: 7 d humidity, a morning shower at 88 % next to the
// 90 tick, at the left of the plot. 30% and 25% are the y-positions of
// 88 and 90 on a 60..100 axis -- close in Y, but not so close to the
// top that the label would already have flipped.
p = peakLabLayout(5, 30, [0, 25, 50, 75, 100]);
check('88 next to a 90 tick at the left edge moves right and below',
  p.xAlign === 'end' && p.below === true, JSON.stringify(p));

p = peakLabLayout(5, 50, [10, 90]);
check('a left-edge peak far from any tick does not need to drop',
  p.xAlign === 'end' && p.below === false, JSON.stringify(p));

const passed = results.filter(Boolean).length;
console.log('\n%d/%d passed', passed, results.length);
process.exit(passed === results.length ? 0 : 1);
