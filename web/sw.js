// Cache only the UI shell; /api always goes to the network, analysis results never enter the cache
const CACHE = 'goodglow-shell-v150';   // v91: finalized makeup card (10-19); v92: three-stage flow + vitiligo woman (10-20); v93: vitiligo cover replaced with 20551205 + page-flip effect (10-21); v94: page flip slowed by 0.8 s; v95: official-site style step 1 (color, type, corner radius, shadow); v96: step 2 two-column first screen + large numbers; v97: shade page copy changed to the user's perspective; v99: capture helper (v98 reserved for the vitiligo line)
const VENDOR = 'goodglow-vendor-konva10.6.0-cv4.12.0-ort1.30.0';   // Third-party libraries: version pinned, separate cache, not cleared when the app is updated (the YuNet model is also stored here automatically on first use; not renamed, to avoid re-downloading the already stored 25 MB)
const SHELL = ['/', '/index.html', '/style.css', '/app.js', '/face_auto.js', '/camouflage.js', '/camo_editor.js', '/makeup_layers.js', '/face_check.js', '/shade_preview.js', '/capture_guide.js', '/i18n.js', '/i18n/en.js', '/vendor_loader.js', '/manifest.webmanifest', '/icon.svg',
  '/img/cover_albinism.jpg', '/img/cover_vitiligo.jpg'];

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (e) => {
  e.waitUntil(caches.keys()
    .then((keys) => Promise.all(keys.filter((k) => k !== CACHE && k !== VENDOR).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener('fetch', (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== 'GET' || url.origin !== location.origin || url.pathname.startsWith('/api/')) return;
  if (url.pathname.startsWith('/vendor/')) {
    // Third-party libraries: cache first (version pinned, large files), downloaded only on first use
    e.respondWith(caches.open(VENDOR).then((c) => c.match(e.request).then((hit) => hit || fetch(e.request).then((res) => {
      if (res.ok) c.put(e.request, res.clone());
      return res;
    }))));
    return;
  }
  // Network first, use the cache only when offline
  e.respondWith(fetch(e.request)
    .then((res) => {
      if (res.ok) {
        const copy = res.clone();
        caches.open(CACHE).then((c) => c.put(e.request, copy));
      }
      return res;
    })
    .catch(() => caches.match(e.request).then((r) => r || caches.match('/index.html'))));
});
