'use strict';

// ---------------------------------------------------------------- Settings
const CAMERA_KIT_URL = 'https://plugins-media.makeupar.com/v2.2-camera-kit/sdk.js';
const MIN_SHORT = 640;             // Reject low resolution (no upscaling). Camera Kit outputs about 680x1080 on phones, so the old threshold of 1080 on the short side would reject every Kit photo (10-22 real device)
const UNEVEN_RATIO = 0.8;
const PREFS_KEY = 'goodglow.prefs.v1';
const FP_KEY = 'goodglow.fingerprint.v1';   // Store only the lighting fingerprint numbers, not the photo
const ITEMS = [
  { key: 'foundation', label: __t('s0159', '底妝') },   // Apply the foundation first (the shade closest to the skin tone), then layer the rest on top
  { key: 'contour', label: __t('s0160', '修容') },       // Very-light-skin facial-feature definition: cool grey-brown, below the cheekbones
  { key: 'highlight', label: __t('s0092', '打亮') },     // Mid-face / upper contour / T-zone
  { key: 'brow', label: __t('s0161', '眉毛') },
  { key: 'lash', label: __t('s0162', '睫毛') },
  { key: 'blush', label: __t('s0163', '腮紅') },
  { key: 'lip', label: __t('s0164', '唇') },
];
const TONES = [
  { id: 'pink_cool', label: __t('s0165', '冷調（偏粉）') },
  { id: 'neutral', label: __t('s0166', '中性') },
  { id: 'warm_ivory', label: __t('s0167', '暖調（偏象牙）') },
  { id: 'neutral', label: __t('s0168', '不知道'), unsure: true },
];

const CONDITIONS = [
  // The screens do not label any condition name (design note: they want to be treated like anyone else); use a neutral description of needs
  { id: 'albinism', label: __t('s0169', '膚色很淺'), desc: __t('s0170', '淺膚色、眉睫顏色淡'), img: 'img/cover_albinism.jpg' },
  { id: 'vitiligo', label: __t('s0171', '膚色深淺不一'), desc: __t('s0172', '臉上有顏色較淺的區塊'), img: 'img/cover_vitiligo.jpg' },
];
// Step bar: photo -> skin tone -> shade -> three questions -> suggestion -> try-on -> palette card
const STEPS = [__t('s0015', '拍照'), __t('s0173', '膚色'), __t('s0174', '預期'), __t('s0175', '色號'), __t('s0176', '三題'), __t('s0177', '建議'), __t('s0019', '試妝'), __t('s0178', '配色卡')];
const VIEW_STEP = { 'view-capture': 0, 'view-check': 1, 'view-expect': 2, 'view-shade': 3, 'view-sens': 4, 'view-advice': 5,
  'view-base': 6, 'view-quick': 6, 'view-adjust': 6, 'view-card': 7 };

const state = {
  presets: null, mode: 'mock',
  style: 'natural', tone: 'neutral', toneUnsure: true, sens: null,
  browPts: [],                             // The eyebrow shape the user tapped (for each of the two brows: head, peak and tail, in relative coordinates); the phone draws the brows only once all 6 points are set
  expected: null, useExpected: false,      // Expected makeup (only the demo photo has one); useExpected: take the shades from the expected makeup
  cardPick: null,                          // The skin-tone swatch the user picked themselves {level, tone}; if picked it takes priority
  photo: null, demo: false,
  quick: null, selection: {}, currentImage: null, item: 'brow',
  skinHex: null, skinTapped: false, lightOk: true, card: null,
  browHex: null, sampleMode: 'skin', recommendation: null,
  condition: 'albinism', patchHex: null, patchRatio: null,
};

// ---------------------------------------------------------------- Utilities
const $ = (id) => document.getElementById(id);
// 10-21 page-turn effect: going forward the new page slides in from the right, going back from the left, over 0.8 seconds.
// In light-sensitive mode (dark screen) or with the system 'reduce motion' setting, only a 0.6 second fade-in. Timing and distance are in style.css as --vt-slide-time / --vt-fade-time / --vt-distance. The page switch itself is still synchronous (later code can measure the layout right away); the animation is only added to the new page.
let currentView = 'view-start';
function show(view) {
  const prev = currentView;
  currentView = view;
  swapView(view);
  if (prev === view) return;
  const rank = (v) => ({ 'view-start': -1, 'view-mirror': 0.5 }[v] ?? VIEW_STEP[v] ?? 99);   // The camera page sits between taking the photo and sampling the skin tone
  const gentle = matchMedia('(prefers-reduced-motion: reduce)').matches || document.documentElement.getAttribute('data-theme') === 'dark';
  const el = document.getElementById(view);
  if (!el) return;
  el.classList.remove('vt-fwd', 'vt-back', 'vt-fade');
  void el.offsetWidth;                                   // Re-trigger the animation (when the same direction is tapped repeatedly)
  el.classList.add(gentle ? 'vt-fade' : (rank(view) < rank(prev) ? 'vt-back' : 'vt-fwd'));
  el.addEventListener('animationend', () => el.classList.remove('vt-fwd', 'vt-back', 'vt-fade'), { once: true });
}
function swapView(view) {
  for (const v of document.querySelectorAll('.view')) v.hidden = v.id !== view;
  const at = VIEW_STEP[view];
  const bar = document.querySelector(`#${view} .stepper`);
  if (bar && at !== undefined) {
    bar.innerHTML = STEPS.map((s, i) => (i < at
      ? __T('s0179', () => `<button class="done" data-step="${i}" aria-label="回到第 ${i + 1} 步：${s}"><b>${i + 1}</b><em>${s}</em></button>`, () => `<button class="done" data-step="${i}" aria-label="Back to step ${i + 1}: ${s}"><b>${i + 1}</b><em>${s}</em></button>`)
      : `<span class="${i === at ? 'on' : ''}"><b>${i + 1}</b><em>${s}</em></span>`)).join('');
    for (const b of bar.querySelectorAll('button[data-step]')) b.onclick = () => goStep(Number(b.dataset.step));
    bar.dataset.now = __T('s0180', () => `第 ${at + 1} 步，共 ${STEPS.length} 步：${STEPS[at]}`, () => `Step ${at + 1} of ${STEPS.length}: ${STEPS[at]}`);
    bar.setAttribute('aria-label', __T('s0180', () => `第 ${at + 1} 步，共 ${STEPS.length} 步：${STEPS[at]}`, () => `Step ${at + 1} of ${STEPS.length}: ${STEPS[at]}`));
  }
  window.scrollTo(0, 0);
}
const STEP_VIEW = ['view-capture', 'view-check', 'view-expect', 'view-shade', 'view-sens', 'view-advice', 'view-base', 'view-card'];
function goStep(i) {
  // Returning to a completed step: pages that need their content refreshed use their original entry function, the others just switch pages
  if (i === 2) return state.expected ? goExpect() : show('view-check');   // With no expected makeup, go back to skin-tone sampling
  if (i === 4) return goSens();
  if (i === 6 && !state.baseLooks) return goBase();
  show(STEP_VIEW[i]);
}
function store(key, val) { try { localStorage.setItem(key, JSON.stringify(val)); } catch (_) { /* Private mode */ } }
function load(key) { try { return JSON.parse(localStorage.getItem(key)); } catch (_) { return null; } }
function speak() { /* Voice guidance has been removed (browser speech is unreliable in English and on some phones); an empty function is kept so call sites need no change */ }
function busy(on, text = __t('s0158', '試妝中…')) {
  $('loading').hidden = !on;
  $('loading-text').textContent = text;
  if (on) speak(text);
}
const BILLED_ROUTES = /^\/api\/(makeup|looks|layers|face)$/;   // routes that can spend a live call or credits: the banner counter is re-read after each one
async function api(path, body) {
  const lang = { 'X-App-Lang': (window.I18N && window.I18N.lang()) || 'zh' };   // Server-side text returns English according to this (Chinese mode returns unchanged)
  const r = await fetch(path, body ? { method: 'POST', headers: { 'Content-Type': 'application/json', ...lang }, body: JSON.stringify(body) } : { headers: lang });
  if (body && state.mode && state.mode !== 'mock' && BILLED_ROUTES.test(path)) refreshBanner();
  const j = await r.json().catch(() => ({ ok: false, hint: __t('s0181', '伺服器回應格式錯誤') }));
  if (!r.ok || j.ok === false) throw new Error(j.hint || __t('s0182', '發生問題，請再試一次'));
  return j;
}
function optionList(item) {
  return item === 'blush' ? state.presets.blush.levels : state.presets[item];
}
function optionOf(item, id) { return optionList(item).find((o) => o.id === id); }
function optionColor(item, id) {
  const o = optionOf(item, id);
  if (!o || id === 'keep') return null;
  if (item === 'foundation') { const f = foundationShade(); return f ? f.hex : null; }
  if (o.from_photo) return (state.recommendation && state.recommendation.brow && state.recommendation.brow[o.from_photo].hex) || null;
  const tone = state.presets.undertones[state.tone];
  const dc = state.recommendation && state.recommendation.depth_colors;   // Blush and lips follow the skin-tone depth band x warm/cool (consistent with the try-on)
  if (item === 'blush') return o.color || (dc ? dc.blush : tone.blush);
  if (o.use_undertone_color) return item === 'lip' && dc ? dc.lip : tone[item === 'lip' ? 'lip' : item];
  return o.color || null;
}
function photoPayload() { return { ...(state.demo ? { demo: true, condition: state.condition } : { image: state.photo }), ...colorPayload() }; }
function foundationShade() { return (state.recommendation && state.recommendation.foundation_shade) || null; }
// Foundation color: if 'match shades using the expected makeup' is chosen, use the inner-cheek color of the expected makeup, otherwise the skin tone sampled from the photo
function pickedCell() {
  const p = state.cardPick;
  return p && state.skinCard ? state.skinCard.cells.find((c) => c.level === p.level && c.tone === p.tone) : null;
}
function baseSkinHex() {
  const cell = pickedCell();
  if (cell) return cell.hex;                                   // The swatch cell the user picked takes priority
  return state.useExpected && state.expected ? state.expected.zones.base.hex : state.skinHex;
}
function colorPayload() {
  const out = state.skinHex ? { skin_hex: baseSkinHex() } : {};   // Lighter-patch: sheer foundation leans toward the surrounding skin tone
  if (state.browHex && state.skinHex) out.brow_hex = state.browHex;
  const f = foundationShade();
  if (f) out.foundation_hex = f.hex;            // Foundation color = the shade closest to the skin tone
  const shape = state.face && state.face.facts && state.face.facts.face_shape;
  if (shape) out.face_shape = shape;            // If there are facial-feature results, pick the highlight pattern by face shape
  return out;
}

// ---------------------------------------------------------------- Start screen
function renderStart() {
  const prefs = load(PREFS_KEY) || {};
  state.style = prefs.style || 'natural';
  state.tone = 'neutral';
  state.toneUnsure = true;                 // Warm/cool is always estimated from the photo first; the shade page lets the user confirm
  state.condition = prefs.condition === 'vitiligo' ? 'vitiligo' : 'albinism';
  $('opt-dark').checked = !!(prefs.darkSet && prefs.dark);       // Only a dark theme the user explicitly picked is honored; the old default dark theme is no longer carried over
  applyTheme();
  const cb = $('condition-options');
  cb.innerHTML = '';
  for (const c of CONDITIONS) {
    const b = document.createElement('button');
    b.className = 'cover-card';
    b.setAttribute('role', 'radio');
    b.setAttribute('aria-checked', String(c.id === state.condition));
    b.innerHTML = __T('s0183', () => `<img src="${c.img}" alt="" loading="eager"><span class="cc-text"><strong>${c.label}</strong><span>${c.desc}</span><em>開始 →</em></span>`, () => `<img src="${c.img}" alt="" loading="eager"><span class="cc-text"><strong>${c.label}</strong><span>${c.desc}</span><em>Start →</em></span>`);
    b.onclick = () => chooseCondition(c);
    cb.appendChild(b);
  }
}
const CARE_TIP = {                        // Skincare reminder (the adjacent suggestion is placed after the skin-tone step, before the try-on and at the top of the palette card)
  albinism: __t('s0066', '☀︎ 淺色肌膚的光防護是第一步：妝前先擦 SPF50+／PA++++ 無色防曬，再用保濕打底，妝會更服貼。'),
  vitiligo: __t('s0184', '☀︎ 妝前先擦 SPF50+／PA++++ 無色防曬，再用保濕打底，妝會更服貼。'),
};
function chooseCondition(c) {
  const prefs = load(PREFS_KEY) || {};
  state.condition = c.id;
  document.querySelectorAll('.care-tip').forEach((el) => { el.textContent = CARE_TIP[c.id] || CARE_TIP.albinism; });
  // 10-21 unified style: the whole site defaults to a white background (official style guide); people who are sensitive to light switch to dark with one tap in the reminder popup, a preference the user already chose stays as is (the system dark mode is also followed)
  applyTheme();
  savePrefs();
  syncWarnDark(c.id === 'albinism');
  speak(__T('s0185', () => `${c.label}。開始前提醒：照光治療日是否上妝，請依醫師指示，新產品先在耳後小範圍試，出門前先擦防曬。`, () => `${c.label}. Before you start: on treatment days, follow your doctor's advice on whether to wear makeup, patch test new products on a small area behind the ear first, and apply sunscreen before you head out.`));
  $('warn-modal').hidden = false;
  $('btn-warn-ok').focus();
}
function savePrefs(darkSet) {
  const prev = load(PREFS_KEY) || {};
  store(PREFS_KEY, { style: state.style, condition: state.condition, dark: $('opt-dark').checked,
    darkSet: !!(darkSet || prev.darkSet) });
}
function applyTheme() {
  document.documentElement.setAttribute('data-theme', $('opt-dark').checked ? 'dark' : 'light');
}
function syncWarnDark(show) {
  const b = $('btn-warn-dark');
  b.hidden = !show;
  const on = $('opt-dark').checked;
  b.setAttribute('aria-pressed', String(on));
  b.textContent = on ? __t('s0186', '已是暗色畫面（點一下改回白底）') : __t('s0026', '光線太亮？改成暗色畫面');
}

// ---------------------------------------------------------------- Viewfinder: use YouCam Camera Kit first, fall back to the native camera on failure
let kitPromise = null, kitActive = false, nativeStream = null, nativeTimer = null, kitWatch = null, kitSeen = false;

function syncBannerHeight() {          // Height of the top bar (the camera view must subtract it so the buttons stay visible)
  const b = $('mode-banner');
  document.documentElement.style.setProperty('--banner-h', (b && !b.hidden ? b.offsetHeight : 0) + 'px');
}
window.addEventListener('resize', syncBannerHeight);

function loadCameraKit() {
  if (kitPromise) return kitPromise;
  kitPromise = new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('timeout')), 15000);
    let started = false;
    // As done in the other YouCam Camera Kit projects: ymkAsyncInit sometimes does not fire, so after the script loads, also poll window.YMK; init and listeners run only once
    const start = function () {
      if (started || !window.YMK) return;
      started = true;
      try {
        const clamp = (v) => Math.max(300, Math.min(1920, Math.round(v)));
        window.YMK.init({ faceDetectionMode: 'skincare', qualityLevel: 'relaxed', videoQuality: '1080p', language: ((window.I18N && window.I18N.lang()) === 'en' ? 'enu' : 'cht'), imageFormat: 'base64',
          width: clamp(window.innerWidth), height: clamp(window.innerHeight - 90) });
        window.YMK.addEventListener('faceQualityChanged', onKitQuality);
        window.YMK.addEventListener('faceDetectionCaptured', onKitCaptured);
        window.YMK.addEventListener('cameraFailed', onKitFailed);
        window.YMK.addEventListener('unsupportedResolution', onKitFailed);       // This phone's camera resolution is not enough: switch to the fallback camera / photo library
        window.YMK.addEventListener('closeClicked', () => { if (kitActive) { closeMirror(); show('view-capture'); } });   // The X on the screen was tapped: return to the photo selection page
        clearTimeout(timer);
        resolve();
      } catch (e) { clearTimeout(timer); reject(e); }
    };
    window.ymkAsyncInit = start;
    window.YMKAsyncInit = start;
    const s = document.createElement('script');
    s.src = CAMERA_KIT_URL;
    s.async = true;
    s.onload = () => {
      const t0 = Date.now();
      const poll = setInterval(() => { if (window.YMK) { clearInterval(poll); start(); } else if (Date.now() - t0 > 8000) clearInterval(poll); }, 100);
    };
    s.onerror = () => { clearTimeout(timer); reject(new Error('load failed')); };
    document.head.appendChild(s);
  });
  kitPromise.catch(() => { kitPromise = null; });
  return kitPromise;
}

async function openMirror() {
  syncBannerHeight();
  show('view-mirror');
  setHint(__t('s0037', '準備鏡頭中…'));
  setChecks({});
  $('btn-shutter').hidden = true;
  $('native-cam').hidden = true;
  speak(__t('s0961', '手機舉到眼睛高度，像照鏡子一樣。'));
  try {
    await loadCameraKit();
    kitActive = true;
    kitSeen = false;
    clearTimeout(kitWatch);
    kitWatch = setTimeout(() => {       // No Camera Kit frame reported within 10 seconds: most likely another tab or app is using the camera
      if (!kitSeen && kitActive) setHint(__t('s0978', '相機沒有回應：請關掉其他正在用相機的分頁或程式，或改從相簿選照片。'));
    }, 10000);
    window.YMK.openCameraKit();
  } catch (_) {
    kitActive = false;
    startNative();
  }
}

