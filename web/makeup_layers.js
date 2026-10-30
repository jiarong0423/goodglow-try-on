'use strict';
// Makeup layer mode (design note: follow the logic of makeup tutorials rather than algorithms; canvas is essentially layered rendering, just like makeup;
// the SVG is really the face's makeup region; OpenCV is for visual localization -> approved 8-layer design).
// Division of labor: OpenCV / MediaPipe localization (light-patch outlines, face mesh and segmentation from camo_editor.js) -> makeup region (SVG path) of each product
// -> Canvas paints layer by layer in makeup order (product color = the actual recommended shade). No facial features are altered and no pixels are generated.
// Layers and numbers: makeup tutorials (lighter-patch concealing: color-correct only on the light patches, conceal from the center outward, add a second layer only where it is still visible, pat rather than rub, set with pressed powder;
// highlight: upper cheekbone edge, nose bridge stopping before the nose tip; contour: lower cheekbone edge, jaw, sides of the nose) plus the opacities and mesh points suggested by an external advisor (10-17).
// iPhone Safari does not support ctx.filter: feathering is always done by computing the opacity with box blurs, then compositing the color with globalCompositeOperation.
(function () {
  const LAYERS = [
    { id: 'corrector', name: __t('s0735', '校色'), product: __t('s0735', '校色') },
    { id: 'conceal', name: __t('s0736', '遮瑕'), product: __t('s0736', '遮瑕') },
    { id: 'conceal2', name: __t('s0737', '遮瑕第二層'), product: __t('s0736', '遮瑕') },
    { id: 'edge', name: __t('s0738', '修邊'), product: __t('s0739', '交界') },
    { id: 'foundation', name: __t('s0740', '粉底'), product: __t('s0740', '粉底') },
    { id: 'powder', name: __t('s0741', '蜜粉'), product: __t('s0741', '蜜粉') },
    // Touch-up: after the base makeup, look again;
    // wherever light skin is still visible (brighter and less saturated than its surroundings), dab the same concealer only there and pat the edges out; small amounts, several times, re-detecting each time.
    // Basis: makeup artists' pinpoint concealing (dab only on the blemish, pat, push the edge until invisible); makeup tutorial 'if one layer cannot cover it, add a second layer in small amounts, several times';
    // lighter-patch concealing guide (the border has the most obvious contrast, so the edge needs special handling)
    { id: 'touch1', name: __t('s0742', '補妝1'), product: __t('s0736', '遮瑕') },
    { id: 'touch2', name: __t('s0743', '補妝2'), product: __t('s0736', '遮瑕') },
    { id: 'touch3', name: __t('s0744', '補妝3'), product: __t('s0736', '遮瑕') },
    { id: 'contour', name: __t('s0160', '修容'), product: __t('s0160', '修容') },
    { id: 'highlight', name: __t('s0092', '打亮'), product: __t('s0092', '打亮') },
    { id: 'contour_fine', name: __t('s0745', '細修容'), product: __t('s0160', '修容') },          // Small-scale light and shade: sides of the nose, eye sockets, under the lower lip (fine details lit by the 3D mesh)
    { id: 'highlight_fine', name: __t('s0746', '細打亮'), product: __t('s0092', '打亮') },
  ];
  // Opacity of each layer (then multiplied by the 'intensity' slider, default 60%). 10-17 approved A+D (design note): fitted to the target makeup with tools/fit_layers_to_target.py
  // (concealer reduced, contour increased, fine contour added; color correction and edge blending fitted to 0), converted to a slider at 60%, then tuned with real browser measurements using tools/measure_layer_render.py:
  // median low-frequency color difference of the lighter-patch demo photo against the target image 6.23 (old values) -> 5.56 (whole face), 7.99 -> 6.76 (light patches). Fine highlight measured worse, so it is set to 0 for now.
  // Old values (before 10-17): color correction 0.5, concealer 0.72, second layer 0.35, edge blending 0.38, foundation 0.28, powder 0.15, contour 0.2, highlight 0.2
  const FINE_GAIN = 0.1;                                             // How much lighting variation counts as 'full' (diffuse 0-1; demo photo p10 / p90 about +-0.08)
  const OPACITY = { corrector: 0, conceal: 0.33, conceal2: 0.67, edge: 0, foundation: 0.5, powder: 0.15,
    contour: 0.75, highlight: 0.5, contour_fine: 0.3, highlight_fine: 0,
    // Touch-up browser measurement 10-17 (lighter-patch demo photo, fitted shades): light-patch 'visibility' (color difference from the surrounding normal skin) median 7.81 -> 4.74, p90 15.96 -> 11.13;
    // patch L* 52.7 -> 49.6 (target 50.8). 0.3 / 0.5 are paler (5.69 / 5.27)
    touch1: 0.8, touch2: 0.8, touch3: 0.8 };
  // Contour and highlight regions: MediaPipe mesh points (suggested by an external advisor, 10-17; take the convex hull of these points, use a line for the nose bridge)
  const CONTOUR = [[234, 93, 132, 58, 216, 207, 187, 50], [454, 323, 361, 288, 436, 427, 411, 280],           // Lower cheekbone edge (above the hollow of the cheek)
    [132, 58, 172, 136, 150, 149, 176, 148, 152], [361, 288, 397, 365, 379, 378, 400, 377, 152],               // Inner side of the jawline
    [193, 122, 196, 3, 51], [417, 351, 419, 248, 281]];                                                        // Sides of the nose
  const HL_CHEEK = [[116, 123, 147, 118, 101, 50], [345, 352, 376, 347, 330, 280]];
  const HL_NOSE_LINE = [168, 6, 197, 195, 5];                       // Nose bridge, stopping before the nose tip
  const HL_NOSE_TIP = [4, 1];
  const HL_FOREHEAD = [10, 151, 9, 107, 336];
  const T_ZONE = [10, 109, 67, 107, 9, 336, 297, 338, 151, 168, 6, 197, 195, 5, 4];
  const SOFT_SPOTS = [33, 133, 362, 263, 64, 294, 61, 291];         // Eye corners, nostril wings, mouth corners: they move and easily cake powder -> layer halved

  const hexRgb = (h) => { const n = parseInt(h.slice(1), 16); return [n >> 16, (n >> 8) & 255, n & 255]; };
  const lighten = (h, k) => { const c = hexRgb(h).map((v) => Math.round(v + (255 - v) * k)); return `#${c.map((v) => v.toString(16).padStart(2, '0')).join('')}`; };
  function boxBlur(src, w, h, r) {
    r = Math.max(1, Math.round(r));
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
  function hull(pts) {                                               // Convex hull (Andrew monotone chain)
    const p = pts.slice().sort((a, b) => a[0] - b[0] || a[1] - b[1]);
    const cross = (o, a, b) => (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]);
    const lo = [], up = [];
    for (const q of p) { while (lo.length >= 2 && cross(lo[lo.length - 2], lo[lo.length - 1], q) <= 0) lo.pop(); lo.push(q); }
    for (const q of p.slice().reverse()) { while (up.length >= 2 && cross(up[up.length - 2], up[up.length - 1], q) <= 0) up.pop(); up.push(q); }
    return lo.slice(0, -1).concat(up.slice(0, -1));
  }
  // Sponge-pat grain: a 128x128 repeating pattern, 70% fine pores + 30% larger irregular air pockets, opacity 0.65-1 (fixed seed, same every time)
  let NOISE = null;
  const DBG = {};                                                    // For measurement: the touch-up region of the most recent render (compare with tools/measure_layer_render.py)
  function noise() {
    if (NOISE) return NOISE;
    const n = 128, a = new Float32Array(n * n).fill(1);
    let seed = 20261017;
    const rnd = () => { seed = (seed * 1664525 + 1013904223) >>> 0; return seed / 4294967296; };
    for (let i = 0; i < n * n * 0.18; i++) a[(rnd() * n * n) | 0] = 0.65 + rnd() * 0.25;
    for (let i = 0; i < 90; i++) {
      const cx = rnd() * n, cy = rnd() * n, rr = 1 + rnd() * 1.5;
      for (let y = Math.floor(cy - rr); y <= cy + rr; y++) for (let x = Math.floor(cx - rr); x <= cx + rr; x++) {
        if ((x - cx) ** 2 + (y - cy) ** 2 <= rr * rr) a[((y + n) % n) * n + ((x + n) % n)] = 0.65 + rnd() * 0.15;
      }
    }
    NOISE = { a, n };
    return NOISE;
  }

  const MASK_FEATHER = 0.018;      // Feathering of the server mask (as a fraction of face width): 10-17 soft-edge comparison, E4 = 0.6% x 3, which leaves the fewest forehead map lines and chin outlines
  function regionOf(S, Z) {          // Makeup region: the face's paintable region plus the neck (body skin from MediaPipe segmentation)
    const n = S.W * S.H, region = new Float32Array(n);
    for (let i = 0; i < n; i++) {
      const body = S.segCls && S.segCls[i] === 2 && S.protect[i] < 0.1 && !(S.hairBg && S.hairBg[i] > 0.3) ? 1 : 0;
      region[i] = Math.max(Z.ok[i], body * (S.skin[i] > 0 || body ? 1 : 0));
    }
    return region;
  }

  // Region (recomputed on every render from the current patches; result cached in Z)
  function zones(S, opt) {
    const { W, H, fw, skin, protect, det } = S;
    const P = (i) => [det.face[i].x * W, det.face[i].y * H];
    const c = document.createElement('canvas'); c.width = W; c.height = H;
    const g = c.getContext('2d', { willReadFrequently: true });
    const raster = (draw) => {
      g.clearRect(0, 0, W, H); g.fillStyle = '#fff'; g.strokeStyle = '#fff'; g.lineJoin = 'round'; g.lineCap = 'round';
      draw(g);
      const d = g.getImageData(0, 0, W, H).data, a = new Float32Array(W * H);
      for (let i = 0; i < W * H; i++) a[i] = d[i * 4 + 3] / 255;
      return a;
    };
    const pathD = window.CamoEditor.pathD;
    const base = S.patches.filter((p) => p.on && !p.round), all = S.patches.filter((p) => p.on);
    const fill = raster((cg) => base.forEach((p) => cg.fill(new Path2D(pathD(p.pts)))));
    const fillAll = raster((cg) => all.forEach((p) => cg.fill(new Path2D(pathD(p.pts)))));
    const ring = raster((cg) => { cg.lineWidth = fw * 0.03; base.forEach((p) => cg.stroke(new Path2D(pathD(p.pts)))); });
    const hullFill = (lists) => raster((cg) => lists.forEach((ids) => { const h = hull(ids.map(P)); cg.beginPath(); h.forEach((q, j) => (j ? cg.lineTo(...q) : cg.moveTo(...q))); cg.closePath(); cg.fill(); }));
    const contour = hullFill(CONTOUR);
    const hlCheek = hullFill(HL_CHEEK), hlFore = hullFill([HL_FOREHEAD]);
    const hlNose = raster((cg) => {
      cg.lineWidth = fw * 0.035; cg.beginPath(); HL_NOSE_LINE.map(P).forEach((q, j) => (j ? cg.lineTo(...q) : cg.moveTo(...q))); cg.stroke();
      const t = HL_NOSE_TIP.map(P); cg.beginPath(); cg.arc((t[0][0] + t[1][0]) / 2, (t[0][1] + t[1][1]) / 2, fw * 0.022, 0, Math.PI * 2); cg.fill();
    });
    const tzone = hullFill([T_ZONE]);
    const soft = raster((cg) => SOFT_SPOTS.map(P).forEach((q) => { cg.beginPath(); cg.arc(q[0], q[1], fw * 0.02, 0, Math.PI * 2); cg.fill(); }));
    // Paintable: skin, minus the eyebrows, eyes and lips (protect); eye corners, nostril wings and mouth corners halved
    const softB = boxBlur(soft, W, H, fw * 0.01), ok = new Float32Array(W * H);
    for (let i = 0; i < W * H; i++) ok[i] = skin[i] * (1 - protect[i]) * (1 - 0.5 * Math.min(1, softB[i] * 1.5));
    return { fill, fillAll, ring, contour, hlCheek, hlFore, hlNose, tzone, ok, raster };
  }

  const linLab = (c) => {
    const x = (0.4124 * c[0] + 0.3576 * c[1] + 0.1805 * c[2]) / 0.95047, y = 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2], z = (0.0193 * c[0] + 0.1192 * c[1] + 0.9505 * c[2]) / 1.08883;
    const f = (t) => (t > 216 / 24389 ? Math.cbrt(t) : (24389 / 27 * t + 16) / 116);
    return [116 * f(y) - 16, 500 * (f(x) - f(y)), 200 * (f(y) - f(z))];
  };
  const toLin = (v) => { v /= 255; return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; };
  const toSrgb = (v) => 255 * Math.max(0, Math.min(1, v <= 0.0031308 ? 12.92 * v : 1.055 * v ** (1 / 2.4) - 0.055));
  // Composite one layer (color + opacity map) onto the canvas with the given blend mode.
  // lin: the product's color under this photo's lighting (linear RGB); sh: lighting at each position (1 = average) -> the layer brightens and darkens along with the face's original light and shade
  function paint(ctx, W, H, lin, alpha, mode, sh) {
    const lc = document.createElement('canvas'); lc.width = W; lc.height = H;
    const lg = lc.getContext('2d'); const id = lg.createImageData(W, H), d = id.data;
    for (let i = 0; i < W * H; i++) {
      const a = alpha[i]; if (a < 0.003) continue;
      const f = sh ? sh[i] : 1;
      d[i * 4] = toSrgb(lin[0] * f); d[i * 4 + 1] = toSrgb(lin[1] * f); d[i * 4 + 2] = toSrgb(lin[2] * f); d[i * 4 + 3] = Math.round(Math.min(1, a) * 255);
    }
    lg.putImageData(id, 0, 0);
    ctx.save(); ctx.globalCompositeOperation = mode; ctx.globalAlpha = 1; ctx.drawImage(lc, 0, 0); ctx.restore();
  }

  // opt: { colors:{corrector,concealer,edge,foundation,contour,highlight,normal}, upTo: layer id (paint up to this layer), off:{layer id:true}, strength:0-1.5 }
  // Each layer = a semi-transparent thin film (10-17 measurement: painting the product color directly over the face turns it into a flat mask and the light, shade and pores disappear):
  // real foundation and concealer are thin films, so the skin's original light/dark and texture show through -> each layer only pushes the 'base color within its region' toward the product color (how far = that layer's opacity),
  // texture stays as is, slightly fainter the thicker it gets; the product color is first converted to how it looks 'under this photo's lighting', then brightens and darkens along with the face's original light and shade
  async function render(opt) {
    const S = window.CamoEditor._debug(), CE = window.CamoEditor;
    if (!S) throw new Error(__t('s0747', '先找淺色區塊'));
    const { W, H, fw, spec } = S, col = opt.colors, k = opt.strength ?? 1, n = W * H;
    const Z = zones(S, opt), NZ = noise();
    const sponge = new Float32Array(n);
    for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) sponge[y * W + x] = NZ.a[(y % NZ.n) * NZ.n + (x % NZ.n)];
    // Shade try-on under this lighting: the recommendation matched shades against the 'corrected skin tone' (col.normal) -> actual skin tone in the photo minus corrected skin tone = the lighting conversion for this photo.
    // The actual skin tone in the photo = median low-frequency color of normal skin outside the light patches (10-17 fix: it originally wrongly used 'corrected skin tone - foundation' as the conversion,
    // which amounts to assuming the foundation equals the person's skin tone, so every product was converted too red and yellow -> the whole face went orange, like a mannequin)
    const sc = S.det.w / W, pl = [], pa = [], pb = [];
    for (let i = 0; i < n; i += 3) {
      if (S.skin[i] < 0.95 || S.protect[i] > 0.05) continue;
      const kk = Math.min(S.det.h - 1, Math.floor(Math.floor(i / W) * sc)) * S.det.w + Math.min(S.det.w - 1, Math.floor((i % W) * sc));
      if (S.core[kk] > 0.5) continue;
      pl.push(S.lowL[i]); pa.push(S.lowA[i]); pb.push(S.lowB[i]);
    }
    const med = (v) => (v.sort((x, y) => x - y), v[v.length >> 1]);
    const nLab = pl.length > 100 ? [med(pl), med(pa), med(pb)] : S.normal.slice();     // Normal skin tone in the photo
    const corr = CE.hexToLab(col.normal), off = nLab.map((v, j) => v - corr[j]);
    const cal = (hex) => CE.hexToLab(hex).map((v, j) => v + off[j]);
    // Concealer / edge blending: makeup tutorial 'if one shade does not match, mix two' -> from the candidate shades pick the one or two closest to the photo's skin tone and mix (linear RGB blend, in steps of 10%)
    const mixInfo = {};
    const mix = (list, key) => {
      const L0 = (Array.isArray(list) ? list : [list]).filter(Boolean);
      if (!L0.length) return null;
      const lin = (h) => hexRgb(h).map(toLin), labOf = (c) => linLab(c).map((v, j) => v + off[j]);
      let best = null;
      for (let a = 0; a < L0.length; a++) for (let b = a; b < L0.length; b++) for (let t = 0; t <= 10; t++) {
        if (a === b && t) continue;
        const ca = lin(L0[a]), cb = lin(L0[b]), c = ca.map((v, q) => v * (1 - t / 10) + cb[q] * t / 10), l = labOf(c);
        const d = Math.hypot(l[0] - nLab[0], l[1] - nLab[1], l[2] - nLab[2]);
        if (!best || d < best.d - 0.05) best = { d, lab: l, a, b, t };
      }
      mixInfo[key] = { de: best.d, a: best.a, b: best.b, t: best.t };
      return best.lab;
    };
    // Lighting: the light and shade of the surrounding normal skin extend continuously (thin-film interpolation, Poisson style) onto the light patches; outside the patches it is just the original low-frequency light and shade
    const unk = new Float32Array(n), grow = boxBlur(Z.fillAll, W, H, fw * 0.015);
    for (let i = 0; i < n; i++) unk[i] = grow[i] > 0.02 ? 1 : 0;
    const M = CE.membrane(unk, opt.lift ?? 3), light = new Float32Array(n);   // lift: brighten toward the lit side (so that it does not look dull after concealing; 10-17 brightened 2 -> 3)
    for (let i = 0; i < n; i++) light[i] = (M && M[3][i] && unk[i]) ? M[0][i] : S.lowL[i];
    // Add the face's light and shade back inside the light patches: the lighting extended from the surroundings is smooth,
    // so when the nose lies entirely inside a patch, the bright nose bridge and the shadowed nose sides get flattened -> use the 3D face mesh's lighting (determined by face shape, unaffected by the lighter-patch's own brightness),
    // subtract the broad average to keep only the 'shape' variation, and add it back converted to L*
    if (spec && spec.diffuse) {
      const dw = boxBlur(spec.diffuse, W, H, fw * 0.08), gainL = opt.shape ?? 32;
      // Fade out gradually at the mesh edge (above the forehead, sides of the face), otherwise a horizontal line appears at the top edge of the mesh (10-17 measurement)
      const cs = boxBlur(spec.cover, W, H, fw * 0.05);
      for (let i = 0; i < n; i++) {
        if (!unk[i] || !spec.cover[i]) continue;
        const edge = Math.max(0, Math.min(1, (cs[i] - 0.5) * 2.5));
        light[i] += edge * Math.max(-12, Math.min(12, gainL * (spec.diffuse[i] - dw[i])));
      }
    }
    const cL = Float32Array.from(S.L), cA = Float32Array.from(S.A), cB = Float32Array.from(S.B);
    const r = Math.max(3, Math.round(fw * 0.02));
    const OP = Object.assign({}, OPACITY, opt.opacity || {});
    // Separate the film's 'base color / texture': blur inside and outside the light patches separately; the border counts as base color (gets covered), not as texture (10-17 A+D)
    // When there is a server-computed light-patch mask (opt.patchMask, adopted 10-17 as E4), do not split at the border: the split would leave a map line along the old outline on the forehead (isolation soft-edge comparison E0-E4)
    const lowOf = opt.patchMask ? (a) => boxBlur(a, W, H, r) : CE.splitBlur ? CE.splitBlur(Z.fillAll, r) : (a) => boxBlur(a, W, H, r);
    // One thin film: alpha = opacity of each pixel; prod = converted product Lab; mode: 'film' covers the color, 'deepen' contours (only toward darker), 'glow' highlights (only toward lighter)
    const bright = opt.brighten ?? 3;                                  // Overall brightening (L*): like choosing a foundation half a shade lighter / a brightening primer
    // abRef: per-pixel color target (the touch-up uses the a/b of the 'skin already made up around it', not the yellow of the concealer itself; 10-17 plan B)
    function film(alpha, prod, mode, up = 0, lt = light, abRef = null) {
      const lL = lowOf(cL), lA = lowOf(cA), lB = lowOf(cB);
      for (let i = 0; i < n; i++) {
        const a = Math.min(1, alpha[i]);
        if (a < 0.003) continue;
        const tL = prod[0] + up + (lt[i] - nLab[0]);                    // The product color brightens and darkens with the lighting at this position
        let dL = tL - lL[i];
        if (mode === 'deepen') dL = Math.min(0, dL);
        if (mode === 'glow') dL = Math.max(0, dL);
        const keep = 1 - 0.1 * a;                                      // The thicker it is applied, the fainter the pores and fine lines get (10-17 'looks like a mannequin, too heavy-handed': 0.3 -> 0.1)
        const dl = cL[i] - lL[i], da = cA[i] - lA[i], db = cB[i] - lB[i];
        cL[i] = lL[i] + a * dL + dl * keep;
        const pA = abRef ? abRef[0][i] : prod[1], pB = abRef ? abRef[1][i] : prod[2];
        cA[i] = lA[i] + a * (pA - lA[i]) * (mode === 'glow' ? 0.4 : 1) + da * keep;
        cB[i] = lB[i] + a * (pB - lB[i]) * (mode === 'glow' ? 0.4 : 1) + db * keep;
      }
    }
    // Touch-up: find the light skin that is still 'noticeable' - where the current base color is brighter and less saturated than the surrounding skin (looks color-deficient); region = face + neck.
    // Surrounding skin = broad average after excluding the candidates (two passes); the brightness of what is added follows the 'skin already made up' around it, not the light patch itself.
    const region = regionOf(S, Z);
    DBG.region = region; DBG.touch = [];
    function touchUp(op) {
      if (!op) return null;
      // Scale of what is noticeable: 0.8% of face width (small spots are also caught; 2% would miss the fine light spots on the cheeks; 10-17 offline comparison)
      const rs = Math.max(1, Math.round(fw * 0.008)), R = fw * 0.08;
      const lL = boxBlur(cL, W, H, rs), lA = boxBlur(cA, W, H, rs), lB = boxBlur(cB, W, H, rs);
      const nb = (v, m) => { const a = new Float32Array(n); for (let i = 0; i < n; i++) a[i] = v[i] * m[i]; const s = boxBlur(a, W, H, R), d = boxBlur(m, W, H, R);
        for (let i = 0; i < n; i++) s[i] = d[i] > 1e-3 ? s[i] / d[i] : v[i]; return s; };
      let known = new Float32Array(n), refL, refC;
      for (let i = 0; i < n; i++) known[i] = region[i] > 0.5 ? 1 : 0;
      const C = new Float32Array(n);
      for (let i = 0; i < n; i++) C[i] = Math.hypot(lA[i], lB[i]);
      for (let pass = 0; pass < 2; pass++) {
        refL = nb(lL, known); refC = nb(C, known);
        if (pass === 0) for (let i = 0; i < n; i++) if ((lL[i] - refL[i]) / 4 + (refC[i] - C[i]) / 3 > 1) known[i] = 0;
      }
      const need = new Float32Array(n);
      let cnt = 0;
      for (let i = 0; i < n; i++) {
        if (region[i] < 0.3) continue;
        const dl = lL[i] - refL[i], score = dl / 4 + (refC[i] - C[i]) / 3;
        if (dl > 1.5 && score > 1) { need[i] = Math.min(1, (score - 1) / 1.5); cnt++; }
      }
      DBG.touch.push({ need, refL });
      if (!cnt) return null;
      // Dab in the center and pat the edge outward (feathering 1% of face width); sponge grain
      const f = boxBlur(need, W, H, fw * 0.01), alpha = new Float32Array(n);
      for (let i = 0; i < n; i++) alpha[i] = op * k * Math.min(1, f[i] * 1.3) * region[i] * (0.85 + 0.15 * sponge[i]);
      return { alpha, ref: refL, area: cnt / n, refA: opt.patchMask ? nb(lA, known) : null, refB: opt.patchMask ? nb(lB, known) : null };
    }
    const concLab = mix(col.concealerList || col.concealer, 'concealer'), edgeLab = mix(col.edgeList || col.edge, 'edge'), foundLab = mix(col.foundationList || col.foundation, 'foundation');
    const on = (id) => !(opt.off && opt.off[id]);
    const order = LAYERS.map((l) => l.id), last = opt.upTo ? order.indexOf(opt.upTo) : order.length - 1;
    const done = [];
    const A = (f) => { const a = new Float32Array(n); for (let i = 0; i < n; i++) a[i] = f(i) * Z.ok[i] * k; return a; };
    for (let li = 0; li <= last; li++) {
      const id = order[li];
      if (!on(id)) continue;
      if (id === 'corrector' && col.corrector) {
        // Dab only on the light patches, shrunk inward by 0.5% of face width (so orange does not stain normal skin): suppress the dead white and add warmth
        const er = boxBlur(Z.fill, W, H, fw * 0.005), m = new Float32Array(n);
        for (let i = 0; i < n; i++) m[i] = er[i] > 0.85 ? 1 : 0;
        const f = boxBlur(m, W, H, fw * 0.015);
        film(A((i) => f[i] * OP.corrector), cal(col.corrector), 'film');
      } else if (id === 'conceal' && col.concealer) {
        // Push from the center outward: the center is fullest (about 0.72), the border about 0.3, fading out within 2% of face width beyond the border; sponge-pat grain
        const d = opt.patchMask ? boxBlur(opt.patchMask, W, H, fw * MASK_FEATHER) : boxBlur(Z.fill, W, H, fw * 0.02);
        film(A((i) => OP.conceal * Math.min(1, d[i] * (opt.patchMask ? 1.3 : 1)) ** 1.2 * sponge[i]), concLab, 'film', bright);
      } else if (id === 'conceal2' && col.concealer) {
        // Second layer: look again and fill only where it is 'still noticeable' (base color is 3 or more brighter than the normal skin tone lit at that spot, and near a light patch)
        const lL = lowOf(cL), near = boxBlur(Z.fillAll, W, H, fw * 0.03), m = new Float32Array(n);
        let cnt = 0;
        for (let i = 0; i < n; i++) if (near[i] > 0.05 && lL[i] > light[i] + 3) { m[i] = Math.min(1, (lL[i] - light[i] - 3) / 6); cnt++; }
        const f = boxBlur(m, W, H, fw * 0.012);
        film(A((i) => OP.conceal2 * Math.min(1, f[i] * 1.3) * sponge[i]), concLab, 'film', bright);
        done.push({ id, area: cnt / n });
        continue;
      } else if (id === 'edge' && col.edge) {
        // A ring of 1.5% of face width on each side of the border, patted outward with the border shade
        const f = boxBlur(Z.ring, W, H, fw * 0.015);
        film(A((i) => OP.edge * Math.min(1, f[i] * 1.4) * sponge[i]), edgeLab, 'film', bright);
      } else if (id === 'foundation' && col.foundation) {
        // A very thin even tone over the whole face; halved in the center of light patches (concealer is already there, avoid being too thick).
        // Makeup tutorial: push the foundation outward to the jawline, neck, hairline and in front of the ears, thinner the farther out, so the face and neck have no visible dividing line
        const d = boxBlur(Z.fill, W, H, fw * 0.02), seg = S.segCls, hair = S.hairBg;
        const near = boxBlur(S.skin, W, H, fw * 0.12), a = new Float32Array(n);
        const jawY = Math.max(...[172, 136, 150, 149, 176, 148, 152, 377, 400, 378, 379, 365, 397].map((q) => S.det.face[q].y * H));
        for (let i = 0; i < n; i++) {
          const inFace = Z.ok[i], face = OP.foundation * (1 - 0.5 * Math.min(1, d[i])) * inFace;
          let out = 0;
          if (seg && (seg[i] === 2 || seg[i] === 3) && S.protect[i] < 0.1) {
            // Skin outside the face (neck, ears, hairline): thinner the farther from the face; going down the neck, push at most to 25% of face width below the jawline
            const y = (i / W) | 0, fade = y > jawY ? Math.max(0, 1 - (y - jawY) / (fw * 0.45)) : 1;
            out = OP.foundation * Math.min(1, near[i] * 2.5) * fade * (1 - (hair ? Math.min(1, hair[i] * 1.5) : 0));
          }
          a[i] = Math.max(face, out * (1 - inFace)) * k;
        }
        film(a, foundLab, 'film', bright);
      } else if (id === 'powder' && col.foundation) {
        // Translucent setting powder, pressed on: light patches + T-zone, to take down shine and soften fine lines (leans toward the foundation color, very light)
        const u = new Float32Array(n);
        for (let i = 0; i < n; i++) u[i] = Math.max(Z.fill[i], Z.tzone[i]);
        const f = boxBlur(u, W, H, fw * 0.03);
        film(A((i) => OP.powder * f[i]), foundLab, 'film');
      } else if (id.startsWith('touch') && col.concealer) {
        let t = touchUp(OP[id]);
        // With a mask: touch-up 1 dabs by the server mask, touch-up 3 by the mask 'detected again after the makeup is on' (opt.patchMask2, second detection)
        const ext = opt.patchMask ? (id === 'touch1' ? opt.patchMask : id === 'touch3' ? opt.patchMask2 : null) : null;
        if (ext) {
          const f = boxBlur(ext, W, H, fw * MASK_FEATHER), alpha = new Float32Array(n);
          let cnt = 0;
          for (let i = 0; i < n; i++) { alpha[i] = OP[id] * k * Math.min(1, f[i] * 1.3) * region[i] * (0.85 + 0.15 * sponge[i]); if (ext[i] > 0.5) cnt++; }
          t = { alpha, ref: t ? t.ref : light, area: cnt / n, refA: t ? t.refA : null, refB: t ? t.refB : null };
        }
        if (t) film(t.alpha, concLab, 'film', 0, t.ref, t.refA ? [t.refA, t.refB] : null);
        done.push({ id, area: t ? t.area : 0 });
        continue;
      } else if (id === 'contour' && col.contour) {
        // Lower cheekbone edge, jawline, sides of the nose: only toward darker, edge pushed out widely (7% of face width)
        const f = boxBlur(Z.contour, W, H, fw * 0.07);
        film(A((i) => OP.contour * Math.min(1, f[i] * 1.6)), cal(col.contour), 'deepen');
      } else if (id === 'highlight' && col.highlight) {
        // Upper cheekbone edge, nose bridge (stopping before the nose tip), a little on the nose tip, a faint touch at the center of the forehead; strength is further multiplied by the 3D mesh lighting (only brighter where it is high)
        const u = new Float32Array(n);
        for (let i = 0; i < n; i++) u[i] = Math.max(Z.hlCheek[i] * 0.8, Z.hlNose[i], Z.hlFore[i] * 0.7);
        const f = boxBlur(u, W, H, fw * 0.045), lt = spec ? spec.wide : null;
        film(A((i) => OP.highlight * Math.min(1, f[i] * 1.5) * (lt ? 0.4 + 0.6 * lt[i] : 1)), cal(lighten(col.highlight, 0.35)), 'glow');
      } else if ((id === 'contour_fine' || id === 'highlight_fine') && spec && spec.diffuse && (id === 'contour_fine' ? col.contour : col.highlight)) {
        // Fine contour / fine highlight: the 'small-scale variation' of the 3D face mesh lighting = lighting - broad average (darker at the sides of the nose, eye sockets, under the lower lip; brighter at the nose bridge, brow bone, chin).
        // Fit to the target makeup (10-17): beyond the broad contour, small-scale light and shade also had to be deepened to look right; browser measurement showed fine contour 0.3 is best and fine highlight gets worse
        const ds = boxBlur(spec.diffuse, W, H, fw * 0.008), dw = boxBlur(spec.diffuse, W, H, fw * 0.04), cs = boxBlur(spec.cover, W, H, fw * 0.05);
        const sgn = id === 'contour_fine' ? -1 : 1, gain = opt.fineGain ?? FINE_GAIN;
        film(A((i) => OP[id] * Math.min(1, Math.max(0, sgn * (ds[i] - dw[i]) / gain)) * Math.max(0, Math.min(1, (cs[i] - 0.5) * 2.5))),
          sgn < 0 ? cal(col.contour) : cal(lighten(col.highlight, 0.35)), sgn < 0 ? 'deepen' : 'glow');
      }
      done.push({ id });
    }
    const c = document.createElement('canvas'); c.width = W; c.height = H;
    const ctx = c.getContext('2d');
    const out = new ImageData(new Uint8ClampedArray(S.px), W, H), o = out.data;
    for (let i = 0; i < n; i++) {
      if (cL[i] === S.L[i] && cA[i] === S.A[i] && cB[i] === S.B[i]) continue;
      const rgb = CE.labToSrgb(cL[i], cA[i], cB[i]);
      o[i * 4] = rgb[0]; o[i * 4 + 1] = rgb[1]; o[i * 4 + 2] = rgb[2];
    }
    ctx.putImageData(out, 0, 0);
    // High-resolution output:
    // the layers are computed at 720 width; the delta (after makeup - original) is almost always a smooth base-color change -> upscale it bilinearly and overlay it on the original, so the original's pore detail is preserved at high resolution
    let imageHi = null, hiSize = null;
    if (opt.outLong) {
      const img = await new Promise((ok, bad) => { const im = new Image(); im.onload = () => ok(im); im.onerror = bad; im.src = S.src; });
      const iw = img.naturalWidth, ih = img.naturalHeight, sc2 = Math.min(1, opt.outLong / Math.max(iw, ih));
      const OW = Math.round(iw * sc2), OH = Math.round(ih * sc2);
      const hc = document.createElement('canvas'); hc.width = OW; hc.height = OH;
      const hg = hc.getContext('2d', { willReadFrequently: true }); hg.drawImage(img, 0, 0, OW, OH);
      const hd = hg.getImageData(0, 0, OW, OH), hp = hd.data, px0 = S.px;
      const dl = [new Float32Array(n), new Float32Array(n), new Float32Array(n)];
      for (let i = 0; i < n; i++) for (let ch = 0; ch < 3; ch++) dl[ch][i] = o[i * 4 + ch] - px0[i * 4 + ch];
      for (let y = 0; y < OH; y++) {
        const gy = Math.min(H - 1, Math.max(0, (y + 0.5) * H / OH - 0.5)), y0 = gy | 0, y1 = Math.min(H - 1, y0 + 1), fy = gy - y0;
        for (let x = 0; x < OW; x++) {
          const gx = Math.min(W - 1, Math.max(0, (x + 0.5) * W / OW - 0.5)), x0 = gx | 0, x1 = Math.min(W - 1, x0 + 1), fx = gx - x0;
          const a = y0 * W + x0, b = y0 * W + x1, cI = y1 * W + x0, d = y1 * W + x1, j = (y * OW + x) * 4;
          for (let ch = 0; ch < 3; ch++) {
            const D = dl[ch], v = (D[a] * (1 - fx) + D[b] * fx) * (1 - fy) + (D[cI] * (1 - fx) + D[d] * fx) * fy;
            if (v) hp[j + ch] = Math.max(0, Math.min(255, hp[j + ch] + v));
          }
        }
      }
      hg.putImageData(hd, 0, 0);
      imageHi = hc.toDataURL('image/jpeg', 0.92); hiSize = [OW, OH];
    }
    return { image: c.toDataURL('image/png'), imageHi, hiSize, layers: done, mix: mixInfo, skinLab: nLab };
  }

  // From the recommendation list pick the one that, 'converted to this photo's lighting, is closest to the surrounding normal skin tone' (makeup tutorial: match the concealer to the skin tone around the light patch).
  // 10-17 measurement: the first item in the list, once converted, was 4 darker than the person's skin tone and redder (L 54 / a* 24 vs 58 / 18), while the second, once converted, equaled the person's skin tone
  function pickClosest(list, normalHex, foundationHex) {
    const CE = window.CamoEditor;
    if (!list || !list.length) return null;
    const nLab = CE.hexToLab(normalHex), fLab = CE.hexToLab(foundationHex), off = nLab.map((v, j) => v - fLab[j]);
    let best = null, bd = Infinity;
    for (const m of list) {
      if (!m || !m.hex) continue;
      const c = CE.hexToLab(m.hex).map((v, j) => v + off[j]), d = Math.hypot(c[0] - nLab[0], c[1] - nLab[1], c[2] - nLab[2]);
      if (d < bd) { bd = d; best = Object.assign({}, m, { fitDE: d }); }
    }
    return best;
  }

  // The makeup region of each layer (for viewing on the editor): returns { id: opacity map }
  function zoneMaps() { const S = window.CamoEditor._debug(); return S ? zones(S, {}) : null; }

  // Makeup region (face + neck, with eyes, eyebrows and lips subtracted): sent to the server's /api/patch-mask as the detection region
  function regionMask() { const S = window.CamoEditor._debug(); return S ? regionOf(S, zones(S, {})) : null; }

  window.MakeupLayers = { LAYERS, render, zoneMaps, regionMask, pickClosest, _debug: () => DBG };
})();
