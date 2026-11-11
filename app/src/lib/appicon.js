/**
 * The fan, spinning outside the page.
 *
 * The header mark already spins while the fan runs (`.mark.spin` in
 * +page.svelte). This does the same thing on the two surfaces that survive
 * the app not being the thing you are looking at:
 *
 *   favicon      a rotating tab icon, for the desktop browser
 *   theme-color  the phone's status-bar tint, for the home-screen install
 *
 * The favicon also carries the board's status LED in its corner -- the same
 * green/red blink codes the enclosure shows, plus a red double-blink when the
 * app wants a look. See statusled.js. That part keeps showing in a background tab,
 * which the spin deliberately does not.
 *
 * The two are deliberately not the same signal twice. A `display: standalone`
 * install has no tab, so the favicon is invisible there; a desktop tab has no
 * status bar. Each platform gets the one that exists on it.
 *
 * What is NOT possible, so nobody goes looking: the home-screen icon itself.
 * apple-touch-icon.png and the manifest icons are rasterised and cached by
 * the launcher when the app is added -- iOS into SpringBoard, Android into
 * the launcher database -- and no API reaches them. Animating an SVG favicon
 * is equally out: Chrome and Safari render favicons as still images and run
 * neither SMIL nor CSS inside one, which is why this rasterises frames by
 * hand.
 *
 * Nothing here is allowed to matter. Every step is guarded and simply gives
 * up on failure, leaving the static icon and the shipped theme-color in
 * place: this is decoration on an app whose job is to be right about a fan.
 */

import { BEAT_MS, CODES, LED_COLORS } from '$lib/statusled.js';

/** Frames around a full turn. 18 at 75 ms is the 1.35 s of `.mark.spin`. */
const FRAMES = 18;
const FRAME_MS = 75;

/** Rasterised big enough to survive a 2x tab strip, small enough to be free. */
const SIZE = 64;

/**
 * Peel the background plate off icon.svg and hand back the glyph alone.
 *
 * static/icon.svg is the one source for this artwork -- tools/make_icons.py
 * renders the PNGs from it too -- so the geometry is read rather than copied.
 * The <rect> has to go: it is the rounded plate, and a rotating rounded
 * square is not the effect we are after. The plate is painted per frame
 * instead, from the same file's own values.
 *
 * The fetch is one request to the fan that served the page, and the browser's
 * ordinary HTTP cache serves it thereafter. Nothing leaves the LAN.
 */
async function loadGlyph(href) {
  const svg = await (await fetch(href, { cache: 'force-cache' })).text();

  const plate = /<rect\b[^>]*>/.exec(svg);
  if (!plate) return null;
  const attr = (name) => {
    const m = new RegExp(`${name}="([^"]*)"`).exec(plate[0]);
    return m ? m[1] : null;
  };
  const box = /viewBox="0 0 (\d+) \d+"/.exec(svg);
  const fill = attr('fill');
  const rx = Number(attr('rx'));
  if (!box || !fill || !Number.isFinite(rx)) return null;

  const img = new Image();
  img.src =
    'data:image/svg+xml;charset=utf-8,' +
    encodeURIComponent(svg.replace(plate[0], ''));
  await img.decode();

  return { img, fill, scale: SIZE / Number(box[1]), rx };
}

/**
 * The status LED: bottom-right, where tab-strip badges conventionally sit.
 * Ringed in the plate color so a blade tip turning under it cannot merge
 * with it. An unlit LED is drawn dark rather than left out, so a blink reads
 * as a lamp going on and off and not as a badge popping in and out.
 */
const LED = { x: 50, y: 50, ring: 13, r: 10 };
const LED_OFF = '#262b36';

