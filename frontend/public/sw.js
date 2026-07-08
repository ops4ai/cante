// Minimal service worker: cache static assets (hashed JS/CSS/images) only.
// HTML pages are NEVER cached — they always come from the network so deploys
// take effect immediately without a hard-refresh dance.
const CACHE_VERSION = 'cante-v7';
const SHELL = ['/manifest.webmanifest', '/favicon.svg'];

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
  // Never cache API calls or HTML pages — always network.
  if (url.pathname.startsWith('/v1/') || url.pathname === '/healthz') {
    return;
  }
  // Only cache hashed static assets (JS/CSS/images with content hash in filename).
  const isHashedAsset = /\/assets\/.*-[a-zA-Z0-9]{8,}\.(js|css|png|svg|ico|woff2?)$/.test(url.pathname);
  if (!isHashedAsset && !url.pathname.endsWith('.png') && !url.pathname.endsWith('.svg') && !url.pathname.endsWith('.ico') && !url.pathname.endsWith('.webmanifest')) {
    // HTML pages and other non-hashed requests: network-only, don't cache.
    return;
  }
  // Cache-first for hashed assets (they're immutable — content hash changes on every build).
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