function onKitQuality(q) {
  kitSeen = true;
  const pos = q.position || q.area;
  setChecks({ face: q.hasFace, dist: q.hasFace ? pos === 'good' : null,
    front: q.hasFace ? q.frontal === 'good' : null, light: q.lighting ? q.lighting !== 'notgood' : null });
  if (!q.hasFace) return setHint(__t('s0188', '把臉放進畫面中間'));
  if (pos === 'toosmall') return setHint(__t('s0189', '再靠近一點'));
  if (pos === 'outofboundary') return setHint(__t('s0190', '臉往畫面中間移一點'));
  if (q.frontal && q.frontal !== 'good') return setHint(__t('s0191', '手機舉到眼睛高度，正對著'));
  if (q.lighting === 'notgood') return setHint(__t('s0192', '有點暗也沒關係，我們會幫你調亮'));
  setHint(__t('s0193', '很好，保持不動…'), true);
}
function onKitCaptured(result) {
  findImage(result).then((url) => { closeMirror(); if (url) goCheck(url); else setHint(__t('s0194', '沒拿到照片，請再試一次')); });
}
function onKitFailed() {
  try { window.YMK.close(); } catch (_) { /* Closed */ }
  kitActive = false;
  startNative();
}
function findImage(obj) {
  const seen = new Set();
  function walk(o) {
    if (!o || seen.has(o)) return null;
    if (typeof o === 'string') {
      if (o.startsWith('data:image')) return o;
      if (o.length > 1000 && /^[A-Za-z0-9+/=\s]+$/.test(o.slice(0, 200))) return `data:image/jpeg;base64,${o}`;
      return null;
    }
    if (typeof Blob !== 'undefined' && o instanceof Blob) return o;
    if (typeof o === 'object') { seen.add(o); for (const v of Object.values(o)) { const r = walk(v); if (r) return r; } }
    return null;
  }
  const found = walk(obj);
  return found instanceof Blob ? blobToDataUrl(found) : Promise.resolve(found);
}
function blobToDataUrl(blob) {
  return new Promise((resolve) => { const r = new FileReader(); r.onload = () => resolve(r.result); r.onerror = () => resolve(null); r.readAsDataURL(blob); });
}
async function startNative() {
  $('native-cam').hidden = false;
  $('btn-shutter').hidden = false;
  try {
    nativeStream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: 'user', width: { ideal: 1440 }, height: { ideal: 1920 } }, audio: false });
    $('video').srcObject = nativeStream;
    if (window.FaceAuto && window.FaceAuto.loadLandmarker) window.FaceAuto.loadLandmarker();   // Load face localization first; only once it is loaded are there live angle, size and edge-cutoff hints
    nativeTimer = setInterval(checkNative, 700);
  } catch (_) {
    $('btn-shutter').hidden = true;
    setHint(__t('s0195', '開不了相機，可以按「相簿」選一張正面照'));
  }
}
let nativeBusy = false;
// Live hints for the fallback camera: measure the lighting every 0.7 seconds, then use face localization + the capture-assistant rules to check whether a face is found, its size, whether it is cut off at the edge, and the angle (if localization has not loaded yet, only the lighting hint is shown)
async function checkNative() {
  const v = $('video');
  if (!v.videoWidth || nativeBusy) return;
  nativeBusy = true;
  try {
    const st = lightStats(v, v.videoWidth, v.videoHeight);
    const lightOk = st.mean >= 60 && st.mean <= 215 && st.ratio >= UNEVEN_RATIO;
    let m = null;
    if (window.FaceAuto && window.FaceAuto.measure && window.CaptureGuide && window.CaptureGuide.liveHint) {
      try { m = await window.FaceAuto.measure(v); } catch (_) { m = null; }
    }
    if (!nativeStream) return;                                     // The camera was already closed at measurement time
    if (m) {
      const g = window.CaptureGuide.assess(m), hint = window.CaptureGuide.liveHint(g);
      const has = (id) => { const it = g.items.find((i) => i.id === id); return it ? it.status === 'pass' : null; };
      setChecks({ face: hint !== 'no_face', dist: hint === 'no_face' ? null : (has('face_size') && has('in_frame')),
        front: hint === 'no_face' ? null : has('frontal'), light: lightOk });
      if (hint === 'no_face') return setHint(__t('s0188', '把臉放進畫面中間'));
      if (hint === 'closer') return setHint(__t('s0189', '再靠近一點'));
      if (hint === 'center') return setHint(__t('s0190', '臉往畫面中間移一點'));
      if (hint === 'front') return setHint(__t('s0191', '手機舉到眼睛高度，正對著'));
    } else {
      setChecks({ light: lightOk });
    }
    if (st.mean < 60) return setHint(__t('s0192', '有點暗也沒關係，我們會幫你調亮'));
    if (st.mean > 215) return setHint(__t('s0196', '太亮了：避開直射的光'));
    if (st.ratio < UNEVEN_RATIO) return setHint(__t('s0197', '左右亮度不一樣：讓光從正前方來'));
    setHint(__t('s0198', '光線可以，按「拍」'), true);
  } finally { nativeBusy = false; }
}
function captureNative() {
  const v = $('video');
  if (!v.videoWidth) return;
  const c = document.createElement('canvas');
  c.width = v.videoWidth; c.height = v.videoHeight;
  c.getContext('2d').drawImage(v, 0, 0);
  closeMirror();
  goCheck(c.toDataURL('image/jpeg', 0.92));
}
function closeMirror() {
  clearTimeout(kitWatch);
  if (kitActive) { try { window.YMK.close(); } catch (_) { /* Closed */ } }
  kitActive = false;
  if (nativeTimer) clearInterval(nativeTimer);
  nativeTimer = null;
  if (nativeStream) nativeStream.getTracks().forEach((t) => t.stop());
  nativeStream = null;
}
function setHint(text, ok = false) { const h = $('hint'); h.textContent = text; h.classList.toggle('ok', ok); }
function setChecks(c) {
  const map = { face: 'chk-face', dist: 'chk-dist', front: 'chk-front', light: 'chk-light' };
  for (const [k, id] of Object.entries(map)) { $(id).classList.toggle('good', c[k] === true); $(id).classList.toggle('bad', c[k] === false); }
}

// ---------------------------------------------------------------- Lighting fingerprint
const probe = document.createElement('canvas');
function lightStats(source, sw, sh) {
  probe.width = 60; probe.height = 80;
  const ctx = probe.getContext('2d', { willReadFrequently: true });
  let cw = sw, ch = sw / 0.75;
  if (ch > sh) { ch = sh; cw = sh * 0.75; }
  ctx.drawImage(source, (sw - cw) / 2, (sh - ch) / 2, cw, ch, 0, 0, 60, 80);
  const d = ctx.getImageData(0, 0, 60, 80).data;
  const region = (x0, x1, y0, y1) => {
    let l = 0, r = 0, b = 0, n = 0;
    for (let y = y0; y < y1; y++) for (let x = x0; x < x1; x++) {
      const i = (y * 60 + x) * 4;
      l += 0.299 * d[i] + 0.587 * d[i + 1] + 0.114 * d[i + 2]; r += d[i]; b += d[i + 2]; n++;
    }
    return { l: l / n, r: r / n, b: b / n };
  };
  const left = region(12, 27, 24, 56), right = region(33, 48, 24, 56), all = region(12, 48, 16, 64);
  return { mean: all.l, ratio: Math.min(left.l, right.l) / Math.max(left.l, right.l, 1), rb: all.r / Math.max(all.b, 1) };
}
function fingerprintMessages(fp) {
  const msgs = [];
  if (fp.mean < 60) msgs.push(['warn', __t('s0199', '照片偏暗，試妝效果會比較淡。')]);
  if (fp.ratio < UNEVEN_RATIO) msgs.push(['warn', __T('s0200', () => `左右亮度差 ${Math.round((1 - fp.ratio) * 100)}%，讓光從正前方來會比較準。`, () => `Brightness differs by ${Math.round((1 - fp.ratio) * 100)}% between left and right; light from straight ahead will be more accurate.`)]);
  const last = load(FP_KEY);
  if (last) {
    const lumDiff = Math.abs(fp.mean - last.mean);
    const tempDiff = Math.abs(fp.rb - last.rb) / Math.max(last.rb, 0.01);
    if (lumDiff > 25 || tempDiff > 0.12) msgs.push(['warn', __t('s0201', '今天的光線和上次不一樣，顏色看起來會不同，不要拿來比。')]);
    else msgs.push(['ok', __t('s0202', '光線和上次差不多。')]);
  }
  if (!msgs.length) msgs.push(['ok', __t('s0203', '光線可以。')]);
  return msgs;
}

// ---------------------------------------------------------------- Confirm photo
function loadImage(src) {
  return new Promise((resolve, reject) => { const img = new Image(); img.onload = () => resolve(img); img.onerror = reject; img.src = src; });
}
async function goCheck(dataUrl, demo = false, points = {}) {
  let img;
  try { img = await loadImage(dataUrl); } catch (_) { alert(__t('s0204', '讀不到這張照片')); return; }
  // 10-21 capture assistant: the user's own photo is first checked for usability; if usable it is auto-cropped so the face is about 66% of the width, 3:4 (crop and resize only, no retouching)
  let guide = null;
  state.guideBlock = false;
  if (!demo) {
    const r = await runCaptureGuide(img);
    guide = r.guide;
    if (r.dataUrl) { dataUrl = r.dataUrl; img = r.img; }
    state.guideBlock = !!(guide && guide.overall === 'block');
  }
  state.photo = dataUrl;
  state.demo = demo;
  state.wbGains = demo ? null : grayWorldGains(img);   // Color casts such as indoor yellow light: estimate the gain with the gray-world method, correct only half, limited to +-8%
  if (!demo) state.expected = null;          // Only the demo photo has an expected makeup
  state.useExpected = false;
  state.cardPick = null; state.suggested = null; state.toneUnsure = true;   // Changing the photo: the swatch selection starts over
  state.browPts = []; if ($('brow-dots')) { drawBrowDots(); $('browpts-status').hidden = true; }
  $('check-photo').src = dataUrl;
  $('check-photo').parentElement.style.setProperty('--ar', String(img.naturalWidth / img.naturalHeight));   // The upper limit of the photo frame width is computed from the aspect ratio
  // Let people tap only once the layout is ready, so tap coordinates are accurate; wait at most 1.5 seconds (background tabs sometimes do not respond to decode)
  await Promise.race([$('check-photo').decode().catch(() => {}), new Promise((r) => setTimeout(r, 1500))]);
  const box = $('check-msgs');
  box.innerHTML = '';
  const short = Math.min(img.naturalWidth, img.naturalHeight);
  const lowRes = !demo && short < MIN_SHORT;
  const fp = lightStats(img, img.naturalWidth, img.naturalHeight);
  const msgs = lowRes
    ? [['warn', __T('s0205', () => `解析度不夠（短邊 ${short}px，需要 ${MIN_SHORT}px 以上）。請用前鏡頭最高畫質重拍；放大的照片試妝會失真。`, () => `Resolution is too low (short side ${short}px, ${MIN_SHORT}px or more needed). Please retake with the front camera at its highest quality; enlarged photos distort the try-on.`)]]
    : fingerprintMessages(fp);
  if (demo) msgs.unshift(['ok', __t('s0206', '這是示範照片（Pexels 公開照片）。')]);
  for (const [cls, text] of msgs) { const p = document.createElement('p'); p.className = cls; p.textContent = text; box.appendChild(p); }
  state.lightOk = demo || msgs.every((m) => m[0] !== 'warn');
  setBrow(null);
  setPatch(null);
  state.patchRatio = null;
  state.baseLooks = null;
  state.face = null;
  if ($('face-tips')) { $('face-tips').hidden = true; $('face-suggest').innerHTML = ''; $('btn-face').textContent = faceLabel(false); }
  const vit = state.condition === 'vitiligo';
  $('patch-row').hidden = !vit;
  $('mode-patch').hidden = !vit;
  $('mode-skin').textContent = vit ? __t('s0207', '點照片取原本膚色') : __t('s0051', '點照片取膚色');
  $('btn-try').textContent = __t('s0057', '看我的色號');
  state.lowRes = lowRes;
  setSampleMode('skin', true);
  $('sample-dot').hidden = true;
  // Demo photos use manually confirmed points; the user's own photo first uses face localization to pick the color automatically (only on failure fall back: for very-light-skin take the lit-side cheek, for lighter-patch ask the user to tap)
  state.skinHex = null;
  clearAutoDots();
  if (state.guideBlock) setSkin(null);                              // A photo rejected by the assistant yields no trustworthy color, so no fake result is shown
  else if (points.normal) setSkin(sampleSkin(img, { x: points.normal[0], y: points.normal[1] }), 'demo');
  else if (!vit) setSkin(autoSkin(img), 'cheek');
  else setSkin(null);
  if (points.patch) setPatch(sampleSkin(img, { x: points.patch[0], y: points.patch[1] }), 'demo');
  if (points.brow) setBrow(sampleSkin(img, { x: points.brow[0], y: points.brow[1] }, true), 'demo');
  if (state.guideBlock) $('auto-status').textContent = __t('s0208', '這張先不取色：請照上面的方法重拍或換一張。');
  else if (!points.normal) runAutoColors(img, dataUrl);
  if (vit && window.FaceAuto) {                                     // Load face localization, face segmentation (forehead to hairline) and OpenCV first, so local concealing is faster later
    window.FaceAuto.loadLandmarker();
    if (window.FaceAuto.segmentCached) setTimeout(() => window.FaceAuto.segmentCached(dataUrl, img), 300);   // Segment the background first (forehead to hairline)
    if (window.Vendor) window.Vendor.opencv().catch(() => {});
  }
  updateTry();
  if (!lowRes && !demo) store(FP_KEY, { mean: fp.mean, ratio: fp.ratio, rb: fp.rb, ts: Date.now() });
  show('view-check');
  const guideText = renderGuide(guide, demo);
  speak([guideText, ...msgs.map((m) => m[1])].filter(Boolean).join(__t('s0391', '。') + __sp()));
}

// ---------------------------------------------------------------- Capture assistant (rules from web/capture_guide.js; the photo is only computed on the phone)
const GUIDE_CHIP = { pass: __t('s0209', '可以用'), warn: __t('s0210', '可以用，有小提醒'), block: __t('s0211', '換一張會更好') };
async function runCaptureGuide(img) {
  if (!window.CaptureGuide || !window.FaceAuto || !window.FaceAuto.measure) return { guide: null };
  busy(true, __t('s0212', '檢查照片中…'));
  let m = null;
  try { m = await Promise.race([window.FaceAuto.measure(img), new Promise((r) => setTimeout(() => r(null), 8000))]); } catch (_) { m = null; }
  busy(false);
  if (!m) return { guide: { overall: 'warn', items: [], unavailable: true } };
  const guide = window.CaptureGuide.assess(m);
  const c = guide.crop;
  if (guide.overall === 'block' || !c || (c.w >= m.imgW * 0.97 && c.h >= m.imgH * 0.97)) return { guide };
  if (Math.min(c.w, c.h) < MIN_SHORT) return { guide };      // After cropping, the short side would fall below the lower limit (real device 590x787): do not crop, keep the original resolution
  const cv = document.createElement('canvas');                // Only crop and resize (2D canvas outputs sRGB); no skin smoothing, no change to the outline
  cv.width = c.w; cv.height = c.h;
  cv.getContext('2d').drawImage(img, c.x, c.y, c.w, c.h, 0, 0, c.w, c.h);
  const url = cv.toDataURL('image/jpeg', 0.92);
  try { const im = await loadImage(url); guide.cropped = true; return { guide, dataUrl: url, img: im }; } catch (_) { return { guide }; }
}
function renderGuide(guide, demo) {
  const card = $('guide-card');
  if (demo || !guide) { card.hidden = true; return ''; }
  const order = { block: 0, warn: 1, pass: 2 };
  const shown = guide.items.filter((i) => i.status === guide.overall && i.status !== 'pass').sort((a, b) => order[a.status] - order[b.status]).slice(0, 2);
  let sum;
  if (guide.unavailable) sum = __t('s0213', '這張照片沒辦法自動檢查，可以直接繼續，或換一張正面照。');
  else if (guide.overall === 'block') sum = __t('s0214', '這張照片的角度或距離還不適合取色；照下面的方法重拍或換一張，結果會準很多。');
  else if (guide.overall === 'warn') sum = __t('s0215', '這張照片可以用！下面是讓結果更準的小提醒。');
  else sum = __t('s0216', '距離、角度和光線都剛好，可以開始了。');
  if (guide.cropped) sum += __t('s0217', '已自動幫你把臉放到剛好的大小（只裁切、縮放，沒有修圖）。');
  card.hidden = false;
  card.dataset.state = guide.overall;
  $('guide-chip').textContent = GUIDE_CHIP[guide.overall];
  $('guide-sum').textContent = sum;
  $('guide-list').innerHTML = shown.map((i) => `<li class="${i.status}">${esc(i.hint)}</li>`).join('');
  $('btn-guide-retake').hidden = guide.overall === 'pass';
  $('btn-guide-retake').className = guide.overall === 'block' ? 'btn primary' : 'btn ghost';
  return [sum, ...shown.map((i) => i.hint)].join('');
}

