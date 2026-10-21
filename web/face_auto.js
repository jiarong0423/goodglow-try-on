'use strict';
// Auto color picking: MediaPipe face landmarks (run on the phone, photo not uploaded) locate the cheeks, forehead, chin and brows,
// then pick skin color, patch color and brow color. Picked colors are always shown as swatches for the person to confirm, and can be corrected by tapping manually.
// If loading fails (offline, old browser) return null, and the UI falls back to manual tapping.

const MP_VERSION = '0.10.14';
const MP_BASE = `https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@${MP_VERSION}`;
const MP_MODEL = 'https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task';

// Point indices of the MediaPipe Face Mesh 468 points (left/right of the face follow the photo)
const FACE_POINTS = {
  cheekLeft: [50, 205, 117, 123, 187],
  cheekRight: [280, 425, 346, 352, 411],
  forehead: [151, 108, 337, 69, 299],
  // Upper edge of the jawline (the face side at the face-neck border); makeup color-picking guides recommend trying shades here
  jawLeft: [215, 192, 213],
  jawRight: [435, 416, 433],
  browLeft: [105, 66, 52, 65],
  browRight: [334, 296, 282, 295],
};

// Multi-class segmentation (same MediaPipe suite): 0 background, 1 hair, 2 body skin, 3 face skin, 4 clothes, 5 other.
// The face-mesh outline only reaches the middle of the forehead (point 10); only the segmentation's "face skin" reaches the hairline
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
      try {                                                        // Warm-up: the first inference needs initialization, so run it once in the background on a small image
        const c = document.createElement('canvas'); c.width = 64; c.height = 64; c.getContext('2d').fillRect(0, 0, 64, 64);
        seg.segment(c).close();
      } catch (_) { /* a warm-up failure does not affect real use */ }
      return seg;
    })().catch((err) => { console.warn('臉部分割載入失敗，額頭只到臉部網格外框', err); segmenterPromise = null; return null; });
  }
  return segmenterPromise;
}
// Returns { arr: Uint8Array (class ids), w, h }; null on failure
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

// Segment each photo only once (about 1 second): start it in the background as soon as the photo is chosen, and reuse the result when entering local concealer
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

// sRGB -> Lab (D65), the same formula as the server's color_engine
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
 * Returns { skin, patch, brow, points } or null (no face found / loading failed).
 * sample(img, point, dark, radius) is provided by app.js (the same color picking).
 * condition: 'albinism' picks the lit-side cheek; 'vitiligo' splits the face skin points into original skin and patch by "chroma / lightness".
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
  const radius = Math.max(2, Math.round(faceW * 300 * 0.025));   // Sampling range scales with face size
  const at = (i) => ({ x: face[i].x, y: face[i].y });
  const pts = (name) => FACE_POINTS[name].map(at);
  const out = { points: {} };

  // Brow color: from each brow's points take the darker 30% (hair)
  const browPts = [...pts('browLeft'), ...pts('browRight')];
  out.brow = medianHex(browPts.map((p) => sample(img, p, true, radius)));
  out.points.brow = browPts;

  if (condition === 'vitiligo') {
    // Use only front-lit points (cheeks, forehead, front edge of the chin); jaw underside and temples are often in shadow
    const skinPts = ['cheekLeft', 'cheekRight', 'forehead', 'jawLeft', 'jawRight'].flatMap(pts).concat([199, 32, 262].map(at));
    const samples = skinPts.map((p) => ({ p, hex: sample(img, p, false, radius) })).map((s) => {
      const [L, a, b] = hexToLab(s.hex);
      return { ...s, L, ratio: Math.hypot(a, b) / Math.max(L, 1) };
    });
    // Clustering looks at "chroma / lightness": a patch lacks melanin, so it is paler and less saturated than the original skin; shadow only darkens everything, and the ratio barely changes (verified on real photos, 10-15)
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
    // If the ratio difference is too small, or the patch cluster has only a few points (mostly reflections), do not treat it as a patch
    if (patchGroup.length >= 2 && hi - lo >= 0.06) {
      const bright = [...patchGroup].sort((x, y) => y.L - x.L).slice(0, Math.max(1, Math.ceil(patchGroup.length / 2)));
      out.patch = medianHex(bright.map((s) => s.hex));       // For the patch take the brighter half, to avoid the border
      out.points.patch = patchGroup.map((s) => s.p);
      out.patch_ratio = Math.round((patchGroup.length / samples.length) * 100) / 100;   // Approximate fraction of sampling points that are patch, used to compute the balance color
    }
  } else {
    // Take the lit-side cheek plus the upper jawline edge together (color-picking guide: try shades at the face-neck border; with side light the average gets pulled dark by shadow)
    const leftPts = [...pts('cheekLeft'), ...pts('jawLeft')], rightPts = [...pts('cheekRight'), ...pts('jawRight')];
    const left = leftPts.map((p) => sample(img, p, false, radius));
    const right = rightPts.map((p) => sample(img, p, false, radius));
    const lum = (h) => hexToLab(h)[0];
    const lm2 = medianHex(left), rm = medianHex(right);
    // When the face is slightly turned, the cheek on the turned-away side is narrowed and its sample points fall on the face edge; instead use the side turned toward the camera, which looks wider (phone test, 10-21)
    const wl = face[1].x - face[234].x, wr = face[454].x - face[1].x;       // Width from the nose tip to the left and right face edges
    const turned = Math.abs(wl - wr) / Math.max(wl + wr, 1e-6) > 0.16;
    const useLeft = turned ? wl > wr : lum(lm2) >= lum(rm);
    out.skin = useLeft ? lm2 : rm;
    out.points.skin = useLeft ? leftPts : rightPts;
  }
  return out;
}

/**
 * 10-21 capture helper: measures the face box, frontal proxy landmarks and cheek lightness for CaptureGuide.assess. The photo is processed on the phone only.
 * Returns { imgW, imgH, face, noseX, mouthX, eyeWL, eyeWR, lightness, detScore }; face=null and detScore=null when no face is found; null on load failure.
 * The face mesh has no confidence score: give 0.9 when a face is found; detection stability (cross-check of two detectors) is not computed in this version.
 */
async function measure(img) {
  const lm = await loadLandmarker();
  if (!lm) return null;
  const W = img.naturalWidth || img.videoWidth || img.width, H = img.naturalHeight || img.videoHeight || img.height;   // Image or <video> (live viewfinder)
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
  for (const i of [50, 280, 117, 346, 205, 425]) {          // Both cheeks and below the cheekbones
    const [x, y] = P(i);
    const d = g.getImageData(Math.max(0, Math.round(x * sc) - 2), Math.max(0, Math.round(y * sc) - 2), 5, 5).data;
    for (let k = 0; k < d.length; k += 4) Ls.push(hexToLab('#' + [d[k], d[k + 1], d[k + 2]].map((v) => v.toString(16).padStart(2, '0')).join(''))[0]);
  }
  Ls.sort((u, v) => u - v);
  return { imgW: W, imgH: H, face: { x: x0, y: y0, w: x1 - x0, h: y1 - y0 }, noseX: P(1)[0], mouthX: (P(61)[0] + P(291)[0]) / 2,
    eyeWL: Math.abs(P(133)[0] - P(33)[0]), eyeWR: Math.abs(P(263)[0] - P(362)[0]), lightness: Ls.length ? Ls[Ls.length >> 1] : null, detScore: 0.9 };
}

window.FaceAuto = { autoColors, loadLandmarker, loadSegmenter, segmentFace, segmentCached, measure };
