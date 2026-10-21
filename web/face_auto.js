'use strict';
// 自動取色：MediaPipe 臉部定位（在手機上跑，照片不上傳）找出臉頰、額頭、下巴、眉毛，
// 再取膚色、白斑、眉色。取到的顏色一律顯示色塊讓本人確認，可再手動點選修正。
// 載入失敗（離線、舊瀏覽器）就回 null，畫面退回手動點選。

const MP_VERSION = '0.10.14';
const MP_BASE = `https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@${MP_VERSION}`;
const MP_MODEL = 'https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task';

// MediaPipe Face Mesh 468 點的編號（臉部左右以照片為準）
const FACE_POINTS = {
  cheekLeft: [50, 205, 117, 123, 187],
  cheekRight: [280, 425, 346, 352, 411],
  forehead: [151, 108, 337, 69, 299],
  // 下顎線上緣（臉和脖子交界的臉側），彩妝選色教學建議在這裡試色
  jawLeft: [215, 192, 213],
  jawRight: [435, 416, 433],
  browLeft: [105, 66, 52, 65],
  browRight: [334, 296, 282, 295],
};

// 多類別分割（同一套 MediaPipe）：0 背景、1 頭髮、2 身體皮膚、3 臉部皮膚、4 衣服、5 其他。
// 臉部網格的外框只到額頭中段（第 10 點），分割的「臉部皮膚」才會到髮際線
const MP_SEG_MODEL = 'https://storage.googleapis.com/mediapipe-models/image_segmenter/selfie_multiclass_256x256/float32/latest/selfie_multiclass_256x256.tflite';
let segmenterPromise = null;
function loadSegmenter() {
  if (!segmenterPromise) {
    segmenterPromise = (async () => {
      const vision = await import(`${MP_BASE}/vision_bundle.mjs`);
      const files = await vision.FilesetResolver.forVisionTasks(`${MP_BASE}/wasm`);
      const seg = await vision.ImageSegmenter.createFromOptions(files, {
        baseOptions: { modelAssetPath: MP_SEG_MODEL, delegate: 'CPU' }, runningMode: 'IMAGE', outputCategoryMask: true, outputConfidenceMasks: false,
      });
      try {                                                        // 暖機：第一次推論要初始化，先在背景用小圖跑掉
        const c = document.createElement('canvas'); c.width = 64; c.height = 64; c.getContext('2d').fillRect(0, 0, 64, 64);
        seg.segment(c).close();
      } catch (_) { /* 暖機失敗不影響正式使用 */ }
      return seg;
    })().catch((err) => { console.warn('臉部分割載入失敗，額頭只到臉部網格外框', err); segmenterPromise = null; return null; });
  }
  return segmenterPromise;
}
// 回傳 { arr: Uint8Array（類別編號）, w, h }；失敗回 null
async function segmentFace(imageOrCanvas) {
  const seg = await loadSegmenter();
  if (!seg) return null;
  try {
    const res = seg.segment(imageOrCanvas), m = res.categoryMask;
    const out = { arr: Uint8Array.from(m.getAsUint8Array()), w: m.width, h: m.height };
    res.close();
    return out;
  } catch (err) { console.warn('臉部分割失敗', err); return null; }
}

// 同一張照片只分割一次（約 1 秒）：選完照片就在背景先跑，進到局部遮瑕時直接拿結果
const segCache = new Map();
function segmentCached(key, imageOrCanvas) {
  if (!segCache.has(key)) {
    if (segCache.size > 3) segCache.delete(segCache.keys().next().value);
    segCache.set(key, segmentFace(imageOrCanvas).then((r) => { if (!r) segCache.delete(key); return r; }));
  }
  return segCache.get(key);
}

let landmarkerPromise = null;
function loadLandmarker() {
  if (!landmarkerPromise) {
    landmarkerPromise = (async () => {
      const vision = await import(`${MP_BASE}/vision_bundle.mjs`);
      const files = await vision.FilesetResolver.forVisionTasks(`${MP_BASE}/wasm`);
      return vision.FaceLandmarker.createFromOptions(files, {
        baseOptions: { modelAssetPath: MP_MODEL, delegate: 'CPU' }, runningMode: 'IMAGE', numFaces: 1,
      });
    })().catch((err) => { console.warn('臉部定位載入失敗，改用手動點選', err); landmarkerPromise = null; return null; });
  }
  return landmarkerPromise;
}