// ---------------------------------------------------------------- Skin-tone sampling (computed locally, not uploaded)
const SKIN_W = 300;
function sampleSkin(img, point, dark = false, radius = 6) {
  // point: relative coordinates 0-1; samples the surrounding (2r+1)^2 pixels (on a 300px-wide thumbnail)
  const h = Math.round(SKIN_W * img.naturalHeight / img.naturalWidth);
  const c = document.createElement('canvas');
  c.width = SKIN_W; c.height = h;
  const g = c.getContext('2d', { willReadFrequently: true });
  g.drawImage(img, 0, 0, SKIN_W, h);
  const pts = [point];
  const px = [];
  for (const p of pts) {
    const cx = Math.round(p.x * SKIN_W), cy = Math.round(p.y * h), r = radius;
    const d = g.getImageData(Math.max(0, cx - r), Math.max(0, cy - r), 2 * r + 1, 2 * r + 1).data;
    for (let i = 0; i < d.length; i += 4) px.push([d[i], d[i + 1], d[i + 2]]);
  }
  // Skin tone uses the median, avoiding eyebrows, moles and glare; eyebrow color takes only the darker 30% (hair), avoiding the skin between hairs
  const pool = dark ? px.sort((a, b) => lum(a) - lum(b)).slice(0, Math.max(1, Math.floor(px.length * 0.3))) : px;
  const med = (k) => pool.map((v) => v[k]).sort((a, b) => a - b)[pool.length >> 1];
  const wb = state.wbGains || [1, 1, 1];                // The user's own photo: white-balance correction before sampling (the demo photo is not corrected)
  return '#' + [0, 1, 2].map((k) => Math.min(255, Math.round(med(k) * wb[k])).toString(16).padStart(2, '0')).join('').toUpperCase();
}
function lum(v) { return 0.2126 * v[0] + 0.7152 * v[1] + 0.0722 * v[2]; }
function grayWorldGains(img) {
  // Gray-world method (suggested by an external advisor, 10-16): the whole-image average should be close to gray; a portrait is not necessarily gray overall, so correct only half, limited to +-8%
  const c = document.createElement('canvas'); c.width = 64; c.height = Math.max(1, Math.round(64 * img.naturalHeight / img.naturalWidth));
  const g = c.getContext('2d', { willReadFrequently: true }); g.drawImage(img, 0, 0, c.width, c.height);
  const d = g.getImageData(0, 0, c.width, c.height).data; const s = [0, 0, 0]; let n = 0;
  for (let i = 0; i < d.length; i += 4) { s[0] += d[i]; s[1] += d[i + 1]; s[2] += d[i + 2]; n++; }
  const m = s.map((v) => v / n), gray = (m[0] + m[1] + m[2]) / 3;
  return m.map((v) => Math.min(1.08, Math.max(0.92, 1 + 0.5 * (gray / Math.max(v, 1) - 1))));
}
function luma(hex) {
  const n = parseInt(hex.slice(1), 16);
  return 0.2126 * (n >> 16) + 0.7152 * ((n >> 8) & 255) + 0.0722 * (n & 255);
}
function autoSkin(img) {
  // Sample the two cheeks separately and use the lit side (with side lighting the average is pulled darker by shadow)
  const left = sampleSkin(img, { x: 0.3, y: 0.62 }), right = sampleSkin(img, { x: 0.7, y: 0.62 });
  return luma(left) >= luma(right) ? left : right;
}
const SRC_TEXT = { auto: __t('s0218', '自動：臉部定位'), tap: __t('s0219', '你點選的位置'), demo: __t('s0220', '示範取色點'), cheek: __t('s0221', '自動取受光側臉頰') };
function setSkin(hex, src = 'tap') {
  state.skinHex = hex;
  const label = state.condition === 'vitiligo' ? __t('s0067', '原本膚色') : __t('s0173', '膚色');
  $('skin-swatch').style.background = hex || 'transparent';
  $('skin-text').textContent = hex ? __T('s0222', () => `${label}：${hex}（${SRC_TEXT[src]}）`, () => `${label}: ${hex} (${SRC_TEXT[src]})`) : __T('s0223', () => `${label}：還沒取（點一下沒有淺色區塊的臉頰或下顎線）`, () => `${label}: not picked yet (tap a cheek or jawline with no lighter patch)`);
  updateTry();
}
function setPatch(hex, src = 'tap') {
  state.patchHex = hex;
  $('patch-swatch').style.background = hex || 'transparent';
  $('patch-text').textContent = hex ? __T('s0224', () => `淺色區塊：${hex}（${SRC_TEXT[src]}）`, () => `Lighter patch: ${hex} (${SRC_TEXT[src]})`) : __t('s0225', '淺色區塊：還沒取（點「取淺色區塊」再點淺色區塊）');
  updateTry();
}
function updateTry() {
  const vit = state.condition === 'vitiligo';
  $('btn-try').disabled = !!state.lowRes || !!state.guideBlock || !state.skinHex || (vit && !state.patchHex);   // Shades can be viewed only once a skin tone has been obtained; if the assistant rejected the photo, switch to another photo first
}
function setBrow(hex, src = 'tap') {
  state.browHex = hex;
  if ($('browpts-tip')) setTimeout(updateBrowTip, 0);
  $('brow-swatch').style.background = hex || 'transparent';
  $('brow-text').textContent = hex ? __T('s0226', () => `眉色：${hex}（${SRC_TEXT[src]}）`, () => `Brow color: ${hex} (${SRC_TEXT[src]})`) : __t('s0227', '眉色：還沒取（點「眉毛」再點眉毛）');
}
function setSampleMode(mode, quiet = false) {
  state.sampleMode = mode;
  for (const m of ['skin', 'patch', 'brow', 'browpts']) $(`mode-${m}`).setAttribute('aria-checked', String(mode === m));
  if (mode === 'browpts' && state.browPts.length >= 6) { state.browPts = []; drawBrowDots(); }
  if (!quiet) speak({ brow: __t('s0228', '請點一下眉毛'), patch: __t('s0229', '請點一下淺色區塊'), skin: __t('s0230', '請點一下臉頰或下顎線，臉和脖子交界的地方'),
    browpts: __t('s0231', '請依序點眉頭、眉峰、眉尾。先點照片左邊那道眉，再點右邊那道。') }[mode]);
}
// ---------------------------------------------------------------- Three-point eyebrow shape (when the eyebrows are very faint, both the official result and the face landmarks put the brows in the wrong place)
const BROW_STEP = [__t('s0232', '眉頭'), __t('s0233', '眉峰'), __t('s0234', '眉尾')];
function drawBrowDots() {
  const box = $('brow-dots');
  box.innerHTML = state.browPts.map((p, i) => `<span class="brow-dot" style="left:${p.x * 100}%;top:${p.y * 100}%">${(i % 3) + 1}</span>`).join('');
  const n = state.browPts.length;
  $('browpts-status').hidden = !n && state.sampleMode !== 'browpts';
  $('browpts-status').textContent = n >= 6 ? __t('s0235', '眉形已設定：試妝時由手機照這 6 點畫眉（官方不畫眉）。再點「點眉形」可以重點。')
    : __T('s0236', () => `下一點：${n < 3 ? '照片左邊那道眉' : '照片右邊那道眉'}的${BROW_STEP[n % 3]}（${n}/6）`, () => `Next point: the ${BROW_STEP[n % 3]} of ${n < 3 ? 'the brow on the left of the photo' : 'the brow on the right of the photo'} (${n}/6)`);
}
function browLight() {
  return !!(state.browHex && state.skinHex) && luma(state.skinHex) - luma(state.browHex) < 25;
}
function updateBrowTip() { $('browpts-tip').hidden = !browLight(); }
function browPale() {                       // Very faint eyebrows (white brows): the try-on does not draw brows and the adjust page does not offer brows (design note: do not draw white brows)
  const fl = state.recommendation && state.recommendation.full_look;
  return fl && typeof fl.brow_pale === 'boolean' ? fl.brow_pale : browLight();
}
function browColorFor(id) {
  const o = optionOf('brow', id);
  if (!o || id === 'keep') return null;
  if (o.from_photo) return (state.recommendation && state.recommendation.brow && state.recommendation.brow[o.from_photo].hex) || o.color || '#8A7D75';
  return o.color;
}
function useLocalBrow(sel) { return state.browPts.length >= 6 && sel.brow && sel.brow !== 'keep'; }
function drawBrowsOn(dataUrl, color) {
  // Three-point brow drawing: a quadratic curve through the brow peak, thick at the head and thin at the tail; first overlay a semi-transparent color (multiplicative, keeping the skin texture), then add fine hair strokes
  return loadImage(dataUrl).then((img) => {
    const W = img.naturalWidth, H = img.naturalHeight;
    const c = document.createElement('canvas'); c.width = W; c.height = H;
    const g = c.getContext('2d'); g.drawImage(img, 0, 0);
    let seed = Math.round(state.browPts.reduce((s, p) => s + p.x * 9973 + p.y * 7919, 1));
    const rnd = () => { seed = (seed * 1103515245 + 12345) % 2147483648; return seed / 2147483648; };
    for (let b = 0; b < 2; b++) {
      const [h, pk, t] = state.browPts.slice(b * 3, b * 3 + 3).map((p) => ({ x: p.x * W, y: p.y * H }));
      const cx = 2 * pk.x - (h.x + t.x) / 2, cy = 2 * pk.y - (h.y + t.y) / 2;
      const len = Math.hypot(t.x - h.x, t.y - h.y);
      const pt = (u) => ({ x: (1 - u) ** 2 * h.x + 2 * (1 - u) * u * cx + u * u * t.x, y: (1 - u) ** 2 * h.y + 2 * (1 - u) * u * cy + u * u * t.y });
      const tan = (u) => { const dx = 2 * (1 - u) * (cx - h.x) + 2 * u * (t.x - cx), dy = 2 * (1 - u) * (cy - h.y) + 2 * u * (t.y - cy); const m = Math.hypot(dx, dy) || 1; return { x: dx / m, y: dy / m }; };
      const width = (u) => len * (0.22 - 0.14 * u);          // Head about 22% and tail about 8% of the brow length (white brows must cover the original brow)
      const top = [], bot = [];
      for (let i = 0; i <= 30; i++) {
        const u = i / 30, p = pt(u), d = tan(u), w = width(u) / 2;
        top.push({ x: p.x + d.y * w, y: p.y - d.x * w }); bot.push({ x: p.x - d.y * w, y: p.y + d.x * w });
      }
      // Draw the brow shape on its own layer: faint at the head, dark in the body, slightly faint at the tail; shrink then enlarge to soften the edges (iPhone Safari does not support canvas filter)
      const m = document.createElement('canvas'); m.width = W; m.height = H;
      const mg = m.getContext('2d');
      const grad = mg.createLinearGradient(h.x, h.y, t.x, t.y);
      grad.addColorStop(0, color + '88'); grad.addColorStop(0.3, color); grad.addColorStop(0.85, color); grad.addColorStop(1, color + '99');
      mg.beginPath(); [...top, ...bot.reverse()].forEach((p, i) => (i ? mg.lineTo(p.x, p.y) : mg.moveTo(p.x, p.y))); mg.closePath();
      mg.fillStyle = grad; mg.fill();
      const k = Math.max(2, Math.round(len * 0.03));
      const s = document.createElement('canvas'); s.width = Math.ceil(W / k); s.height = Math.ceil(H / k);
      const sg = s.getContext('2d'); sg.imageSmoothingQuality = 'high'; sg.drawImage(m, 0, 0, s.width, s.height);
      g.save();
      g.globalCompositeOperation = 'multiply'; g.imageSmoothingQuality = 'high';
      g.globalAlpha = 0.5; g.drawImage(s, 0, 0, W, H);
      g.globalAlpha = 0.3; g.strokeStyle = color; g.lineWidth = Math.max(1, len * 0.005); g.lineCap = 'round';
      for (let n = 0; n < 140; n++) {                         // Fine hairs: along the brow direction, slanting slightly upward; fewer at the head, more in the body
        const u = Math.sqrt(rnd()), p = pt(u), d = tan(u), off = (rnd() - 0.5) * width(u) * 0.9;
        const sx = p.x - d.y * off, sy = p.y + d.x * off, l = len * (0.04 + 0.05 * rnd()), ang = Math.atan2(d.y, d.x) + (d.x >= 0 ? -0.35 : 0.35);
        g.beginPath(); g.moveTo(sx, sy); g.lineTo(sx + Math.cos(ang) * l, sy + Math.sin(ang) * l); g.stroke();
      }
      g.restore();
    }
    return c.toDataURL('image/jpeg', 0.92);
  });
}
async function onPhotoTap(e) {
  const imgEl = $('check-photo');
  const rect = imgEl.getBoundingClientRect();
  const pt = { x: (e.clientX - rect.left) / rect.width, y: (e.clientY - rect.top) / rect.height };
  if (pt.x < 0 || pt.x > 1 || pt.y < 0 || pt.y > 1) return;
  const dot = $('sample-dot');
  dot.style.left = `${pt.x * 100}%`; dot.style.top = `${pt.y * 100}%`; dot.hidden = false;
  clearTimeout(sampleDotTimer);
  sampleDotTimer = setTimeout(() => { dot.hidden = true; }, 1600);        // the tap marker leaves the face after a moment
  if ($('auto-dots').children.length) fadeAutoDotsSoon();                  // a tap shows the earlier sample points again, briefly
  const img = await loadImage(state.photo);
  if (state.sampleMode === 'browpts') {
    if (state.browPts.length >= 6) state.browPts = [];
    state.browPts.push(pt); drawBrowDots(); updateBrowTip();
    $('sample-dot').hidden = true;
    speak(state.browPts.length >= 6 ? __t('s0237', '眉形設定好了') : BROW_STEP[state.browPts.length % 3]);
    return;
  }
  if (state.sampleMode === 'brow') { setBrow(sampleSkin(img, pt, true)); speak(__t('s0238', '已取你的眉色')); updateBrowTip(); return; }
  if (state.sampleMode === 'patch') { setPatch(sampleSkin(img, pt)); speak(__t('s0239', '已取淺色區塊的顏色')); return; }
  setSkin(sampleSkin(img, pt));
  speak(__t('s0240', '已改用你點選的膚色'));
}

// ---------------------------------------------------------------- Automatic color picking (face_auto.js)
let autoDotTimer = null;
let sampleDotTimer = null;
function fadeAutoDotsSoon() {              // markers show where the color was taken, then fade so nothing stays on the face
  const box = $('auto-dots');
  box.classList.remove('fade');
  clearTimeout(autoDotTimer);
  autoDotTimer = setTimeout(() => box.classList.add('fade'), 2200);
}
function clearAutoDots() { $('auto-dots').innerHTML = ''; $('auto-status').textContent = ''; $('auto-dots').classList.remove('fade'); }
function drawAutoDots(points) {
  const box = $('auto-dots');
  box.innerHTML = '';
  for (const [kind, list] of Object.entries(points)) {
    for (const p of list) {
      const d = document.createElement('span');
      d.className = `auto-dot ${kind}`;
      d.style.left = `${p.x * 100}%`; d.style.top = `${p.y * 100}%`;
      box.appendChild(d);
    }
  }
  fadeAutoDotsSoon();
}
async function runAutoColors(img, dataUrl) {
  if (!window.FaceAuto) return;
  $('auto-status').textContent = __t('s0241', '自動找臉部位置、取色中…');
  const r = await window.FaceAuto.autoColors(img, state.condition, sampleSkin);
  if (state.photo !== dataUrl) return;                       // The photo was changed in the meantime
  if (!r) { $('auto-status').textContent = __t('s0242', '沒有找到臉的位置，請在照片上點選取色。'); return; }
  if (r.skin) setSkin(r.skin, 'auto');
  if (r.patch) setPatch(r.patch, 'auto');
  state.patchRatio = r.patch_ratio ?? null;
  if (r.brow) setBrow(r.brow, 'auto');
  drawAutoDots(r.points);
  const vit = state.condition === 'vitiligo';
  $('auto-status').textContent = vit && !r.patch
    ? __t('s0243', '已自動取原本膚色和眉色；沒有找到明顯的淺色區塊，請點「取淺色區塊」再點淺色區塊。')
    : __t('s0244', '已自動取色（照片上的小點是取色位置）；不對可以再點選修正。');
  speak($('auto-status').textContent);
}

