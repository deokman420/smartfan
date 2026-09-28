/**
 * Cache the shell so the app opens without the network, then get out of
 * the way. Live data never comes from here -- it arrives from the fan, and
 * a cached reading would be a lie with a timestamp on it.
 *
 * Three rules, and the order matters:
 *
 * 1. **The API is never touched.** `/api/*` is the live state of a fan;
 *    there is no version of "a cached one" that is not a lie. It is excluded
 *    explicitly rather than relying on it never landing in the cache.
 *
 * 2. **Navigations go to the network first**, falling back to the cache.
 *    Cache-first was the obvious choice and the wrong one here: the app is
 *    installed to a home screen and served by a device on the same LAN, so
 *    the network is a few milliseconds away and always the better answer.
 *    Cache-first meant a push to the fan's flash showed up on the phone one
 *    launch late -- the old shell was served from cache while the new worker
 *    installed behind it, and only the launch after that got the new app.
 *    That is a confusing thing to hand someone who just updated their fan.
 *
 * 3. **Hashed assets stay cache-first.** Everything under _app/immutable is
 *    named by its own content, so a hit is always correct and a miss is
 *    always a real fetch. This is what makes the app open instantly and work
 *    with the wifi off.
 */
import { build, files, version } from '$service-worker';

const CACHE = `smartfan-${version}`;
const SHELL = [...build, ...files];

self.addEventListener('install', (event) => {
  event.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const { request } = event;
  if (request.method !== 'GET') return;

  const url = new URL(request.url);
  if (url.origin !== location.origin) return;
  if (url.pathname.startsWith('/api/')) return; // live state; never cached

  // A navigation: prefer the fan, fall back to what we have. The timeout
  // matters because "offline" on wifi is often not a refused connection but
  // a socket that hangs, and an app that takes thirty seconds to open is
  // indistinguishable from a broken one.
  if (request.mode === 'navigate') {
    event.respondWith(
      Promise.race([
        fetch(request),
        new Promise((_, reject) => setTimeout(() => reject(new Error('slow')), 2500))
      ])
        .then((res) => {
          const copy = res.clone();
          caches.open(CACHE).then((c) => c.put(request, copy)).catch(() => {});
          return res;
        })
        .catch(() => caches.match(request).then((hit) => hit || caches.match('./index.html')))
    );
    return;
  }

  event.respondWith(
    caches.match(request).then((hit) => {
      if (hit) return hit;
      return fetch(request).catch(() =>
        // A navigation that misses the cache offline still has to land
        // somewhere, or the app looks broken rather than disconnected.
        request.mode === 'navigate' ? caches.match('./index.html') : Response.error()
      );
    })
  );
});