function drawLed(ctx, plate, color) {
  ctx.fillStyle = plate;
  ctx.beginPath();
  ctx.arc(LED.x, LED.y, LED.ring, 0, 2 * Math.PI);
  ctx.fill();

  ctx.fillStyle = color ? LED_COLORS[color] : LED_OFF;
  ctx.beginPath();
  ctx.arc(LED.x, LED.y, LED.r, 0, 2 * Math.PI);
  ctx.fill();

  if (color) {
    // The specular dot that makes a colored circle read as a lens.
    ctx.fillStyle = 'rgba(255, 255, 255, 0.45)';
    ctx.beginPath();
    ctx.arc(LED.x - 3, LED.y - 3, 3, 0, 2 * Math.PI);
    ctx.fill();
  }
}

/**
 * One PNG data URL per (rotation, LED) pair, drawn on first use and kept, so
 * every frame after the first costs a Map lookup and an assignment. The
 * whole space is 18 rotations x 4 LED states, about 70 small PNGs at most.
 *
 * `led` is undefined for no LED at all, null for an unlit one, or a color.
 */
function frameCache(glyph) {
  const canvas = document.createElement('canvas');
  canvas.width = canvas.height = SIZE;
  const ctx = canvas.getContext('2d');
  if (!ctx) return null;

  const mid = SIZE / 2;
  const cache = new Map();
  return (rot, led) => {
    const key = `${rot}|${led === undefined ? '-' : (led ?? 'off')}`;
    let url = cache.get(key);
    if (url) return url;

    ctx.clearRect(0, 0, SIZE, SIZE);

    ctx.fillStyle = glyph.fill;
    ctx.beginPath();
    ctx.roundRect(0, 0, SIZE, SIZE, glyph.rx * glyph.scale);
    ctx.fill();

    ctx.save();
    ctx.translate(mid, mid);
    ctx.rotate((rot / FRAMES) * 2 * Math.PI);
    ctx.drawImage(glyph.img, -mid, -mid, SIZE, SIZE);
    ctx.restore();

    if (led !== undefined) drawLed(ctx, glyph.fill, led);

    url = canvas.toDataURL('image/png');
    cache.set(key, url);
    return url;
  };
}

/**
 * Start following `fanOn`, a readable store of whether the fan is running,
 * and optionally `led`, a readable store of a statusled.js code (or null).
 *
 * Returns a teardown that stops the animation and puts both surfaces back the
 * way app.html shipped them.
 */