// ---------------------------------------------------------------- Lighter-patch: skin-tone analysis (local, no credits spent)
function recommendPayload() {
  const vit = state.condition === 'vitiligo';
  return { condition: state.condition, style: state.style, brow_hex: state.browHex,
    undertone: state.toneUnsure ? null : state.tone, sensitivity: state.sens,
    ...(vit ? { normal_hex: baseSkinHex(), patch_hex: state.patchHex, patch_ratio: state.patchRatio } : { skin_hex: baseSkinHex() }) };
}
const LEVEL_TEXT = {
  single: __t('s0245', '色差不大：一個接近原本膚色的色號就夠。'),
  two_shade: __t('s0246', '色差明顯：建議兩個色號，一個打底、一個修邊緣。'),
  two_shade_plus: __t('s0247', '色差很明顯：建議兩個色號，必要時先修色。'),
};
function bestCode(ms) {
  // When even the nearest shade is 'not a close match', do not pretend: say plainly that the catalog does not have it, and show the color to look for as a swatch
  if (!ms.length || ms[0].verdict_code === 'no_match') return __t('s0249', '型錄沒有接近的色號，照色塊到專櫃找');
  return ms[0].fit ? `${ms[0].brand} ${ms[0].code}` : ms[0].code;
}
function shadeRows(base) {
  const found = base.foundation || [], conc = base.concealer || base.base;
  const stripes = base.stripes || null;
  const rows = [{ k: state.cardPick ? __t('s0250', '整臉底妝（對你選的膚色）') : state.useExpected ? __t('s0251', '整臉底妝（對預期妝容的膚色）') : __t('s0252', '整臉底妝（對淺色區塊周圍的膚色）'), v: bestCode(found), hex: base.normal, matches: found.slice(0, 3), card: base.card, stripes },
    { k: __t('s0253', '遮瑕打底色（點在淺色區塊上，接近周圍膚色）'), v: bestCode(conc), hex: base.normal, matches: conc.slice(0, 3) }];
  if (base.edge.length) rows.push({ k: __t('s0254', '修邊色（讓交界淡化）'), v: bestCode(base.edge), hex: base.edge_target, matches: base.edge.slice(0, 3), card: base.edge_card });
  if (base.corrector.length) rows.push({ k: __t('s0255', '局部校色（只點在淺色區塊上）'), v: base.corrector[0].code, hex: base.corrector[0].hex, matches: base.corrector, note: base.corrector_reason });
  return rows;
}
async function fetchRecommendation(text) {
  busy(true, text);
  try {
    const r = await api('/api/recommend', recommendPayload());
    state.recommendation = r.recommendation;
    if (state.toneUnsure) { state.tone = r.recommendation.undertone_id; state.toneFromSkin = state.tone; }
    return true;
  } catch (e) { alert(e.message); return false; } finally { busy(false); }
}
const ZONE_LABEL = { base: __t('s0256', '底妝（內頰）'), highlight: __t('s0257', '打亮（鼻樑＋額頭）'), contour: __t('s0258', '修容（外頰＋下顎）') };
function zonesHtml(z) {
  return ['base', 'highlight', 'contour'].map((k) => `<div class="zone"><span class="zone-sw" style="background:${esc(z[k].hex)}"></span>`
    + __T('s0259', () => `<b>${ZONE_LABEL[k]}</b><span>色卡第 ${z[k].level} 級</span></div>`, () => `<b>${ZONE_LABEL[k]}</b><span>Card level ${z[k].level}</span></div>`)).join('');
}
function goExpect() {
  const ex = state.expected;
  $('expect-img').src = ex.image;
  $('expect-tag').textContent = __t('s0060', '模擬');
  $('btn-expect-toggle').setAttribute('aria-pressed', 'false');
  $('btn-expect-toggle').textContent = __t('s0061', '看你的照片');
  $('expect-how').textContent = ex.how;
  $('expect-zones').innerHTML = zonesHtml(ex.zones);
  show('view-expect');
  speak(__T('s0260', () => `預期妝容，模擬。底妝色卡第 ${ex.zones.base.level} 級，打亮第 ${ex.zones.highlight.level} 級，修容第 ${ex.zones.contour.level} 級。`, () => `Expected look, simulated. Foundation card level ${ex.zones.base.level}, highlight level ${ex.zones.highlight.level}, contour level ${ex.zones.contour.level}.`));
}
function toggleExpect() {
  const showOrig = $('btn-expect-toggle').getAttribute('aria-pressed') !== 'true';
  $('btn-expect-toggle').setAttribute('aria-pressed', String(showOrig));
  $('btn-expect-toggle').textContent = showOrig ? __t('s0261', '看預期妝容') : __t('s0061', '看你的照片');
  $('expect-img').src = showOrig ? state.photo : state.expected.image;
  $('expect-tag').textContent = showOrig ? __t('s0134', '你的照片') : __t('s0060', '模擬');
}
const TONE_OF_ID = { pink_cool: 'cool', neutral: 'neutral', warm_ivory: 'warm' };
const ID_OF_TONE = { cool: 'pink_cool', neutral: 'neutral', warm: 'warm_ivory' };
const TONE_NAME = { cool: __t('s0262', '冷調'), neutral: __t('s0166', '中性'), warm: __t('s0263', '暖調') };
function renderPick(rec) {
  // The user picks a skin-tone swatch themselves: the cell the program suggested is labeled 'suggested'; tapping re-matches the shades from that cell
  if (!state.skinCard) return;
  if (!state.suggested || !state.cardPick) state.suggested = { level: rec.skin_level, tone: TONE_OF_ID[rec.undertone_id] || 'neutral' };
  const sug = state.suggested, cur = state.cardPick || sug;
  // 10-21 (design note): the rewritten version A (user's point of view) replaces 'the program suggests...'. Basis: the official skin-tone reading runs dark and yellow for very light skin (measured dE00 9.8-12.5,
  // local run notes (not shipped)). This sentence applies only to the entrance for very light skin; the other entrance cannot say 'fair', and only the word 'program' is removed
  const hint = state.condition === 'albinism'
    ? __t('s0264', '為了不讓白皙的你被配到太深、偏黃的粉底，我們直接用你照片上的膚色來幫你對色。若覺得不像，隨時都能點選別格調整喔！')
    : __T('s0265', () => `建議第 ${sug.level} 級・${TONE_NAME[sug.tone]}（色卡上標「建議」的那格）。照片會受光線影響，覺得不像可以自己點一格。`, () => `Suggested: level ${sug.level}・${TONE_NAME[sug.tone]} (the cell marked "Suggested" on the card). Photos are affected by lighting; if it doesn't look right, you can tap a cell yourself.`);
  $('pick-note').textContent = state.cardPick
    ? __T('s0266', () => `你選的是第 ${cur.level} 級・${TONE_NAME[cur.tone]}（建議第 ${sug.level} 級・${TONE_NAME[sug.tone]}）。照片會受光線影響，以你自己看到的為準。`, () => `You chose level ${cur.level}・${TONE_NAME[cur.tone]} (suggested: level ${sug.level}・${TONE_NAME[sug.tone]}). Photos are affected by lighting, so go by what you see yourself.`)
    : hint;
  const tb = $('pick-tone');
  tb.innerHTML = '';
  for (const t of ['cool', 'neutral', 'warm']) {
    const b = document.createElement('button');
    b.className = 'chip';
    b.setAttribute('role', 'radio');
    b.setAttribute('aria-checked', String(t === cur.tone));
    b.textContent = TONE_NAME[t];
    b.onclick = () => choosePick(cur.level, t);
    tb.appendChild(b);
  }
  const sb = $('pick-strip');
  sb.innerHTML = '';
  for (const c of state.skinCard.cells.filter((x) => x.tone === cur.tone)) {
    const b = document.createElement('button');
    b.className = 'pick-cell';
    b.style.background = c.hex;
    b.classList.toggle('light', hexLuma(c.hex) > 150);       // dark numerals on pale swatches, white on deep ones
    b.setAttribute('role', 'radio');
    b.setAttribute('aria-checked', String(c.level === cur.level));
    b.setAttribute('aria-label', __T('s0267', () => `第 ${c.level} 級 ${c.level_name}`, () => `Level ${c.level} ${c.level_name}`));
    b.innerHTML = __T('s0268', () => `<span>${c.level}</span>${c.level === sug.level && cur.tone === sug.tone ? '<em>建議</em>' : ''}`, () => `<span>${c.level}</span>${c.level === sug.level && cur.tone === sug.tone ? '<em>Suggested</em>' : ''}`);
    b.onclick = () => choosePick(c.level, cur.tone);
    sb.appendChild(b);
  }
}
function hexLuma(hex) {
  const n = parseInt(String(hex).replace('#', ''), 16);
  return 0.2126 * ((n >> 16) & 255) + 0.7152 * ((n >> 8) & 255) + 0.0722 * (n & 255);
}
function choosePick(level, tone) {
  state.cardPick = { level, tone };
  state.toneUnsure = false;                       // If the user picked warm/cool themselves, follow the user's choice
  state.tone = ID_OF_TONE[tone];
  state.recommendation = null;
  speak(__T('s0269', () => `第 ${level} 級，${TONE_NAME[tone]}`, () => `Level ${level}, ${TONE_NAME[tone]}`));
  goShade();
}
async function goShade() {
  if (!await fetchRecommendation(__t('s0270', '找最接近你膚色的色號…'))) return;
  renderPick(state.recommendation);
  $('shade-zones').hidden = !(state.useExpected && state.expected);
  if (state.useExpected && state.expected) $('shade-zones').innerHTML = zonesHtml(state.expected.zones);
  const rec = state.recommendation, b = rec.base;
  const vit = state.condition === 'vitiligo';
  $('shade-pair').hidden = !vit;
  const tone = state.presets.undertones[state.tone].label;
  if (vit) {
    $('sk-normal').style.background = b.normal; $('sk-normal-hex').textContent = b.normal;
    $('sk-normal-label').textContent = state.cardPick ? __t('s0271', '你選的膚色') : state.useExpected ? __t('s0272', '預期膚色') : __t('s0067', '原本膚色');
    $('sk-patch').style.background = b.patch; $('sk-patch-hex').textContent = b.patch;
    $('shade-summary').textContent = state.cardPick
      ? __T('s0273', () => `你選的膚色色卡：第 ${rec.skin_level} 級・${tone}。淺色區塊和這個膚色差 ${Math.round(b.delta_e)}：${LEVEL_TEXT[b.level] || ''}`, () => `Your chosen skin tone card: level ${rec.skin_level}・${tone}. The lighter patch differs from this skin tone by ${Math.round(b.delta_e)}: ${LEVEL_TEXT[b.level] || ''}`)
      : state.useExpected
      ? __T('s0274', () => `從預期妝容取色：底妝色卡第 ${rec.skin_level} 級・${tone}。淺色區塊和預期膚色差 ${Math.round(b.delta_e)}：${LEVEL_TEXT[b.level] || ''}`, () => `Color taken from the expected look: foundation card level ${rec.skin_level}・${tone}. The lighter patch differs from the expected skin tone by a color difference (ΔE) of ${Math.round(b.delta_e)}: ${LEVEL_TEXT[b.level] || ''}`)
      : __T('s0275', () => `膚色色卡第 ${rec.skin_level} 級・${tone}（照片推估）。色差 ${Math.round(b.delta_e)}：${LEVEL_TEXT[b.level] || ''}`, () => `Skin tone card level ${rec.skin_level}・${tone} (estimated from the photo). Color difference ${Math.round(b.delta_e)}: ${LEVEL_TEXT[b.level] || ''}`);
    $('shade-rows').innerHTML = shadeRows(b).map(rowHtml).join('');
  } else {
    const found = b.foundation || [];
    $('shade-summary').textContent = state.cardPick ? __T('s0276', () => `你選的膚色色卡：第 ${rec.skin_level} 級・${tone}。`, () => `Your chosen skin tone card: level ${rec.skin_level}・${tone}.`)
      : state.useExpected ? __T('s0277', () => `從預期妝容取色：底妝色卡第 ${rec.skin_level} 級・${tone}。`, () => `Color taken from the expected look: foundation card level ${rec.skin_level}・${tone}.`) : __T('s0278', () => `膚色色卡第 ${rec.skin_level} 級・${tone}（照片推估）。`, () => `Skin tone card level ${rec.skin_level}・${tone} (estimated from the photo).`);
    $('shade-rows').innerHTML = rowHtml({ k: __t('s0279', '底妝（最接近你膚色的色號）'), v: bestCode(found), hex: baseSkinHex(),
      matches: found.slice(0, 3), card: b.card, stripes: b.stripes, key: 'foundation' });
  }
  $('shade-note').textContent = (state.lightOk ? '' : __t('s0280', '這張照片光線不理想，色號僅供參考。')) + catalogNote();
  show('view-shade');
  setupPreview();
  setupCamo();
  speak(`${$('shade-summary').textContent}`);
}
// ---------------------------------------------------------------- Foundation preview (shade_preview.js, computed on the phone, no credits spent)
const pv = { hex: null, list: [], picking: false, look: 'base', gen: 0 };
// Today's makeup template: color codes come from the full looks of three makeup moods,
// local run notes (not shipped) (the three platinum-blonde queen looks) and the fair_male_grooming study (grooming lip balm)
const LOOK_TEMPLATES = [
  { id: 'base', label: __t('s0281', '只有底妝'), layers: [] },
  { id: 'grooming', label: __t('s0282', '理容'), cov: 50, layers: [{ item: 'lip', hex: '#C99198', a: 0.25, name: __t('s0283', '潤色護唇') }] },
  { id: 'natural', label: __t('s0284', '自然'), cov: 30, layers: [{ item: 'blush', hex: '#F0B2C0', a: 0.35, name: __t('s0163', '腮紅') },
    { item: 'highlight', hex: '#F9E9E2', a: 0.35, name: __t('s0092', '打亮') }, { item: 'lip', hex: '#C97A8E', a: 0.4, name: __t('s0164', '唇') }] },
  { id: 'polished', label: __t('s0285', '精緻'), cov: 35, layers: [{ item: 'contour', hex: '#877770', a: 0.3, name: __t('s0160', '修容') },
    { item: 'highlight', hex: '#F8E8E1', a: 0.4, name: __t('s0092', '打亮') }, { item: 'blush', hex: '#F0B2C0', a: 0.35, name: __t('s0163', '腮紅') },
    { item: 'lip', hex: '#A31930', a: 0.7, name: __t('s0164', '唇') }] },
  { id: 'queen_day', label: __t('s0286', '女王 日常'), cov: 35, layers: [{ item: 'eyeshadow', hex: '#B6A9AE', a: 0.35, name: __t('s0287', '眼影') },
    { item: 'blush', hex: '#D7A4B8', a: 0.4, name: __t('s0163', '腮紅') }, { item: 'highlight', hex: '#E9DFE8', a: 0.35, name: __t('s0092', '打亮') },
    { item: 'lip', hex: '#B9859A', a: 0.5, name: __t('s0164', '唇') }] },
  { id: 'queen_court', label: __t('s0288', '女王 宮廷'), cov: 40, layers: [{ item: 'eyeshadow', hex: '#9B8992', a: 0.45, name: __t('s0287', '眼影') },
    { item: 'liner', hex: '#776B74', a: 0.55, name: __t('s0289', '眼線') }, { item: 'contour', hex: '#A3949E', a: 0.3, name: __t('s0160', '修容') },
    { item: 'blush', hex: '#C98DA8', a: 0.4, name: __t('s0163', '腮紅') }, { item: 'highlight', hex: '#E8DDE8', a: 0.4, name: __t('s0092', '打亮') },
    { item: 'lip', hex: '#A96F89', a: 0.65, name: __t('s0164', '唇') }] },
  { id: 'queen_night', label: __t('s0290', '女王 夜宴'), cov: 45, layers: [{ item: 'eyeshadow', hex: '#9B8C97', a: 0.4, name: __t('s0287', '眼影') },
    { item: 'eyeshadow_outer', hex: '#716674', a: 0.6, name: __t('s0291', '眼尾煙燻') }, { item: 'lid_shimmer', hex: '#C3B8C9', a: 0.35, name: __t('s0292', '眼皮微光') },
    { item: 'liner', hex: '#665B6B', a: 0.65, name: __t('s0289', '眼線') }, { item: 'contour', hex: '#948791', a: 0.35, name: __t('s0160', '修容') },
    { item: 'blush', hex: '#B97798', a: 0.45, name: __t('s0163', '腮紅') }, { item: 'highlight', hex: '#E9DDE9', a: 0.4, name: __t('s0092', '打亮') },
    { item: 'lip', hex: '#8E5B75', a: 0.75, name: __t('s0164', '唇') }] },
];
const lookOf = (id) => LOOK_TEMPLATES.find((t) => t.id === id) || LOOK_TEMPLATES[0];
function renderPvLooks() {
  $('pv-looks').innerHTML = LOOK_TEMPLATES.map((t) => `<button class="chip" role="radio" aria-checked="${t.id === pv.look}" data-id="${t.id}">${esc(t.label)}</button>`).join('');
  for (const b of $('pv-looks').querySelectorAll('.chip')) {
    b.onclick = () => {
      pv.look = b.dataset.id;
      const t = lookOf(pv.look);
      if (t.cov != null) $('pv-cov').value = t.cov;
      renderPvLooks(); pvRender(); pvLookMatches(); speak(t.label);
    };
  }
}
async function pvLookMatches() {
  // Lips and blush in the template -> the closest shades in the catalog (sorted by color difference)
  const t = lookOf(pv.look), box = $('pv-look-match');
  const want = t.layers.filter((l) => l.item === 'lip' || l.item === 'blush');
  if (!want.length) { box.innerHTML = ''; return; }
  const token = (pv.matchToken = (pv.matchToken || 0) + 1);
  try {
    const rows = [];
    for (const l of want) {
      const r = await api('/api/match', { hex: l.hex, category: l.item, source: 'all', top: 2 });
      rows.push({ k: `${t.label}・${l.name}`, v: r.matches.length ? `${r.matches[0].brand} ${r.matches[0].code}` : __t('s0293', '照色塊找'), hex: l.hex, matches: r.matches });
    }
    if (token === pv.matchToken) box.innerHTML = rows.map(rowHtml).join('');
  } catch (e) { if (token === pv.matchToken) box.innerHTML = ''; }
}
function covLabel(v) { return v <= 30 ? __t('s0294', '輕薄') : v <= 55 ? __t('s0284', '自然') : v <= 80 ? __t('s0295', '遮蓋較高') : __t('s0296', '全覆蓋'); }
function pvShadeList() {
  // Candidates: three trial strips (one step lighter / closest / one step deeper) plus the closest shade of each brand
  const b = state.recommendation && state.recommendation.base;
  if (!b) return [];
  const list = [];
  const add = (x, tag, key) => { if (x && x.hex && !list.some((y) => y.hex === x.hex)) list.push({ hex: x.hex, key: key || '', label: `${tag ? tag + ' ' : ''}${x.code}` }); };
  for (const st of (b.stripes && b.stripes.stripes) || []) add(st, st.role, st.role_key);
  for (const m of (b.foundation || []).slice(0, 3)) add(m, '');
  return list;
}
async function setupPreview() {
  const card = $('pv-card');
  if (!window.ShadePreview || !state.photo) { card.hidden = true; return; }
  card.hidden = false;
  pvSetPicking(false);
  $('pv-dot').hidden = true;
  $('pv-match').innerHTML = '';
  pv.list = pvShadeList();
  if (!pv.list.some((x) => x.hex === pv.hex)) pv.hex = (pv.list.find((x) => x.key === 'closest') || pv.list[0] || {}).hex || null;
  renderPvChips();
  renderPvLooks();
  $('pv-status').textContent = __t('s0298', '找臉的位置中…');
  const gen = ++pv.gen;                          // When photos are changed quickly, a stale preparation result must not overwrite the new one
  try {
    await window.ShadePreview.prepare(state.photo);
    if (gen !== pv.gen) return;
    $('pv-status').textContent = __t('s0299', '點色號換顏色，拉遮蓋力看覆蓋程度；也可以用吸管點照片取色。');
    pvRender();
  } catch (e) {
    if (gen !== pv.gen) return;
    $('pv-status').textContent = __T('s0300', () => `這張照片沒辦法預覽：${e.message}`, () => `This photo can't be previewed: ${e.message}`);
    $('pv-img').src = state.photo;
  }
}
function renderPvChips() {
  $('pv-shades').innerHTML = pv.list.map((x) => `<button class="chip" role="radio" aria-checked="${x.hex === pv.hex}" data-hex="${esc(x.hex)}">`
    + `<span class="sw" style="background:${esc(x.hex)}"></span>${esc(x.label)}</button>`).join('');
  for (const b of $('pv-shades').querySelectorAll('.chip')) {
    b.onclick = () => { pv.hex = b.dataset.hex; renderPvChips(); pvRender(); speak(b.textContent); };
  }
}
let pvTimer = 0;
function pvRender() {
  const v = Number($('pv-cov').value);
  $('pv-cov-v').textContent = __T('s0301', () => `${v}（${covLabel(v)}）`, () => `${v} (${covLabel(v)})`);
  const orig = $('pv-orig').getAttribute('aria-pressed') === 'true';
  clearTimeout(pvTimer);                         // Coalesce consecutive events while the slider is dragged (requestAnimationFrame is not used: it does not run while the page is in the background)
  pvTimer = setTimeout(() => {
    try {
      const layers = lookOf(pv.look).layers;
      $('pv-img').src = orig || (!pv.hex && !layers.length) ? state.photo : window.ShadePreview.render(pv.hex, v / 100, layers);
      const area = $('pv-area').getAttribute('aria-pressed') === 'true' && !orig;
      $('pv-svg').hidden = !area;
      if (area) $('pv-svg').innerHTML = window.ShadePreview.svgOverlay(layers.length ? layers.map((l) => l.item) : []);
    }
    catch (e) { $('pv-status').textContent = e.message; }
  }, 30);
}
function pvSetPicking(on) {
  pv.picking = on;
  $('pv-pick').setAttribute('aria-pressed', String(on));
  $('pv-img').parentElement.classList.toggle('picking', on);
}
async function pvPickAt(e) {
  if (!pv.picking) return;
  const r = $('pv-img').getBoundingClientRect();
  const x = (e.clientX - r.left) / r.width, y = (e.clientY - r.top) / r.height;
  if (x < 0 || x > 1 || y < 0 || y > 1) return;
  const dot = $('pv-dot');
  dot.style.left = `${x * 100}%`; dot.style.top = `${y * 100}%`; dot.hidden = false;
  pvSetPicking(false);
  const hex = window.ShadePreview.sample(x, y);
  if (!hex) {
    dot.hidden = true;
    $('pv-status').textContent = __t('s0302', '這裡不是臉上的皮膚（或太暗、太亮）。請點臉頰或下顎附近、避開眉毛、眼睛、嘴唇和頭髮，再按一次吸管。');
    speak(__t('s0303', '請點臉上的皮膚'));
    return;
  }
  $('pv-status').textContent = __T('s0304', () => `取到 ${hex}，查對應色號中…`, () => `Got ${hex}; looking up the matching shade…`);
  try {
    // Eyedropper color -> skin-tone swatch cell -> counter shade of the same cell (the same set as the shade page), plus a concealer
    const res = await api('/api/card', { undertone: state.tone, undertone_unsure: state.toneUnsure && !state.toneFromSkin,
      selection: {}, skin_hex: hex, condition: 'albinism', source: 'all' });
    const f = res.items.find((i) => i.item === 'foundation'), c = res.items.find((i) => i.item === 'concealer');
    const rows = [];
    if (f) rows.push({ k: __t('s0305', '你點的顏色 → 底妝色號'), v: f.matches.length ? bestCode(f.matches) : __t('s0293', '照色塊找'), hex, card: f.card, matches: f.matches.slice(0, 3), key: 'foundation' });
    if (c) rows.push({ k: CARD_ITEM.concealer, v: c.matches.length ? bestCode(c.matches) : __t('s0293', '照色塊找'), hex: c.hex, card: c.card, matches: c.matches.slice(0, 3), note: c.how });
    $('pv-match').innerHTML = rows.map(rowHtml).join('');
    const add = [{ hex, label: __T('s0306', () => `吸管 ${hex}`, () => `Eyedropper ${hex}`) }, ...(f ? f.matches.slice(0, 3).map((m) => ({ hex: m.hex, label: m.code })) : [])];
    for (const a of add.reverse()) if (!pv.list.some((yy) => yy.hex === a.hex)) pv.list.unshift(a);
    pv.hex = f && f.matches[0] ? f.matches[0].hex : hex;
    renderPvChips();
    pvRender();
    $('pv-status').textContent = __T('s0307', () => `取到 ${hex}；下方是對應色號，已換成最接近的一支來預覽。`, () => `Got ${hex}; the matching shades are below, and the closest one is now used for the preview.`);
    speak(__T('s0308', () => `取到顏色，最接近的色號是 ${f && f.matches[0] ? f.matches[0].code : '沒有'}`, () => `Color picked; closest shade: ${f && f.matches[0] ? f.matches[0].code : 'none'}`));
  } catch (err) {
    $('pv-status').textContent = err.message;
  }
}

