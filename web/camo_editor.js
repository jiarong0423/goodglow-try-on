'use strict';
// Local concealer for light patches: AI finds boundaries -> SVG vector paths (editable) -> layered Canvas rendering (computed on the phone, no upload, no points deducted).
// Request (10-16): supplied an architecture and PoC of "AI image analysis + SVG vector paths + pixel-level Canvas rendering" and asked for it to be implemented.
// 1. Boundary finding: reuses the light-patch detection in camouflage.js (chroma/lightness clustering); OpenCV.js findContours + approxPolyDP extract each patch's outline.
// 2. SVG paths: outline points are smoothed into a path string with quadratic Beziers (using midpoints of adjacent points); each patch can be toggled and its nodes dragged to adjust the boundary.
// 3. Layered Canvas (Lab): color-correction layer (warm-orange neutralization, so the result does not look gray) -> concealer layer (normal skin color sampled around each patch) -> texture layer (high-frequency detail from the original image).
//    Feathering uses a box blur (iPhone Safari does not support ctx.filter); white brows, eyes and the inner mouth have separate protected zones that are never modified.
// The UI always labels this as "local concealer simulated on the phone".

(function () {
  const FACE_OVAL = [10, 338, 297, 332, 284, 251, 389, 356, 454, 323, 361, 288, 397, 365, 379, 378, 400, 377, 152, 148,
    176, 149, 150, 136, 172, 58, 132, 93, 234, 127, 162, 21, 54, 103, 67, 109];
  const PROTECT = [
    [70, 63, 105, 66, 107, 55, 65, 52, 53, 46], [300, 293, 334, 296, 336, 285, 295, 282, 283, 276],           // brows
    [33, 7, 163, 144, 145, 153, 154, 155, 133, 173, 157, 158, 159, 160, 161, 246],                               // eyes
    [362, 382, 381, 380, 374, 373, 390, 249, 263, 466, 388, 387, 386, 385, 384, 398],
    [61, 146, 91, 181, 84, 17, 314, 405, 321, 375, 291, 409, 270, 269, 267, 0, 37, 39, 40, 185],                // whole lips (outer lip line; on 10-17 only the inner mouth was protected, and large patches lightened the lips)
  ];
  const BROW_TOP = [70, 63, 105, 66, 107, 336, 296, 334, 293, 300];   // upper edge of the brows (lower bound of the forehead zone)
  const ALPHA_CORE = 80, ALPHA_WIDE = 24, SIDE_W = 0.6;   // highlight concentration (skin typically 10-60), side-light strength
  const LIPS_OUTER = [61, 146, 91, 181, 84, 17, 314, 405, 321, 375, 291, 409, 270, 269, 267, 0, 37, 39, 40, 185];
  const RENDER_W = 720;
  const RING_L_PCT = 0.7;                // which percentile of the surrounding skin lightness is the target (the lit side)
  const HI_L = 10;                       // at 100% highlight, max L* raised at the face center (10-17 review suggested lightness +10-15%, saturation -5%; was 8)
  const HI_WIDE = 1.3;                   // light-spot widening factor (review: highlight feathering must be very soft, fading out naturally)
  // Highlight (makeup highlighter): one Gaussian light spot each for forehead center, nose bridge, under-eye cheekbone (C-shaped area) and chin: [grid point, width (x face width), height (x face width), strength]
  const HI_SPOTS = [[151, 0.13, 0.07, 1], [9, 0.05, 0.05, 0.7], [6, 0.03, 0.06, 0.9], [195, 0.025, 0.05, 0.9], [4, 0.025, 0.025, 0.6],
    [117, 0.07, 0.04, 0.8], [346, 0.07, 0.04, 0.8], [199, 0.05, 0.035, 0.7]];
  const WARM = [3.0, 2.8];               // color-correction layer: peach/warm-orange delta a*, delta b*

  function srgbToLab(r, g, b) {
    const lin = (c) => { c /= 255; return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4; };
    const R = lin(r), G = lin(g), B = lin(b);
    const x = (0.4124 * R + 0.3576 * G + 0.1805 * B) / 0.95047;
    const y = 0.2126 * R + 0.7152 * G + 0.0722 * B;
    const z = (0.0193 * R + 0.1192 * G + 0.9505 * B) / 1.08883;
    const f = (t) => (t > 216 / 24389 ? Math.cbrt(t) : (24389 / 27 * t + 16) / 116);
    return [116 * f(y) - 16, 500 * (f(x) - f(y)), 200 * (f(y) - f(z))];
  }
  function labToSrgb(L, a, b) {
    const fy = (L + 16) / 116, fx = fy + a / 500, fz = fy - b / 200;
    const inv = (t) => (t ** 3 > 216 / 24389 ? t ** 3 : (116 * t - 16) / (24389 / 27));
    const X = inv(fx) * 0.95047, Y = inv(fy), Z = inv(fz) * 1.08883;
    const lin = [3.2406 * X - 1.5372 * Y - 0.4986 * Z, -0.9689 * X + 1.8758 * Y + 0.0415 * Z, 0.0557 * X - 0.204 * Y + 1.057 * Z];
    return lin.map((c) => 255 * Math.max(0, Math.min(1, c <= 0.0031308 ? 12.92 * c : 1.055 * c ** (1 / 2.4) - 0.055)));
  }
  const hexToLab = (hex) => { const n = parseInt(hex.slice(1), 16); return srgbToLab(n >> 16, (n >> 8) & 255, n & 255); };
  // Blur inside and outside the patch separately: inside a light patch average only with the patch interior, outside only with the exterior,
  // so the border (the patch edge) falls into the low frequency and the makeup layer can cover it; if everything is blurred together, the border stays in the texture and the outline remains visible no matter how it is layered.
  // Measured with tools/fit_layers_to_target.py: with the border in the low frequency, the fitting error drops 1.62 -> 1.59 (when color is fitted too).
  // Returns blur(src): the mask blur is computed only once, then each channel needs one more blur.
  function makeSplitBlur(inside, w, h, r) {
    const n = w * h, mIn = new Float32Array(n), mOut = new Float32Array(n);
    for (let i = 0; i < n; i++) { const m = inside[i] > 0.5 ? 1 : 0; mIn[i] = m; mOut[i] = 1 - m; }
    const bIn = boxBlur(mIn, w, h, r), bOut = boxBlur(mOut, w, h, r);
    return (src) => {
      const a = new Float32Array(n), b = new Float32Array(n);
      for (let i = 0; i < n; i++) { a[i] = src[i] * mIn[i]; b[i] = src[i] * mOut[i]; }
      const ba = boxBlur(a, w, h, r), bb = boxBlur(b, w, h, r), out = new Float32Array(n);
      for (let i = 0; i < n; i++) {
        const own = mIn[i] ? (bIn[i] > 1e-3 ? ba[i] / bIn[i] : NaN) : (bOut[i] > 1e-3 ? bb[i] / bOut[i] : NaN);
        out[i] = Number.isFinite(own) ? own : (ba[i] + bb[i]);       // own side has no pixels (does not happen for normal-sized patches): fall back to the whole-area average
      }
      return out;
    };
  }
  function boxBlur(src, w, h, r) {
    let a = src;
    for (let pass = 0; pass < 2; pass++) {
      const b = new Float32Array(w * h), c = new Float32Array(w * h);
      for (let y = 0; y < h; y++) {
        let s = 0; const row = y * w;
        for (let x = -r; x <= r; x++) s += a[row + Math.min(w - 1, Math.max(0, x))];
        for (let x = 0; x < w; x++) { b[row + x] = s / (2 * r + 1); s += a[row + Math.min(w - 1, x + r + 1)] - a[row + Math.max(0, x - r)]; }
      }
      for (let x = 0; x < w; x++) {
        let s = 0;
        for (let y = -r; y <= r; y++) s += b[Math.min(h - 1, Math.max(0, y)) * w + x];
        for (let y = 0; y < h; y++) { c[y * w + x] = s / (2 * r + 1); s += b[Math.min(h - 1, y + r + 1) * w + x] - b[Math.max(0, y - r) * w + x]; }
      }
      a = c;
    }
    return a;
  }
  const loadImg = (src) => new Promise((ok, bad) => { const i = new Image(); i.onload = () => ok(i); i.onerror = bad; i.src = src; });

  // Quadratic Bezier smoothing (PoC approach): each segment uses the original point as control point and goes to the midpoint of the two neighbors -> smooth outline, no jaggies
  function pathD(pts) {
    if (pts.length < 3) return '';
    const f = (v) => v.toFixed(1), n = pts.length;
    const mid = (i) => [(pts[i][0] + pts[(i + 1) % n][0]) / 2, (pts[i][1] + pts[(i + 1) % n][1]) / 2];
    const m0 = mid(n - 1);
    let d = `M${f(m0[0])} ${f(m0[1])}`;
    for (let i = 0; i < n; i++) { const m = mid(i); d += ` Q${f(pts[i][0])} ${f(pts[i][1])} ${f(m[0])} ${f(m[1])}`; }
    return d + ' Z';
  }

  // Delaunay triangulation (Bowyer-Watson; this OpenCV.js build has no Subdiv2D). 468 points, O(n^2) is fast enough
  function delaunay(pts) {
    const n = pts.length, xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1]);
    const minX = Math.min(...xs), minY = Math.min(...ys), d = Math.max(Math.max(...xs) - minX, Math.max(...ys) - minY) * 20;
    const all = pts.concat([[minX - d, minY - d], [minX + 2 * d, minY - d], [minX - d, minY + 2 * d]]);
    const circ = (i, j, k) => {
      const [ax, ay] = all[i], [bx, by] = all[j], [cx, cy] = all[k], D = 2 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by));
      if (Math.abs(D) < 1e-12) return { x: 0, y: 0, r2: Infinity };
      const ux = ((ax * ax + ay * ay) * (by - cy) + (bx * bx + by * by) * (cy - ay) + (cx * cx + cy * cy) * (ay - by)) / D;
      const uy = ((ax * ax + ay * ay) * (cx - bx) + (bx * bx + by * by) * (ax - cx) + (cx * cx + cy * cy) * (bx - ax)) / D;
      return { x: ux, y: uy, r2: (ax - ux) ** 2 + (ay - uy) ** 2 };
    };
    let tris = [[n, n + 1, n + 2, circ(n, n + 1, n + 2)]];
    for (let p = 0; p < n; p++) {
      const [px, py] = all[p], bad = [], keep = [];
      for (const t of tris) ((px - t[3].x) ** 2 + (py - t[3].y) ** 2 < t[3].r2 ? bad : keep).push(t);
      const edges = new Map();
      for (const t of bad) for (const [a, b] of [[t[0], t[1]], [t[1], t[2]], [t[2], t[0]]]) {
        const k = a < b ? `${a},${b}` : `${b},${a}`;
        edges.set(k, edges.has(k) ? null : [a, b]);
      }
      for (const e of edges.values()) if (e) keep.push([e[0], e[1], p, circ(e[0], e[1], p)]);
      tris = keep;
    }
    return tris.filter((t) => t[0] < n && t[1] < n && t[2] < n).map((t) => [t[0], t[1], t[2]]);
  }

  // 3D highlight: the 468 MediaPipe mesh points have depth z,
  // triangulate -> vertex normals (average of adjacent triangle faces) -> per-pixel interpolation into a normal map -> Blinn-Phong specular highlight (light source in front and above).
  // Forehead, nose bridge and cheekbones light up following the bone shape, and the faint highlight on the face sides moves with them. Normals are not derived from a grayscale gradient: lighter-patch edges would be treated as bumps and light up.
  // Returns core (concentrated highlight, alpha=24) and wide (diffuse, alpha=8, with a bit of warm color mixed at the edge to simulate translucency)
  function meshSpecular(det, W, H, skin, protect) {
    const N = 468, P3 = det.face.slice(0, N).map((p) => [p.x * W, p.y * H, p.z * W]);
    const tris = delaunay(P3.map((p) => [p[0], p[1]]));
    if (tris.length < 300) return null;
    const vn = P3.map(() => [0, 0, 0]);
    for (const [a, b, c] of tris) {
      const u = [P3[b][0] - P3[a][0], P3[b][1] - P3[a][1], P3[b][2] - P3[a][2]], v = [P3[c][0] - P3[a][0], P3[c][1] - P3[a][1], P3[c][2] - P3[a][2]];
      let nx = u[1] * v[2] - u[2] * v[1], ny = u[2] * v[0] - u[0] * v[2], nz = u[0] * v[1] - u[1] * v[0];
      if (nz > 0) { nx = -nx; ny = -ny; nz = -nz; }                 // face the camera (in MediaPipe a smaller z is closer to the camera)
      for (const k of [a, b, c]) { vn[k][0] += nx; vn[k][1] += ny; vn[k][2] += nz; }
    }
    for (const v of vn) { const l = Math.hypot(...v) || 1; v[0] /= l; v[1] /= l; v[2] /= l; }
    // Three lights: front-above plus left and right obliquely above (common in makeup lighting; with only one front light the sides of the nose bridge and the cheekbones get no light)
    const half = (L) => { const l = Math.hypot(...L), h = [L[0] / l, L[1] / l, L[2] / l - 1], hl = Math.hypot(...h); return h.map((v) => v / hl); };
    const HS = [half([0, -0.6, -1]), half([-0.55, -0.45, -1]), half([0.55, -0.45, -1])], HW = [1, SIDE_W, SIDE_W];
    // No highlight on the lips (outer lip line polygon)
    const lip = document.createElement('canvas'); lip.width = W; lip.height = H;
    const lg = lip.getContext('2d', { willReadFrequently: true }); lg.fillStyle = '#fff'; lg.beginPath();
    LIPS_OUTER.forEach((k, j) => (j ? lg.lineTo(P3[k][0], P3[k][1]) : lg.moveTo(P3[k][0], P3[k][1]))); lg.closePath(); lg.fill();
    const ld = lg.getImageData(0, 0, W, H).data, lipM = new Float32Array(W * H);
    for (let i = 0; i < W * H; i++) lipM[i] = ld[i * 4 + 3] / 255;
    const lipB = boxBlur(lipM, W, H, Math.max(3, Math.round(W * 0.008)));
    const core = new Float32Array(W * H), wide = new Float32Array(W * H), diffuse = new Float32Array(W * H), cover = new Float32Array(W * H);
    const LF = [0, -0.6, -1].map((v, _, a) => v / Math.hypot(...a));   // diffuse light: front-above (adds volume to the makeup layer)
    for (const [a, b, c] of tris) {
      const [ax, ay] = P3[a], [bx, by] = P3[b], [cx, cy] = P3[c];
      const den = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy);
      if (Math.abs(den) < 1e-6) continue;
      const x0 = Math.max(0, Math.floor(Math.min(ax, bx, cx))), x1 = Math.min(W - 1, Math.ceil(Math.max(ax, bx, cx)));
      const y0 = Math.max(0, Math.floor(Math.min(ay, by, cy))), y1 = Math.min(H - 1, Math.ceil(Math.max(ay, by, cy)));
      for (let y = y0; y <= y1; y++) for (let x = x0; x <= x1; x++) {
        const w1 = ((by - cy) * (x - cx) + (cx - bx) * (y - cy)) / den, w2 = ((cy - ay) * (x - cx) + (ax - cx) * (y - cy)) / den, w3 = 1 - w1 - w2;
        if (w1 < -1e-3 || w2 < -1e-3 || w3 < -1e-3) continue;
        let nx = w1 * vn[a][0] + w2 * vn[b][0] + w3 * vn[c][0], ny = w1 * vn[a][1] + w2 * vn[b][1] + w3 * vn[c][1], nz = w1 * vn[a][2] + w2 * vn[b][2] + w3 * vn[c][2];
        const l = Math.hypot(nx, ny, nz) || 1, i = y * W + x;
        let cs = 0, ws = 0;
        for (let q = 0; q < 3; q++) {
          const d = (nx * HS[q][0] + ny * HS[q][1] + nz * HS[q][2]) / l;
          if (d > 0) { cs = Math.max(cs, HW[q] * d ** ALPHA_CORE); ws = Math.max(ws, HW[q] * d ** ALPHA_WIDE); }
        }
        core[i] = cs; wide[i] = ws;
        diffuse[i] = Math.max(0, (nx * LF[0] + ny * LF[1] + nz * LF[2]) / l); cover[i] = 1;
      }
    }
    let mx = 0;
    for (let i = 0; i < W * H; i++) { const m = skin[i] * (1 - protect[i]) * (1 - Math.min(1, lipB[i] * 1.5)); core[i] *= m; wide[i] *= m; if (core[i] > mx) mx = core[i]; }
    if (mx < 0.05) return null;
    for (let i = 0; i < W * H; i++) { core[i] /= mx; wide[i] = Math.min(1, wide[i] / Math.max(mx, 0.3)); }
    return { core: boxBlur(core, W, H, 2), wide: boxBlur(wide, W, H, 2), diffuse: boxBlur(diffuse, W, H, 3), cover, tris: tris.length };
  }

  let S = null;

  // Prepare: photo Lab, low frequency, face skin region, protected zones, core mask of light patches (camouflage.js)
  async function prepare(src, normalHex, patchHex) {
    if (S && S.src === src && S.normalHex === normalHex && S.patchHex === patchHex) return S;
    if (!window.Camouflage) throw new Error(__t('s0710', '沒有載入 camouflage.js'));
    const img = await loadImg(src);
    const det = await window.Camouflage.detectPatches(img, normalHex, patchHex, 0, 1);
    if (!det) throw new Error(__t('s0711', '找不到臉或淺色區塊'));
    const W = Math.min(RENDER_W, img.naturalWidth), H = Math.round(W * img.naturalHeight / img.naturalWidth);
    const cv = document.createElement('canvas'); cv.width = W; cv.height = H;
    const g = cv.getContext('2d', { willReadFrequently: true }); g.drawImage(img, 0, 0, W, H);
    const px = g.getImageData(0, 0, W, H).data;
    const L = new Float32Array(W * H), A = new Float32Array(W * H), B = new Float32Array(W * H);
    for (let i = 0; i < W * H; i++) { const lab = srgbToLab(px[i * 4], px[i * 4 + 1], px[i * 4 + 2]); L[i] = lab[0]; A[i] = lab[1]; B[i] = lab[2]; }
    const P = (i) => [det.face[i].x * W, det.face[i].y * H];
    const xs = FACE_OVAL.map((i) => P(i)[0]), fw = Math.max(...xs) - Math.min(...xs);
    const r = Math.max(3, Math.round(fw * 0.03));
    const raster = (draw) => {
      const c = document.createElement('canvas'); c.width = W; c.height = H;
      const cg = c.getContext('2d', { willReadFrequently: true }); cg.fillStyle = '#fff'; cg.strokeStyle = '#fff'; cg.lineJoin = 'round';
      draw(cg);
      const d = cg.getImageData(0, 0, W, H).data, a = new Float32Array(W * H);
      for (let i = 0; i < W * H; i++) a[i] = d[i * 4 + 3] / 255;
      return a;
    };
    const poly = (ids) => { const p = new Path2D(); ids.forEach((i, k) => (k ? p.lineTo(...P(i)) : p.moveTo(...P(i)))); p.closePath(); return p; };
    const skinRaw = raster((cg) => cg.fill(poly(FACE_OVAL)));
    // Forehead and chin zones:
    // the face-mesh outline only reaches the middle of the forehead (point 10); the upper forehead up to the hairline and the chin edge are added from MediaPipe's multi-class segmentation "face skin".
    const xs0 = Math.min(...xs), xs1 = Math.max(...xs);
    const browPts = BROW_TOP.map(P).sort((u, v) => v[0] - u[0]);
    const zoneF = raster((cg) => {                                  // forehead: above the upper brow edge, within the face width
      cg.beginPath(); cg.moveTo(xs0, 0); cg.lineTo(xs1, 0); cg.lineTo(xs1, browPts[0][1]);
      browPts.forEach((q) => cg.lineTo(q[0], q[1])); cg.lineTo(xs0, browPts[browPts.length - 1][1]); cg.closePath(); cg.fill();
    });
    const chinY = P(17)[1] + fw * 0.02, jx0 = P(172)[0], jx1 = P(397)[0];
    const zoneC = raster((cg) => cg.fillRect(Math.min(jx0, jx1), chinY, Math.abs(jx1 - jx0), H - chinY));   // chin: below the lower lip, between the jaw angles on both sides
    // Segmentation uses a separate regular canvas (a willReadFrequently canvas sent to MediaPipe was measured 6x slower: 1.4 s vs 0.2 s)
    const segCv = document.createElement('canvas'); segCv.width = W; segCv.height = H; segCv.getContext('2d').drawImage(img, 0, 0, W, H);
    const S0normalL = hexToLab(normalHex)[0];
    const seg = window.FaceAuto && window.FaceAuto.segmentCached ? await window.FaceAuto.segmentCached(src, segCv) : null;
    let hairBg = null, segAdded = 0, segCls = null;
    if (seg) {
      const sx = seg.w / W, sy = seg.h / H, colIdx = new Int32Array(W);
      for (let x = 0; x < W; x++) colIdx[x] = Math.min(seg.w - 1, Math.floor(x * sx));
      hairBg = new Float32Array(W * H); segCls = new Uint8Array(W * H);   // segCls: the makeup layer needs to know neck and ears (foundation is pushed away)
      for (let y = 0, i = 0; y < H; y++) for (let x = 0, row = Math.min(seg.h - 1, Math.floor(y * sy)) * seg.w; x < W; x++, i++) {
        const k = seg.arr[row + colIdx[x]];
        segCls[i] = k;
        // Hair and background: the concealer feathering must not cover them. For hair only exclude "dark" pixels (hair strands): in areas of the hairline classed as hair,
        // light skin between the strands can still be concealed (10-17 diagnosis: the whole light patch next to the upper-right curly hair was excluded as hair)
        if (k === 0 || (k === 1 && L[i] < S0normalL - 8)) hairBg[i] = 1;
        if (k === 3 && (zoneF[i] > 0.5 || zoneC[i] > 0.5) && skinRaw[i] < 0.5) { skinRaw[i] = 1; segAdded++; }
      }
      hairBg = boxBlur(hairBg, W, H, Math.max(2, Math.round(fw * 0.01)));
    }
    const sb = boxBlur(skinRaw, W, H, Math.max(3, Math.round(fw * 0.04)));     // soften the face-region edge so the upper forehead edge is not a hard line
    const skin = new Float32Array(W * H);
    for (let i = 0; i < W * H; i++) {
      skin[i] = Math.max(skinRaw[i] * 0.999, Math.min(1, sb[i] * 1.6));
      if (hairBg) skin[i] *= 1 - Math.min(1, hairBg[i] * 1.5);
    }
    const protRaw = raster((cg) => {
      cg.lineWidth = Math.max(3, fw * 0.02);
      PROTECT.forEach((ids, k) => { const p = poly(ids); cg.fill(p); if (k < 2) cg.stroke(p); });
    });
    const pb = boxBlur(protRaw, W, H, Math.max(2, Math.round(fw * 0.015)));
    const protect = new Float32Array(W * H);
    for (let i = 0; i < W * H; i++) protect[i] = Math.max(protRaw[i], Math.min(1, pb[i] * 1.5));
    // Low frequency: blur inside and outside the light patches (detection core, converted to this resolution) separately
    const coreW = new Float32Array(W * H), csx = det.w / W, csy = det.h / H;
    for (let y = 0, i = 0; y < H; y++) for (let x = 0; x < W; x++, i++) coreW[i] = det.core[Math.min(det.h - 1, Math.floor(y * csy)) * det.w + Math.min(det.w - 1, Math.floor(x * csx))];
    const split = makeSplitBlur(coreW, W, H, r);
    S = { src, normalHex, patchHex, W, H, px, L, A, B, lowL: split(L), lowA: split(A), lowB: split(B),
      skin, protect, fw, det, normal: hexToLab(normalHex), patches: [] };
    S.core = Float32Array.from(det.core);
    S.segCls = segCls; S.hairBg = hairBg;
    const hi = new Float32Array(W * H);                              // highlight weight map (0-1), computed only once
    for (const [id, sx, sy, amp] of HI_SPOTS) {
      const [cx, cy] = P(id), ax = sx * fw * HI_WIDE, ay = sy * fw * HI_WIDE;
      const x0 = Math.max(0, Math.floor(cx - 3 * ax)), x1 = Math.min(W, Math.ceil(cx + 3 * ax));
      const y0 = Math.max(0, Math.floor(cy - 3 * ay)), y1 = Math.min(H, Math.ceil(cy + 3 * ay));
      for (let y = y0; y < y1; y++) for (let x = x0; x < x1; x++) {
        const v = amp * Math.exp(-0.5 * (((x - cx) / ax) ** 2 + ((y - cy) / ay) ** 2)), i = y * W + x;
        if (v > hi[i]) hi[i] = v;
      }
    }
    for (let i = 0; i < W * H; i++) hi[i] *= skin[i] * (1 - protect[i]);
    S.hi = hi;                                                       // old Gaussian light spots (fallback when the 3D highlight fails)
    try { S.spec = meshSpecular(det, W, H, skin, protect); } catch (e) { S.spec = null; }
    const rr = Math.max(1, Math.round(fw * 0.006));
    const sm = { L: boxBlur(L, W, H, rr), A: boxBlur(A, W, H, rr), B: boxBlur(B, W, H, rr) };   // shared by both zones
    const zoneAll = Float32Array.from(skin, (v) => (v > 0.9 ? 1 : 0));   // also run a local reference on the whole face (fine light dots on cheeks and jaw)
    S.zones = { seg: !!seg, segAdded, forehead: zoneDetect(__t('s0712', '額頭'), zoneF, sm), chin: zoneDetect(__t('s0713', '下巴'), zoneC, sm), face: zoneDetect(__t('s0714', '全臉'), zoneAll, sm) };
    return S;
  }

  // Zone detection: the forehead is often oily and brighter than the cheeks, the chin is often in shadow with stubble -- a single lightness threshold for the whole face would miss or misdetect.
  // Each zone computes its own "surrounding skin" lightness as reference (cancelling lighting differences), then judges by chroma/lightness (light patches have low chroma); the result is merged into the light-patch core.
  function zoneDetect(name, zone, sm) {
    const { W, H, skin, protect, fw, det } = S;
    const sL = sm.L, sA = sm.A, sB = sm.B;
    const pLab = hexToLab(S.patchHex), nLab = S.normal;
    const qOf = (l, a, b) => Math.hypot(a, b) / Math.max(1, l);
    const qn = qOf(...nLab), qp = qOf(...pLab), mid = (qn + qp) / 2;
    const idx = [];
    for (let i = 0; i < W * H; i++) if (zone[i] > 0.5 && skin[i] > 0.9 && protect[i] < 0.1) idx.push(i);
    if (idx.length < 200) return { name, n: 0, added: 0 };
    const pig = idx.filter((i) => qOf(sL[i], sA[i], sB[i]) >= mid).map((i) => sL[i]).sort((a, b) => a - b);
    const refL = pig.length >= Math.max(200, idx.length * 0.05) ? pig[pig.length >> 1] : nLab[0];
    const step = Math.max(4, 0.3 * (pLab[0] - nLab[0]));
    // Local reference (10-17 residual-dot diagnosis: forehead center lightness was 57.6 and the temples darker; one reference per zone would miss light patches at the temples and the two hairline corners):
    // each pixel is compared with the "surrounding skin within 6% of the face width nearby"; when there is no surrounding skin nearby, fall back to the whole-zone reference
    const pigM = new Float32Array(W * H);
    for (const i of idx) if (qOf(sL[i], sA[i], sB[i]) >= mid) pigM[i] = 1;
    const rad = Math.max(6, Math.round(fw * 0.06));
    const num = boxBlur(Float32Array.from(sL, (v, i) => v * pigM[i]), W, H, rad), den = boxBlur(pigM, W, H, rad);
    const raw = new Float32Array(W * H);
    let n = 0;
    for (const i of idx) {
      const q = qOf(sL[i], sA[i], sB[i]), ref = den[i] > 0.15 ? num[i] / den[i] : refL;
      if ((q < mid && sL[i] > ref + 1) || sL[i] > ref + step) raw[i] = 1;
    }
    const dn = boxBlur(raw, W, H, Math.max(2, Math.round(fw * 0.008)));   // remove stray dots (small oily highlights)
    const sc = det.w / W;
    let added = 0;
    for (let i = 0; i < W * H; i++) {
      if (dn[i] <= 0.5 || zone[i] <= 0.5) continue;
      n++;
      const k = Math.min(det.h - 1, Math.floor(Math.floor(i / W) * sc)) * det.w + Math.min(det.w - 1, Math.floor((i % W) * sc));
      if (S.core[k] < 0.5) { S.core[k] = 1; added++; }
    }
    return { name, n, added, refL: +refL.toFixed(1), pigPct: +(pig.length / idx.length).toFixed(2), area: idx.length };
  }

  // Outline: core mask of light patches -> (expand outward by `expand` pixels) -> OpenCV external contours -> approxPolyDP simplification -> convert to render coordinates
  async function findPatches(expand = 3) {
    if (!S) throw new Error(__t('s0715', '先 prepare'));
    const cvx = await window.Vendor.opencv();
    const { det, W } = S, w = det.w, h = det.h, sc = W / w;
    const bin = new Uint8Array(w * h);
    for (let i = 0; i < w * h; i++) bin[i] = S.core[i] > 0.5 ? 255 : 0;       // includes the result of the forehead and chin zone detection
    const m = cvx.matFromArray(h, w, cvx.CV_8UC1, bin);
    const k = cvx.getStructuringElement(cvx.MORPH_ELLIPSE, new cvx.Size(3, 3));
    cvx.morphologyEx(m, m, cvx.MORPH_CLOSE, k);                      // fill small holes, join broken lines
    const e = Math.max(0, Math.round(expand));
    if (e > 0) { const ke = cvx.getStructuringElement(cvx.MORPH_ELLIPSE, new cvx.Size(2 * e + 1, 2 * e + 1)); cvx.dilate(m, m, ke); ke.delete(); }
    const cs = new cvx.MatVector(), hi = new cvx.Mat();
    cvx.findContours(m, cs, hi, cvx.RETR_EXTERNAL, cvx.CHAIN_APPROX_NONE);
    const minArea = w * h * 0.0003, out = [];                       // was 0.0006; halved so fine light dots on the cheeks also become patches (10-17)
    for (let i = 0; i < cs.size(); i++) {
      const c = cs.get(i), area = cvx.contourArea(c);
      if (area >= minArea) {
        const ap = new cvx.Mat(); cvx.approxPolyDP(c, ap, 1.2, true);
        const d = ap.data32S, pts = [];
        for (let j = 0; j < d.length; j += 2) pts.push([d[j] * sc, d[j + 1] * sc]);
        if (pts.length >= 3) out.push({ id: out.length + 1, pts, on: true, area: area * sc * sc });
        ap.delete();
      }
      c.delete();
    }
    [m, k, cs, hi].forEach((x) => x.delete());
    out.sort((a, b) => b.area - a.area).forEach((p, i) => { p.id = i + 1; });
    S.patches = out;
    out.forEach((p) => ringColor(p));
    return out;
  }

  // Target color of each patch: median low-frequency color of the ring around it (1-4% of face width outside the boundary) that is on the face, not in other light patches and not in protected zones
  // src (from round 2 on): take surrounding colors from "the current concealed result", excluding blank areas not yet concealed (src.excl); old, already concealed patches may be sampled
  function ringColor(p, src) {
    const { W, H, fw, skin, protect, det } = S;
    src = src || p.src;
    const L = src ? src.L : S.L, A = src ? src.A : S.A, B = src ? src.B : S.B;
    const sc = det.w / W;
    const xs = p.pts.map((q) => q[0]), ys = p.pts.map((q) => q[1]);
    const pad = Math.ceil(fw * 0.05);
    const x0 = Math.max(0, Math.floor(Math.min(...xs)) - pad), x1 = Math.min(W - 1, Math.ceil(Math.max(...xs)) + pad);
    const y0 = Math.max(0, Math.floor(Math.min(...ys)) - pad), y1 = Math.min(H - 1, Math.ceil(Math.max(...ys)) + pad);
    const bw = x1 - x0 + 1, bh = y1 - y0 + 1;
    const c = document.createElement('canvas'); c.width = bw; c.height = bh;
    const g = c.getContext('2d', { willReadFrequently: true });
    const path = new Path2D(pathD(p.pts.map((q) => [q[0] - x0, q[1] - y0])));
    g.strokeStyle = '#fff'; g.lineJoin = 'round';
    g.lineWidth = fw * 0.08; g.stroke(path); g.fillStyle = '#fff'; g.fill(path);
    const outer = g.getImageData(0, 0, bw, bh).data;
    g.clearRect(0, 0, bw, bh); g.lineWidth = fw * 0.02; g.stroke(path); g.fill(path);
    const inner = g.getImageData(0, 0, bw, bh).data;
    const others = new Uint8Array(bw * bh);
    for (const o of S.patches) {
      if (o === p || (o.round || 0) !== (p.round || 0)) continue;
      g.clearRect(0, 0, bw, bh); g.fill(new Path2D(pathD(o.pts.map((q) => [q[0] - x0, q[1] - y0]))));
      const od = g.getImageData(0, 0, bw, bh).data;
      for (let i = 0; i < bw * bh; i++) if (od[i * 4 + 3] > 0) others[i] = 1;
    }
    const Ls = [], As = [], Bs = [];
    for (let yy = 0; yy < bh; yy++) for (let xx = 0; xx < bw; xx++) {
      const j = yy * bw + xx;
      if (outer[j * 4 + 3] < 128 || inner[j * 4 + 3] > 0 || others[j]) continue;
      const i = (yy + y0) * W + xx + x0;
      if (skin[i] < 0.9 || protect[i] > 0.1) continue;
      if (src) { if (src.excl[i]) continue; Ls.push(L[i]); As.push(A[i]); Bs.push(B[i]); continue; }
      const k480 = Math.min(det.h - 1, Math.floor((yy + y0) * sc)) * det.w + Math.min(det.w - 1, Math.floor((xx + x0) * sc));
      if (det.mask[k480] > 0.05 || S.core[k480] > 0.5) continue;                                // skip pixels already judged as light patch (including the border)
      Ls.push(L[i]); As.push(A[i]); Bs.push(B[i]);                        // raw pixels: blurred colors would mix in light-patch color
    }
    const pct = (v, q) => v.sort((a, b) => a - b)[Math.min(v.length - 1, Math.floor(v.length * q))];
    // Lightness takes the 70th percentile of the "lit" surrounding, color takes the median (: "skin looks dark; see if the highlight can improve it": the median mixes in shadow on the face sides,
    // the largest patch, sitting in the T-zone at the face center, was filled to 44.3, darker than the surrounding-skin median 47.2; camouflage.js also used the lit half earlier)
    p.ring = Ls.length >= 30 ? [pct(Ls, RING_L_PCT), pct(As, 0.5), pct(Bs, 0.5)] : S.normal.slice();     // if nothing is found around, use the overall original skin color
    p.ringN = Ls.length;
    return p.ring;
  }

  // Render: coverage 0-1, feather (render pixels), corrector = color-correction layer, keepLight = keep light/shade (conceal only part of the lightness)
  // Fill one patch's target color into tL/tA/tB (patch + expansion to the feather range); only read this patch's bounding box, not the whole canvas (ten times faster with many patches)
  function paintTarget(g, p, feather, tL, tA, tB, wm) {
    const { W, H } = S, pad = feather * 2 + 2;
    const xs = p.pts.map((q) => q[0]), ys = p.pts.map((q) => q[1]);
    const x0 = Math.max(0, Math.floor(Math.min(...xs) - pad)), x1 = Math.min(W, Math.ceil(Math.max(...xs) + pad));
    const y0 = Math.max(0, Math.floor(Math.min(...ys) - pad)), y1 = Math.min(H, Math.ceil(Math.max(...ys) + pad));
    if (x1 <= x0 || y1 <= y0) return;
    g.clearRect(x0, y0, x1 - x0, y1 - y0);
    const path = new Path2D(pathD(p.pts));
    g.lineWidth = feather * 4; g.stroke(path); g.fill(path);
    const d = g.getImageData(x0, y0, x1 - x0, y1 - y0).data, bw = x1 - x0;
    for (let y = y0; y < y1; y++) for (let x = x0; x < x1; x++) {
      if (d[((y - y0) * bw + x - x0) * 4 + 3] > 0) { const i = y * W + x; tL[i] = p.ring[0]; tA[i] = p.ring[1]; tB[i] = p.ring[2]; if (wm) wm[i] = 1; }
    }
    g.clearRect(x0, y0, x1 - x0, y1 - y0);
  }

  // Blur only within the mask (weighted average): colors outside the range are not mixed in
  function normBlur(arr, mask, rad) {
    const { W, H } = S, n = W * H, am = new Float32Array(n);
    for (let i = 0; i < n; i++) am[i] = arr[i] * mask[i];
    const num = boxBlur(am, W, H, rad), den = boxBlur(mask, W, H, rad), out = new Float32Array(n);
    for (let i = 0; i < n; i++) out[i] = den[i] > 1e-3 ? num[i] / den[i] : arr[i];
    return out;
  }
  // Target-color smoothing: each patch has its own target color, so the border of two adjacent patches jumps a step -> hard blocky edges after concealing (review: "patchwork look is sharper").
  // Blur only within the area that has target colors, so borders become gradients; outside the range the overall original skin color is kept.
  function smoothTargets(tL, tA, tB, wm, rad) {
    const sL = normBlur(tL, wm, rad), sA = normBlur(tA, wm, rad), sB = normBlur(tB, wm, rad);
    for (let i = 0; i < tL.length; i++) if (wm[i] > 0) { tL[i] = sL[i]; tA[i] = sA[i]; tB[i] = sB[i]; }
  }
  // "Keep sheen": lightness uses a mix of the "large-range average inside the patch" and the normal low frequency as base color -> the large bright sheen on forehead and cheekbones is not flattened into the base
  // (review: "flattened the fine sheen of the original skin, the matte look of heavy foundation"). Averaging only inside the patch, so dark surrounding skin is not mixed in (no light edge appears).
  const shineLow = (low, cur, mask, s, wideR) => {
    if (s <= 0) return low;
    const wide = normBlur(cur, mask, wideR), out = new Float32Array(low.length);
    for (let i = 0; i < low.length; i++) out[i] = mask[i] > 0.02 ? (1 - s) * low[i] + s * wide[i] : low[i];
    return out;
  };

  // Illumination-continuous target color (Poisson / membrane interpolation, the core of OpenCV seamlessClone; this OpenCV.js build does not compile seamlessClone in, so we solve it ourselves):
  // the patch interior is the unknown region, the surrounding normal skin is the boundary; solve the Laplace equation -> color extends smoothly from all sides (bright side bright, dark side dark, gradient in between),
  // instead of one target color for the whole patch. Hair, brows/eyes and background are not boundaries (natural boundary). Solved with SOR at 1/4 resolution, then bilinearly upscaled.
  // Review posted "Poisson blending ... factor in the lighting continuity and gradient of the surrounding normal skin" -> "should be doable" (10-17).
  function membraneTargets(unk, lift) {
    const { W, H, L, A, B, skin, protect } = S, f = 4, cw = Math.ceil(W / f), ch = Math.ceil(H / f), n = cw * ch;
    const sum = [new Float32Array(n), new Float32Array(n), new Float32Array(n)], kn = new Float32Array(n), un = new Float32Array(n), tot = new Float32Array(n);
    for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
      const i = y * W + x, c = ((y / f) | 0) * cw + ((x / f) | 0);
      tot[c]++;
      if (unk[i] > 0.5) { un[c]++; continue; }
      if (skin[i] > 0.9 && protect[i] < 0.1) { kn[c]++; sum[0][c] += L[i]; sum[1][c] += A[i]; sum[2][c] += B[i]; }
    }
    const state = new Int8Array(n);                                  // 1 unknown, 2 known (boundary), 0 not involved
    let gm = [0, 0, 0], gn = 0;
    for (let c = 0; c < n; c++) {
      if (un[c] > 0) state[c] = 1;
      else if (kn[c] > tot[c] * 0.5) { state[c] = 2; for (let k = 0; k < 3; k++) { sum[k][c] /= kn[c]; gm[k] += sum[k][c]; } gn++; }
    }
    if (!gn) return null;
    gm = gm.map((v) => v / gn);
    const F = sum.map((s) => { const a = Float32Array.from(s); for (let c = 0; c < n; c++) if (state[c] === 1) a[c] = gm[sum.indexOf(s)]; return a; });
    const nb = [-1, 1, -cw, cw], w = 1.9;
    for (let it = 0; it < 500; it++) {
      for (let y = 0; y < ch; y++) for (let x = 0; x < cw; x++) {
        const c = y * cw + x;
        if (state[c] !== 1) continue;
        let m = 0, s0 = 0, s1 = 0, s2 = 0;
        for (const d of nb) {
          if ((d === -1 && x === 0) || (d === 1 && x === cw - 1) || (d === -cw && y === 0) || (d === cw && y === ch - 1)) continue;
          const e = c + d;
          if (!state[e]) continue;
          s0 += F[0][e]; s1 += F[1][e]; s2 += F[2][e]; m++;
        }
        if (!m) continue;
        F[0][c] += w * (s0 / m - F[0][c]); F[1][c] += w * (s1 / m - F[1][c]); F[2][c] += w * (s2 / m - F[2][c]);
      }
    }
    // Upscale back to full resolution (bilinear, weighted over participating cells only; cells of brows/eyes, hair and background have no value and must not be mixed in, otherwise bright spots and orange stripes appear)
    const out = [new Float32Array(W * H), new Float32Array(W * H), new Float32Array(W * H), new Uint8Array(W * H)];
    for (let y = 0; y < H; y++) {
      const gy = Math.min(ch - 1, Math.max(0, (y + 0.5) / f - 0.5)), y0 = gy | 0, y1 = Math.min(ch - 1, y0 + 1), fy = gy - y0;
      for (let x = 0; x < W; x++) {
        const gx = Math.min(cw - 1, Math.max(0, (x + 0.5) / f - 0.5)), x0 = gx | 0, x1 = Math.min(cw - 1, x0 + 1), fx = gx - x0, i = y * W + x;
        const cs = [y0 * cw + x0, y0 * cw + x1, y1 * cw + x0, y1 * cw + x1], ws = [(1 - fx) * (1 - fy), fx * (1 - fy), (1 - fx) * fy, fx * fy];
        let wt = 0, v0 = 0, v1 = 0, v2 = 0;
        for (let q = 0; q < 4; q++) { if (!state[cs[q]]) continue; const wq = ws[q]; wt += wq; v0 += F[0][cs[q]] * wq; v1 += F[1][cs[q]] * wq; v2 += F[2][cs[q]] * wq; }
        if (wt < 1e-4) continue;                                      // no values around: keep the original target color
        out[0][i] = v0 / wt + lift * unk[i]; out[1][i] = v1 / wt; out[2][i] = v2 / wt; out[3][i] = 1;
      }
    }
    return out;
  }

  function compute(opt = {}) {
    if (!S) throw new Error(__t('s0715', '先 prepare'));
    const coverage = Math.min(1, Math.max(0, Number(opt.coverage ?? 0.85)));
    const feather = Math.max(1, Math.round(Number(opt.feather ?? 11)));
    const shine = Math.min(1, Math.max(0, Number(opt.shine ?? 0.5)));
    const wideR = Math.max(6, Math.round(S.fw * 0.06)), tR = Math.max(feather, Math.round(S.fw * 0.03));
    const corrector = opt.corrector !== false, keepLight = opt.keepLight !== false, concealer = opt.concealer !== false;
    const { W, H, px, L, A, B, lowL, lowA, lowB, skin, protect } = S;
    const c = document.createElement('canvas'); c.width = W; c.height = H;
    const g = c.getContext('2d', { willReadFrequently: true });
    // Target color: skin color around each patch (expanded to the feather range); places not covered by any patch use the overall original skin color
    const tL = new Float32Array(W * H).fill(S.normal[0]), tA = new Float32Array(W * H).fill(S.normal[1]), tB = new Float32Array(W * H).fill(S.normal[2]);
    g.fillStyle = '#fff'; g.strokeStyle = '#fff'; g.lineJoin = 'round';
    const base = S.patches.filter((p) => !p.round);
    const wm = new Float32Array(W * H);
    for (const p of base) if (p.on && p.ring) paintTarget(g, p, feather, tL, tA, tB, wm);
    smoothTargets(tL, tA, tB, wm, tR);
    const fill = opt.fill || 'membrane';
    // Mask: fill the enabled patches -> feather (interior stays 1, edge fades outward) -> only on the face, minus protected zones
    g.clearRect(0, 0, W, H);
    for (const p of base) if (p.on) g.fill(new Path2D(pathD(p.pts)));
    const d = g.getImageData(0, 0, W, H).data, raw = new Float32Array(W * H);
    for (let i = 0; i < W * H; i++) raw[i] = d[i * 4 + 3] / 255;
    const bl = boxBlur(raw, W, H, feather);
    if (fill === 'membrane') {
      // Unknown region = patches (including those of all rounds) expanded a bit, so the boundary does not pick up border light color; lift = how much L* to raise toward the lit side
      const all = new Float32Array(W * H);
      g.clearRect(0, 0, W, H); g.lineWidth = Math.max(4, S.fw * 0.015); g.lineJoin = 'round';
      for (const p of S.patches) if (p.on) { const pa = new Path2D(pathD(p.pts)); g.fill(pa); g.stroke(pa); }
      const da = g.getImageData(0, 0, W, H).data;
      for (let i = 0; i < W * H; i++) all[i] = da[i * 4 + 3] / 255;
      const M = membraneTargets(all, Number(opt.lift ?? 3));
      if (M) for (let i = 0; i < W * H; i++) if (M[3][i] && (wm[i] > 0 || all[i] > 0)) { tL[i] = M[0][i]; tA[i] = M[1][i]; tB[i] = M[2][i]; }
    }
    const kL = keepLight ? 0.6 : 1;                                   // keep light/shade: lightness moves only 60% toward the surrounding skin, so the center looks lit rather than unconcealed
    // Color-correction layer: the whiter the spot, the more it neutralizes (like a makeup artist adding a bit of correction only at the whitest spots); the thicker the concealer, the less correction shows through
    const decay = concealer ? 1 - 0.25 * coverage : 1;
    // Multiple thin passes: pass 1 applies the whole patch (with color correction); each later pass only fills the places that "still show a difference",
    // filling as much as the difference (residual / 6 as weight), leaving already blended places untouched -> more natural than one thick coat, and the face does not flatten.
    const passes = Math.min(3, Math.max(1, Math.round(Number(opt.passes ?? 1))));
    const cL = Float32Array.from(L), cA = Float32Array.from(A), cB = Float32Array.from(B);
    const mk = new Float32Array(W * H);
    for (let i = 0; i < W * H; i++) mk[i] = Math.max(raw[i], Math.min(1, bl[i] * 1.3)) * skin[i] * (1 - protect[i]);
    const r = Math.max(3, Math.round(S.fw * 0.03));
    for (let pass = 0; pass < passes; pass++) {
      const lL = shineLow(pass ? boxBlur(cL, W, H, r) : lowL, cL, raw, shine, wideR);
      const lA = pass ? boxBlur(cA, W, H, r) : lowA, lB = pass ? boxBlur(cB, W, H, r) : lowB;
      for (let i = 0; i < W * H; i++) {
        const m = mk[i];
        if (m < 0.004) continue;
        let wgt = 1;
        if (pass) { wgt = Math.min(1, Math.hypot(lL[i] - tL[i], lA[i] - tA[i], lB[i] - tB[i]) / 6); if (wgt < 0.05) continue; }
        const cc = concealer ? coverage * m * wgt : 0;
        const cw = corrector && pass === 0 ? m * decay * Math.min(1, Math.max(0.3, (lowL[i] - tL[i]) / 12)) : 0;   // color correction only in the first pass
        // Replace only the low-frequency base color (correction + concealer); high-frequency texture (pores, fine lines) stays as in the original
        cL[i] += cc * kL * (tL[i] - lL[i]) - cw * 1.5;          // the correction darkens slightly (multiplicative effect)
        cA[i] += cc * (tA[i] - lA[i]) + cw * WARM[0];
        cB[i] += cc * (tB[i] - lB[i]) + cw * WARM[1];
      }
    }
    // From round 2 on:
    // each blank area found by findResidual becomes a new SVG patch (round >= 1) whose target color = the "already concealed" skin around it;
    // each round adds another layer on the previous result, filling as much as the difference (same as multiple thin passes), still keeping texture and excluding protected zones.
    const maxRound = Math.max(0, ...S.patches.map((p) => p.round || 0));
    for (let k = 1; concealer && k <= maxRound; k++) {      // do not stack when only color correction (no concealer) is applied
      const list = S.patches.filter((p) => p.round === k && p.on && p.ring);
      if (!list.length) continue;
      g.clearRect(0, 0, W, H);
      const kT = { L: Float32Array.from(tL), A: Float32Array.from(tA), B: Float32Array.from(tB) };
      const wk = new Float32Array(W * H);
      if (fill !== 'membrane') {                                     // illumination-continuous mode: round patches reuse the same continuous target color
        for (const p of list) paintTarget(g, p, feather, kT.L, kT.A, kT.B, wk);
        smoothTargets(kT.L, kT.A, kT.B, wk, tR);
      }
      g.clearRect(0, 0, W, H);
      for (const p of list) g.fill(new Path2D(pathD(p.pts)));
      const dk = g.getImageData(0, 0, W, H).data, rk = new Float32Array(W * H);
      for (let i = 0; i < W * H; i++) rk[i] = dk[i * 4 + 3] / 255;
      const bk = boxBlur(rk, W, H, feather);
      const lL = shineLow(boxBlur(cL, W, H, r), cL, rk, shine, wideR), lA = boxBlur(cA, W, H, r), lB = boxBlur(cB, W, H, r);
      for (let i = 0; i < W * H; i++) {
        const m = Math.max(rk[i], Math.min(1, bk[i] * 1.3)) * skin[i] * (1 - protect[i]);
        if (m < 0.004) continue;
        const wgt = Math.min(1, Math.hypot(lL[i] - kT.L[i], lA[i] - kT.A[i], lB[i] - kT.B[i]) / 6);
        if (wgt < 0.05) continue;
        const cc = coverage * m * wgt;
        cL[i] += cc * kL * (kT.L[i] - lL[i]); cA[i] += cc * (kT.A[i] - lA[i]); cB[i] += cc * (kT.B[i] - lB[i]);
        if (rk[i] > 0) { tL[i] = kT.L[i]; tA[i] = kT.A[i]; tB[i] = kT.B[i]; mk[i] = Math.max(mk[i], m); raw[i] = Math.max(raw[i], rk[i]); }
      }
    }
    // Blank areas: skin whose low-frequency color after concealing is still brighter than the target with color difference > 3; only look at concealed patches and the 4% face-width ring around them (do not touch the face's natural highlights)
    const fL = boxBlur(cL, W, H, r), fA = boxBlur(cA, W, H, r), fB = boxBlur(cB, W, H, r);
    const near = boxBlur(raw, W, H, Math.max(3, Math.round(S.fw * 0.04)));
    const rL = shineLow(fL, cL, raw, shine, wideR);                  // blank areas are judged with the same base color: preserved sheen does not count as blank
    const resid = new Uint8Array(W * H);
    let rN = 0, sN = 0;
    for (let i = 0; i < W * H; i++) {
      if (skin[i] < 0.9 || protect[i] > 0.05) continue;
      sN++;
      if (near[i] < 0.01) continue;
      const dE = Math.hypot(rL[i] - tL[i], fA[i] - tA[i], fB[i] - tB[i]);
      if (dE > 3 && rL[i] > tL[i] + 1.5) { resid[i] = 1; rN++; }
    }
    // Highlight layer (opt.highlight 0-1): raise the high points at the face center as a whole and slightly lower saturation (off-white), add only low frequency, texture unchanged; scoring is computed before this, so it does not affect the color difference
    const hiAmt = Math.min(1, Math.max(0, Number(opt.highlight ?? 0)));
    if (hiAmt > 0 && S.spec && opt.hiMode !== 'spots') {
      // 3D highlight: core off-white (slightly lower saturation), the outer ring with a trace of warm color (translucency of light entering the skin)
      const { core, wide } = S.spec;
      for (let i = 0; i < W * H; i++) {
        const c = core[i] * hiAmt, wd = wide[i] * hiAmt;
        if (wd < 0.004) continue;
        cL[i] += HI_L * (0.7 * c + 0.3 * wd);
        cA[i] = cA[i] * (1 - 0.05 * c) + 1.0 * Math.max(0, wd - c);
        cB[i] = cB[i] * (1 - 0.08 * c) + 0.8 * Math.max(0, wd - c);
      }
    } else if (hiAmt > 0) {
      for (let i = 0; i < W * H; i++) {
        const w = S.hi[i] * hiAmt;
        if (w < 0.004) continue;
        cL[i] += w * HI_L; cA[i] *= 1 - 0.05 * w; cB[i] *= 1 - 0.08 * w;   // slightly lower saturation, toward off-white, not pure white
      }
    }
    return { W, H, cL, cA, cB, tL, tA, tB, mk, fL, fA, fB, resid, residPct: sN ? rN / sN : 0, passes, rounds: maxRound };
  }

  function render(opt = {}) {
    const st = compute(opt), { W, H, cL, cA, cB, tL, tA, tB, mk, fL, fA, fB } = st, { px, L, A, B } = S;
    const c = document.createElement('canvas'); c.width = W; c.height = H;
    const g = c.getContext('2d', { willReadFrequently: true });
    const out = new ImageData(new Uint8ClampedArray(px), W, H), o = out.data;
    let changed = 0;
    for (let i = 0; i < W * H; i++) {
      if (cL[i] === L[i] && cA[i] === A[i] && cB[i] === B[i]) continue;
      const rgb = labToSrgb(cL[i], cA[i], cB[i]);
      o[i * 4] = rgb[0]; o[i * 4 + 1] = rgb[1]; o[i * 4 + 2] = rgb[2];
      changed++;
    }
    // Result score: mean color difference (Lab deltaE76) between the low-frequency light-patch core and its own surrounding skin color
    const sc = S.det.w / W;
    let sum = 0, cnt = 0;
    for (let y = 0; y < H; y += 2) for (let x = 0; x < W; x += 2) {
      const i = y * W + x;
      if (mk[i] < 0.5 || S.core[Math.floor(y * sc) * S.det.w + Math.floor(x * sc)] < 0.5) continue;
      sum += Math.hypot(fL[i] - tL[i], fA[i] - tA[i], fB[i] - tB[i]); cnt++;
    }
    g.putImageData(out, 0, 0);
    return { image: c.toDataURL('image/png'), changedPct: changed / (W * H), deltaE: cnt ? sum / cnt : 0,
      residPct: st.residPct, passes: st.passes, rounds: st.rounds };
  }

  // Find blank areas: conceal once with the current settings -> places still showing a difference get contours via OpenCV -> become the next round's SVG patches (toggleable, draggable points)
  async function findResidual(opt = {}) {
    if (!S) throw new Error(__t('s0715', '先 prepare'));
    const cvx = await window.Vendor.opencv();
    const st = compute(opt), { W, H } = st;
    const round = st.rounds + 1;
    if (round > 3) return { added: 0, round: st.rounds, residPct: st.residPct, limit: true };
    const bin = new Uint8Array(W * H);
    for (let i = 0; i < W * H; i++) bin[i] = st.resid[i] ? 255 : 0;
    const m = cvx.matFromArray(H, W, cvx.CV_8UC1, bin);
    const k = cvx.getStructuringElement(cvx.MORPH_ELLIPSE, new cvx.Size(5, 5));
    cvx.morphologyEx(m, m, cvx.MORPH_OPEN, k);                       // remove stray dots
    cvx.morphologyEx(m, m, cvx.MORPH_CLOSE, k);
    const ke = cvx.getStructuringElement(cvx.MORPH_ELLIPSE, new cvx.Size(5, 5)); cvx.dilate(m, m, ke);
    const excl = new Uint8Array(m.data);                             // blank areas (after expansion): not counted when sampling surrounding colors
    const cs = new cvx.MatVector(), hi = new cvx.Mat();
    cvx.findContours(m, cs, hi, cvx.RETR_EXTERNAL, cvx.CHAIN_APPROX_NONE);
    const minArea = W * H * 0.0003, added = [];
    const src = { L: st.cL, A: st.cA, B: st.cB, excl };
    for (let i = 0; i < cs.size(); i++) {
      const c = cs.get(i), area = cvx.contourArea(c);
      if (area >= minArea) {
        const ap = new cvx.Mat(); cvx.approxPolyDP(c, ap, 1.5, true);
        const d = ap.data32S, pts = [];
        for (let j = 0; j < d.length; j += 2) pts.push([d[j], d[j + 1]]);
        if (pts.length >= 3) added.push({ pts, on: true, area, round, src });
        ap.delete();
      }
      c.delete();
    }
    [m, k, ke, cs, hi].forEach((x) => x.delete());
    let id = Math.max(0, ...S.patches.map((p) => p.id));
    added.sort((a, b) => b.area - a.area).forEach((p) => { p.id = ++id; S.patches.push(p); });
    added.forEach((p) => ringColor(p, src));
    return { added: added.length, round: added.length ? round : st.rounds, residPct: st.residPct };
  }
  function clearRounds() { if (S) S.patches = S.patches.filter((p) => !p.round); }

  function setPoint(id, k, x, y) { const p = S && S.patches.find((q) => q.id === id); if (p && p.pts[k]) { p.pts[k] = [x, y]; } }
  function toggle(id, on) { const p = S && S.patches.find((q) => q.id === id); if (p) p.on = on ?? !p.on; return p && p.on; }
  function refreshRing(id) { const p = S && S.patches.find((q) => q.id === id); return p ? ringColor(p) : null; }

  window.CamoEditor = { prepare, findPatches, findResidual, clearRounds, render, pathD, setPoint, toggle, refreshRing,
    membrane: (unk, lift) => membraneTargets(unk, lift || 0), boxBlur: (a, r) => boxBlur(a, S.W, S.H, Math.max(1, Math.round(r))),
    splitBlur: (inside, r) => makeSplitBlur(inside, S.W, S.H, Math.max(1, Math.round(r))),
    hexToLab, labToSrgb,
    patches: () => (S ? S.patches : []), size: () => (S ? { W: S.W, H: S.H } : null), _debug: () => S };
})();