// sRGB → Lab（D65），和伺服器 color_engine 同一套公式
function hexToLab(hex) {
  const n = parseInt(hex.slice(1), 16);
  const lin = (c) => { c /= 255; return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4; };
  const r = lin(n >> 16), g = lin((n >> 8) & 255), b = lin(n & 255);
  const x = (0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047;
  const y = 0.2126 * r + 0.7152 * g + 0.0722 * b;
  const z = (0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883;
  const f = (t) => (t > 216 / 24389 ? Math.cbrt(t) : (24389 / 27 * t + 16) / 116);
  return [116 * f(y) - 16, 500 * (f(x) - f(y)), 200 * (f(y) - f(z))];
}
function medianHex(hexes) {
  const ch = [0, 1, 2].map((k) => hexes.map((h) => (parseInt(h.slice(1), 16) >> (16 - 8 * k)) & 255).sort((a, b) => a - b));
  return '#' + ch.map((c) => c[c.length >> 1].toString(16).padStart(2, '0')).join('').toUpperCase();
}

/**
 * 回傳 { skin, patch, brow, points } 或 null（找不到臉／載入失敗）。
 * sample(img, point, dark, radius) 由 app.js 提供（同一套取色）。
 * condition：'albinism' 取受光側臉頰；'vitiligo' 依「彩度÷亮度」把臉部膚色點分成原本膚色與白斑兩群。
 */
async function autoColors(img, condition, sample) {
  const lm = await loadLandmarker();
  if (!lm) return null;
  let res;
  try { res = lm.detect(img); } catch (err) { console.warn('臉部定位失敗', err); return null; }
  const face = res && res.faceLandmarks && res.faceLandmarks[0];
  if (!face) return null;
  const xs = face.map((p) => p.x);
  const faceW = Math.max(...xs) - Math.min(...xs);
  const radius = Math.max(2, Math.round(faceW * 300 * 0.025));   // 取色範圍跟著臉的大小
  const at = (i) => ({ x: face[i].x, y: face[i].y });
  const pts = (name) => FACE_POINTS[name].map(at);
  const out = { points: {} };

  // 眉色：兩邊眉毛各點取較暗的 30%（毛髮）
  const browPts = [...pts('browLeft'), ...pts('browRight')];
  out.brow = medianHex(browPts.map((p) => sample(img, p, true, radius)));
  out.points.brow = browPts;

  if (condition === 'vitiligo') {
    // 只用正面受光的點（臉頰、額頭、下巴前緣）；下顎底、太陽穴常在陰影裡
    const skinPts = ['cheekLeft', 'cheekRight', 'forehead', 'jawLeft', 'jawRight'].flatMap(pts).concat([199, 32, 262].map(at));
    const samples = skinPts.map((p) => ({ p, hex: sample(img, p, false, radius) })).map((s) => {
      const [L, a, b] = hexToLab(s.hex);
      return { ...s, L, ratio: Math.hypot(a, b) / Math.max(L, 1) };
    });
    // 分群看「彩度÷亮度」：白斑缺黑色素，比原本膚色淡又不飽和；陰影只是整體變暗，比值不太變（ 實照驗證）
    let lo = Math.min(...samples.map((s) => s.ratio)), hi = Math.max(...samples.map((s) => s.ratio));
    const split = () => [samples.filter((s) => Math.abs(s.ratio - lo) <= Math.abs(s.ratio - hi)),
      samples.filter((s) => Math.abs(s.ratio - lo) > Math.abs(s.ratio - hi))];
    for (let it = 0; it < 12; it++) {
      const [a, b] = split();
      if (!a.length || !b.length) break;
      lo = a.reduce((t, s) => t + s.ratio, 0) / a.length;
      hi = b.reduce((t, s) => t + s.ratio, 0) / b.length;
    }
    const [patchGroup, normalGroup] = split();
    out.skin = medianHex(normalGroup.map((s) => s.hex));
    out.points.skin = normalGroup.map((s) => s.p);
    // 比值差太小、或白斑那群只有一點（多半是反光）就不當成白斑
    if (patchGroup.length >= 2 && hi - lo >= 0.06) {
      const bright = [...patchGroup].sort((x, y) => y.L - x.L).slice(0, Math.max(1, Math.ceil(patchGroup.length / 2)));
      out.patch = medianHex(bright.map((s) => s.hex));       // 白斑取較亮的一半，避開交界
      out.points.patch = patchGroup.map((s) => s.p);
      out.patch_ratio = Math.round((patchGroup.length / samples.length) * 100) / 100;   // 白斑約佔取色點的比例，算平衡色用
    }
  } else {
    // 受光側的臉頰＋下顎線上緣一起取（選色教學：在臉和脖子交界試色；側光時平均會被陰影拉暗）
    const leftPts = [...pts('cheekLeft'), ...pts('jawLeft')], rightPts = [...pts('cheekRight'), ...pts('jawRight')];
    const left = leftPts.map((p) => sample(img, p, false, radius));
    const right = rightPts.map((p) => sample(img, p, false, radius));
    const lum = (h) => hexToLab(h)[0];
    const lm2 = medianHex(left), rm = medianHex(right);
    // 臉有點轉向時，轉開的那側臉頰被拉窄、取色點會落到臉的邊緣；改取轉向鏡頭、看得比較寬的那一側（ 手機實測）
    const wl = face[1].x - face[234].x, wr = face[454].x - face[1].x;       // 鼻尖到左、右臉緣的寬度
    const turned = Math.abs(wl - wr) / Math.max(wl + wr, 1e-6) > 0.16;
    const useLeft = turned ? wl > wr : lum(lm2) >= lum(rm);
    out.skin = useLeft ? lm2 : rm;
    out.points.skin = useLeft ? leftPts : rightPts;
  }
  return out;
}

/**
 * 拍攝小幫手：量臉框、正面代理點位、臉頰亮度，給 CaptureGuide.assess 用。照片只在手機上算。
 * 回傳 { imgW, imgH, face, noseX, mouthX, eyeWL, eyeWR, lightness, detScore }；找不到臉時 face=null、detScore=null；載入失敗回 null。
 * 臉部網格沒有信心分數：找到臉就給 0.9；偵測穩定度（兩個偵測器互相比對）這一版沒算。
 */
async function measure(img) {
  const lm = await loadLandmarker();
  if (!lm) return null;
  const W = img.naturalWidth || img.videoWidth || img.width, H = img.naturalHeight || img.videoHeight || img.height;   // 圖片或 <video>（即時取景）
  let res;
  try { res = lm.detect(img); } catch (err) { console.warn('臉部定位失敗', err); return null; }
  const face = res && res.faceLandmarks && res.faceLandmarks[0];
  if (!face) return { imgW: W, imgH: H, face: null, detScore: null };
  const P = (i) => [face[i].x * W, face[i].y * H];
  const xs = face.map((q) => q.x * W), ys = face.map((q) => q.y * H);
  const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys), y1 = Math.max(...ys);
  const sc = 300 / W, c = document.createElement('canvas');
  c.width = 300; c.height = Math.max(1, Math.round(H * sc));
  const g = c.getContext('2d', { willReadFrequently: true });
  g.drawImage(img, 0, 0, c.width, c.height);
  const Ls = [];
  for (const i of [50, 280, 117, 346, 205, 425]) {          // 兩頰與顴骨下方
    const [x, y] = P(i);
    const d = g.getImageData(Math.max(0, Math.round(x * sc) - 2), Math.max(0, Math.round(y * sc) - 2), 5, 5).data;
    for (let k = 0; k < d.length; k += 4) Ls.push(hexToLab('#' + [d[k], d[k + 1], d[k + 2]].map((v) => v.toString(16).padStart(2, '0')).join(''))[0]);
  }
  Ls.sort((u, v) => u - v);
  return { imgW: W, imgH: H, face: { x: x0, y: y0, w: x1 - x0, h: y1 - y0 }, noseX: P(1)[0], mouthX: (P(61)[0] + P(291)[0]) / 2,
    eyeWL: Math.abs(P(133)[0] - P(33)[0]), eyeWR: Math.abs(P(263)[0] - P(362)[0]), lightness: Ls.length ? Ls[Ls.length >> 1] : null, detScore: 0.9 };
}

window.FaceAuto = { autoColors, loadLandmarker, loadSegmenter, segmentFace, segmentCached, measure };