// ---------------------------------------------------------------- Local concealing for light patches (camo_editor.js: OpenCV finds the boundary -> editable SVG path -> Canvas layering)
const camo = { gen: 0, stage: null, layer: null, sel: null, timer: 0, mode: 'makeup', off: {}, check: null, rgen: 0 };
const camoOn = (id) => $(id).getAttribute('aria-pressed') === 'true';
async function setupCamo() {
  const card = $('camo-card');
  if (state.condition !== 'vitiligo' || !window.CamoEditor || !window.Vendor || !state.photo || !state.skinHex || !state.patchHex) { card.hidden = true; return; }
  card.hidden = false;
  const gen = ++camo.gen;
  camoLabels();
  $('camo-status').textContent = __t('s0309', '找淺色區塊的邊界中…（第一次要載入影像工具，約 2 秒）');
  try {
    await CamoEditor.prepare(state.photo, state.skinHex, state.patchHex);
    await CamoEditor.findPatches(Number($('camo-expand').value));
    if (gen !== camo.gen) return;
    camo.sel = null;
    await camoDrawEditor();
    camoLayerChips();
    camoRender();
    camoCrossCheck(gen);
  } catch (e) {
    if (gen !== camo.gen) return;
    $('camo-status').textContent = __T('s0310', () => `這張照片沒辦法做局部遮瑕預覽：${e.message}`, () => `This photo can't be previewed with spot concealing: ${e.message}`);
    $('camo-img').src = state.photo;
  }
}
function camoLabels() {
  $('camo-cov-v').textContent = `${$('camo-cov').value}%`;
  $('camo-feather-v').textContent = `${$('camo-feather').value}`;
  $('camo-expand-v').textContent = `${$('camo-expand').value}`;
  $('camo-hi-v').textContent = `${$('camo-hi').value}%`;
  $('camo-shine-v').textContent = `${$('camo-shine').value}%`;
  if ($('mk-strength-v')) $('mk-strength-v').textContent = `${$('mk-strength').value}%`;
  const np = Number($('camo-passes').value);
  $('camo-passes-v').textContent = np === 1 ? __t('s0311', '1 次') : __T('s0312', () => `${np} 次（第 2 次起只補還有差的地方）`, () => `${np} times (from the 2nd time on, only the spots that still differ are filled in)`);
}
const CAMO_ROUND_COLOR = ['#2fbf71', '#ff9f1c', '#3a86ff', '#e63946'];   // Green = caught the first time; orange / blue / red = the blanks left after rounds 1 / 2 / 3
async function camoDrawEditor() {
  const K = await Vendor.konva(), sz = CamoEditor.size();
  if (camo.stage) camo.stage.destroy();
  const dispW = $('camo-img').clientWidth || sz.W, sc = dispW / sz.W;
  camo.stage = new K.Stage({ container: 'camo-stage', width: dispW, height: Math.round(sz.H * sc) });
  camo.layer = new K.Layer({ scaleX: sc, scaleY: sc });
  camo.stage.add(camo.layer);
  camo.layer.visible(camoOn('camo-edges'));
  for (const p of CamoEditor.patches()) {
    const sel = p.id === camo.sel;
    const col = CAMO_ROUND_COLOR[p.round || 0];
    const path = new K.Path({ data: CamoEditor.pathD(p.pts), stroke: p.on ? col : '#9a9a9a', strokeWidth: (sel ? 3 : 2) / sc,
      dash: p.on ? null : [6 / sc, 4 / sc], fill: sel ? 'rgba(47,191,113,0.12)' : 'rgba(0,0,0,0.001)', hitStrokeWidth: 12 / sc });
    path.on('click tap', () => { camo.sel = p.id; camoDrawEditor(); camoStatus(); });
    camo.layer.add(path);
    if (sel) {
      p.pts.forEach((q, k) => {
        const dot = new K.Circle({ x: q[0], y: q[1], radius: 6 / sc, fill: '#fff', stroke: col, strokeWidth: 2 / sc, draggable: true });
        dot.on('dragmove', () => { CamoEditor.setPoint(p.id, k, dot.x(), dot.y()); path.data(CamoEditor.pathD(p.pts)); });
        dot.on('dragend', () => { CamoEditor.refreshRing(p.id); camoRender(); });
        camo.layer.add(dot);
      });
    }
  }
  camo.layer.draw();
}
// Notes on the forehead / chin zone detection (camo_editor.js zoneDetect): each zone uses its own lit brightness as the reference
// Products for the makeup-layer mode: shades come straight from the recommendation; the concealer is picked from the recommendation list as the one 'closest to the surrounding skin once converted to this photo'
// Demo photo: concealer, foundation and contour use the shades and opacities fitted by 'fitting to the target makeup'.
// The fit was done under the conversion of the expected makeup's base color (base.normal), so it applies only when 'match shades using the expected makeup' is on; the user's own photo has no target image.
function camoFit() {
  return state.useExpected && state.expected && state.expected.layer_fit ? state.expected.layer_fit : null;
}
function camoProducts() {
  const b = state.recommendation && state.recommendation.base;
  if (!b || !b.foundation || !b.foundation.length || !window.MakeupLayers) return null;
  const fit = camoFit();
  if (fit) {
    const base = camoProductsFrom(b, b.foundation[0]);
    return Object.assign(base, { concealer: fit.shades.concealer, foundation: fit.shades.foundation, contour: fit.shades.contour });
  }
  return camoProductsFrom(b, b.foundation[0]);
}
function camoProductsFrom(b, f) {
  const z = (state.expected && state.expected.zones) || {};
  const derive = (dl) => {
    const l = CamoEditor.hexToLab(f.hex), rgb = CamoEditor.labToSrgb(l[0] + dl, l[1], l[2]);
    return `#${rgb.map((v) => Math.round(v).toString(16).padStart(2, '0')).join('')}`;
  };
  return {
    corrector: (b.corrector || [])[0], edge: (b.edge || [])[0], foundation: f, normal: b.normal,
    concealer: MakeupLayers.pickClosest(b.concealer, b.normal, f.hex) || (b.concealer || [])[0],
    contour: { code: __t('s0313', '修容色'), hex: z.contour ? z.contour.hex : derive(-12) },
    highlight: { code: __t('s0314', '打亮色'), hex: z.highlight ? z.highlight.hex : derive(6) },
  };
}
const camoColors = (p) => {
  const b = state.recommendation.base, hx = (l) => (l || []).map((m) => m && m.hex).filter(Boolean);
  return { corrector: p.corrector && p.corrector.hex, concealer: p.concealer && p.concealer.hex, edge: p.edge && p.edge.hex,
    foundation: p.foundation.hex, contour: p.contour.hex, highlight: p.highlight.hex, normal: p.normal,
    // Makeup tutorial 'if one shade does not match, mix two': the concealer may be mixed from concealer + foundation candidates, the edge blend from border candidates, the foundation from foundation candidates
    concealerList: camoFit() ? [p.concealer.hex] : [...new Set([...hx(b.concealer), ...hx(b.foundation)])], edgeList: hx(b.edge),
    foundationList: camoFit() ? [p.foundation.hex] : hx(b.foundation) };
};
const camoName = (m) => (m ? (m.brand ? `${m.brand} ${m.code}` : m.code) : '');
function camoLayerChips() {
  const box = $('mk-layers');
  if (!box || !window.MakeupLayers || box.childElementCount) return;
  MakeupLayers.LAYERS.forEach((l, i) => {
    const b = document.createElement('button');
    b.className = 'chip'; b.textContent = `${i + 1} ${l.name}`; b.setAttribute('aria-pressed', 'true');
    b.onclick = () => { const on = b.getAttribute('aria-pressed') !== 'true'; b.setAttribute('aria-pressed', String(on)); camo.off[l.id] = !on; camoRender(); };
    box.appendChild(b);
  });
}
function camoSetMode(mode) {
  camo.mode = mode;
  $('camo-mode-mk').setAttribute('aria-pressed', String(mode === 'makeup'));
  $('camo-mode-algo').setAttribute('aria-pressed', String(mode !== 'makeup'));
  $('mk-controls').hidden = mode !== 'makeup';
  $('camo-algo').hidden = mode === 'makeup';
  camoRender();
}
// Face localization cross-check
async function camoCrossCheck(gen) {
  if (!window.FaceCheck) return;
  try {
    const img = new Image();
    await new Promise((ok, bad) => { img.onload = ok; img.onerror = bad; img.src = state.photo; });
    const r = await FaceCheck.crossCheck(img, CamoEditor._debug().det.face);
    if (gen !== camo.gen) return;
    camo.check = r;
    camoRender();
  } catch (e) { camo.check = { level: 'error' }; }
}
// Concealer mix recipe (makeup tutorial: if one shade does not match, mix two) + how large the remaining difference is once converted to the photo
function camoMixNote(mix, cols) {
  const m = mix && mix.concealer;
  if (!m || !cols.concealerList) return '';
  const b = state.recommendation.base, all = [...(b.concealer || []), ...(b.foundation || []), ...(b.edge || []),
    ...(camoFit() ? Object.values(camoFit().shades) : [])];
  const nm = (h) => { const x = all.find((q) => q.hex === h); return x ? camoName(x) : h; };
  const A = cols.concealerList[m.a], B = cols.concealerList[m.b];
  const recipe = m.a === m.b || m.t === 0 ? nm(A) : m.t === 10 ? nm(B) : __T('s0315', () => `${nm(A)} ${10 - m.t}：${nm(B)} ${m.t}`, () => `${nm(A)} ${10 - m.t} : ${nm(B)} ${m.t}`);
  const warn = m.de > 6 ? __T('s0316', () => `，換算到你的照片後最接近也還差 ΔE ${m.de.toFixed(1)}，上妝後會看得出色差，建議專櫃自然光再試色`, () => `, but even the closest match is still ΔE ${m.de.toFixed(1)} off once converted to your photo; a color difference will be visible after applying, so we suggest testing the shade in natural light at the counter`) : __T('s0317', () => `，換算到你的照片後差 ΔE ${m.de.toFixed(1)}`, () => `, ΔE ${m.de.toFixed(1)} off once converted to your photo`);
  return __T('s0318', () => `遮瑕用 ${recipe} 混合${warn}。`, () => `Concealer: mix ${recipe}${warn}.`);
}
function camoCheckNote() {
  const r = camo.check;
  if (!r) return '';
  if (r.level === 'agree') return __T('s0319', () => ` 臉部定位：兩種模型一致（平均差 ${Math.round(r.meanErr * 100)}% 眼距）。`, () => `Face detection: the two models agree (average difference ${Math.round(r.meanErr * 100)}% of eye distance).`);
  if (r.level === 'close') return __T('s0320', () => ` 臉部定位：兩種模型大致一致（平均差 ${Math.round(r.meanErr * 100)}% 眼距）。`, () => `Face detection: the two models mostly agree (average difference ${Math.round(r.meanErr * 100)}% of eye distance).`);
  if (r.level === 'disagree') return __T('s0321', () => ` 臉部定位：兩種模型位置差比較多（${Math.round(r.meanErr * 100)}% 眼距），上妝範圍可能偏掉，建議換一張正面、光線均勻的照片，或拖拉外框調整。`, () => `Face detection: the two models differ quite a bit in position (${Math.round(r.meanErr * 100)}% of eye distance), so the makeup area may be off. Try a front-facing photo with even lighting, or drag the frame to adjust.`);
  if (r.level === 'noface') return __t('s0322', ' 臉部定位：第二個模型沒找到臉，只用一種定位，請確認外框位置。');
  return '';
}
function camoZoneNote() {
  const z = (CamoEditor._debug() || {}).zones;
  if (!z) return '';
  const part = [z.forehead, z.chin].filter((q) => q && q.area).map((q) => q.name).join(__t('s0323', '、'));
  return part ? __T('s0324', () => ` ${part}已分區判斷${z.seg ? '（額頭含到髮際）' : ''}。`, () => `${part} assessed by zone${z.seg ? ' (forehead includes the hairline)' : ''}.`) : '';
}
function camoStatus() {
  const ps = CamoEditor.patches(), on = ps.filter((p) => p.on).length;
  const sel = ps.find((p) => p.id === camo.sel);
  const nr = ps.filter((p) => p.round).length, rounds = Math.max(0, ...ps.map((p) => p.round || 0));
  $('camo-status').textContent = __T('s0753', () => `找到 ${ps.length - nr} 塊淺色區塊${nr ? `，另補 ${rounds} 輪留白 ${nr} 塊` : ''}，正在遮 ${on} 塊。`, () => `Found ${ps.length - nr} lighter patches${nr ? `, plus ${rounds} extra rounds to fill the uncovered gaps (${nr} patches)` : ''}, currently covering ${on} patch(es).`)
    + __sp() + (sel ? __T('s0327', () => `選取第 ${sel.id} 塊（${sel.on ? '遮' : '不遮'}）：拖拉圓點調整邊界。`, () => `Selected patch ${sel.id} (${sel.on ? 'covered' : 'not covered'}): drag the dots to adjust the edge.`) : __t('s0328', '點綠色外框可以選取一塊。'))
    + camoZoneNote();
}
const camoOpt = () => ({ coverage: Number($('camo-cov').value) / 100, feather: Number($('camo-feather').value),
  corrector: camoOn('camo-corr'), keepLight: camoOn('camo-light'), passes: Number($('camo-passes').value), highlight: Number($('camo-hi').value) / 100, shine: Number($('camo-shine').value) / 100 });
// Catch the blanks and fill one more round:
// places that still show a difference after concealing -> OpenCV catches the outline -> new SVG patch (changeable color, can be toggled and its points dragged) -> cover with another layer using the already-concealed skin tone around it. At most 3 rounds.
// Demo photo measurement: blanks 56% -> 19% -> 1%, color difference 5.5 -> 2.8 -> 2.0; from round 2 the nose-bridge highlight gets flattened, so one round is recommended.
async function camoRefine() {
  if (!CamoEditor.size() || camo.busy) return;                  // On repeated taps, do not catch twice in the same round
  camo.busy = true;
  $('camo-status').textContent = __t('s0329', '找還看得出來的留白中…');
  let f;
  try { f = await CamoEditor.findResidual(camoOpt()); } finally { camo.busy = false; }
  if (f.limit) { $('camo-status').textContent = __t('s0330', '已經補到第 3 輪（上限）；再補臉會變平。可以按「清掉補的輪次」重來。'); return; }
  if (!f.added) { $('camo-status').textContent = __T('s0331', () => `沒有再抓到明顯的留白（約 ${Math.round(f.residPct * 100)}%），不用再補。`, () => `No obvious uncovered spots found (about ${Math.round(f.residPct * 100)}%), so no more filling in is needed.`); return; }
  await camoDrawEditor();
  camoRender();
}
// Light-patch mask (computed by the server, adopted 10-17 as E4): the web page sends the photo + makeup region to the computer's /api/patch-mask and gets back a per-pixel mask;
// the simulated image after the makeup is sent once more (second detection) to fill only what is still visible. If the computer is unreachable, fall back to detection on the phone as before.
const camoMask = { k1: null, m1: null, ratio: null, k2: null, m2: null, off: false, offAt: 0 };
function camoMaskPng(arr, W, H) {
  const c = document.createElement('canvas'); c.width = W; c.height = H;
  const g = c.getContext('2d'), id = g.createImageData(W, H);
  for (let i = 0; i < W * H; i++) { const v = Math.round(Math.max(0, Math.min(1, arr[i])) * 255); id.data[i * 4] = id.data[i * 4 + 1] = id.data[i * 4 + 2] = v; id.data[i * 4 + 3] = 255; }
  g.putImageData(id, 0, 0); return c.toDataURL('image/png');
}
async function camoMaskRead(url, W, H) {
  const img = await loadImage(url), c = document.createElement('canvas'); c.width = W; c.height = H;
  const g = c.getContext('2d', { willReadFrequently: true }); g.drawImage(img, 0, 0, W, H);
  const d = g.getImageData(0, 0, W, H).data, a = new Float32Array(W * H);
  for (let i = 0; i < a.length; i++) a[i] = d[i * 4] / 255;
  return a;
}
function camoMasks(base) {                    // Several redraws at the same time send only once (10-17 measurement: two consecutive renders would send twice)
  const key = JSON.stringify([state.photo.length, state.photo.slice(-48), base.colors, base.strength, base.opacity, base.off]);
  if (camoMask.inflight && camoMask.inflightKey === key) return camoMask.inflight;
  camoMask.inflightKey = key;
  camoMask.inflight = camoMasksRun(base).finally(() => { if (camoMask.inflightKey === key) camoMask.inflight = null; });
  return camoMask.inflight;
}
async function camoMasksRun(base) {
  const S = CamoEditor._debug();
  if (camoMask.off && Date.now() - camoMask.offAt > 30000) camoMask.off = false;   // When unreachable, retry after 30 seconds (no refresh needed)
  if (!S || camoMask.off || !MakeupLayers.regionMask) return null;
  const region = camoMaskPng(MakeupLayers.regionMask(), S.W, S.H);
  const k1 = `${state.photo.length}:${state.photo.slice(-48)}:${S.W}x${S.H}`;
  if (camoMask.k1 !== k1) {
    const r = await api('/api/patch-mask', { image: state.photo, region });
    Object.assign(camoMask, { k1, m1: await camoMaskRead(r.mask, S.W, S.H), ratio: r.ratio, k2: null, m2: null });
  }
  const k2 = JSON.stringify([k1, base.colors, base.strength, base.opacity, base.off]);
  if (camoMask.k2 !== k2) {
    const pre = await MakeupLayers.render(Object.assign({}, base, { patchMask: camoMask.m1 }));
    const r = await api('/api/patch-mask', { image: pre.image, region });
    Object.assign(camoMask, { k2, m2: await camoMaskRead(r.mask, S.W, S.H) });
  }
  return { patchMask: camoMask.m1, patchMask2: camoMask.m2 };
}
function camoMaskNote() {
  if (camoMask.off) return __t('s0332', ' 電腦偵測暫時連不上，先用手機上的偵測。');
  return camoMask.m1 ? __T('s0333', () => ` 淺色範圍：電腦逐像素偵測（約佔上妝範圍 ${Math.round(camoMask.ratio * 100)}%），上完妝再檢查一次補殘留。`, () => `Lighter area: detected pixel by pixel on the server (about ${Math.round(camoMask.ratio * 100)}% of the makeup area); check once more after applying to fill in anything left.`) : '';
}
function camoRender() {
  camoLabels();
  clearTimeout(camo.timer);
  camo.timer = setTimeout(async () => {
    try {
      if (camoOn('camo-orig')) { $('camo-img').src = state.photo; return; }
      const prods = camo.mode === 'makeup' ? camoProducts() : null;
      if (prods) {
        const rg = ++camo.rgen;
        const ideal = camoOn('mk-ideal'), cols = camoColors(prods);
        if (ideal) { const b = state.recommendation.base; Object.assign(cols, { concealer: b.normal, edge: b.normal, foundation: b.normal, concealerList: null, edgeList: null, foundationList: null }); }
        const fit = ideal ? null : camoFit();
        const base = { colors: cols, off: camo.off, strength: Number($('mk-strength').value) / 100, opacity: fit ? fit.opacity : undefined };
        let masks = null;
        try { masks = await camoMasks(base); } catch (e) { camoMask.off = true; camoMask.offAt = Date.now(); }
        if (rg !== camo.rgen) return;
        const r = await MakeupLayers.render(Object.assign({}, base, masks || {}));
        if (rg !== camo.rgen) return;
        $('camo-img').src = r.image;
        camoStatus();
        const used = MakeupLayers.LAYERS.filter((l) => !camo.off[l.id]).map((l) => l.name).join('→');
        $('camo-status').textContent += __T('s0334', () => ` 化妝塗層（模擬）：${used}。${ideal ? '目前是理想色號（剛好等於你的膚色）對照。' : camoMixNote(r.mix, cols)}${camoMaskNote()}${camoCheckNote()}`, () => `Makeup layers (simulated): ${used}. ${ideal ? 'Currently compared against the ideal shade (exactly your skin tone).' : camoMixNote(r.mix, cols)}${camoMaskNote()}${camoCheckNote()}`);
        return;
      }
      const r = CamoEditor.render(camoOpt());
      $('camo-img').src = r.image;
      camoStatus();
      $('camo-status').textContent += __T('s0335', () => ` 淺色區塊和周圍膚色的色差：${r.deltaE.toFixed(1)}（越小越融合）；還看得出的留白約 ${Math.round(r.residPct * 100)}%。${camoCheckNote()}`, () => `Color difference between the lighter patch and the surrounding skin tone: ${r.deltaE.toFixed(1)} (smaller blends better); uncovered spots still visible: about ${Math.round(r.residPct * 100)}%. ${camoCheckNote()}`);
    } catch (e) { $('camo-status').textContent = e.message; }
  }, 40);
}
// Illustrated step tutorial:
// each step uses the user's own photo to compute, on the phone, how that step looks + how to do it + recommended shade + color difference; the text is condensed from the recommendation data (base.steps, with sources).
async function camoGuide() {
  const base = state.recommendation && state.recommendation.base;
  const box = $('camo-guide-box'), list = $('camo-guide');
  if (!base || !CamoEditor.size()) return;
  box.hidden = false;
  list.innerHTML = __t('s0336', '<li><span></span><p>產生步驟圖中…</p></li>');
  const S = CamoEditor._debug(), W = S.W, H = S.H;
  const fx = S.det.face.map((p) => p.x * W), fy = S.det.face.map((p) => p.y * H);
  const x0 = Math.max(0, Math.min(...fx) - 0.06 * W), x1 = Math.min(W, Math.max(...fx) + 0.06 * W);
  const y0 = Math.max(0, Math.min(...fy) - 0.06 * H), y1 = Math.min(H, Math.max(...fy) + 0.04 * H);
  const load = (u) => new Promise((ok, bad) => { const i = new Image(); i.onload = () => ok(i); i.onerror = bad; i.src = u; });
  const crop = async (u, outline) => {
    const im = await load(u), cw = 224, ch = Math.round(cw * (y1 - y0) / (x1 - x0));
    const c = document.createElement('canvas'); c.width = cw; c.height = ch;
    const g = c.getContext('2d'); g.drawImage(im, x0, y0, x1 - x0, y1 - y0, 0, 0, cw, ch);
    if (outline) {
      g.save(); g.scale(cw / (x1 - x0), ch / (y1 - y0)); g.translate(-x0, -y0);
      g.strokeStyle = '#2fbf71'; g.lineWidth = 3 * (x1 - x0) / cw; g.setLineDash([8 * (x1 - x0) / cw, 5 * (x1 - x0) / cw]);
      for (const p of CamoEditor.patches()) if (p.on) g.stroke(new Path2D(CamoEditor.pathD(p.pts)));
      g.restore();
    }
    return c.toDataURL('image/jpeg', 0.85);
  };
  const sw = (m) => (m && m.hex ? `<span class="shade"><i style="background:${esc(m.hex)}"></i>${esc(m.brand ? `${m.brand} ${m.code}` : m.code)}</span>` : '');
  const prods = camo.mode === 'makeup' ? camoProducts() : null;
  if (prods) {
    // Makeup-layer mode: each step is drawing up to that layer (the same 8 layers), and the text follows the makeup tutorial
    const cols = camoColors(prods), strength = Number($('mk-strength').value) / 100;
    const msteps = [
      { t: __t('s0337', '保濕＋防曬'), d: __t('s0338', '先擦保濕，再擦防曬，等吸收再上妝；廣範圍塗抹防曬，保護整片肌膚。照光治療日是否上妝，請依醫師指示。') },
      { t: __t('s0339', '校色：只點在淺色區塊上'), d: __t('s0340', '綠色虛線就是範圍。用指尖或小刷在淺色區塊中央薄薄點上蜜桃／橘色，不要沾到旁邊的正常皮膚。'), up: 'corrector', outline: true, m: prods.corrector },
      { t: __t('s0341', '遮瑕：從中央往外推'), d: __t('s0342', '用最接近淺色區塊「周圍」膚色的遮瑕，先點在區塊中央，再用海綿拍按、往外推進周圍皮膚，邊緣才不會有一圈。拍按不要搓。'), up: 'conceal', m: prods.concealer },
      { t: __t('s0343', '第二層：只補還看得出來的地方'), d: __t('s0344', '等第一層服貼，再薄薄補在還看得出差異的地方。兩到三層薄的，比一層厚的自然。'), up: 'conceal2', m: prods.concealer },
      { t: __t('s0345', '修邊：交界往外拍開'), d: __t('s0346', '交界最容易看出色差，用交界色號沿邊緣一圈往外拍開，讓淺色區塊和周圍膚色接起來。'), up: 'edge', m: prods.edge },
      { t: __t('s0347', '粉底＋蜜粉：整臉統一'), d: __t('s0348', '全臉很薄一層粉底讓色調一致（淺色區塊上已經有遮瑕，少放一點）；再用粉撲把透明蜜粉按壓在淺色區塊和 T 字，不要刷。'), up: 'powder', m: prods.foundation },
      { t: __t('s0349', '補妝：只點還看得出來的地方'), d: __t('s0350', '上完底妝退後看一次，哪裡還看得出淺色，就只在那裡用同一支遮瑕點一點、邊緣拍開；少量多次，不要整片再蓋。'), up: 'touch3', m: prods.concealer },
      { t: __t('s0351', '修容＋打亮：找回立體感'), d: __t('s0352', '修容在顴骨下緣、下顎線、鼻側，邊緣推開；打亮只點在顴骨上緣、鼻樑（停在鼻頭前），自然妝不打整個額頭。'), up: 'highlight', m: prods.contour },
    ];
    const mItems = [];
    for (const st of msteps) {
      const fit = camoFit();
      const mk = camoMask.m1 && !camoMask.off ? { patchMask: camoMask.m1, patchMask2: camoMask.m2 } : {};   // Use the mask computed on the screen that time
      const u = st.up ? (await MakeupLayers.render(Object.assign({ colors: cols, upTo: st.up, off: camo.off, strength, opacity: fit ? fit.opacity : undefined }, mk))).image : state.photo;
      const img = st.up ? await crop(u, st.outline) : await crop((() => { const c = document.createElement('canvas'); c.width = W; c.height = H; c.getContext('2d').putImageData(new ImageData(new Uint8ClampedArray(S.px), W, H), 0, 0); return c.toDataURL('image/jpeg', 0.9); })());
      mItems.push(__T('s0353', () => `<li><img src="${img}" alt="${esc(st.t)}示意"><div><b>${esc(st.t)}</b><p>${esc(st.d)}</p>${sw(st.m)}</div></li>`, () => `<li><img src="${img}" alt="${esc(st.t)} illustration"><div><b>${esc(st.t)}</b><p>${esc(st.d)}</p>${sw(st.m)}</div></li>`));
      list.innerHTML = mItems.join('') + (mItems.length < msteps.length ? __t('s0354', '<li><span></span><p>產生下一步…</p></li>') : '');
      await new Promise((ok) => setTimeout(ok, 0));
    }
    camoRender();
    return;
  }
  const cov = 0.6;
  const steps = [
    { t: __t('s0337', '保濕＋防曬'), d: __t('s0355', '先擦保濕，再擦無色防曬。照光治療日是否上妝，請依醫師指示。'), r: { coverage: 0, concealer: false, corrector: false } },
    { t: __t('s0255', '局部校色（只點在淺色區塊上）'), d: __t('s0356', '用指尖在綠色虛線範圍內薄點蜜桃／橘色校色，分小區塊，不要整臉上；不用綠色（疊上膚色粉底會發灰）。'), r: { coverage: 0, concealer: false, corrector: true }, outline: true, m: (base.corrector || [])[0] },
    { t: __t('s0357', '遮瑕第一層：海綿點壓'), d: __t('s0358', '用接近淺色區塊「周圍」膚色的遮瑕，只點在淺色區塊上，用海綿點壓、不要來回搓（用力摩擦會刺激皮膚）。'), r: { coverage: cov, passes: 1 }, m: (base.concealer || [])[0] },
    { t: __t('s0343', '第二層：只補還看得出來的地方'), d: __t('s0359', '等一下讓第一層服貼，再薄薄補在還看得出差異的地方；已經融合的地方不要再疊，才不會厚重。'), r: { coverage: cov, passes: 2 }, m: (base.concealer || [])[0] },
    { t: __t('s0345', '修邊：交界往外拍開'), d: __t('s0360', '交界色差最明顯，用淺一點的遮瑕點在邊緣、往外拍開淡化，讓淺色區塊和周圍膚色接起來。'), r: { coverage: cov, passes: 3, feather: 16 }, m: (base.edge || [])[0] },
    { t: __t('s0361', '定妝＋打亮'), d: __t('s0362', '少量蜜粉輕壓定妝，T 字控油；最後在額頭中央、鼻樑、眼下顴骨、下巴點一點打亮，臉才不會遮完顯得暗沉、扁平。'), r: { coverage: cov, passes: 3, feather: 16, highlight: Math.max(0.4, Number($('camo-hi').value) / 100) }, m: (base.foundation || [])[0] },
  ];
  const items = [];
  let last = '';
  for (const st of steps) {
    const r = st.r ? CamoEditor.render(Object.assign({ corrector: camoOn('camo-corr'), keepLight: camoOn('camo-light') }, st.r)) : null;
    const img = st.same ? last : await crop(r.image, st.outline);
    last = img;
    items.push(__T('s0363', () => `<li><img src="${img}" alt="${esc(st.t)}示意"><div><b>${esc(st.t)}</b><p>${esc(st.d)}</p>${sw(st.m)}`, () => `<li><img src="${img}" alt="${esc(st.t)} illustration"><div><b>${esc(st.t)}</b><p>${esc(st.d)}</p>${sw(st.m)}`)
      + __T('s0754', () => `${r ? `<p class="de">${st === steps[0] ? '原本' : '這一步之後'}色差 ${r.deltaE.toFixed(1)}（越小越融合）</p>` : ''}</div></li>`, () => `${r ? `<p class="de">${st === steps[0] ? 'Original' : 'After this step'} color difference ${r.deltaE.toFixed(1)} (smaller blends in better)</p>` : ''}</div></li>`));
    list.innerHTML = items.join('') + (items.length < steps.length ? __t('s0354', '<li><span></span><p>產生下一步…</p></li>') : '');
    await new Promise((ok) => setTimeout(ok, 0));
  }
  camoRender();
}
function camoPress(id, after) {
  $(id).onclick = () => { const on = !camoOn(id); $(id).setAttribute('aria-pressed', String(on)); after(on); };
}

