/**
 * Cache the shell so the app opens without the network, then get out of
 * the way. Live data never comes from here -- it arrives over MQTT, and a
 * cached reading would be a lie with a timestamp on it.
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
