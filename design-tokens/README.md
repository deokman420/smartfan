# design-tokens

`tokens.css` is the only place in this repository that may declare a color,
typeface, radius or shadow literal. Everything else uses `var(--token)`.

## Why it exists

The palette is phbeks.com's. It used to live in two copies — one inlined in
`app/src/app.css`, one in the site's `blog/blog.css` — with nothing keeping
them honest, which is how a design system quietly becomes two design
systems. phbeks-site *names* a canonical `design-tokens/tokens.css` but does
not ship one, so `blog/blog.css` is the live artifact and the values here
were transcribed from it.

## The three blocks

`tokens.css` is split deliberately:

- **Brand** — byte-for-byte from `blog.css`. Don't tune these to taste. If
  the site's palette moves, re-transcribe and note the date.
- **Project** — what this app needs on top, plus the one deliberate
  divergence (`--text-dim`). Every entry carries its reason and its measured
  contrast, so a divergence is visible rather than accidental.
- **Light** — the light scheme, under `:root[data-theme='light']`.

## The light scheme

It was **authored here, not transcribed**: `blog.css` is dark-only — no
light values, no `prefers-color-scheme` block — so there was nothing
upstream to copy. That makes this the light half of the design system
rather than a Smartfan-local theme, and it is written to be lifted into
`blog.css` unchanged.

It is a palette, not an inversion. Every hue is the brand hue at the same H
and S, walked *down* in lightness until it clears its floor on the light
surfaces, so the family survives the switch. The visible cost: AAA teal on
white is dark. `--accent` goes `#00d4aa` → `#00614e`, because 7:1 against
white admits nothing brighter. Light mode is a quieter thing than dark mode
here, and that is arithmetic rather than taste.

**One caveat on lifting it.** The scheme is applied by an explicit
`data-theme` attribute on `<html>`, never by the media query alone, so the
palette is written once and `system` is the same code path as an override.
`app/src/lib/theme.js` resolves it, and an inline script in `app.html` sets
it before first paint. A consumer without JavaScript — which `blog.css`
would be — needs the block repeated under
`@media (prefers-color-scheme: light) { :root:not([data-theme='dark']) }`.
That is the one thing that does not lift across unchanged.

## Contrast

Target is WCAG AAA: 7:1 normal text, 4.5:1 large, and WCAG's 3:1 for
graphics. The checker re-measures every token against the surfaces it is
actually rendered on, **in both schemes**:

```
node design-tokens/check-contrast.mjs     # or: npm run check:tokens, from app/
```

It exits non-zero on a failure. The surface list is per token, not global —
`--bg-card-2` backs only the form fields and the chart range buttons, and
`--bg-card-hover` only appears under a pointer on `.stat`. If you use a tone
somewhere new, widen its list in the script and let it tell you whether the
color is allowed there.

It also reports when the light block *inherits* a token from dark rather
than overriding it, and fails outright if a surface does — dark text on a
surface still at `#0c0f16` makes every other ratio meaningless.

Three tokens are fill-only and must never carry text: `--blue`, `--purple`
and `--text-muted`.

The semantic trio is **two senses, two tokens**. `--good` / `--warn` /
`--bad` are the fill sense — gauge bands, legend dots, the Nav alert — at
the 3:1 graphic floor. `--good-text` / `--warn-text` / `--bad-text` are the
text sense at 7:1, and the `.tone-*` classes use only those.

Dark got away with one value for years because a near-black page lets a
vivid color clear 7:1 by accident. Light cannot: walking all three to 7:1
against white produced a muddy brown gauge band, and leaving them vivid
made them unreadable as text. `--bad`/`--bad-text` already worked this way;
the light scheme is what forced the other two to follow.

## Consuming it

`app/src/app.css` imports it directly:

```css
@import '../../design-tokens/tokens.css';
```

Vite inlines it at build time. The import reaches above the Vite root, so
`app/vite.config.js` widens `server.fs.allow` for the dev server.

The standalone pages at the repo root — `build-sheet.html`,
`broker-setup.html`, `troubleshoot.html` — are **not** on these tokens. They
share a separate workshop palette (IBM Plex, tan accent) that is itself
duplicated three ways. Folding those into a second token set is a job of its
own.