function renderSens() {
  for (const g of document.querySelectorAll('#view-sens [data-q]')) {
    const q = g.dataset.q;
    for (const b of g.querySelectorAll('.chip')) {
      const v = b.dataset.v === 'true' ? true : b.dataset.v === 'false' ? false : b.dataset.v;
      b.setAttribute('role', 'radio');
      b.setAttribute('aria-checked', String(!!state.sens && state.sens[q] === v));
      b.onclick = () => { state.sens = { ...(state.sens || {}), [q]: v }; renderSens(); speak(b.textContent); };
    }
  }
}
function goSens() { renderSens(); show('view-sens'); speak(__t('s0366', '三個小問題，用來調整保養和妝容建議。')); }
const LOOK_ITEMS = [['foundation', __t('s0159', '底妝')], ['contour', __t('s0160', '修容')], ['highlight', __t('s0092', '打亮')], ['brow', __t('s0367', '眉')], ['lash', __t('s0162', '睫毛')], ['blush', __t('s0163', '腮紅')], ['lip', __t('s0164', '唇')]];
function personalAdvice() {
  const s = state.sens || {}, out = [];
  if (s.irritation === 'often' || s.fragrance === 'yes') out.push(__t('s0368', '你比較容易敏感：選無香料、低刺激的產品，妝容已幫你減量。'));
  else if (s.irritation === 'sometimes' || s.fragrance === 'unsure') out.push(__t('s0369', '偶爾會不舒服：新產品先在耳後或下顎試 2–3 天。'));
  if (s.phototherapy) out.push(__t('s0370', '正在照光或擦藥：這些日子要不要上妝，請依醫師指示；擦藥的地方先問醫師再上妝。'));
  return out;
}
async function goAdvice() {
  if (!await fetchRecommendation(__t('s0371', '整理保養與妝容建議…'))) return;
  const rec = state.recommendation, full = rec.full_look;
  const pa = personalAdvice();
  $('adv-personal').hidden = !pa.length;
  $('adv-personal').innerHTML = pa.map((x) => `<p>${esc(x)}</p>`).join('');
  $('adv-care').innerHTML = (rec.care || []).map((c) => `<li><b>${esc(c.text)}</b><span class="why">${esc(c.why)}</span></li>`).join('');
  $('adv-sensitive').innerHTML = sensitiveHtml(rec.sensitive);
  const sb = $('adv-styles');
  sb.innerHTML = '';
  for (const [id, st] of Object.entries(state.presets.styles)) {
    const b = document.createElement('button');
    b.className = 'chip';
    b.setAttribute('role', 'radio');
    b.setAttribute('aria-checked', String(id === state.style));
    b.textContent = st.label;
    b.onclick = () => { if (id === state.style) return; state.style = id; savePrefs(); goAdvice(); };
    sb.appendChild(b);
  }
  $('adv-look-why').textContent = full.why;
  $('adv-look').innerHTML = LOOK_ITEMS.map(([k, label]) => {
    const id = full.selection[k] || 'keep', o = optionOf(k, id);
    const color = id === 'keep' ? null : optionColor(k, id);
    return `<li class="${id === 'keep' ? 'off' : ''}"><span class="lk">${label}</span>${color ? `<span class="sw" style="background:${color}"></span>` : ''}<span>${esc(o ? o.label : id)}</span></li>`;
  }).join('');
  $('adv-look-adj').innerHTML = full.adjustments.map((x) => `<li>${esc(x)}</li>`).join('');
  $('adv-look-src').textContent = full.source_note;
  show('view-advice');
  loadFinalLook();
  speak(__T('s0372', () => `建議：${(rec.care || []).map((c) => c.text).join('，')}。妝容：${full.why}`, () => `Suggestions: ${(rec.care || []).map((c) => c.text).join(', ')}. Makeup: ${full.why}`));
}

// ---------------------------------------------------------------- Final look (simulated): /api/final-look ( hooking it into both entrances)
const FINAL_WHO_LABEL = { F: __t('s0373', '女生示範'), M: __t('s0374', '男生示範') };
async function loadFinalLook(who) {
  const card = $('final-card'), msg = $('final-msg');
  try {
    const q = new URLSearchParams({ condition: state.condition, ...(who ? { who } : {}) });
    const j = await api(`/api/final-look?${q}`);
    $('final-img').src = j.image;
    $('final-base').src = j.base.image;
    $('final-off-fig').hidden = !j.official.image;
    if (j.official.image) $('final-off').src = j.official.image;
    $('final-off-note').textContent = j.official.note;
    $('final-credit').textContent = j.credit || '';
    $('final-steps').innerHTML = j.steps.map((s) => `<li>${s.hex ? `<span class="sw" style="background:${s.hex}"></span>` : ''}<b>${esc(s.item)}</b><span class="why">${esc(s.where)}</span></li>`).join('');
    const wb = $('final-who');
    wb.innerHTML = '';
    wb.hidden = j.who_options.length < 2;
    for (const w of j.who_options) {
      const b = document.createElement('button');
      b.className = 'chip';
      b.setAttribute('role', 'radio');
      b.setAttribute('aria-checked', String(w === j.who));
      b.textContent = FINAL_WHO_LABEL[w] || w;
      b.onclick = () => { if (w !== j.who) loadFinalLook(w); };
      wb.appendChild(b);
    }
    msg.hidden = true;
    card.hidden = false;
  } catch (e) {
    console.error('ERROR final-look', e);
    msg.textContent = __t('s0375', '定案妝容暫時看不到，其他建議照常可用。');
    msg.hidden = false;
    card.hidden = false;
  }
}

// ---------------------------------------------------------------- Simplified flow: get shade -> full-face foundation -> one-tap add makeup
// ---- Flip card
// Two images are stacked in the same frame and only which one shows is toggled; the animation uses opacity only (style.css .fc-stage), and for motion-sensitive people it just switches directly.
const HINT_ID = { base: 'base-hint', adj: 'adj-hint-flip' };
const HINT_DONE = { base: false, adj: false };     // Once flipped, the hint is no longer shown (it covers the face; once people know how, it is not needed)
const FACE = { base: 'photo', adj: 'photo' };       // The side currently shown: photo = your photo, look = the look you chose
function paintFace(p, face) {
  FACE[p] = face;
  $(`${p}-original`).classList.toggle('on', face === 'photo');
  $(`${p}-current`).classList.toggle('on', face === 'look');
  $(`${p}-cap-orig`).hidden = face !== 'photo';
  $(`${p}-caption`).hidden = face !== 'look';
  const b = $(`btn-${p}-flip`);
  b.setAttribute('aria-pressed', String(face === 'look'));
  b.textContent = face === 'photo' ? __t('s0957', '看你選的樣子') : __t('s0061', '看你的照片');
  // Every generated picture carries a badge and a provenance line that say what it is: a replayed real YouCam call (with its recording date), a live call,
  // YouCam plus local simulation, or a pure simulation (demo mode with your own photo, or the local fallback when YouCam cannot process a step)
  const src = SOURCE[p] || {};
  $(`${p}-tag`).hidden = face !== 'look' || src.kind === 'photo';           // 'Keep as is' shows the photo itself: no badge, no provenance
  $(`${p}-tag`).textContent = provBadge(src);
  $(`${p}-prov`).textContent = face === 'look' && src.kind !== 'photo' ? provLine(src) : '';
  const hint = $(HINT_ID[p]);
  hint.hidden = HINT_DONE[p];
  hint.textContent = face === 'photo' ? __t('s0958', '點一下看你選的樣子') : __t('s0959', '點一下回到你的照片');      // The hint can be tapped (static, no flashing); it sits on the top edge of the photo and is hidden after the first tap
}
function flipFace(p, face) { if (FACE[p] !== face) { HINT_DONE[p] = true; paintFace(p, face); } }
// Where the picture on each flip card came from: kind = replay_exact | replay_closest | sim_look | live | demo_label | local (from /api/makeup "source"), plus the local parts added on the phone
const SOURCE = { base: { kind: 'photo', local: [] }, adj: { kind: 'photo', local: [] } };
function setSource(p, source, localParts) { SOURCE[p] = { ...(source || {}), local: localParts || [] }; }
function provBadge(src) {
  if (src.kind === 'live') return src.local.length ? __t('s0987', 'YouCam＋本機模擬') : __t('s0986', 'YouCam 即時');
  if (src.kind === 'replay_exact' || src.kind === 'replay_closest') return src.local.length ? __t('s0987', 'YouCam＋本機模擬') : __t('s0985', 'YouCam 重播');
  return __t('s0060', '模擬');
}
function provLine(src) {
  let main;
  if (src.kind === 'live') main = __t('s0981', '即時 YouCam 結果（真實呼叫）');
  else if (src.kind === 'replay_exact' && src.recorded_at) main = __T('s0982', () => `重播真實 YouCam 呼叫結果（錄於 ${src.recorded_at}）`, () => `Replayed real YouCam call (recorded ${src.recorded_at})`);
  else if (src.kind === 'replay_exact' || src.kind === 'replay_closest') main = __t('s0983', '重播真實 YouCam 呼叫結果（最接近的已錄組合）');
  else if (src.kind === 'local') main = __t('s0967', '本機模擬（YouCam 暫時處理不了）');
  else if (src.kind === 'sim_look') main = __t('s1000', '模擬：展示模式沒有這組選項的 YouCam 錄製結果，顯示的是本機預先合成的完成妝容');
  else main = __t('s0984', '模擬：展示模式未呼叫 YouCam（你的照片原樣加上標示）');
  return [main].concat(src.local || []).join(' + ');
}
for (const p of ['base', 'adj']) {
  const toggle = () => flipFace(p, FACE[p] === 'photo' ? 'look' : 'photo');
  $(`btn-${p}-flip`).onclick = toggle;
  $(`${p}-stage`).onclick = toggle;
}

// ---- Helper layer: the parts YouCam cannot do alone (no region parameter, cannot handle extreme angles or very faint eyebrows...) are filled in by local coloring on the phone
// Local coloring = foundation preview (ShadePreview) + local concealing for the lighter-patch entrance (Camouflage); both are rule-based computation, do not call YouCam, spend no credits, and are all labeled 'simulated'.
async function localAssistImage() {
  if (!window.ShadePreview || !state.photo) return null;
  try {
    await window.ShadePreview.prepare(state.photo);
    const list = pvShadeList();
    const hex = (list.find((x) => x.key === 'closest') || list[0] || {}).hex || pv.hex;
    if (!hex) return null;
    let image = window.ShadePreview.render(hex, 0.6, lookOf(pv.look).layers);
    if (state.condition === 'vitiligo' && window.Camouflage && state.skinHex && state.patchHex) {
      const cam = await window.Camouflage.apply(image, state.photo, state.skinHex, state.patchHex).catch(() => null);
      if (cam) image = cam.image;
    }
    return image;
  } catch (_) { return null; }
}
function sourceName(src) {
  if (src && src.kind === 'live') return __t('s0963', 'YouCam 試妝');
  if (src && (src.kind === 'replay_exact' || src.kind === 'replay_closest')) return __t('s0985', 'YouCam 重播');
  return __t('s0060', '模擬');
}
function sourceLine(parts) { return __t('s0962', '來源：') + parts.join(' + '); }

async function goBase() {
  if (!state.recommendation && !await fetchRecommendation(__t('s0270', '找最接近你膚色的色號…'))) return;
  const rec = state.recommendation;
  if (!rec.foundation_shade) { goQuick(); return; }            // With no shade, fall back to the original three choices
  state.baseLooks = {
    // Step one = foundation + highlight (very-light-skin also gets contour): very-light-skin 'makes features more dimensional', lighter-patch 'highlights the T-zone' (design note 10-16)
    foundation: { foundation: rec.looks.glow.selection.foundation, highlight: rec.looks.glow.selection.highlight || 'center',
      contour: rec.looks.glow.selection.contour || 'keep' },
    glow: rec.looks.glow.selection,
    clear: rec.looks.clear.selection,
    full: rec.full_look ? rec.full_look.selection : rec.looks.glow.selection,
  };
  $('base-original').src = state.photo;
  paintFace('base', 'photo');                    // On entering, show your photo first; the effect appears only when flipped
  renderBaseShades();
  await applyBase('full');
  show('view-base');
  if (rec.sensitive) speak(rec.sensitive.title);
}
const BASE_TITLE = { keep: __t('s0138', '保留原樣'), full: __t('s0136', '完整妝容'), foundation: __t('s0376', '全臉底妝＋面中打亮'), glow: __t('s0377', '底妝打亮＋好氣色'), clear: __t('s0378', '底妝打亮＋好氣色＋清晰') };
const BASE_KEYS = ['keep', 'full', 'foundation', 'glow', 'clear'];
// Demo mode only replays a few recorded real results; when the photo is the same after changing options, say so clearly so people do not think the button is broken
const MOCK_SAME = __t('s0379', '展示模式只錄了幾種組合，這個選項的變化要在正式模式（實際試妝）才看得到。');
const BASE_FIRST = { albinism: __t('s0380', '全臉底妝＋修容打亮'), vitiligo: __t('s0381', '全臉底妝＋T 字打亮') };
function baseTitle(which) { return which === 'foundation' ? (BASE_FIRST[state.condition] || BASE_TITLE.foundation) : BASE_TITLE[which]; }
async function applyBase(which) {
  for (const b of BASE_KEYS) $(`btn-base-${b}`).setAttribute('aria-pressed', String(b === which));
  if (which === 'keep') {
    state.selection = {};
    for (const it of ITEMS) state.selection[it.key] = 'keep';
    state.currentImage = state.photo;
    $('base-current').src = state.photo;
    $('base-caption').textContent = BASE_TITLE.keep;
    $('base-desc').textContent = __t('s0382', '不加任何妝。');
    setSource('base', { kind: 'photo' }, []);
    paintFace('base', FACE.base);
    speak(__t('s0138', '保留原樣'));
    return;
  }
  const look = state.baseLooks[which];
  const sel = {};
  for (const it of ITEMS) sel[it.key] = look[it.key] || 'keep';
  busy(true, which === 'foundation' ? __t('s0383', '全臉上底妝中…') : which === 'full' ? __t('s0384', '上完整妝容中…') : __t('s0385', '加妝中…'));
  try {
    const local = useLocalBrow(sel);                // The user tapped the brow shape: the official result does not draw brows, so the phone draws them after the result arrives
    const res = await api('/api/makeup', { style: state.style, undertone: state.tone, condition: state.condition,
      selection: local ? { ...sel, brow: 'keep' } : sel, ...photoPayload() });
    let image = res.image, extra = '', usedCam = false;
    const sameAsBefore = state.mode === 'mock' && which !== 'keep' && state.baseRaw === res.image && state.baseWhich !== which;
    state.baseRaw = res.image;                       // YouCam's original result (before local concealing), for comparison
    state.baseWhich = which;
    if (state.condition === 'vitiligo' && window.Camouflage && state.skinHex && state.patchHex) {
      // Lighter-patch: the try-on cannot target a region -> identify the lighter-patch area on the phone, simulate 'dabbing the foundation in the surrounding skin tone onto the patches', then blend it across the whole face
      busy(true, __t('s0386', '找出淺色區塊範圍、局部遮瑕中…'));
      const cam = await window.Camouflage.apply(res.image, state.photo, state.skinHex, state.patchHex).catch(() => null);
      if (cam) {
        image = cam.image;
        usedCam = true;
        extra = __T('s0387', () => `＋淺色區塊局部遮瑕（手機上模擬，判別到淺色區塊約佔臉部 ${Math.round(cam.ratio * 100)}%）`, () => `+ spot concealing on the lighter patch (simulated on your phone; the lighter patches detected make up about ${Math.round(cam.ratio * 100)}% of the face)`);
      } else {
        extra = __t('s0388', '（這張照片沒能判別淺色區塊範圍，只顯示全臉底妝）');
      }
    }
    if (local) image = await drawBrowsOn(image, browColorFor(sel.brow));
    state.selection = sel;
    state.currentImage = image;
    $('base-current').src = image;
    $('base-caption').textContent = baseTitle(which) + (extra && /^[＋+]/.test(extra) ? __sp() + __t('s0390', '＋局部遮瑕') : '');
    $('base-desc').textContent = describe(sel) + (extra ? __t('s0391', '。') + extra : '') + (sameAsBefore ? __T('s0392', () => `。${MOCK_SAME}`, () => `. ${MOCK_SAME}`) : '');
    $('base-source').textContent = sourceLine([sourceName(res.source), usedCam ? __t('s0964', '本機局部遮瑕（模擬）') : null, local ? __t('s0965', '本機畫眉（模擬）') : null].filter(Boolean));
    setSource('base', res.source, [usedCam ? __t('s0964', '本機局部遮瑕（模擬）') : null, local ? __t('s0965', '本機畫眉（模擬）') : null].filter(Boolean));
    if (state.mode === 'mock' && state.demo) $('base-desc').textContent += __sp() + __t('s0988', '示範模式下，這兩張示範照的各個妝容與原圖平均每個像素只差約 3–8（滿分 255），因為畫面是錄好的真實呼叫結果，示範模式不能重新試妝；點圖片在「你的照片／你選的樣子」之間切換比較。');
    paintFace('base', FACE.base);
    busy(false);
    speak(__T('s0393', () => `${baseTitle(which)}。${describe(sel)}`, () => `${baseTitle(which)}. ${describe(sel)}`));
  } catch (e) {
    const fb = sel.foundation !== 'keep' ? await localAssistImage() : null;     // The part local code can take over exists only when there is foundation to apply
    if (!fb) { busy(false); alert(e.message); return; }
    state.selection = sel;
    state.currentImage = fb;
    $('base-current').src = fb;
    $('base-caption').textContent = baseTitle(which);
    $('base-desc').textContent = __t('s0966', '這一步 YouCam 暫時處理不了，改用手機上的本機模擬：只有底妝與局部遮瑕，其他項目維持原樣。') + __sp() + `(${e.message})`;
    $('base-source').textContent = sourceLine([__t('s0967', '本機模擬（YouCam 暫時處理不了）')]);
    setSource('base', { kind: 'local' }, []);
    paintFace('base', FACE.base);
    busy(false);
    speak($('base-desc').textContent);
  }
}
function sensitiveHtml(sn) {
  if (!sn) return '';
  return `<h3>${esc(sn.title)}</h3><ul class="steps">`
    + sn.items.map((x) => `<li>${esc(x.text)}<span class="why">${esc(x.why)}</span></li>`).join('') + '</ul>';
}
function renderBaseShades() {
  const b = state.recommendation.base;
  const vit = state.condition === 'vitiligo';
  if (vit) {
    const rows = shadeRows(b);
    rows[0].k = __t('s0394', '全臉底妝（對淺色區塊周圍的膚色）');
    $('base-shades').innerHTML = rowHtml(rows[0]);
    $('base-vit').hidden = false;
    $('base-vit-rows').innerHTML = rows.slice(1).map(rowHtml).join('');
    $('base-vit-steps').innerHTML = b.steps.map((t) => `<li>${esc(t.replace(/\*\*/g, ''))}</li>`).join('');
  } else {
    const found = b.foundation || [];
    $('base-shades').innerHTML = rowHtml({ k: __t('s0395', '全臉底妝（最接近你膚色的色號）'), v: bestCode(found), hex: state.skinHex,
      matches: found.slice(0, 3), card: b.card, stripes: b.stripes });
    $('base-vit').hidden = true;
  }
  $('base-note').textContent = catalogNote();
  $('base-sensitive').innerHTML = sensitiveHtml(state.recommendation.sensitive);
}