export function watchAppIcon(fanOn, led = null) {
  if (typeof document === 'undefined') return () => {};

  const iconLink = document.querySelector('link[rel="icon"]');
  const themeMeta = document.querySelector('meta[name="theme-color"]');
  const root = document.documentElement;

  // Read back what the tag and the tokens already say rather than restating
  // either. tokens.css is canonical for the colors; app.html is canonical
  // for the icon's path, which is %sveltekit.assets%-relative and not
  // something this module should think it knows.
  const staticIcon = iconLink?.href ?? null;
  const shippedTheme = themeMeta?.content ?? null;

  // Read per paint, not once at startup. --bar and --bar-active are declared
  // in both schemes, so caching them meant the status bar kept the colors of
  // whichever scheme happened to be up when the app booted -- an app opened
  // in the dark and still open at sunrise would tint the light theme's status
  // bar near-black. Two getPropertyValue calls on a state change is nothing.
  const barColor = (name, fallback) =>
    getComputedStyle(root).getPropertyValue(name).trim() || fallback;

  // Honoured rather than worked around: app.css already flattens every
  // animation under this query, and a favicon nobody asked to move is worse
  // than one on the page, because it moves in a tab you are not looking at.
  const calm = window.matchMedia?.('(prefers-reduced-motion: reduce)');

  let frameFor = null;     // (rotation, led) -> data URL, once the glyph loads
  let spinTimer = null;
  let beatTimer = null;
  let rot = 0;
  let beat = 0;
  let running = false;
  let code = null;         // a key of CODES, or null for no LED
  let shown = null;        // the href last written, so a no-op is free
  let dead = false;

  const paintTheme = (on) => {
    const bar = barColor('--bar', shippedTheme);
    if (themeMeta) themeMeta.content = on ? barColor('--bar-active', bar) : bar;
    // iOS standalone ignores theme-color under a black-translucent status
    // bar, where the page itself is what shows through. app.css keys the
    // backdrop off this so that install gets the signal too.
    if (on) root.dataset.fan = 'on';
    else delete root.dataset.fan;
  };

  const setHref = (href) => {
    if (!iconLink || !href || href === shown) return;
    iconLink.href = shown = href;
  };

  // Spin only while there is an eye on it. A favicon swap repaints the tab,
  // and doing that forever in a background tab is a real cost for a 32px
  // decoration on a page nobody has in front of them.
  const spinning = () =>
    running && !dead && document.visibilityState === 'visible' && !calm?.matches;

  // The LED is the opposite case: a background tab is exactly where it earns
  // its place, since the tab strip is how you see it from another tab. So it
  // blinks hidden too -- throttled to a beat a second by the browser, which
  // costs one Map lookup a second, and only for a code that blinks at all.
  // Under reduced motion it holds the code's steady color instead.
  const beats = () => {
    const c = !dead && code ? CODES[code] : null;
    if (!c) return null;
    return calm?.matches ? [c.steady] : c.beats;
  };

  const paint = () => {
    const b = beats();
    const spin = spinning();
    if (!frameFor || (!spin && !b)) {
      setHref(staticIcon);
      return;
    }
    setHref(frameFor(spin ? rot : 0, b ? b[beat % b.length] : undefined));
  };

  const stop = (t) => {
    if (t !== null) clearInterval(t);
    return null;
  };

  const sync = () => {
    if (spinning() && frameFor) {
      spinTimer ??= setInterval(() => {
        rot = (rot + 1) % FRAMES;
        paint();
      }, FRAME_MS);
    } else {
      spinTimer = stop(spinTimer);
    }

    const b = beats();
    if (b && b.length > 1 && frameFor) {
      beatTimer ??= setInterval(() => {
        beat += 1;           // paint() takes it modulo the current pattern
        paint();
      }, BEAT_MS);
    } else {
      beatTimer = stop(beatTimer);
    }
    paint();
  };

  if (staticIcon) {
    loadGlyph(staticIcon)
      .then((glyph) => {
        if (dead || !glyph) return;
        frameFor = frameCache(glyph);
        sync();
      })
      .catch(() => {
        /* No drawn favicon this session. The static one is already right. */
      });
  }

  const unsubscribe = fanOn.subscribe((on) => {
    running = !!on;
    paintTheme(running);
    sync();
  });

  // A new code restarts on its own first beat, and gets a fresh timer: the
  // beat interval closes over the old pattern's length.
  const unsubscribeLed = led
    ? led.subscribe((next) => {
        const valid = next && CODES[next] ? next : null;
        if (valid === code) return;
        code = valid;
        beat = 0;
        sync();
      })
    : () => {};

  // The status bar has to follow the scheme as well as the fan, and the fan
  // is the only thing that was repainting it: switching to light with the fan
  // idle left a near-black tint over a white app until the fan next changed
  // state, which on a quiet afternoon is hours. Watching the attribute rather
  // than subscribing to theme.js keeps this module's only input the one store
  // it is handed, and picks up the pre-paint write from app.html too.
  const themeWatcher =
    typeof MutationObserver === 'undefined'
      ? null
      : new MutationObserver(() => paintTheme(running));
  themeWatcher?.observe(root, { attributes: true, attributeFilter: ['data-theme'] });

  document.addEventListener('visibilitychange', sync);
  calm?.addEventListener?.('change', sync);

  return () => {
    dead = true;
    unsubscribe();
    unsubscribeLed();
    themeWatcher?.disconnect();
    document.removeEventListener('visibilitychange', sync);
    calm?.removeEventListener?.('change', sync);
    spinTimer = stop(spinTimer);
    beatTimer = stop(beatTimer);
    setHref(staticIcon);
    if (themeMeta && shippedTheme !== null) themeMeta.content = shippedTheme;
    delete root.dataset.fan;
  };
}
