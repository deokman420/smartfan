/**
 * Light, dark, or whatever the phone is doing.
 *
 * Three settings, two schemes. `system` is the default and is not a third
 * palette: it resolves to one of the other two and keeps resolving, so a
 * phone on a sunset schedule changes the app under you without a reload.
 *
 * The resolution happens HERE rather than in a media query, and the whole
 * light palette in tokens.css hangs off one `[data-theme='light']` selector
 * because of it. The alternative -- a `prefers-color-scheme` block beside
 * the attribute block -- means writing the palette twice and keeping two
 * copies honest forever. An app that cannot run without JavaScript does not
 * need to buy anything with that. (tokens.css says what a consumer WITHOUT
 * JavaScript would have to add; blog.css is the one that would care.)
 *
 * The attribute is also set by an inline script in app.html, before first
 * paint. Without it every load of the light theme starts with a dark flash,
 * because this module is in the bundle and the bundle arrives after the
 * first frame. The two must agree; app.html says so at the top of it.
 */
import { writable, derived, get } from 'svelte/store';

const KEY = 'smartfan.theme';

/** The three things the setting can be. `system` is not a scheme. */
export const CHOICES = ['system', 'light', 'dark'];

function load() {
  // Same defensiveness as link.js: private windows, cleared site data and
  // thumbnailers all make this throw or come back empty, and none of them
  // is a reason to fail to draw the app.
  try {
    const saved = localStorage.getItem(KEY);
    if (CHOICES.includes(saved)) return saved;
  } catch {
    /* fall through */
  }
  return 'system';
}

/** What the user chose: 'system' | 'light' | 'dark'. */
export const theme = writable(load());

const query = () =>
  typeof window !== 'undefined' && window.matchMedia
    ? window.matchMedia('(prefers-color-scheme: light)')
    : null;

/** What that actually resolves to right now: 'light' | 'dark'. */
export const resolved = derived(theme, ($theme, set) => {
  if ($theme !== 'system') {
    set($theme);
    return;
  }
  const mq = query();
  const follow = () => set(mq?.matches ? 'light' : 'dark');
  follow();
  // Only subscribed while the choice IS system, so an explicit light or dark
  // does not quietly keep a listener alive for a signal it ignores.
  mq?.addEventListener?.('change', follow);
  return () => mq?.removeEventListener?.('change', follow);
});

export function setTheme(next) {
  if (!CHOICES.includes(next)) return;
  theme.set(next);
  try {
    localStorage.setItem(KEY, next);
  } catch {
    /* The choice still applies to this session; it just will not survive it. */
  }
}

/**
 * Put the resolved scheme on <html> and keep it there.
 *
 * Returns a teardown. Safe to call before the DOM exists -- it does nothing
 * and hands back a no-op -- because +layout.svelte is the only caller and
 * SvelteKit prerenders it.
 */
export function startTheme() {
  if (typeof document === 'undefined') return () => {};
  return resolved.subscribe((scheme) => {
    document.documentElement.dataset.theme = scheme;
  });
}

/** The current resolved scheme, for code that needs it once and not as a store. */
export function currentScheme() {
  return get(resolved);
}