// ---------------------------------------------------------------- Quick three choices
async function goQuick() {
  // Match the skin tone first (local, no credits): when 'not sure' is chosen, use the warm/cool estimated from the photo directly
  state.toneFromSkin = null;
  if (state.skinHex) {
    try {
      const r = await api('/api/recommend', recommendPayload());
      state.recommendation = r.recommendation;
      if (state.toneUnsure) { state.tone = r.recommendation.undertone_id; state.toneFromSkin = state.tone; }
    } catch (_) { state.recommendation = null; }
  }
  busy(true, __t('s0396', '產生兩種妝…'));
  try {
    const res = await api('/api/looks', { style: state.style, undertone: state.tone, condition: state.condition, ...photoPayload() });
    state.quick = res.looks;
  } catch (e) { busy(false); alert(e.message); return; }
  busy(false);
  const cards = [
    { id: 'keep', title: __t('s0138', '保留原樣'), desc: __t('s0397', '不加任何妝'), image: state.photo, selection: {} },
    { id: 'glow', title: __t('s0140', '好氣色'), desc: describe(state.quick.glow.selection), image: state.quick.glow.image, selection: state.quick.glow.selection, source: state.quick.glow.source },
    { id: 'clear', title: __t('s0141', '清晰'), desc: describe(state.quick.clear.selection), image: state.quick.clear.image, selection: state.quick.clear.selection, source: state.quick.clear.source },
  ];
  const box = $('quick-cards');
  box.innerHTML = '';
  for (const c of cards) {
    const b = document.createElement('button');
    b.className = 'quick-card';
    // A generated picture says what it is on the card too: replayed or live YouCam, or a simulation (the keep-as-is card is the photo itself)
    const tag = c.source && c.source.kind && c.source.kind !== 'photo' ? `<span class="sim-tag fc-tag">${provBadge({ ...c.source, local: [] })}</span>` : '';
    b.innerHTML = `${tag}<img alt="${c.title}" src="${c.image}"><strong>${c.title}</strong><span>${c.desc}</span>`;
    b.onclick = () => goAdjust(c);
    box.appendChild(b);
  }
  $('quick-note').textContent = (state.toneFromSkin ? __T('s0398', () => `依照片膚色推估：${state.presets.undertones[state.toneFromSkin].label}（請確認，可以回第一頁改）。`, () => `Estimated from your photo's skin tone: ${state.presets.undertones[state.toneFromSkin].label} (please check; you can go back to page 1 to change it).`) : '')
    + __t('s0399', '點一張你喜歡的，下一步可以逐項調整。')
    + (state.mode === 'mock' ? __sp() + __t('s0400', '（展示模式：示範照片顯示的是錄好的相近結果，不一定完全對應選項。）') : '');
  show('view-quick');
  speak(__t('s0401', '三種妝：保留原樣、好氣色、清晰。點一張喜歡的。'));
}
function describe(sel) {
  const parts = [];
  for (const it of ITEMS) { const id = sel[it.key]; if (id && id !== 'keep') parts.push(__T('s0402', () => `${it.label}：${optionOf(it.key, id).label}`, () => `${it.label}: ${optionOf(it.key, id).label}`)); }
  return parts.join(__t('s0323', '、')) || __t('s0403', '不加妝');
}

// ---------------------------------------------------------------- Fine adjustment
function goAdjust(card) {
  state.selection = {};
  for (const it of ITEMS) state.selection[it.key] = card.selection[it.key] || 'keep';
  $('layer-strip').hidden = true;
  state.currentImage = card.image;
  // The picture on the adjust page carries its own source label from the first view, not only after an option is changed
  setSource('adj', card.source || { kind: 'photo' }, (card.source && card.source.local) || []);
  $('adj-original').src = state.photo;
  $('adj-current').src = card.image;
  paintFace('adj', 'photo');
  $('adj-caption').textContent = card.title;
  $('adj-hint').hidden = true;
  $('adj-cost').textContent = state.mode === 'live' ? __t('s0404', '每換一次會試妝一次（1 點）。') : __t('s0405', '展示模式：不會扣點。');
  renderTabs();
  show('view-adjust');
  speak(__T('s0406', () => `已選${card.title}。可以逐項調整眉毛、睫毛、腮紅、唇。`, () => `Selected ${card.title}. You can now adjust brows, lashes, blush and lips one by one.`));
}
function renderTabs() {
  const tabs = $('item-tabs');
  tabs.innerHTML = '';
  const items = ITEMS.filter((it) => !(it.key === 'brow' && browPale()));
  if (!items.some((it) => it.key === state.item)) state.item = items[0].key;
  for (const it of items) {
    const b = document.createElement('button');
    b.className = 'chip';
    b.setAttribute('role', 'tab');
    b.setAttribute('aria-selected', String(it.key === state.item));
    b.textContent = it.label;
    b.onclick = () => { state.item = it.key; renderTabs(); speak(it.label); };
    tabs.appendChild(b);
  }
  const allowed = state.presets.styles[state.style].options[state.item]
    .filter((id) => state.browHex || !(optionOf(state.item, id) || {}).from_photo)
    .filter((id) => id === 'keep' || state.item !== 'foundation' || foundationShade());
  const box = $('item-options');
  box.innerHTML = '';
  for (const id of allowed) {
    const o = optionOf(state.item, id);
    const color = optionColor(state.item, id);
    const b = document.createElement('button');
    b.className = 'chip';
    b.setAttribute('role', 'radio');
    b.setAttribute('aria-checked', String(state.selection[state.item] === id));
    b.innerHTML = `${color ? `<span class="sw" style="background:${color}"></span>` : ''}${o.label}`;
    b.onclick = () => changeOption(state.item, id);
    box.appendChild(b);
  }
  $('item-note').hidden = state.item !== 'foundation';
  $('item-note').textContent = state.item === 'foundation' ? foundationNote() : '';
  $('btn-layers').textContent = layersLabel();
}
function foundationNote() {
  const f = foundationShade();
  if (!f) return __t('s0407', '先在照片上取膚色，才能自動對應底妝色號。');
  const fit = f.fit ? __T('s0408', () => `，${f.fit}`, () => `, ${f.fit}`) : '';
  const o = optionOf('foundation', state.selection.foundation || 'keep');
  const limits = o && o.limits ? ` ${o.limits}` : '';      // E.g. BB cream: quick, but few shades and light coverage
  return __T('s0409', () => `底妝顏色自動用最接近你膚色的色號：${f.brand} ${f.code}（膚色色卡第 ${f.level} 級${fit}）。YouCam 底妝會依皮膚範圍全臉均勻上。${limits}`, () => `Foundation color automatically uses the shade closest to your skin tone: ${f.brand} ${f.code} (skin tone card level ${f.level}${fit}). YouCam foundation is applied evenly over the whole face within the skin area. ${limits}`);
}
async function changeOption(item, id) {
  if (state.selection[item] === id) return;
  const next = { ...state.selection, [item]: id };
  busy(true);
  try {
    const local = useLocalBrow(next);
    const res = await api('/api/makeup', { style: state.style, undertone: state.tone,
      selection: local ? { ...next, brow: 'keep' } : next, ...photoPayload() });
    const sameAsBefore = state.mode === 'mock' && res.image === state.currentImage;
    const image = local ? await drawBrowsOn(res.image, browColorFor(next.brow)) : res.image;
    state.selection = next;
    $('layer-strip').hidden = true;                 // After options change, the old layer-by-layer results no longer correspond
    state.currentImage = image;
    $('adj-current').src = image;
    $('adj-caption').textContent = __t('s0147', '目前的妝');
    setSource('adj', res.source, local ? [__t('s0965', '本機畫眉（模擬）')] : []);
    flipFace('adj', 'look');                        // After options change, flip to show the effect
    paintFace('adj', 'look');
    const hint = [sameAsBefore ? MOCK_SAME : '', res.hint || ''].filter(Boolean).join(' ');
    $('adj-hint').hidden = !hint;
    $('adj-hint').textContent = hint;
    busy(false);
    renderTabs();
    speak(__T('s0410', () => `${ITEMS.find((i) => i.key === item).label}：${optionOf(item, id).label}${res.hint ? '。' + res.hint : ''}`, () => `${ITEMS.find((i) => i.key === item).label}: ${optionOf(item, id).label}${res.hint ? '. ' + res.hint : ''}`));
  } catch (e) {
    const fb = (next.foundation || 'keep') !== 'keep' ? await localAssistImage() : null;
    if (!fb) { busy(false); alert(e.message); return; }
    state.selection = next;
    $('layer-strip').hidden = true;
    state.currentImage = fb;
    $('adj-current').src = fb;
    $('adj-caption').textContent = __t('s0967', '本機模擬（YouCam 暫時處理不了）');
    setSource('adj', { kind: 'local' }, []);
    flipFace('adj', 'look');
    paintFace('adj', 'look');
    $('adj-hint').hidden = false;
    $('adj-hint').textContent = __t('s0966', '這一步 YouCam 暫時處理不了，改用手機上的本機模擬：只有底妝與局部遮瑕，其他項目維持原樣。') + __sp() + `(${e.message})`;
    busy(false);
    renderTabs();
  }
}

function catalogNote() {
  // Demo catalog mode (for recording videos and screenshots): no third-party brand names appear on screen
  return state.catalogKind === 'demo'
    ? __t('s0411', '畫面上的色號是示範型錄（虛構品牌）。實際使用時，可改載入品牌提供的色號檔：系統依膚色色卡的深淺與冷暖，再加上與膚色的色差排序，列出最接近的色號。請以專櫃自然光試色為準。')
    : __t('s0412', '底妝、遮瑕：系統依膚色色卡（深淺 10 級×冷暖）與色差，從各品牌色號檔列出最接近的；冷暖用品牌自己的標示。色票是網頁顯示色，請以專櫃自然光試色為準。');
}

// ---------------------------------------------------------------- View layer by layer: foundation -> + blush -> + lips -> + brows -> + lashes
function layerCount() { return ['foundation', 'blush', 'lip', 'brow', 'lash'].filter((k) => (state.selection[k] || 'keep') !== 'keep').length; }
function layersLabel() {
  const n = layerCount();
  return __T('s0755', () => `逐層看這個妝（${n} 層${state.mode === 'mock' ? '，展示模式不扣點' : `，多 ${n} 點`}）`, () => `See this look layer by layer (${n} layers${state.mode === 'mock' ? ', no credits deducted in Demo mode' : `, ${n} extra credits`})`);
}
async function runLayers() {
  if (!layerCount()) { alert(__t('s0415', '目前是保留原樣，沒有可以疊的妝。')); return; }
  busy(true, __t('s0416', '一層一層試妝中…'));
  let r;
  try {
    r = await api('/api/layers', { style: state.style, undertone: state.tone, condition: state.condition,
      selection: state.selection, ...photoPayload() });
  } catch (e) { busy(false); alert(e.message); return; }
  busy(false);
  const strip = $('layer-strip');
  const cells = [{ label: __t('s0134', '你的照片'), image: state.photo }].concat(r.layers.map((l) => ({ label: __T('s0417', () => `＋${l.item_label}（${l.option_label}）`, () => `+${l.item_label} (${l.option_label})`), image: l.image })));
  strip.innerHTML = cells.map((c, i) => `<figure><img alt="${esc(c.label)}" src="${c.image}"><figcaption>${i ? i + '. ' : ''}${esc(c.label)}</figcaption></figure>`).join('');
  strip.hidden = false;
  speak(cells.map((c) => c.label).join(__t('s0418', '，')));
}

// ---------------------------------------------------------------- Suggestions by facial feature (runs only when the user taps; official mode 10 points)
function faceLabel(again) {
  return __T('s0419', () => `${again ? '重新分析五官' : '依五官給建議'}${state.mode === 'mock' ? '（展示模式：不扣點）' : '（多 10 點）'}`, () => `${again ? 'Re-analyze features' : 'Get suggestions by features'}${state.mode === 'mock' ? ' (Demo mode: no credits deducted)' : ' (10 extra credits)'}`);
}
async function runFace() {
  busy(true, __t('s0420', '分析五官中…'));
  let r;
  try { r = await api('/api/face', { style: state.style, ...photoPayload() }); }
  catch (e) { busy(false); alert(e.message); return; }
  busy(false);
  if (!r.available) { $('face-tips').hidden = false; $('face-tips').innerHTML = `<li>${esc(r.hint)}</li>`; return; }
  state.face = r.advice;
  $('face-tips').hidden = false;
  $('face-tips').innerHTML = r.advice.tips.map((t) => `<li>${esc(t)}</li>`).join('') + `<li class="muted">${esc(r.advice.note)}</li>`;
  const box = $('face-suggest');
  box.innerHTML = '';
  const allowed = state.presets.styles[state.style].options;
  for (const [item, id] of Object.entries(r.advice.suggest)) {
    if (!allowed[item] || !allowed[item].includes(id) || (optionOf(item, id) || {}).from_photo && !state.browHex) continue;
    if (state.selection[item] === id) continue;
    const b = document.createElement('button');
    b.className = 'chip';
    b.textContent = __T('s0421', () => `套用：${ITEMS.find((i) => i.key === item).label}「${optionOf(item, id).label}」`, () => `Applied: ${ITEMS.find((i) => i.key === item).label} "${optionOf(item, id).label}"`);
    b.onclick = () => { box.removeChild(b); changeOption(item, id); };
    box.appendChild(b);
  }
  $('btn-face').textContent = faceLabel(true);
  speak(r.advice.tips.join(__t('s0391', '。') + __sp()));
}

