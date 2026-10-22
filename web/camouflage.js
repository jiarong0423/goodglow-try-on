'use strict';
// Local concealer simulation for vitiligo patches (computed on the phone; the photo is not uploaded and no points are deducted).
// Why we build this ourselves: the YouCam try-on foundation can only be applied evenly over the whole face and its concealer only to fixed areas, so a vitiligo patch cannot be targeted;
// the actual technique (from videos supplied by the owner and a vitiligo-covering guide) is to dab a foundation close to the surrounding skin color "onto the patch" first, then blend it over the whole face.
// Method:
// 1. Face landmarks (MediaPipe) outline the face; eyes and mouth are cut out.
// 2. Each pixel on the face is compared with two sampled colors (original skin, patch): a pixel that looks more like the patch and is brighter than the original skin counts as patch.
// 3. The patch area is despeckled and its edges feathered.
// 4. On the try-on result, the patch area's "low-frequency base color" is replaced with "the lit surrounding skin after try-on plus a little warm color", keeping high-frequency texture and light/shade:
//    patches vary in depth (fully or partly depigmented); shifting by one block average would leave bright patches uncovered and pull dark ones too far;
//    instead, each place's local base color of the patch is computed and brought to the target color (frequency separation). The warm compensation acts like a peach/orange color corrector, so the result does not look gray .
// The UI always labels this as "local concealer simulated on the phone".

