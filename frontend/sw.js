// Ambientika Local – Service Worker v3.1
const CACHE = 'ambientika-local-v3';

// Core assets (müssen vorhanden sein)
const CORE_ASSETS = [
  '/',
  '/index.html',
  '/manifest.json'
];

// Optionale Assets (werden gecacht wenn vorhanden, kein Fehler wenn nicht)
const OPT_ASSETS = [
  '/icons/icon-192.png',
  '/icons/icon-512.png'
];

// Install: pre-cache core assets, optionale ignorieren bei Fehler
self.addEventListener('install', e => {
  e.waitUntil(
    caches.open(CACHE).then(async c => {
      await c.addAll(CORE_ASSETS);
      // Optionale Assets einzeln cachen, Fehler ignorieren
      await Promise.allSettled(OPT_ASSETS.map(url =>
        fetch(url).then(r => r.ok ? c.put(url, r) : null).catch(() => null)
      ));
    }).then(() => self.skipWaiting())
  );
});

// Activate: remove old caches
self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

// Fetch: network-first for API/WS, cache-first for shell
self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);

  // Always fetch API and WebSocket live
  if (url.pathname.startsWith('/api') || url.pathname.startsWith('/ws')) {
    return;
  }

  // Network-first with cache fallback for app shell
  e.respondWith(
    fetch(e.request)
      .then(res => {
        if (res && res.status === 200 && e.request.method === 'GET') {
          const clone = res.clone();
          caches.open(CACHE).then(c => c.put(e.request, clone));
        }
        return res;
      })
      .catch(() => caches.match(e.request).then(cached => cached || caches.match('/index.html')))
  );
});
