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

/** One PNG data URL per rotation, pre-rendered so a frame costs an assignment. */
function renderFrames(glyph) {
  const canvas = document.createElement('canvas');
  canvas.width = canvas.height = SIZE;
  const ctx = canvas.getContext('2d');
  if (!ctx) return null;

  const mid = SIZE / 2;
  const out = [];
  for (let i = 0; i < FRAMES; i++) {
    ctx.clearRect(0, 0, SIZE, SIZE);

    ctx.fillStyle = glyph.fill;
    ctx.beginPath();
    ctx.roundRect(0, 0, SIZE, SIZE, glyph.rx * glyph.scale);
    ctx.fill();

    ctx.save();
    ctx.translate(mid, mid);
    ctx.rotate((i / FRAMES) * 2 * Math.PI);
    ctx.drawImage(glyph.img, -mid, -mid, SIZE, SIZE);
    ctx.restore();

    out.push(canvas.toDataURL('image/png'));
  }
  return out;
}

/**
 * Start following `fanOn`, a readable store of whether the fan is running.
 *
 * Returns a teardown that stops the animation and puts both surfaces back the
 * way app.html shipped them.
 */
export function watchAppIcon(fanOn) {
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

  let frames = null;
  let timer = null;
  let frame = 0;
  let running = false;
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

  const stopSpin = () => {
    if (timer !== null) clearInterval(timer);
    timer = null;
    if (iconLink && staticIcon) iconLink.href = staticIcon;
  };

  const startSpin = () => {
    if (timer !== null || !frames || !iconLink) return;
    timer = setInterval(() => {
      frame = (frame + 1) % frames.length;
      iconLink.href = frames[frame];
    }, FRAME_MS);
  };

  // Spin only while there is an eye on it. A favicon swap repaints the tab,
  // and doing that forever in a background tab is a real cost for a 32px
  // decoration on a page nobody has in front of them.
  const sync = () => {
    const show = running && !dead && document.visibilityState === 'visible' && !calm?.matches;
    if (show) startSpin();
    else stopSpin();
  };

  if (staticIcon) {
    loadGlyph(staticIcon)
      .then((glyph) => {
        if (dead || !glyph) return;
        frames = renderFrames(glyph);
        sync();
      })
      .catch(() => {
        /* No animated favicon this session. The static one is already right. */
      });
  }

  const unsubscribe = fanOn.subscribe((on) => {
    running = !!on;
    paintTheme(running);
    sync();
  });

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
    themeWatcher?.disconnect();
    document.removeEventListener('visibilitychange', sync);
    calm?.removeEventListener?.('change', sync);
    stopSpin();
    if (themeMeta && shippedTheme !== null) themeMeta.content = shippedTheme;
    delete root.dataset.fan;
  };
}