// ---------------------------------------------------------------- Palette card
function cardRows() {
  const rows = [];
  const s = state.presets.styles[state.style];
  rows.push({ k: __t('s0121', '妝感'), v: s.label });
  rows.push({ k: __t('s0173', '膚色'), v: state.presets.undertones[state.tone].label + (state.toneFromSkin ? __sp() + __t('s0422', '（照片推估）') : '') });
  for (const it of ITEMS) {
    if (it.key === 'foundation') continue;      // Foundation goes in the row of its 'matching shade'
    const id = state.selection[it.key] || 'keep';
    rows.push({ k: it.label, v: optionOf(it.key, id).label, hex: optionColor(it.key, id) });
  }
  return rows;
}
function foundationTried() {
  const id = state.selection.foundation || 'keep';
  return id === 'keep' ? '' : __sp() + __T('s0423', () => `（試妝：底妝「${optionOf('foundation', id).label}」）`, () => `(Try-on: foundation "${optionOf('foundation', id).label}")`);
}
const SOURCE_LABEL = { demo: __t('s0424', '示範型錄'), official: __t('s0425', '官網'), partner: __t('s0426', '公開色票 2018') };
const CARD_ITEM = { foundation: __t('s0427', '底妝（原膚色）'), concealer: __t('s0428', '遮瑕（泛紅、黑眼圈）'), brow: __t('s0161', '眉毛'), lash: __t('s0162', '睫毛'), blush: __t('s0163', '腮紅'), lip: __t('s0164', '唇') };
function esc(t) { return String(t).replace(/[&<>"]/g, (ch) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[ch])); }
function matchesFor(key) {
  const it = state.card && state.card.items.find((x) => x.item === key);
  return it ? it.matches : [];
}
// Link to the brand's own product page, only when the catalog row carries one (official shade data has source_url; the demo catalog and alias mode have none)
function productLink(m) {
  const label = __t('s0992', '看商品頁');
  return typeof m.url === 'string' && /^https:\/\//.test(m.url) ? ` <a class="shop" href="${esc(m.url)}" target="_blank" rel="noopener noreferrer">${esc(label)}</a>` : '';
}
// Plain-text summary for the beauty advisor: skin tone level and undertone, then every item with its matched shades (brand, product, code, and the product page when known)
function advisorText() {
  const rows = cardRowsWithMatches();
  const cell = (rows.find((r) => r.card) || {}).card;
  const rec = state.recommendation || {};
  const tone = state.presets.undertones[state.tone].label;
  const head = cell ? __T('s0994', () => `膚色色卡：第 ${cell.level} 級（${cell.level_name}）・${cell.tone_label}`, () => `Skin tone card: level ${cell.level} (${cell.level_name})・${cell.tone_label}`)
    : __T('s0997', () => `膚色色卡：第 ${rec.skin_level || '–'} 級・${tone}`, () => `Skin tone card: level ${rec.skin_level || '–'}・${tone}`);
  const lines = [__t('s0993', '給櫃姐的摘要'), head, __t('s0995', '色號清單（品牌 產品 色號）：')];
  for (const r of rows) {
    if (!r.matches || !r.matches.length) continue;
    lines.push(__T('s0999', () => `${r.k}：${r.v}`, () => `${r.k}: ${r.v}`));
    for (const m of r.matches) lines.push(`  - ${m.brand} ${m.product} ${m.code}${m.url ? ' ' + m.url : ''}`);
  }
  lines.push(__t('s0996', '以專櫃自然光試色為準。'));
  return lines.join('\n');
}
async function copyForAdvisor() {
  const text = advisorText();
  $('staff-text').value = text;
  let ok = false;
  try { if (navigator.clipboard && navigator.clipboard.writeText) { await navigator.clipboard.writeText(text); ok = true; } } catch (e) { ok = false; }
  $('staff-text').hidden = ok;
  $('copy-status').textContent = ok ? __t('s0990', '已複製') : __t('s0991', '無法自動複製，請長按下方文字選取');
  $('copy-status').hidden = false;
  if (!ok) { $('staff-text').focus(); $('staff-text').select(); }
}
function matchLine(m) {
  // Skin-tone items (via the skin-tone swatch) show 'same depth, same warm/cool'; others (brow, lip, blush) show the color difference
  return m.fit ? __T('s0429', () => `${m.brand} ${m.product}：${m.code}（${m.fit}）`, () => `${m.brand} ${m.product}: ${m.code} (${m.fit})`) : __T('s0430', () => `${m.brand} ${m.product}：${m.code}（${m.verdict}，色差 ${m.delta_e}）`, () => `${m.brand} ${m.product}: ${m.code} (${m.verdict}, color difference ${m.delta_e})`);
}
function cardStrip(cell) {
  if (!cell || !state.skinCard) return '';
  const sw = state.skinCard.cells.filter((c) => c.tone === cell.tone)
    .map((c) => __T('s0431', () => `<span class="strip-cell${c.level === cell.level ? ' on' : ''}" style="background:${c.hex}" title="第 ${c.level} 級"></span>`, () => `<span class="strip-cell${c.level === cell.level ? ' on' : ''}" style="background:${c.hex}" title="Level ${c.level}"></span>`)).join('');
  return __T('s0432', () => `<div class="strip"><div class="strip-label">膚色色卡：第 ${cell.level} 級（${cell.level_name}）・${cell.tone_label}`, () => `<div class="strip"><div class="strip-label">Skin tone card: level ${cell.level} (${cell.level_name})・${cell.tone_label}`)
    + __T('s0433', () => `${cell.tone_confirm ? '（冷暖請本人確認）' : ''}</div><div class="strip-row">${sw}</div></div>`, () => `${cell.tone_confirm ? ' (please confirm cool/warm yourself)' : ''}</div><div class="strip-row">${sw}</div></div>`);
}
function cardRowsWithMatches() {
  const rows = cardRows();
  const vitBase = state.condition === 'vitiligo' && state.recommendation && state.recommendation.base;
  if (vitBase) { const sr = shadeRows(vitBase); sr[0].v += foundationTried(); rows.splice(2, 0, ...sr); }
  else if (state.skinHex) {
    const it = state.card && state.card.items.find((x) => x.item === 'foundation');
    rows.splice(2, 0, { k: CARD_ITEM.foundation, v: (it && it.matches.length ? bestCode(it.matches) : __t('s0434', '對應色號')) + foundationTried(), hex: state.skinHex,
      key: 'foundation', card: it && it.card, matches: it ? it.matches.slice(0, 4) : [],
      stripes: state.recommendation && state.recommendation.base && state.recommendation.base.stripes });
  }
  const con = state.card && state.card.items.find((x) => x.item === 'concealer');
  if (con) {
    const at = rows.findIndex((r) => r.key === 'foundation');
    rows.splice(at + 1, 0, { k: CARD_ITEM.concealer, v: con.matches.length ? bestCode(con.matches) : __t('s0293', '照色塊找'), hex: con.hex,
      key: 'concealer', card: con.card, matches: con.matches.slice(0, 3), note: con.how });
  }
  for (const r of rows) {
    const it = ITEMS.find((x) => x.label === r.k);
    if (it) r.key = it.key;
    if (!r.matches) r.matches = r.key ? matchesFor(r.key) : [];
    const face = (r.key === 'highlight' || r.key === 'contour') && state.card && state.card.items.find((x) => x.item === r.key && x.how);
    if (face) { r.hex = face.hex; r.note = __T('s0435', () => `${face.how}（色號${face.basis}，目標色 ${face.hex}）${face.warn ? ' ' + face.warn : ''}`, () => `${face.how} (${face.basis}, target color ${face.hex})${face.warn ? ' ' + face.warn : ''}`); }
  }
  return rows;
}
function stripesHtml(st) {
  // Three trial strips: one step lighter / closest / one step deeper from the same product line, one strip each along the jawline
  if (!st || !st.stripes || st.stripes.length < 2) return '';
  const items = st.stripes.map((x) => `<span class="stripe"><span class="stripe-sw" style="background:${esc(x.hex)}"></span>`
    + `<b>${esc(x.role)}</b> ${esc(x.code)}</span>`).join('');
  return __T('s0436', () => `<div class="stripes"><div class="stripes-k">專櫃試色三條（${esc(st.stripes[0].brand)} ${esc(st.stripes[0].product)}）</div>`, () => `<div class="stripes"><div class="stripes-k">Three counter test stripes (${esc(st.stripes[0].brand)} ${esc(st.stripes[0].product)})</div>`)
    + `<div class="stripes-row">${items}</div><div class="mix">${esc(st.how || '')}</div></div>`;
}
function rowHtml(r) {
  const ms = (r.matches || []).map((m) => `<div class="match"><span class="sw" style="background:${esc(m.hex)}"></span>`
    + `${esc(matchLine(m))}<span class="tag">${esc(SOURCE_LABEL[m.source] || m.source)}</span>${productLink(m)}`
    + __T('s0756', () => `${m.mix ? `<div class="mix">也可以：${esc(m.mix.lighter)} 和 ${esc(m.mix.deeper)} 各取一半混合</div>` : ''}</div>`, () => `${m.mix ? `<div class="mix">You can also mix half ${esc(m.mix.lighter)} with half ${esc(m.mix.deeper)}</div>` : ''}</div>`)).join('');
  const gap = r.key === 'foundation' && r.matches.length && !r.matches[0].fit && ['gap', 'no_match'].includes(r.matches[0].verdict_code)
    ? __t('s0438', '<div class="match">型錄裡沒有很接近的色號；也可能是照片偏暗或偏色，建議在窗邊自然光重拍，或點選較亮的臉頰。</div>') : '';
  return `<div class="color-row"><div class="big-sw" style="background:${r.hex || 'transparent'}"></div>`
    + `<div><div class="k">${esc(r.k)}</div><div class="v">${esc(r.v)}</div>${r.hex ? `<div class="hex">${esc(r.hex)}</div>` : ''}${cardStrip(r.card)}${ms}${stripesHtml(r.stripes)}${gap}${r.note ? `<div class="match">${esc(r.note)}</div>` : ''}</div></div>`;
}
async function goCard() {
  busy(true, __t('s0439', '對照色號中…'));
  try {
    state.card = await api('/api/card', { undertone: state.tone, undertone_unsure: state.toneUnsure && !state.toneFromSkin,
      selection: state.selection, skin_hex: state.skinHex, ...colorPayload(),
      ...(state.useExpected && state.expected ? { zones: state.expected.zones } : {}),
      condition: state.condition, source: 'all' });
  } catch (e) {
    state.card = null;
    alert(e.message);
  } finally { busy(false); }
  const care = (state.card && state.card.care) || [];
  $('card-sensitive').innerHTML = sensitiveHtml(state.card && state.card.sensitive);
  $('care-steps').innerHTML = care.map((c) => `<li>${esc(c.text)}<span class="why">${esc(c.why)}</span></li>`).join('');
  $('color-card').innerHTML = cardRowsWithMatches().map(rowHtml).join('');
  $('card-face').hidden = !state.face;
  if (state.face) $('card-face-tips').innerHTML = state.face.tips.map((t) => `<li>${esc(t)}</li>`).join('');
  $('catalog-note').textContent = (state.lightOk ? '' : __t('s0440', '這張照片光線不理想，膚色對應僅供參考。')) + catalogNote();
  show('view-card');
}
function saveCard() {
  const rows = cardRowsWithMatches();
  const c = document.createElement('canvas');
  c.width = 1100; c.height = 140 + rows.length * 130;
  const g = c.getContext('2d');
  g.fillStyle = '#ffffff'; g.fillRect(0, 0, c.width, c.height);
  g.fillStyle = '#241f1c'; g.font = 'bold 48px -apple-system, "PingFang TC", sans-serif'; g.fillText(__t('s0151', '我的配色卡'), 40, 80);
  rows.forEach((r, i) => {
    const y = 130 + i * 130;
    if (r.hex) { g.fillStyle = r.hex; g.fillRect(40, y, 80, 80); }
    g.strokeStyle = '#cccccc'; g.strokeRect(40, y, 80, 80);
    g.fillStyle = '#6b605a'; g.font = '28px -apple-system, "PingFang TC", sans-serif'; g.fillText(r.k, 150, y + 30);
    g.fillStyle = '#241f1c'; g.font = 'bold 36px -apple-system, "PingFang TC", sans-serif'; g.fillText(`${r.v}${r.hex ? '  ' + r.hex : ''}`, 150, y + 72);
    if (r.matches.length) { g.fillStyle = '#6b605a'; g.font = '24px -apple-system, "PingFang TC", sans-serif'; g.fillText(matchLine(r.matches[0]), 150, y + 102); }
  });
  const a = document.createElement('a');
  a.href = c.toDataURL('image/png');
  a.download = __t('s0444', '配色卡.png');
  a.click();
}

// ---------------------------------------------------------------- Bindings and startup
$('opt-dark').onchange = () => { applyTheme(); savePrefs(true); };
$('btn-start').onclick = openMirror;
$('btn-warn-dark').onclick = () => { $('opt-dark').checked = !$('opt-dark').checked; applyTheme(); savePrefs(true); syncWarnDark(true); };
$('btn-warn-ok').onclick =() => { $('warn-modal').hidden = true; show('view-capture'); speak(__t('s0446', '拍一張，或上傳一張正面照。')); };
$('btn-capture-back').onclick = () => show('view-start');
$('file-upload').onchange = async (e) => {
  const f = e.target.files && e.target.files[0];
  e.target.value = '';
  if (f) goCheck(await blobToDataUrl(f));
};
$('btn-demo').onclick = async () => {
  busy(true, __t('s0447', '載入示範照片…'));
  try { const r = await api(`/api/demo-photo?condition=${state.condition}`); busy(false); state.expected = r.expected || null; goCheck(r.image, true, r.points || {}); }
  catch (e) { busy(false); alert(e.message); }
};
$('btn-cancel-cam').onclick = () => { closeMirror(); show('view-capture'); };
$('btn-shutter').onclick = captureNative;
$('file-input').onchange = async (e) => {
  const f = e.target.files && e.target.files[0];
  e.target.value = '';
  if (!f) return;
  closeMirror();
  goCheck(await blobToDataUrl(f));
};
$('btn-retake').onclick = () => show('view-capture');
$('btn-guide-retake').onclick = () => show('view-capture');
$('btn-try').onclick = () => { state.recommendation = null; state.useExpected = false; state.cardPick = null; state.suggested = null; if (state.expected) goExpect(); else goShade(); };   // Result first: skin tone -> (expected makeup) -> shade -> three questions -> suggestion -> try-on
$('btn-expect-toggle').onclick = toggleExpect;
$('btn-expect-back').onclick = () => show('view-check');
$('btn-expect-next').onclick = () => { state.useExpected = true; state.cardPick = null; state.suggested = null; state.recommendation = null; goShade(); };
$('btn-shade-back').onclick = () => (state.expected ? goExpect() : show('view-check'));
$('btn-shade-next').onclick = goSens;
$('btn-sens-skip').onclick = () => { state.sens = null; goAdvice(); };
$('btn-sens-next').onclick = goAdvice;
$('btn-adv-try').onclick = goBase;
$('btn-adv-card').onclick = goCard;
for (const b of BASE_KEYS) $(`btn-base-${b}`).onclick = async () => { await applyBase(b); flipFace('base', b === 'keep' ? 'photo' : 'look'); };      // When a makeup is chosen, flip to show it; when 'keep as is' is chosen, flip back to your photo
$('btn-base-adjust').onclick = () => goAdjust({ selection: state.selection, image: state.currentImage, title: $('base-caption').textContent, source: SOURCE.base });
$('btn-base-card').onclick = goCard;
$('mode-patch').onclick = () => setSampleMode('patch');
$('check-photo').onclick = onPhotoTap;
$('mode-skin').onclick = () => setSampleMode('skin');
$('mode-brow').onclick = () => setSampleMode('brow');
$('pv-cov').oninput = pvRender;
$('pv-pick').onclick = () => { pvSetPicking(!pv.picking); if (pv.picking) { $('pv-status').textContent = __t('s0448', '點照片上你想當底妝顏色的地方（例如膚色最勻的臉頰）。'); speak(__t('s0449', '請點照片取色')); } };
$('pv-orig').onclick = () => { const on = $('pv-orig').getAttribute('aria-pressed') !== 'true'; $('pv-orig').setAttribute('aria-pressed', String(on)); pvRender(); };
$('pv-img').onclick = pvPickAt;
$('camo-cov').oninput = camoRender;
$('camo-feather').oninput = camoRender;
$('camo-passes').oninput = camoRender;
$('camo-hi').oninput = camoRender;
$('camo-shine').oninput = camoRender;
$('camo-expand').onchange = async () => {   // Expanding outward re-detects the outline (manually dragged boundaries are reset)
  camoLabels();
  try { await CamoEditor.findPatches(Number($('camo-expand').value)); camo.sel = null; await camoDrawEditor(); camoRender(); }
  catch (e) { $('camo-status').textContent = e.message; }
};
$('camo-toggle').onclick = () => {
  if (!camo.sel) { $('camo-status').textContent = __t('s0450', '先點一塊綠色外框，再按「開／關這一塊」。'); return; }
  CamoEditor.toggle(camo.sel); camoDrawEditor(); camoRender();
};
$('camo-mode-mk').onclick = () => camoSetMode('makeup');
$('camo-mode-algo').onclick = () => camoSetMode('algo');
$('mk-strength').oninput = camoRender;
camoPress('mk-ideal', camoRender);
$('camo-refine').onclick = () => camoRefine().catch((e) => { $('camo-status').textContent = e.message; });
$('camo-refine-clear').onclick = async () => { CamoEditor.clearRounds(); camo.sel = null; await camoDrawEditor(); camoRender(); };
$('camo-guide-btn').onclick = () => camoGuide().catch((e) => { $('camo-status').textContent = e.message; });
camoPress('camo-corr', camoRender);
camoPress('camo-light', camoRender);
camoPress('camo-orig', camoRender);
camoPress('camo-edges', (on) => { if (camo.layer) { camo.layer.visible(on); camo.layer.draw(); } });
$('pv-area').onclick = () => { const on = $('pv-area').getAttribute('aria-pressed') !== 'true'; $('pv-area').setAttribute('aria-pressed', String(on)); pvRender(); };
$('mode-browpts').onclick = () => { setSampleMode('browpts'); drawBrowDots(); };
$('btn-back-quick').onclick = () => show(state.baseLooks ? 'view-base' : 'view-quick');
$('btn-to-card').onclick = goCard;
$('btn-face').onclick = runFace;
$('btn-layers').onclick = runLayers;
$('btn-show-staff').onclick = () => { $('staff-content').innerHTML = __t('s0451', '<h2>我想找這些顏色</h2>') + cardRowsWithMatches().map(rowHtml).join(''); $('copy-status').hidden = true; $('staff-text').hidden = true; $('staff-view').hidden = false; };
$('btn-copy-staff').onclick = copyForAdvisor;
$('btn-copy-card').onclick = () => { $('staff-content').innerHTML = __t('s0451', '<h2>我想找這些顏色</h2>') + cardRowsWithMatches().map(rowHtml).join(''); $('staff-view').hidden = false; copyForAdvisor(); };
$('btn-close-staff').onclick = () => { $('staff-view').hidden = true; };
$('btn-save-card').onclick = saveCard;
$('btn-restart').onclick = () => { state.recommendation = null; state.sens = null; renderStart(); show('view-start'); };

// ---- Judge sign-in, merged into the app (the server only enables it with --judge-live; the standalone page /judge stays as a fallback)
async function judgePost(path, body) {
  const r = await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) });
  return { status: r.status, json: await r.json().catch(() => ({})) };
}
function openJudgeDialog() {
  const dlg = $('judge-dialog');
  $('judge-msg').textContent = '';
  $('judge-pass').value = '';
  if (dlg.showModal) dlg.showModal(); else window.location.href = '/judge';
  $('judge-user').focus();
}
async function judgeSignOut() { await judgePost('/api/judge-logout'); window.location.reload(); }
$('judge-cancel').onclick = () => $('judge-dialog').close();
$('judge-form').onsubmit = async (ev) => {
  ev.preventDefault();
  const go = $('judge-go');
  go.disabled = true;
  $('judge-msg').textContent = '';
  try {
    const { status, json } = await judgePost('/api/judge-login', { username: $('judge-user').value, password: $('judge-pass').value });
    if (status === 200 && json.ok) { window.location.reload(); return; }
    $('judge-msg').textContent = status === 429 ? 'Too many attempts. Please wait a minute.' : 'Wrong username or password.';
  } catch (_) {
    $('judge-msg').textContent = 'Could not reach the server. Please try again.';
  }
  go.disabled = false;
};

function bannerText(s) {
  return s.mode === 'mock' ? __t('s0452', '展示模式：不會呼叫 YouCam、不扣點') : __T('s0453', () => `正式模式：本次已試妝 ${s.calls_used}/${s.max_calls} 次`, () => `Live mode: ${s.calls_used}/${s.max_calls} try-ons used this time`);
}
async function refreshBanner() {
  try {
    const t = $('mode-text');
    if (t) t.textContent = bannerText(await api('/api/status'));
  } catch (_) { /* the counter keeps its last value; the next call refreshes it */ }
}

(async function init() {
  api('/api/skin-card').then((r) => { state.skinCard = r; }).catch(() => {});
  try {
    state.presets = await api('/api/presets');
    const s = await api('/api/status');
    state.mode = s.mode;
    state.catalog = s.catalog || 'all';
    state.catalogKind = s.catalog_kind || (state.catalog === 'demo' ? 'demo' : 'official');
    if (s.catalog_stats) {            // Homepage numbers: the catalog actually loaded (a clean clone has only the fictional demo catalog)
      $('stat-shades').textContent = s.catalog_stats.shades;
      $('stat-brands').textContent = s.catalog_stats.brands;
    }
    if (state.catalogKind === 'demo') {
      $('stat-shades-label').textContent = __t('s0927', '示範型錄色號可對照');
      $('stat-brands-label').textContent = __t('s0928', '虛構品牌');
    }
    $('btn-face').textContent = faceLabel(false);
    const b = $('mode-banner');
    b.hidden = false;
    b.textContent = '';
    const t = document.createElement('span');
    t.id = 'mode-text';
    t.textContent = bannerText(s);
    b.append(t);
    if (s.judge_login) {              // The server has judge login enabled (--judge-live): the banner gets login / logout entries linking to a separate English login page
      const a = document.createElement('a');
      a.href = '/judge';           // without JavaScript the link still reaches the standalone sign-in page
      a.className = 'banner-link';
      a.textContent = s.judge_signed_in ? 'Judge: sign out' : 'Judge sign-in';
      a.onclick = (ev) => { ev.preventDefault(); if (s.judge_signed_in) judgeSignOut(); else openJudgeDialog(); };
      b.append(' · ', a);
    }
  } catch (e) {
    $('mode-banner').hidden = false;
    $('mode-banner').textContent = __t('s0454', '連不上伺服器');
    return;
  }
  renderStart();
  show('view-start');
})();

if ('serviceWorker' in navigator && (location.protocol === 'https:' || ['localhost', '127.0.0.1'].includes(location.hostname))) {
  navigator.serviceWorker.register('sw.js').catch(() => { /* Works without a SW (service worker) too */ });
}
