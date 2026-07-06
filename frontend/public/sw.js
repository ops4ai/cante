// Minimal service worker: cache the app shell so the backoffice is usable
// offline / installable on phones. Network-first for /v1 (API), cache-first
// for static assets. Bump CACHE_VERSION on deploy to refresh the shell.
const CACHE_VERSION = 'cante-v3';
const SHELL = ['/', '/index.html', '/manifest.webmanifest', '/favicon.svg'];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_VERSION).then((cache) => cache.addAll(SHELL)).then(() => self.skipWaiting()),
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE_VERSION).map((k) => caches.delete(k))),
    ).then(() => self.clients.claim()),
  );
});

self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);
  // Never cache the API — always go to network (the live data is the point).
  if (url.pathname.startsWith('/v1/') || url.pathname === '/healthz') {
    return;
  }
  // Cache-first for everything else (static assets, hashed JS/CSS).
  event.respondWith(
    caches.match(event.request).then(
      (cached) =>
        cached ||
        fetch(event.request).then((resp) => {
          if (resp.ok && event.request.method === 'GET') {
            const copy = resp.clone();
            caches.open(CACHE_VERSION).then((cache) => cache.put(event.request, copy));
          }
          return resp;
        }).catch(() => cached),
    ),
  );
});
