/* Verifies the contrast claims written into tokens.css, so they stay facts
   rather than comments that were true once.

   Usage: node design-tokens/check-contrast.mjs
   Exits non-zero if any text token drops below its target on a surface it
   is actually rendered on, in either scheme.

   Both schemes, separately. Scanning the file as one flat list of tokens --
   which this did while there was only one scheme -- now silently scores the
   light --text against the dark --bg, because a later declaration wins the
   regex and the two palettes share every name. Each :root block is read on
   its own instead, and light inherits from dark exactly as the cascade does:
   a token the light block does not override IS the dark value at runtime, so
   it has to be measured against the light surfaces it will actually land on.
   That inheritance is the check that earns its keep -- it is what catches a
   token added to dark and forgotten in light. */

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const css = readFileSync(join(here, 'tokens.css'), 'utf8');

/** The hex tokens declared inside one `:root...{ }` block. */
function block(selector) {
  const at = css.indexOf(selector + ' {');
  if (at < 0) return null;
  const body = css.slice(at, css.indexOf('\n}', at));
  return Object.fromEntries(
    [...body.matchAll(/^\s*(--[a-z0-9-]+):\s*(#[0-9a-fA-F]{6})\s*;/gm)].map((m) => [m[1], m[2]])
  );
}

const dark = block(':root');
const lightOverrides = block(":root[data-theme='light']");
if (!dark || !lightOverrides) {
  console.log('tokens.css is missing a :root or :root[data-theme=\'light\'] block');
  process.exit(1);
}
const light = { ...dark, ...lightOverrides };

const SCHEMES = [
  ['dark', dark, null],
  ['light', light, lightOverrides]
];

const channels = (hex) => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255);
const luminance = (hex) => {
  const [r, g, b] = channels(hex).map((v) =>
    v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4
  );
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
};
const ratio = (fg, bg) => {
  const [a, b] = [luminance(fg), luminance(bg)];
  return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
};

/* Each text token, the floor it must clear, and the surfaces it actually
   lands on in this app. Surfaces are per token rather than one blanket set
   because they genuinely differ: --bg-card-2 backs only the form fields
   and the chart range buttons, where the only text is --text and
   --text-dim, and --bg-card-hover exists only under a pointer on .stat,
   where none of these tones is rendered. Widen a token's list when you use
   it somewhere new, and let this script tell you whether you may.

   Floors: 7 is AAA for normal text; 4.5 is AAA for large text (>=18.66px
   bold or >=24px), which the hero gauge reading and state line are. */
const TEXT = [
  ['--text', 7, ['--bg', '--bg-card', '--bg-card-2']],
  ['--text-dim', 7, ['--bg', '--bg-card', '--bg-card-2']],
  ['--accent', 7, ['--bg', '--bg-card']],
  ['--bad-text', 7, ['--bg', '--bg-card']],
  ['--warn-text', 7, ['--bg', '--bg-card']],
  ['--good-text', 7, ['--bg', '--bg-card']]
];

/* The fill sense of the semantic trio: gauge bands, legend dots, the Nav
   alert. Graphics, so the floor is WCAG's 3:1 for non-text, and they are
   checked rather than merely listed as fill-only because a band nobody can
   see against the card is as broken as unreadable text.

   --good and --warn used to sit in TEXT at 7:1, which the dark scheme met
   by luck and the light scheme could only meet by going muddy. They are
   fills now, and .tone-ok / .tone-warn take --good-text / --warn-text. */
const GRAPHIC = [
  ['--good', 3, ['--bg', '--bg-card']],
  ['--warn', 3, ['--bg', '--bg-card']],
  ['--bad', 3, ['--bg', '--bg-card']]
];

/* Tokens that must never be used as text, listed so a future change that
   promotes one is caught in review. */
const FILL_ONLY = ['--blue', '--purple', '--text-muted'];

let failed = 0;
const pad = (s, n) => String(s).padEnd(n);

for (const [scheme, tokens, overrides] of SCHEMES) {
  console.log(`\n── ${scheme} ${'─'.repeat(58)}`);
  console.log(`${pad('token', 13)}${pad('floor', 7)}measured on its own surfaces`);
  // Text first, then the graphic trio. The floor column is what tells them
  // apart: a 3.0 there is a band or a dot, never a text token that slipped.
  for (const [token, floor, surfaces] of [...TEXT, ...GRAPHIC]) {
    const fg = tokens[token];
    if (!fg) {
      console.log(`${pad(token, 13)}MISSING from tokens.css`);
      failed++;
      continue;
    }
    // A token the light block leaves alone is the dark value, which is
    // almost never right on a light surface. Say so rather than letting a
    // passing ratio imply it was considered.
    const inherited = overrides && !(token in overrides);
    const cells = surfaces.map((surface) => {
      const r = ratio(fg, tokens[surface]);
      if (r < floor) failed++;
      return `${surface} ${r.toFixed(2)}${r < floor ? ' FAIL' : ''}`;
    });
    console.log(
      `${pad(token, 13)}${pad(floor.toFixed(1), 7)}${cells.join('   ')}` +
        (inherited ? '   <- inherited from dark' : '')
    );
  }

  for (const token of FILL_ONLY) {
    if (!tokens[token]) {
      console.log(`  ${token} MISSING from tokens.css`);
      failed++;
    }
  }
}

/* Every surface a text token is measured against must itself be declared in
   the light block. Inheriting a SURFACE is the failure that makes every
   ratio above meaningless -- dark text on a surface that is still #0c0f16 --
   so it is an error rather than a note. */
const surfaces = [...new Set([...TEXT, ...GRAPHIC].flatMap(([, , s]) => s))];
for (const surface of surfaces) {
  if (!(surface in lightOverrides)) {
    console.log(`\n${surface} is not declared in the light scheme`);
    failed++;
  }
}

console.log(`\nfill-only, never text: ${FILL_ONLY.join(', ')}`);
console.log(
  failed
    ? `\n${failed} problem(s).`
    : '\nEvery text token clears its floor on every surface it lands on, ' +
        'in both schemes.'
);
process.exit(failed ? 1 : 0);
