// Friday Live service worker — keeps the PWA installable and allows
// showing a persistent "voice session active" notification while the
// phone is in a pocket / screen is off.

const CACHE_NAME = 'friday-live-v1';
const CORE_ASSETS = [
  '/friday-live',
  '/friday-live/manifest.json',
];

self.addEventListener('install', event => {
  self.skipWaiting();
  event.waitUntil(
    caches.open(CACHE_NAME).then(cache => cache.addAll(CORE_ASSETS).catch(() => {}))
  );
});

self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

// Network-first for the HTML shell, cache-fallback — so we keep working
// offline briefly if connectivity drops mid-drive.
self.addEventListener('fetch', event => {
  const req = event.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;

  // WebSocket upgrade requests must not be intercepted.
  if (url.pathname.startsWith('/ws/')) return;

  // Don't cache API calls.
  if (url.pathname.startsWith('/api/')) return;

  if (url.pathname === '/friday-live' || url.pathname === '/friday-live/' ||
      url.pathname === '/friday-live/manifest.json') {
    event.respondWith(
      fetch(req).then(res => {
        const copy = res.clone();
        caches.open(CACHE_NAME).then(cache => cache.put(req, copy)).catch(() => {});
        return res;
      }).catch(() => caches.match(req))
    );
  }
});

// Focus the existing Friday Live client when the persistent notification
// is tapped, rather than opening a fresh tab.
self.addEventListener('notificationclick', event => {
  event.notification.close();
  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then(list => {
      for (const c of list) {
        if (c.url.includes('/friday-live') || c.url.endsWith('/')) {
          return c.focus();
        }
      }
      return self.clients.openWindow('/friday-live');
    })
  );
});

// Messages from the page — used for keep-alive pings and state updates.
self.addEventListener('message', event => {
  const msg = event.data || {};
  if (msg.type === 'ping') {
    // no-op; existence of this listener keeps the SW briefly alive
  }
});
