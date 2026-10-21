'use strict';
// Capture helper: judges whether this photo has a chance of being recognized by the official service .
// Pure functions: inputs are numbers already measured by the page (face box, a few landmark points, lightness, detection score); no DOM, no network, and the photo never leaves the phone.
// Thresholds and sources follow docs/EVIDENCE_LIMITS.md (branch shared/evidence-limits 6b8044d); the evidence field holds the evidence-table row number;
// rows without a matching entry give a source string (e.g. the HD spec comes from the tools/noise_test.py pre-check).
// Each item outputs status: 'pass' passed / 'warn' reminder (can continue; the app compensates or it is only a suggestion) / 'block' blocked (retake or pick another photo).
(function (root) {
  // Chinese/English switching: in the browser web/i18n.js provides __t / __T; node tests do not have it, so the text stays Chinese
  const __t = root.__t || ((id, zh) => zh);
  const __T = root.__T || ((id, zhFn) => zhFn());
  const T = {
    FACE_OK: 0.55,          // Face width / image width >= 0.55 passes the guidance; it is then auto-cropped to about 0.66 (official requirement > 0.60, evidence table row 2)
    FACE_MIN: 0.35,         // Below this, the cropped resolution is usually insufficient, so ask the user to move closer
    CROP_FACE: 0.66,        // Normalized crop: face width as a fraction of crop width
    OFFSET_OK: 0.08,        // Frontal proxy: how far the nose tip and mouth center deviate from the face midline (in units of face width)
    OFFSET_BLOCK: 0.15,     // Evidence table row 1: side-face deviation 0.162 > 0.15 is blocked
    EYE_RATIO_OK: 0.90,     // Eye width ratio (smaller / larger)
    EYE_RATIO_BLOCK: 0.75,
    EDGE_MARGIN: 0.04,      // Face box less than 4% of the face width from the image edge counts as too close to the edge (top of head and chin are easily cut off, evidence table row 3)
    HD_SHORT_MIN: 1080,     // Official skin analysis HD: submitted image short side >= 1080, long side <= 2560 (tools/noise_test.py pre-check)
    HD_LONG_MAX: 2560,
    L_LOW: 30, L_HIGH: 90,  // Face lightness L*: an engineering reference line, strong light is not required; if too dark the app brightens it (light-sensitivity friendly)
    DET_OK: 0.6,            // Detection score below this: do not block, only remind (evidence table row 4: detection is unstable on very light skin with low contrast)
    LANDMARK_JITTER_WARN: 0.05,   // Landmark difference between the two detectors (in units of eye distance) above this triggers a reminder
  };
  const RANK = { pass: 0, warn: 1, block: 2 };

  function item(id, status, hint, evidence, value) {
    return { id, status, hint, evidence, value: value === undefined ? null : value };
  }

  // m: { imgW, imgH, face: {x, y, w, h} (face box, original-image pixels), noseX, mouthX (pixels), eyeWL, eyeWR (widths of the two eyes, pixels),
  //      lightness (median face L*), detScore (0-1, null if no face detected), jitter (landmark difference of the two detectors, in eye-distance units, optional) }
  function assess(m) {
    const items = [];
    if (!m || !m.face || m.detScore === null || m.detScore === undefined || !(m.face.w > 0)) {
      items.push(item('face_found', 'block', __t('s0716', '找不到清楚的臉：請換一張正面、整張臉入鏡的照片。'), [1, 3]));
      return finish(items, null, m);
    }
    const f = m.face, fw = f.w, mid = f.x + fw / 2;

    // 1 Detection stability (often unstable on very light skin with low contrast: do not block, only remind)
    if (m.detScore < T.DET_OK || (m.jitter !== undefined && m.jitter !== null && m.jitter > T.LANDMARK_JITTER_WARN)) {
      items.push(item('detect_stability', 'warn', __t('s0717', '臉的位置抓得不太穩，結果可能有點偏；可以換個角度再拍一張，或直接繼續。'), [4],
        { detScore: round(m.detScore), jitter: m.jitter === undefined ? null : round(m.jitter) }));
    } else {
      items.push(item('detect_stability', 'pass', __t('s0718', '臉的位置抓得到。'), [4], { detScore: round(m.detScore) }));
    }

    // 2 Face size
    const ratio = fw / m.imgW;
    if (ratio >= T.FACE_OK) items.push(item('face_size', 'pass', __t('s0719', '臉的大小剛好。'), [2], round(ratio)));
    else if (ratio >= T.FACE_MIN) items.push(item('face_size', 'warn', __t('s0720', '再靠近一點，讓臉占滿畫面多一些；我們也會自動幫你裁到剛好。'), [2], round(ratio)));
    else items.push(item('face_size', 'block', __t('s0721', '臉有點太小了：手機拿近一點再拍。'), [2], round(ratio)));

    // 3 Frontal (nose tip and mouth center deviation from the face midline; eye width ratio)
    const offN = Math.abs((m.noseX ?? mid) - mid) / fw, offM = Math.abs((m.mouthX ?? mid) - mid) / fw;
    const off = Math.max(offN, offM);
    const er = (m.eyeWL > 0 && m.eyeWR > 0) ? Math.min(m.eyeWL, m.eyeWR) / Math.max(m.eyeWL, m.eyeWR) : 1;
    const yawV = { offset: round(off), eyeRatio: round(er) };
    if (off > T.OFFSET_BLOCK || er < T.EYE_RATIO_BLOCK) {
      items.push(item('frontal', 'block', __t('s0722', '臉有點側了：手機舉到眼睛高度、正對鏡頭再拍一張。'), [1], yawV));
    } else if (off > T.OFFSET_OK || er < T.EYE_RATIO_OK) {
      items.push(item('frontal', 'warn', __t('s0723', '臉稍微有點側：可以把手機舉到眼睛高度、正對鏡頭。'), [1], yawV));
    } else items.push(item('frontal', 'pass', __t('s0724', '臉正對鏡頭。'), [1], yawV));

    // 4 Whether anything is cut off (top of head, chin, sides)
    const mg = T.EDGE_MARGIN * fw;
    const outside = f.x < 0 || f.y < 0 || f.x + fw > m.imgW || f.y + f.h > m.imgH;
    const tight = f.x < mg || f.y < mg || m.imgW - (f.x + fw) < mg || m.imgH - (f.y + f.h) < mg;
    if (outside) items.push(item('in_frame', 'block', __t('s0725', '臉超出畫面了：往中間移一點，頭頂和下巴都要拍進來。'), [3]));
    else if (tight) items.push(item('in_frame', 'warn', __t('s0726', '臉有點貼邊：往中間移一點，頭頂和下巴留一點空間。'), [3]));
    else items.push(item('in_frame', 'pass', __t('s0727', '整張臉都在畫面裡。'), [3]));

    // 5 Resolution (computed from the size after auto-crop)
    const crop = cropBox(m);
    const shortSide = crop ? Math.min(crop.w, crop.h) : Math.min(m.imgW, m.imgH);
    if (shortSide < T.HD_SHORT_MIN) {
      items.push(item('resolution', 'warn', __t('s0728', '照片解析度偏低：取色和試妝都能用，膚質分析可能沒辦法做；可以換一張比較清楚的照片。'),
        __t('s0729', 'HD 規格：tools/noise_test.py 預檢'), Math.round(shortSide)));
    } else items.push(item('resolution', 'pass', __t('s0730', '照片夠清楚。'), __t('s0729', 'HD 規格：tools/noise_test.py 預檢'), Math.round(shortSide)));

    // 6 Lightness (strong light is not required; if too dark the app brightens it)
    const L = m.lightness;
    if (L === undefined || L === null) items.push(item('lightness', 'pass', __t('s0731', '光線不用特別調整。'), __t('s0732', '工程參考線 L* 30–90')));
    else if (L < T.L_LOW) items.push(item('lightness', 'warn', __t('s0733', '光線有點暗也沒關係，我們會幫你調亮；不用開閃光燈。'), __t('s0732', '工程參考線 L* 30–90'), round(L)));
    else if (L > T.L_HIGH) items.push(item('lightness', 'warn', __t('s0734', '畫面有點太亮：稍微避開直射的光就好，不用找更亮的地方。'), __t('s0732', '工程參考線 L* 30–90'), round(L)));
    else items.push(item('lightness', 'pass', __t('s0203', '光線可以。'), __t('s0732', '工程參考線 L* 30–90'), round(L)));

    return finish(items, crop, m);
  }

  // Normalized crop: centered on the face, face width about 0.66 of the crop width, 3:4; if it exceeds the original image it is pushed inward, and if that is not possible it is shrunk to the largest feasible size
  function cropBox(m) {
    if (!m || !m.face || !(m.face.w > 0)) return null;
    const f = m.face, cx = f.x + f.w / 2, cy = f.y + f.h * 0.45;
    let w = f.w / T.CROP_FACE, h = w * 4 / 3;
    if (w > m.imgW || h > m.imgH) { const s = Math.min(m.imgW / w, m.imgH / h); w *= s; h *= s; }
    let x = cx - w / 2, y = cy - h / 2;
    x = Math.min(Math.max(0, x), m.imgW - w); y = Math.min(Math.max(0, y), m.imgH - h);
    return { x: Math.round(x), y: Math.round(y), w: Math.round(w), h: Math.round(h), faceRatio: round(f.w / w) };
  }

  function finish(items, crop, m) {
    const overall = items.reduce((a, it) => (RANK[it.status] > RANK[a] ? it.status : a), 'pass');
    return { overall, items, crop: overall === 'block' ? null : crop, thresholds: T };
  }
  function round(v) { return v === null || v === undefined ? null : Math.round(v * 1000) / 1000; }

  // For live viewfinder (backup camera): condenses the assess result into "the one thing to flag right now", in priority order: no face found -> too small -> cut off at edge -> side face -> OK
  function liveHint(res) {
    if (!res || !res.items) return 'unknown';
    const st = (id) => { const it = res.items.find((i) => i.id === id); return it ? it.status : 'pass'; };
    if (res.items.some((i) => i.id === 'face_found')) return 'no_face';
    if (st('face_size') !== 'pass') return 'closer';
    if (st('in_frame') !== 'pass') return 'center';
    if (st('frontal') !== 'pass') return 'front';
    return 'ok';
  }

  const api = { assess, cropBox, liveHint, THRESHOLDS: T };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.CaptureGuide = api;
})(typeof window !== 'undefined' ? window : globalThis);
