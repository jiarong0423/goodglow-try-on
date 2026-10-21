// 只快取介面外殼；/api 一律走網路，分析結果不進快取
const CACHE = 'goodglow-shell-v134';   // v91：定案妝容卡；v92：三段流程＋白斑女；v93：白斑封面換 20551205＋翻頁特效；v94：翻頁放慢 0.8 秒；v95：官網風格第 1 步（色、字、圓角、陰影）；v96：第 2 步首屏兩欄＋大數字；v97：色號頁說明改使用者視角；v99：拍攝小幫手（v98 留給白斑線）
const VENDOR = 'goodglow-vendor-konva10.6.0-cv4.12.0-ort1.30.0';   // 第三方程式庫：版本固定，獨立快取，App 改版不會清掉（YuNet 模型第一次用到時也會自動存進這裡，不改名以免已存的 25 MB 重下載）
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
    // 第三方程式庫：快取優先（版本固定、檔案大），第一次用到才下載
    e.respondWith(caches.open(VENDOR).then((c) => c.match(e.request).then((hit) => hit || fetch(e.request).then((res) => {
      if (res.ok) c.put(e.request, res.clone());
      return res;
    }))));
    return;
  }
  // 網路優先，離線才用快取
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