(function () {
  // MediaPipe Face Mesh outline and facial features (point indices)
  const FACE_OVAL = [10, 338, 297, 332, 284, 251, 389, 356, 454, 323, 361, 288, 397, 365, 379, 378, 400, 377, 152, 148,
    176, 149, 150, 136, 172, 58, 132, 93, 234, 127, 162, 21, 54, 103, 67, 109];
  const LEFT_EYE = [33, 7, 163, 144, 145, 153, 154, 155, 133, 173, 157, 158, 159, 160, 161, 246];
  const RIGHT_EYE = [362, 382, 381, 380, 374, 373, 390, 249, 263, 466, 388, 387, 386, 385, 384, 398];
  const LIPS = [61, 146, 91, 181, 84, 17, 314, 405, 321, 375, 291, 409, 270, 269, 267, 0, 37, 39, 40, 185];
  const WORK_W = 480;              // working resolution (width)
  const T_LINE = [151, 9, 168, 6, 197, 195, 5];   // forehead center -> between brows -> nose bridge -> nose tip

  function srgbToLab(r, g, b) {
    const lin = (c) => { c /= 255; return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4; };
    const R = lin(r), G = lin(g), B = lin(b);
    const x = (0.4124 * R + 0.3576 * G + 0.1805 * B) / 0.95047;
    const y = 0.2126 * R + 0.7152 * G + 0.0722 * B;
    const z = (0.0193 * R + 0.1192 * G + 0.9505 * B) / 1.08883;
    const f = (t) => (t > 216 / 24389 ? Math.cbrt(t) : (24389 / 27 * t + 16) / 116);
    return [116 * f(y) - 16, 500 * (f(x) - f(y)), 200 * (f(y) - f(z))];
  }
  function hexToRgb(hex) { const n = parseInt(hex.slice(1), 16); return [n >> 16, (n >> 8) & 255, n & 255]; }
  function labToSrgb(L, a, b) {
    const fy = (L + 16) / 116, fx = fy + a / 500, fz = fy - b / 200;
    const inv = (t) => (t ** 3 > 216 / 24389 ? t ** 3 : (116 * t - 16) / (24389 / 27));
    const X = inv(fx) * 0.95047, Y = inv(fy), Z = inv(fz) * 1.08883;
    const lin = [3.2406 * X - 1.5372 * Y - 0.4986 * Z, -0.9689 * X + 1.8758 * Y + 0.0415 * Z, 0.0557 * X - 0.204 * Y + 1.057 * Z];
    return lin.map((c) => 255 * Math.max(0, Math.min(1, c <= 0.0031308 ? 12.92 * c : 1.055 * c ** (1 / 2.4) - 0.055)));
  }

  function polygonMask(ctx, w, h, face, groups) {
    // groups: [[point indices..., whether to cut out]]; returns Uint8Array (1 = skin on the face that can be concealed)
    ctx.clearRect(0, 0, w, h);
    for (const [ids, cut] of groups) {
      ctx.globalCompositeOperation = cut ? 'destination-out' : 'source-over';
      ctx.beginPath();
      ids.forEach((i, k) => { const p = face[i]; (k ? ctx.lineTo : ctx.moveTo).call(ctx, p.x * w, p.y * h); });
      ctx.closePath();
      ctx.fillStyle = '#fff';
      ctx.fill();
    }
    ctx.globalCompositeOperation = 'source-over';
    const d = ctx.getImageData(0, 0, w, h).data;
    const m = new Uint8Array(w * h);
    for (let i = 0; i < m.length; i++) m[i] = d[i * 4 + 3] > 128 ? 1 : 0;
    return m;
  }

  function boxBlur(src, w, h, r) {
    // Separable box blur (two passes) = approximate Gaussian, used to remove stray dots and feather
    let a = Float32Array.from(src), b = new Float32Array(src.length);
    for (let pass = 0; pass < 2; pass++) {
      for (let y = 0; y < h; y++) {
        let acc = 0, row = y * w;
        for (let x = -r; x <= r; x++) acc += a[row + Math.min(w - 1, Math.max(0, x))];
        for (let x = 0; x < w; x++) {
          b[row + x] = acc / (2 * r + 1);
          acc += a[row + Math.min(w - 1, x + r + 1)] - a[row + Math.max(0, x - r)];
        }
      }
      for (let x = 0; x < w; x++) {
        let acc = 0;
        for (let y = -r; y <= r; y++) acc += b[Math.min(h - 1, Math.max(0, y)) * w + x];
        for (let y = 0; y < h; y++) {
          a[y * w + x] = acc / (2 * r + 1);
          acc += b[Math.min(h - 1, y + r + 1) * w + x] - b[Math.max(0, y - r) * w + x];
        }
      }
    }
    return a;
  }

  function loadImg(src) {
    return new Promise((res, rej) => { const i = new Image(); i.onload = () => res(i); i.onerror = rej; i.src = src; });
  }

  /**
   * Detect the patch area (on the original image): returns { mask: Float32Array(0-1, feathered), w, h, ratio }, or null on failure.
   */
  async function detectPatches(original, normalHex, patchHex, extend = 0, blend = 1) {
    const lm = await window.FaceAuto.loadLandmarker();
    if (!lm) return null;
    let res;
    try { res = lm.detect(original); } catch (_) { return null; }
    const face = res && res.faceLandmarks && res.faceLandmarks[0];
    if (!face) return null;
    const w = WORK_W, h = Math.round(WORK_W * original.naturalHeight / original.naturalWidth);
    const c = document.createElement('canvas'); c.width = w; c.height = h;
    const g = c.getContext('2d', { willReadFrequently: true });
    const skin = polygonMask(g, w, h, face, [[FACE_OVAL, false], [LEFT_EYE, true], [RIGHT_EYE, true], [LIPS, true]]);
    g.drawImage(original, 0, 0, w, h);
    const px = g.getImageData(0, 0, w, h).data;
    const nLab = srgbToLab(...hexToRgb(normalHex)), pLab = srgbToLab(...hexToRgb(patchHex));
    const raw = new Float32Array(w * h);
    let faceN = 0, patchN = 0;
    for (let i = 0; i < raw.length; i++) {
      if (!skin[i]) continue;
      faceN++;
      const [L, a, b] = srgbToLab(px[i * 4], px[i * 4 + 1], px[i * 4 + 2]);
      // Patch (including partly depigmented areas and borders): brighter than the original skin by at least "30% of the lightness difference between the two colors", or its color already resembles the patch more
      //
      const dn = (L - nLab[0]) ** 2 + 2 * ((a - nLab[1]) ** 2 + (b - nLab[2]) ** 2);
      const dp = (L - pLab[0]) ** 2 + 2 * ((a - pLab[1]) ** 2 + (b - pLab[2]) ** 2);
      const lighter = L > nLab[0] + Math.max(4, 0.3 * (pLab[0] - nLab[0]));
      if (lighter || (dp < dn && L > nLab[0] + 3)) { raw[i] = 1; patchN++; }
    }
    const r = Math.max(2, Math.round(w * 0.006));
    let soft = boxBlur(raw, w, h, r);                       // despeckle: remove isolated points below one half
    for (let i = 0; i < soft.length; i++) soft[i] = soft[i] > 0.45 && skin[i] ? 1 : 0;
    const core = Float32Array.from(soft);                  // patch body (not expanded)
    if (extend > 0) {
      // Boundary expansion: extend the patch outline outward by `extend` pixels (working resolution), then feather, to remove the bright line around the patch edge (外部建議)
      const grown = boxBlur(soft, w, h, Math.max(1, Math.round(extend)));
      for (let i = 0; i < soft.length; i++) soft[i] = grown[i] > 0.12 && skin[i] ? 1 : 0;
    }
    // Edge feathering; when blend > 1 the transition band is widened so the patch blends progressively into the surrounding skin
    soft = boxBlur(soft, w, h, Math.max(2, Math.round(w * (extend > 0 ? 0.016 : 0.012) * blend)));
    return { mask: soft, core, w, h, ratio: faceN ? patchN / faceN : 0, face };
  }

  /**
   * Local concealer on the try-on result: fill the patch area's local base color toward the surrounding skin color (+ warm color), keeping texture. Returns a dataURL.
   * Default = V8: V7 parameters + the center brightening changed to a T-shaped progressive form (centerMode 'tband', +9). Owner,: "the bright T-zone blends into the detected/filled vitiligo patches"
   *   and "the goal is natural"; measured locally: patch color difference 8.2 -> 4.3, nose bridge brighter than the outer cheek by 13 -> 21 .
   *   V7 can be reproduced with { centerMode: 'gauss' }; V6 with { centerMode: 'ellipse', center: 8, warm: [0, 0], extend: 0, darkSpot: 0 } and strength 0.85.
   * strength: 0-1 (default 0.88: bring the color difference down to about 5.5-6.5 and no further; do not force it to 0)
   * opts.center = how much brighter (L*, default 9) the face center stays than its surroundings after concealing: 外部建議suggested +6, but measured locally the center was only 12 brighter than the outer cheek,
   *   the target is 18-20, which +9 / range 0.20 reaches; see local run notes (not shipped)); opts.centerMode = 'gauss' (default: centered on the nose bridge and the two apples of the cheeks,
   *   decreasing to 0 toward the patch edge) or 'ellipse' (V6: an ellipse centered on the nose plus a T-band). This user's patches lie in a naturally highlighted area,
   *   and pulling the whole area to the dark skin tone of the outer contour would look flat and dirty
   * opts.warm = [delta a*, delta b*] warm compensation (default [1.8, 1.2], more red than yellow; V5's [3, 4] looked orange and dirty, V6's [0, 0] looked cold gray)
   * opts.sigma = Gaussian range of the center (x face width, default 0.20); opts.extend = outward expansion of the patch boundary (original-image pixels, default 10); opts.darkSpot = brightening of dark spots on the cheeks (L*, default 4)
   * opts.blend = multiplier of the edge transition band width (default 1); opts.neckFade = how far the concealer weight fades from the mouth corners down to the chin (default 1 = no fade)
   * opts.fill = 'local' (nearest-color sampling: the patch is filled with the low-frequency color of nearby pigmented skin, so the bright T-zone blends naturally into the patch and gets progressively darker toward the outer cheek;
   *   only large patches with no surrounding skin nearby fall back to the overall target color + center brightening) or 'global' (V7: overall target color + center brightening).
   *   Owner,: "the bright T-zone blends into the detected/filled vitiligo patches". Measured on this photo, the skin next to the patch is rather dark, so nearest-color sampling darkens the whole patch; not adopted.
   *   'tzone': take the measured lightness of pigmented skin in the T-zone (forehead center to nose tip, chin) as the bright end and the lit outer-cheek skin as the dark end,
   *   and fill the patch progressively between the two ends according to its distance from the T-zone (opts.tzoneSigma = range, x face width, default 0.18). Measured: the nose bridge is naturally bright and
   *   was counted as patch, so the measured bright end was too dark; not adopted either.
   *   opts.centerMode = 'tband': the center brightening weight uses a T shape (forehead center to nose tip + chin) decreasing progressively outward; brightening amount = opts.center
   * opts.transition = how much concealment is kept at the patch center (0-1, default unset = same strength over the whole patch): the edge keeps strength to level with the surrounding skin,
   *   the closer to the patch center the less is concealed, the center only reaches transition times, leaving a lighter middle tone -- a transition rather than a uniform dark fill
   *   . opts.transitionWidth = depth blur radius (x face width, default 0.035),
   *   opts.transitionStart = how deep before it starts to fade (default 0.5; raising it widens the fully concealed band at the edge)
   * opts.split = conceal color and lightness separately : { chroma: 0.90, edgeL: strength, centerL: 0.52, rim: 15, falloff: 35,
   *   seam: 8, seamSigma: 4 } (distance unit = original-image pixels). Color (a*, b* + warm color) is concealed at chroma over the whole patch; only lightness L* keeps edgeL for
   *   rim pixels inward from the patch edge, then drops to centerL over falloff pixels with a cosine gradient -- the center is bright but its color is already warm skin, so it looks lit rather than unconcealed.
   *   The fade applies only at the face center (under the eyes, nose bridge, inner cheeks, chin); patch centers at the temple hairline and jaw angles are still concealed to edgeL.
   *   seam = how many pixels inside and outside the patch boundary get cross-boundary smoothing (low frequency sigma ~ seamSigma, pores preserved), removing the thin light line along the boundary.
   *   Owner,: "a transition rather than pressing it into a dark color". opts.centerWarm = warm color at the patch center [delta a*, delta b*] (default same as warm; gradient from edge to center).
   *   V10 : split.lumaExtend = lightness concealment only reaches the original patch boundary (set 0; normal skin outside darker than the target
   *   keeps its lightness, only pixels brighter than the target are darkened), while color still expands with opts.extend; split.hfBand / hfKeep = within hfBand pixels inside the original boundary,
   *   the bright edge from "pixel minus low frequency" is kept only hfKeep times (at the boundary the low frequency mixes in the dark outer skin, and the difference printed back as texture becomes a bright edge).
   * opts.tWidth = width of the T-zone highlight band (x face width, default 0.12; V10 narrowed it by 25% -> 0.09)
   * opts.darkSpotAB = [delta a*, delta b*] adjusted together when brightening dark spots (default [0, 0]; V9 suggested [-1, -1])
   * opts.darkSpotGrow = how many original-image pixels the dark-spot mask expands to [right, down] (default none; V10 [8, 8], covering the dark groove at the lower edge)
   * opts.makeup = second stage "makeup" :
   *   after the concealer has leveled things out, apply T-zone highlight and outer contouring to the whole facial skin (not only the patches), moving toward the swatch color while preserving texture:
   *   { hi: '#hex' (T-zone target color, one shade lighter than the swatch), hiAmt: 0-1, co: '#hex' (contour target color, one shade darker than the swatch), coAmt: 0-1, tWidth: 0.12, coWidth: 0.09 (contour band width, x face width)}.
   *   T-zone = forehead center to nose tip + chin; contour = a ring just inside the face outline (temples, below the cheekbones, jaw), face center untouched.
   * opts.mode = 'lowfreq' (default) or 'mean' (old method)
   */
  async function apply(resultSrc, originalSrc, normalHex, patchHex, strength = 0.88, opts = {}) {
    const warm = opts.warm || [1.8, 1.2], mode = opts.mode || 'lowfreq';
    const center = opts.center === undefined ? 9 : opts.center;
    const centerMode = opts.centerMode || 'tband';
    const darkSpot = opts.darkSpot === undefined ? 4 : opts.darkSpot;
    const extendPx = opts.extend === undefined ? 10 : opts.extend;
    const [result, original] = await Promise.all([loadImg(resultSrc), loadImg(originalSrc)]);
    const det = await detectPatches(original, normalHex, patchHex, extendPx * WORK_W / (original.naturalWidth || WORK_W),
      opts.blend || 1);
    const neckFade = opts.neckFade === undefined ? 1 : opts.neckFade;
    const fill = opts.fill || 'global';
    const transition = opts.transition;
    const split = opts.split ? Object.assign({ chroma: 0.9, edgeL: strength, centerL: 0.52, rim: 15, falloff: 35, seam: 8, seamSigma: 4, lumaExtend: null, hfBand: 0, hfKeep: 1 }, opts.split) : null;
    const centerWarm = opts.centerWarm || warm;
    if (!det) return null;
    const px2w = det.w / (original.naturalWidth || det.w);   // original-image pixels -> working resolution
    const W = result.naturalWidth, H = result.naturalHeight;
    const c = document.createElement('canvas'); c.width = W; c.height = H;
    const g = c.getContext('2d', { willReadFrequently: true });
    g.drawImage(result, 0, 0);
    const img = g.getImageData(0, 0, W, H), d = img.data;
      // Average colors of the "patch area" and the "surrounding skin" in the try-on result (pixels on the face, away from the border)
    const sum = { p: [0, 0, 0, 0], n: [0, 0, 0, 0] };
    const mAt = (x, y) => det.mask[Math.min(det.h - 1, Math.floor(y * det.h / H)) * det.w + Math.min(det.w - 1, Math.floor(x * det.w / W))];
    const sc = document.createElement('canvas'); sc.width = det.w; sc.height = det.h;
    const sg = sc.getContext('2d', { willReadFrequently: true });
    const skin = polygonMask(sg, det.w, det.h, det.face, [[FACE_OVAL, false], [LEFT_EYE, true], [RIGHT_EYE, true], [LIPS, true]]);
    const skinAt = (x, y) => skin[Math.min(det.h - 1, Math.floor(y * det.h / H)) * det.w + Math.min(det.w - 1, Math.floor(x * det.w / W))];
    const nRef = srgbToLab(...hexToRgb(normalHex))[0];
    const normals = [];
    for (let y = 0; y < H; y += 2) {
      for (let x = 0; x < W; x += 2) {
        if (!skinAt(x, y)) continue;
        const m = mAt(x, y), i = (y * W + x) * 4;
        if (m > 0.9) { sum.p[0] += d[i]; sum.p[1] += d[i + 1]; sum.p[2] += d[i + 2]; sum.p[3]++; continue; }
        if (m >= 0.05) continue;
        const L = srgbToLab(d[i], d[i + 1], d[i + 2])[0];
        if (L < nRef - 25) continue;                           // surrounding skin must not include shadows, brows, stubble
        normals.push([L, d[i], d[i + 1], d[i + 2]]);
      }
    }
    if (!sum.p[3] || normals.length < 20) return null;
    // Target color = the "lit" surrounding skin after try-on (the brighter half): using the overall average would include shadows and pull the patch into a dull block (owner: "too heavy")
    normals.sort((u, v) => v[0] - u[0]);
    const lit = normals.slice(0, Math.max(10, Math.floor(normals.length / 2)));
    for (const q of lit) { sum.n[0] += q[1]; sum.n[1] += q[2]; sum.n[2] += q[3]; sum.n[3]++; }
    const litMean = [0, 1, 2].map((k) => sum.n[k] / sum.n[3]);
    const patchMean = [0, 1, 2].map((k) => sum.p[k] / sum.p[3]);
    let target = litMean;
    if (warm[0] || warm[1]) {
      const [L, a, b] = srgbToLab(...litMean);
      target = labToSrgb(L, a + warm[0], b + warm[1]);
    }
    if (mode === 'mean') {
      const shift = [0, 1, 2].map((k) => target[k] - patchMean[k]);
      for (let y = 0; y < H; y++) {
        for (let x = 0; x < W; x++) {
          const m = mAt(x, y);
          if (m < 0.02) continue;
          const a = m * strength, i = (y * W + x) * 4;
          for (let k = 0; k < 3; k++) d[i + k] = Math.max(0, Math.min(255, d[i + k] + shift[k] * a));
        }
      }
    } else {
      // Low-frequency base color: blur using only patch pixels (mask-weighted), so the border does not mix in surrounding skin color
      const w = det.w, h = det.h;
      sg.clearRect(0, 0, w, h);
      sg.drawImage(result, 0, 0, w, h);
      const sp = sg.getImageData(0, 0, w, h).data;
      const wgt = new Float32Array(w * h), ch = [0, 1, 2].map(() => new Float32Array(w * h));
      for (let i = 0; i < w * h; i++) {
        const m = skin[i] && det.mask[i] > 0.5 ? det.mask[i] : 0;
        wgt[i] = m;
        for (let k = 0; k < 3; k++) ch[k][i] = sp[i * 4 + k] * m;
      }
      const r = Math.max(3, Math.round(w * 0.025));
      const bw = boxBlur(wgt, w, h, r), bc = ch.map((a) => boxBlur(a, w, h, r));
      // The ring extended into surrounding skin: use the low-frequency base color of "all skin", so the patch's shift is not applied to normal skin and does not press out a dark rim
      const sw = new Float32Array(w * h), sch = [0, 1, 2].map(() => new Float32Array(w * h));
      for (let i = 0; i < w * h; i++) { if (skin[i]) { sw[i] = 1; for (let k = 0; k < 3; k++) sch[k][i] = sp[i * 4 + k]; } }
      const bsw = boxBlur(sw, w, h, r), bsc = sch.map((a) => boxBlur(a, w, h, r));
      const coreSoft = boxBlur(det.core, w, h, Math.max(2, Math.round(w * 0.006)));
      // Patch "depth": the farther from the patch edge, the closer to 1 (large-radius blur of the patch body, about 0.5 at the edge)
      const depthMap = transition === undefined ? null : boxBlur(det.core, w, h, Math.max(4, Math.round(w * (opts.transitionWidth || 0.035))));
      // Nearest-color sampling: use only skin that is "neither patch nor shadow/brows", blur at two radii to get the low-frequency color of nearby skin
      let tz = null;
      if (fill === 'tzone' || centerMode === 'tband') {
        // T-zone mask: a thick band from forehead center to nose tip plus chin; measure the average color of the "non-patch" skin within it (YouCam's T-zone highlight is also here)
        const f2 = det.face, P2 = (i) => [f2[i].x * w, f2[i].y * h];
        const fw2 = Math.hypot(P2(234)[0] - P2(454)[0], P2(234)[1] - P2(454)[1]);
        sg.clearRect(0, 0, w, h); sg.fillStyle = sg.strokeStyle = '#fff'; sg.lineCap = sg.lineJoin = 'round';
        sg.lineWidth = fw2 * (opts.tWidth || 0.12); sg.beginPath(); T_LINE.forEach((i, k) => (k ? sg.lineTo : sg.moveTo).call(sg, ...P2(i))); sg.stroke();
        sg.beginPath(); sg.arc((P2(199)[0] + P2(175)[0]) / 2, (P2(199)[1] + P2(175)[1]) / 2, fw2 * 0.07, 0, Math.PI * 2); sg.fill();
        const tp = sg.getImageData(0, 0, w, h).data, t0 = new Float32Array(w * h), acc = [0, 0, 0, 0];
        for (let i = 0; i < w * h; i++) {
          if (tp[i * 4 + 3] <= 128 || !skin[i]) continue;
          t0[i] = 1;
          if (det.mask[i] < 0.05) { for (let k = 0; k < 3; k++) acc[k] += sp[i * 4 + k]; acc[3]++; }
        }
        if (acc[3] > 20 || centerMode === 'tband') {
          const cT = acc[3] ? [0, 1, 2].map((k) => acc[k] / acc[3]) : target;
          // Progressively decreasing weight outward: the T-band itself = 1, smooth falloff made with several large-radius blurs
          let wT = boxBlur(t0, w, h, Math.max(4, Math.round(fw2 * (opts.tzoneSigma || 0.18) * 0.5)));
          wT = boxBlur(wT, w, h, Math.max(4, Math.round(fw2 * (opts.tzoneSigma || 0.18) * 0.5)));
          let mx = 0; for (let i = 0; i < w * h; i++) mx = Math.max(mx, wT[i]);
          for (let i = 0; i < w * h; i++) wT[i] = Math.min(1, wT[i] / (mx * 0.6 || 1));
          tz = { cT, wT };
        }
      }
      let near = null;
      if (fill === 'local') {
        const tL = srgbToLab(...target)[0], nw = new Float32Array(w * h), nch = [0, 1, 2].map(() => new Float32Array(w * h));
        for (let i = 0; i < w * h; i++) {
          if (!skin[i] || det.mask[i] > 0.05) continue;
          if (srgbToLab(sp[i * 4], sp[i * 4 + 1], sp[i * 4 + 2])[0] < tL - 22) continue;
          nw[i] = 1; for (let k = 0; k < 3; k++) nch[k][i] = sp[i * 4 + k];
        }
        const r1 = Math.max(4, Math.round(w * 0.04)), r2 = Math.max(8, Math.round(w * 0.1));
        near = [r1, r2].map((rr) => ({ w: boxBlur(nw, w, h, rr), c: nch.map((a) => boxBlur(a, w, h, rr)) }));
      }
      // Face-center weight: an ellipse centered on the nose (including inner cheeks, under the eyes, around the mouth) plus the forehead-to-nose-tip T-band, feathered with a large radius
      let cm = null, lift = [0, 0, 0];
      const f = det.face, P = (i) => [f[i].x * w, f[i].y * h];
      const fw = Math.hypot(P(234)[0] - P(454)[0], P(234)[1] - P(454)[1]);
      if (center > 0 && centerMode === 'gauss') {
        // Gaussian weights centered on the nose bridge (6), mid nose bridge (195) and the two apples of the cheeks (205, 425), multiplied by "distance from the patch edge", decreasing to 0 at the edge
        const cs = [6, 195, 205, 425].map(P), sig = fw * (opts.sigma || 0.2);
        const inner = boxBlur(det.core, w, h, Math.max(3, Math.round(w * 0.03)));
        cm = new Float32Array(w * h);
        for (let y = 0; y < h; y++) {
          for (let x = 0; x < w; x++) {
            let best = 0;
            for (const [cx, cy] of cs) best = Math.max(best, Math.exp(-((x - cx) ** 2 + (y - cy) ** 2) / (2 * sig * sig)));
            cm[y * w + x] = best * inner[y * w + x];
          }
        }
      } else if (center > 0) {
        sg.clearRect(0, 0, w, h);
        sg.fillStyle = sg.strokeStyle = '#fff';
        sg.beginPath(); sg.ellipse(...P(195), fw * 0.3, fw * 0.4, 0, 0, Math.PI * 2); sg.fill();
        sg.lineCap = sg.lineJoin = 'round'; sg.lineWidth = fw * 0.14;
        sg.beginPath(); T_LINE.forEach((i, k) => (k ? sg.lineTo : sg.moveTo).call(sg, ...P(i))); sg.stroke();
        const cp = sg.getImageData(0, 0, w, h).data, c0 = new Float32Array(w * h);
        for (let i = 0; i < w * h; i++) c0[i] = cp[i * 4 + 3] > 128 ? 1 : 0;
        cm = boxBlur(c0, w, h, Math.max(4, Math.round(w * 0.05)));
      }
      if (cm) {
        const [tL, ta, tb] = srgbToLab(...target);
        const hi = labToSrgb(Math.min(95, tL + center), ta, tb);
        lift = [0, 1, 2].map((k) => hi[k] - target[k]);
      }
      // Dark spots on the cheeks: pixels within the cheek area, not patch, and at least 10 darker than lit skin get local brightening darkSpot (after the patch is concealed, dark spots look like dirt)
      let dm = null, dlift = [0, 0, 0];
      if (darkSpot > 0) {
        const tLab = srgbToLab(...target), d0 = new Float32Array(w * h);
        for (const ci of [205, 425]) {
          const [cx, cy] = P(ci), rx = fw * 0.17, ry = fw * 0.13;
          for (let y = Math.max(0, Math.floor(cy - ry)); y < Math.min(h, Math.ceil(cy + ry)); y++) {
            for (let x = Math.max(0, Math.floor(cx - rx)); x < Math.min(w, Math.ceil(cx + rx)); x++) {
              const i = y * w + x;
              if (((x - cx) / rx) ** 2 + ((y - cy) / ry) ** 2 > 1 || !skin[i] || det.mask[i] > 0.05) continue;
              if (srgbToLab(sp[i * 4], sp[i * 4 + 1], sp[i * 4 + 2])[0] < tLab[0] - 10) d0[i] = 1;
            }
          }
        }
        if (opts.darkSpotGrow) {
          // Expand to the right and down (original-image pixels -> working resolution), covering the dark groove between the lower edge of the dark spot and the patch border
          const gx = Math.round(opts.darkSpotGrow[0] * px2w), gy = Math.round(opts.darkSpotGrow[1] * px2w), g0 = Float32Array.from(d0);
          for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
            const i = y * w + x; if (!skin[i] || d0[i]) continue;
            for (let oy = 0; oy <= gy && !d0[i]; oy++) for (let ox = 0; ox <= gx; ox++) {
              if (y - oy >= 0 && x - ox >= 0 && g0[(y - oy) * w + x - ox]) { d0[i] = 1; break; }
            }
          }
        }
        dm = boxBlur(d0, w, h, Math.max(2, Math.round(w * 0.012)));
        const ab = opts.darkSpotAB || [0, 0];
        const hi = labToSrgb(Math.min(95, tLab[0] + darkSpot), tLab[1] + ab[0], tLab[2] + ab[1]);
        dlift = [0, 1, 2].map((k) => hi[k] - target[k]);
      }
      // V9: "distance from the edge" inside the patch (working-resolution pixels, 3-4 chamfer distance) + face-center weight + boundary seam band
      let lumT = null, seamBand = null, lumaMask = null, hfBand = null;
      if (split) {
        const INF = 1e9, dist = new Float32Array(w * h);
        for (let i = 0; i < w * h; i++) dist[i] = det.core[i] > 0.5 ? INF : 0;
        for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
          const i = y * w + x; if (!dist[i]) continue;
          let v = dist[i];
          if (x > 0) v = Math.min(v, dist[i - 1] + 3);
          if (y > 0) { v = Math.min(v, dist[i - w] + 3); if (x > 0) v = Math.min(v, dist[i - w - 1] + 4); if (x < w - 1) v = Math.min(v, dist[i - w + 1] + 4); }
          dist[i] = v;
        }
        for (let y = h - 1; y >= 0; y--) for (let x = w - 1; x >= 0; x--) {
          const i = y * w + x; if (!dist[i]) continue;
          let v = dist[i];
          if (x < w - 1) v = Math.min(v, dist[i + 1] + 3);
          if (y < h - 1) { v = Math.min(v, dist[i + w] + 3); if (x < w - 1) v = Math.min(v, dist[i + w + 1] + 4); if (x > 0) v = Math.min(v, dist[i + w - 1] + 4); }
          dist[i] = v;
        }
        // Face center: an ellipse centered on the nose (under the eyes, inner cheeks, around the mouth, chin) plus the forehead-center-to-nose-tip T-band, feathered; temple corners and jaw angles excluded
        sg.clearRect(0, 0, w, h); sg.fillStyle = sg.strokeStyle = '#fff';
        sg.beginPath(); sg.ellipse(...P(195), fw * 0.27, fw * 0.45, 0, 0, Math.PI * 2); sg.fill();
        sg.lineCap = sg.lineJoin = 'round'; sg.lineWidth = fw * 0.14;
        sg.beginPath(); T_LINE.forEach((i, k) => (k ? sg.lineTo : sg.moveTo).call(sg, ...P(i))); sg.stroke();
        const cp = sg.getImageData(0, 0, w, h).data, c0 = new Float32Array(w * h);
        for (let i = 0; i < w * h; i++) c0[i] = cp[i * 4 + 3] > 128 ? 1 : 0;
        const cen = boxBlur(c0, w, h, Math.max(3, Math.round(w * 0.04)));
        const rim = split.rim * px2w, fall = Math.max(1, split.falloff * px2w);
        lumT = new Float32Array(w * h);
        for (let i = 0; i < w * h; i++) {
          const dd = dist[i] / 3 - rim;
          const t = dd <= 0 ? 0 : dd >= fall ? 1 : (1 - Math.cos(Math.PI * dd / fall)) / 2;
          lumT[i] = t * Math.min(1, cen[i]);
        }
        if (split.seam > 0) {
          // Store on det for the last step (full-resolution seam smoothing)
          const rb = Math.max(1, Math.round(split.seam * px2w / 2));
          const cb = boxBlur(det.core, w, h, rb), sIn = boxBlur(skin, w, h, rb * 2);
          seamBand = new Float32Array(w * h);
          for (let i = 0; i < w * h; i++) {
            seamBand[i] = Math.max(0, 1 - Math.abs(2 * cb[i] - 1)) * Math.min(1, Math.max(0, (sIn[i] - 0.9) / 0.1));
          }
          det.seamBand = seamBand;
        }
        if (split.lumaExtend !== null) {
          // Lightness is concealed only to the original patch boundary: the patch body is feathered with a small radius and upscaled, equal to 1 at the boundary and dropping to 0 within about 2 cells outward
          const rr = Math.max(1, Math.round(split.lumaExtend * px2w) + 2), cbL = boxBlur(det.core, w, h, rr);
          lumaMask = new Float32Array(w * h);
          for (let i = 0; i < w * h; i++) lumaMask[i] = Math.min(1, 2 * cbL[i]);
        }
        if (split.hfBand > 0 && split.hfKeep < 1) {
          // Ring band just inside the original boundary (hfBand pixels inward)
          const rh = Math.max(1, Math.round(split.hfBand * px2w / 2)), ch2 = boxBlur(det.core, w, h, rh);
          hfBand = new Float32Array(w * h);
          for (let i = 0; i < w * h; i++) hfBand[i] = det.core[i] > 0.5 ? Math.max(0, 1 - Math.abs(2 * ch2[i] - 1)) : 0;
        }
      }
      if (opts.makeup) {
        // Weights of the V11 makeup layer (working resolution): T-band + chin, a ring just inside the face outline
        const mk = opts.makeup;
        sg.clearRect(0, 0, w, h); sg.fillStyle = sg.strokeStyle = '#fff'; sg.lineCap = sg.lineJoin = 'round';
        sg.lineWidth = fw * (mk.tWidth || 0.12); sg.beginPath(); T_LINE.forEach((i, k) => (k ? sg.lineTo : sg.moveTo).call(sg, ...P(i))); sg.stroke();
        sg.beginPath(); sg.arc((P(199)[0] + P(175)[0]) / 2, (P(199)[1] + P(175)[1]) / 2, fw * 0.07, 0, Math.PI * 2); sg.fill();
        const tp = sg.getImageData(0, 0, w, h).data, t0 = new Float32Array(w * h);
        for (let i = 0; i < w * h; i++) t0[i] = tp[i * 4 + 3] > 128 && skin[i] ? 1 : 0;
        let wT = boxBlur(t0, w, h, Math.max(3, Math.round(fw * 0.05)));
        let mx = 0; for (let i = 0; i < w * h; i++) mx = Math.max(mx, wT[i]);
        for (let i = 0; i < w * h; i++) wT[i] = Math.min(1, wT[i] / (mx * 0.7 || 1)) * skin[i];
        const ov = polygonMask(sg, w, h, f, [[FACE_OVAL, false]]), inner = boxBlur(ov, w, h, Math.max(4, Math.round(fw * (mk.coWidth || 0.09))));
        sg.clearRect(0, 0, w, h); sg.fillStyle = '#fff';
        sg.beginPath(); sg.ellipse(...P(195), fw * 0.24, fw * 0.36, 0, 0, Math.PI * 2); sg.fill();
        const cp2 = sg.getImageData(0, 0, w, h).data, c2 = new Float32Array(w * h);
        for (let i = 0; i < w * h; i++) c2[i] = cp2[i * 4 + 3] > 128 ? 1 : 0;
        const cen2 = boxBlur(c2, w, h, Math.max(3, Math.round(fw * 0.06)));
        let wC = new Float32Array(w * h);
        for (let i = 0; i < w * h; i++) wC[i] = skin[i] ? Math.min(1, Math.max(0, (1 - inner[i]) * 2.2)) * (1 - Math.min(1, cen2[i])) * (1 - wT[i]) : 0;
        wC = boxBlur(wC, w, h, Math.max(2, Math.round(fw * 0.03)));
        det.mk = { wT, wC };
      }
      for (let y = 0; y < H; y++) {
        const wy = Math.min(h - 1, Math.floor(y * h / H));
        for (let x = 0; x < W; x++) {
          const j = wy * w + Math.min(w - 1, Math.floor(x * w / W)), i = (y * W + x) * 4;
          const m = mAt(x, y), dw = dm ? dm[j] : 0;
          if (m < 0.02 && dw < 0.02) continue;
          if (dw >= 0.02) for (let k = 0; k < 3; k++) d[i + k] = Math.max(0, Math.min(255, d[i + k] + dlift[k] * dw));
          if (m < 0.02) continue;
          let a = m * strength;
          if (depthMap) {
            const dep = Math.min(1, Math.max(0, (depthMap[j] - (opts.transitionStart || 0.5)) / 0.35));   // the higher transitionStart, the wider the fully concealed band at the edge
            a *= 1 - (1 - transition) * dep;
          }
          if (neckFade < 1) {
            // Lower-face fade: between below the mouth corners (17) and the chin (152), the weight decreases progressively from 1 to neckFade, so the jaw-to-neck transition does not cut off like a mask
            const t = Math.min(1, Math.max(0, (y / H - f[17].y) / Math.max(1e-3, f[152].y - f[17].y)));
            a *= 1 - (1 - neckFade) * t;
          }
          const cw = cm ? cm[j] : 0, core = coreSoft[j];
          // Confidence of nearest-color sampling: use the small radius if enough surrounding skin is nearby, otherwise the large radius, and if still insufficient fall back to the overall target color + center brightening
          let nearW = 0, nearC = null;
          if (near) {
            const s1 = Math.min(1, near[0].w[j] / 0.2), s2 = Math.min(1, near[1].w[j] / 0.1);
            if (s1 > 0 || s2 > 0) {
              const c1 = near[0].w[j] > 0.01 ? near[0].c.map((a) => a[j] / near[0].w[j]) : null;
              const c2 = near[1].w[j] > 0.01 ? near[1].c.map((a) => a[j] / near[1].w[j]) : c1;
              nearC = [0, 1, 2].map((k) => (c1 ? c1[k] * s1 : 0) + (c2 ? c2[k] : 0) * (1 - s1));
              nearW = Math.max(s1, s2);
            }
          }
          const lowV = [0, 0, 0], tgtV = [0, 0, 0];
          for (let k = 0; k < 3; k++) {
            const lowP = bw[j] > 0.05 ? bc[k][j] / bw[j] : patchMean[k];
            const lowS = bsw[j] > 0.05 ? bsc[k][j] / bsw[j] : lowP;
            const low = lowP * core + lowS * (1 - core);
            const glob = tz && fill === 'tzone' ? target[k] + (tz.cT[k] + (target[k] - litMean[k]) - target[k]) * tz.wT[j]
              : target[k] + lift[k] * (tz && centerMode === 'tband' ? tz.wT[j] : cw);
            const tgt = nearC ? (nearC[k] + (target[k] - litMean[k])) * nearW + glob * (1 - nearW) : glob;
            lowV[k] = low; tgtV[k] = tgt;
            if (!split) d[i + k] = Math.max(0, Math.min(255, d[i + k] + (tgt - low) * a));
          }
          if (split) {
            // Color is concealed over the whole patch at chroma; lightness goes progressively from edge edgeL to center centerL (a already includes mask feathering and lower-face fade, converted to a ratio)
            const t = lumT[j], scale = a / strength;
            // V10: lightness uses the mask that reaches only "the original boundary" (undo the lower-face fade in a: a / (m x strength))
            const scaleL = lumaMask ? lumaMask[j] * a / (m * strength) : scale;
            const aL = scaleL * (split.edgeL - (split.edgeL - split.centerL) * t), aC = scale * split.chroma;
            const pl = srgbToLab(d[i], d[i + 1], d[i + 2]), ll = srgbToLab(...lowV), tl = srgbToLab(...tgtV);
            tl[1] += (centerWarm[0] - warm[0]) * t; tl[2] += (centerWarm[1] - warm[1]) * t;
            let L2 = pl[0] + (tl[0] - ll[0]) * aL;
            if (hfBand && hfBand[j] > 0 && pl[0] > ll[0]) L2 -= (1 - split.hfKeep) * hfBand[j] * (pl[0] - ll[0]);   // inside the boundary, keep only hfKeep of the bright edge
            if (lumaMask && core < 0.5) L2 = pl[0] > tl[0] ? Math.max(tl[0], Math.min(pl[0], L2)) : pl[0];       // outside the boundary: only darken pixels brighter than the target
            const o = labToSrgb(L2, pl[1] + (tl[1] - ll[1]) * aC, pl[2] + (tl[2] - ll[2]) * aC);
            d[i] = o[0]; d[i + 1] = o[1]; d[i + 2] = o[2];
          }
        }
      }
    }
    if (split && split.seam > 0 && det.seamBand) {
      // Seam smoothing: within the boundary band, replace the "mid-low frequency" with a blur of sigma ~ seamSigma, keeping the finest pore texture (外部建議: "coastline")
      const rL = Math.max(1, Math.round(split.seamSigma)), band = det.seamBand;
      for (let k = 0; k < 3; k++) {
        const ch = new Float32Array(W * H);
        for (let q = 0; q < W * H; q++) ch[q] = d[q * 4 + k];
        const lo = boxBlur(ch, W, H, rL), fine = boxBlur(ch, W, H, 1);
        for (let y = 0; y < H; y++) {
          const wy = Math.min(det.h - 1, Math.floor(y * det.h / H));
          for (let x = 0; x < W; x++) {
            const bw2 = band[wy * det.w + Math.min(det.w - 1, Math.floor(x * det.w / W))];
            if (bw2 < 0.01) continue;
            const q = y * W + x;
            d[q * 4 + k] = Math.max(0, Math.min(255, ch[q] + (lo[q] - fine[q]) * bw2));
          }
        }
      }
    }
    if (opts.makeup && det.mk) {
      // Second-stage makeup: move the low-frequency base color toward the T-zone / contour target colors; pixels get the same shift (texture unchanged)
      const mk = opts.makeup, ww = det.w, hh = det.h, { wT, wC } = det.mk;
      const lowL = [0, 1, 2].map(() => new Float32Array(ww * hh));
      for (let y = 0; y < hh; y++) for (let x = 0; x < ww; x++) {
        const q = (Math.min(H - 1, Math.floor((y + 0.5) * H / hh)) * W + Math.min(W - 1, Math.floor((x + 0.5) * W / ww))) * 4;
        const lab = srgbToLab(d[q], d[q + 1], d[q + 2]);
        for (let k = 0; k < 3; k++) lowL[k][y * ww + x] = lab[k];
      }
      const lowB = lowL.map((a) => boxBlur(a, ww, hh, Math.max(3, Math.round(ww * 0.02))));
      const hiLab = srgbToLab(...hexToRgb(mk.hi)), coLab = srgbToLab(...hexToRgb(mk.co));
      const sh = [0, 1, 2].map(() => new Float32Array(ww * hh));
      for (let i = 0; i < ww * hh; i++) {
        const a1 = (mk.hiAmt || 0) * wT[i], a2 = (mk.coAmt || 0) * wC[i];
        for (let k = 0; k < 3; k++) sh[k][i] = (hiLab[k] - lowB[k][i]) * a1 + (coLab[k] - lowB[k][i]) * a2;
      }
      const shB = sh.map((a) => boxBlur(a, ww, hh, 2));
      for (let y = 0; y < H; y++) {
        const wy = Math.min(hh - 1, Math.floor(y * hh / H));
        for (let x = 0; x < W; x++) {
          const j = wy * ww + Math.min(ww - 1, Math.floor(x * ww / W));
          if (Math.abs(shB[0][j]) < 0.05 && Math.abs(shB[1][j]) < 0.05 && Math.abs(shB[2][j]) < 0.05) continue;
          const q = (y * W + x) * 4, lab = srgbToLab(d[q], d[q + 1], d[q + 2]);
          const o = labToSrgb(lab[0] + shB[0][j], lab[1] + shB[1][j], lab[2] + shB[2][j]);
          d[q] = o[0]; d[q + 1] = o[1]; d[q + 2] = o[2];
        }
      }
    }
    g.putImageData(img, 0, 0);
    return { image: c.toDataURL('image/jpeg', 0.92), ratio: det.ratio };
  }

  window.Camouflage = { apply, detectPatches };
})();
