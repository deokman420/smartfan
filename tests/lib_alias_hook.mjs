/* Resolve SvelteKit's `$lib/` alias for plain `node --test` runs.
 *
 * The app's modules import each other as `$lib/history.js`, which Vite
 * resolves and Node does not. Rewriting those imports to relative paths
 * just to make them testable would be the test dictating the source, so
 * the alias is taught to the loader instead -- which also means any future
 * test can import any `$lib/` module without touching it.
 */
let libRoot;

export async function initialize(data) {
  libRoot = data.libRoot;
}

export async function resolve(specifier, context, next) {
  if (specifier.startsWith('$lib/')) {
    return next(new URL(specifier.slice(5), libRoot).href, context);
  }
  return next(specifier, context);
}
