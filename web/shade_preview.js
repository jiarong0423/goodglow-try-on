'use strict';
// 本機底妝預覽（在手機上算，照片不上傳、不扣點）。
//
// 為什麼要自己做：YouCam 試妝要花點數、每換一個色號就要再跑一次；先在手機上即時比較色號，選定再交給 YouCam 真正試妝。
// 做法（Lab）：
// 1. 臉部定位（MediaPipe，face_auto.js 已載入的同一個模型）畫出臉部遮罩：臉輪廓內，挖掉眼睛、眉毛、嘴唇，邊緣羽化。
// 2. 低頻（大範圍明暗）＝模糊後的照片；目標＝色號的顏色＋照片原本的大範圍明暗起伏（保留立體，不變平面面具）。
// 3. 遮蓋力 c：低頻往目標靠 c；最細一層（雀斑、毛孔）留 1 − 0.95c；五官輪廓那層的明暗保留（顏色也跟著換成色號），鼻子嘴巴不糊。
// 畫面一律標明「手機上的底妝預覽」，真正的上妝效果以 YouCam 試妝為準。

(function () {
  const FACE_OVAL = [10, 338, 297, 332, 284, 251, 389, 356, 454, 323, 361, 288, 397, 365, 379, 378, 400, 377, 152, 148,
    176, 149, 150, 136, 172, 58, 132, 93, 234, 127, 162, 21, 54, 103, 67, 109];
  const HOLES = [
    [33, 7, 163, 144, 145, 153, 154, 155, 133, 173, 157, 158, 159, 160, 161, 246],          // 右眼
    [362, 382, 381, 380, 374, 373, 390, 249, 263, 466, 388, 387, 386, 385, 384, 398],       // 左眼
    [70, 63, 105, 66, 107, 55, 65, 52, 53, 46],                                             // 右眉
    [300, 293, 334, 296, 336, 285, 295, 282, 283, 276],                                     // 左眉
    [61, 146, 91, 181, 84, 17, 314, 405, 321, 375, 291, 409, 270, 269, 267, 0, 37, 39, 40, 185],  // 唇
  ];
  const LIP_IN_IDS = [78, 95, 88, 178, 87, 14, 317, 402, 318, 324, 308, 415, 310, 311, 312, 13, 82, 81, 80, 191];
  const WORK_W = 720;              // 運算解析度（寬）
  const KEEP_RELIEF = 0.85;        // 保留原本大範圍明暗的比例（立體感）
  const MAX_TEXTURE_CUT = 0.95;    // 全覆蓋時，高頻紋理最多去掉 95%

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
  const hexToRgb = (hex) => { const n = parseInt(hex.slice(1), 16); return [n >> 16, (n >> 8) & 255, n & 255]; };
  const rgbToHex = (r, g, b) => '#' + [r, g, b].map((v) => Math.round(v).toString(16).padStart(2, '0')).join('').toUpperCase();

  function boxBlur(src, w, h, r) {     // 兩次可分離方框模糊（近似高斯）
    let a = src, b = new Float32Array(w * h);
    for (let pass = 0; pass < 2; pass++) {
      for (let y = 0; y < h; y++) {
        let s = 0; const row = y * w;
        for (let x = -r; x <= r; x++) s += a[row + Math.min(w - 1, Math.max(0, x))];
        for (let x = 0; x < w; x++) {
          b[row + x] = s / (2 * r + 1);
          s += a[row + Math.min(w - 1, x + r + 1)] - a[row + Math.max(0, x - r)];
        }
      }
      const c = new Float32Array(w * h);
      for (let x = 0; x < w; x++) {
        let s = 0;
        for (let y = -r; y <= r; y++) s += b[Math.min(h - 1, Math.max(0, y)) * w + x];
        for (let y = 0; y < h; y++) {
          c[y * w + x] = s / (2 * r + 1);
          s += b[Math.min(h - 1, y + r + 1) * w + x] - b[Math.max(0, y - r) * w + x];
        }
      }
      a = c; b = new Float32Array(w * h);
    }
    return a;
  }
  function loadImg(src) {
    return new Promise((ok, bad) => { const i = new Image(); i.onload = () => ok(i); i.onerror = bad; i.src = src; });
  }

  let cur = null;                  // 目前準備好的照片

  async function prepare(src) {
    if (cur && cur.src === src) return cur;
    const img = await loadImg(src);
    const w = Math.min(WORK_W, img.naturalWidth), h = Math.round(w * img.naturalHeight / img.naturalWidth);
    const cv = document.createElement('canvas'); cv.width = w; cv.height = h;
    const g = cv.getContext('2d'); g.drawImage(img, 0, 0, w, h);
    const px = g.getImageData(0, 0, w, h).data;
    const lm = window.FaceAuto && await window.FaceAuto.loadLandmarker();
    if (!lm) throw new Error(__t('s0748', '臉部定位沒有載入'));
    const res = lm.detect(cv);
    const faces = (res && res.faceLandmarks) || [];
    if (!faces.length) throw new Error(__t('s0749', '找不到臉'));
    const span = (f) => Math.max(...f.map((p) => p.x)) - Math.min(...f.map((p) => p.x));
    const face = faces.reduce((a, b) => (span(b) > span(a) ? b : a));      // 多人照：取最大的那張臉
    if (face.length < 468) throw new Error(__t('s0750', '臉部點位不完整'));
    const P = (i) => [face[i].x * w, face[i].y * h];
    const xs = face.map((p) => p.x * w), fw = Math.max(...xs) - Math.min(...xs);
    // 遮罩
    // → 臉輪廓只往內羽化；眉（外擴）、眼球、內口另做保護區：內部恆為 1、只往外柔化，所有妝層最後乘上 (1 − 保護)。
    const poly = (ids) => { const pp = new Path2D(); ids.forEach((i, k) => (k ? pp.lineTo(...P(i)) : pp.moveTo(...P(i)))); pp.closePath(); return pp; };
    const rasterOf = (draw) => {
      const c = document.createElement('canvas'); c.width = w; c.height = h;
      const g2 = c.getContext('2d'); g2.fillStyle = '#fff'; g2.strokeStyle = '#fff'; g2.lineJoin = 'round';
      draw(g2);
      const d = g2.getImageData(0, 0, w, h).data, a = new Float32Array(w * h);
      for (let i = 0; i < w * h; i++) a[i] = d[i * 4 + 3] / 255;
      return a;
    };
    const faceRaw = rasterOf((g2) => g2.fill(poly(FACE_OVAL)));
    // 臉部網格的外框只到額頭中段，粉底預覽到那條線就停、極淺膚色會看到一道方塊邊（ 手機實測）。
    // 有臉部分割時，把「額頭上方、兩側太陽穴之間」被分成臉部皮膚的範圍併進來，邊緣就落在髮際線；分割失敗維持原樣。
    try {
      const segCv = document.createElement('canvas'); segCv.width = w; segCv.height = h; segCv.getContext('2d').drawImage(img, 0, 0, w, h);
      const seg = window.FaceAuto && window.FaceAuto.segmentCached ? await window.FaceAuto.segmentCached(src, segCv) : null;
      if (seg) {
        const topY = P(10)[1] + fw * 0.03, x0 = Math.min(P(127)[0], P(356)[0]), x1 = Math.max(P(127)[0], P(356)[0]);
        const sx = seg.w / w, sy = seg.h / h;
        for (let y = 0, i = 0; y < h; y++) {
          const row = Math.min(seg.h - 1, Math.floor(y * sy)) * seg.w;
          for (let x = 0; x < w; x++, i++) {
            if (y < topY && x > x0 && x < x1 && seg.arr[row + Math.min(seg.w - 1, Math.floor(x * sx))] === 3) faceRaw[i] = 1;
          }
        }
      }
    } catch (_) { /* 分割失敗：維持網格範圍 */ }
    const protRaw = rasterOf((g2) => {
      g2.lineWidth = Math.max(3, fw * 0.02);
      for (const ids of [HOLES[2], HOLES[3]]) { const pp = poly(ids); g2.fill(pp); g2.stroke(pp); }       // 眉：外擴，容納點位誤差
      g2.lineWidth = Math.max(2, fw * 0.008);
      for (const ids of [HOLES[0], HOLES[1]]) { const pp = poly(ids); g2.fill(pp); g2.stroke(pp); }       // 眼球＋睫毛根
      g2.fill(poly(LIP_IN_IDS));                                                                         // 內口
    });
    const pb = boxBlur(protRaw, w, h, Math.max(2, Math.round(fw * 0.02)));   // 只往外柔化、過渡寬一點：全覆蓋時眉毛周圍才不會留一圈沒上妝的框
    const protect = new Float32Array(w * h);
    for (let i = 0; i < w * h; i++) protect[i] = Math.max(protRaw[i], Math.min(1, pb[i] * 1.5));
    const lipRaw = rasterOf((g2) => { g2.lineWidth = Math.max(3, fw * 0.015); const pp = poly(HOLES[4]); g2.fill(pp); g2.stroke(pp); });
    const fb = boxBlur(faceRaw, w, h, Math.max(3, Math.round(fw * 0.035)));
    const lb = boxBlur(lipRaw, w, h, Math.max(2, Math.round(fw * 0.01)));
    const mask = new Float32Array(w * h);        // 底妝：臉內（只往內羽化）、扣唇、扣保護區
    for (let i = 0; i < w * h; i++) mask[i] = Math.min(faceRaw[i], fb[i]) * (1 - Math.max(lipRaw[i], lb[i])) * (1 - protect[i]);
    // Lab 與低頻
    const L = new Float32Array(w * h), A = new Float32Array(w * h), B = new Float32Array(w * h);
    for (let i = 0; i < w * h; i++) { const lab = srgbToLab(px[i * 4], px[i * 4 + 1], px[i * 4 + 2]); L[i] = lab[0]; A[i] = lab[1]; B[i] = lab[2]; }
    const r = Math.max(3, Math.round(fw * 0.03));
    const lowL = boxBlur(L, w, h, r), lowA = boxBlur(A, w, h, r), lowB = boxBlur(B, w, h, r);
    // 細節分兩層：毛孔雀斑（比 fine 更細）會被遮蓋；五官輪廓（fine 與 low 之間）保留，鼻子嘴巴才不會糊掉
    const rf = Math.max(1, Math.round(fw * 0.008));
    const fineL = boxBlur(L, w, h, rf), fineA = boxBlur(A, w, h, rf), fineB = boxBlur(B, w, h, rf);
    const vals = [];                              // 臉部明度基準用中位數（不被淺色區塊、陰影拉走）
    for (let i = 0; i < w * h; i += 3) if (mask[i] > 0.8) vals.push(lowL[i]);
    vals.sort((x, y) => x - y);
    cur = { src, w, h, px, L, A, B, lowL, lowA, lowB, fineL, fineA, fineB, mask, protect, meanL: vals.length ? vals[vals.length >> 1] : 70, fw,
            pts: face.map((p) => [p.x * w, p.y * h]), masks: {} };
    return cur;
  }

  // ---------------------------------------------------------------- 妝容範圍（SVG 路徑）
  // 範圍用臉部點位組成 SVG 路徑：
  // canvas 用 Path2D 畫成遮罩上色；同一組路徑也能疊成 SVG 外框，讓人看到每一項妝畫在哪裡。
  const EYE_UP = { r: [33, 246, 161, 160, 159, 158, 157, 173, 133], l: [263, 466, 388, 387, 386, 385, 384, 398, 362] };
  const BROW_LOW = { r: [46, 53, 52, 65, 55], l: [276, 283, 282, 295, 285] };   // 眉下緣（外→內），用時重採樣成和上眼瞼同點數
  const LIP_OUT = [61, 146, 91, 181, 84, 17, 314, 405, 321, 375, 291, 409, 270, 269, 267, 0, 37, 39, 40, 185];
  const LIP_IN = [78, 95, 88, 178, 87, 14, 317, 402, 318, 324, 308, 415, 310, 311, 312, 13, 82, 81, 80, 191];
  const f1 = (v) => v.toFixed(1);
  const pathOf = (pts, close = true) => pts.map((p, k) => `${k ? 'L' : 'M'}${f1(p[0])} ${f1(p[1])}`).join(' ') + (close ? ' Z' : '');
  const mix = (a, b, t) => [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t];

  // 每一項：{shape:'fill'|'stroke'|'radial', d, hole?, width?, cx, cy, r, blur}（座標＝運算解析度的像素）
  function regions() {
    const P = cur.pts, fw = cur.fw, R = {};
    const at = (i) => P[i];
    R.lip = [{ shape: 'fill', d: pathOf(LIP_OUT.map(at)), hole: pathOf(LIP_IN.map(at)), blur: fw * 0.006 }];
    R.blush = [[50, 117, 127], [280, 346, 356]].map(([a, b, tp]) => {   // 橢圓，長軸斜向太陽穴
      const c = mix(at(a), at(b), 0.45), t = at(tp);
      return { shape: 'radial', cx: c[0], cy: c[1], r: fw * 0.13, ry: fw * 0.085, rot: Math.atan2(t[1] - c[1], t[0] - c[0]), blur: fw * 0.02 };
    });
    R.contour = [[93, 207], [323, 427]].map(([a, b]) => ({ shape: 'stroke', d: pathOf([mix(at(a), at(b), 0.08), mix(at(a), at(b), 0.75)], false), width: fw * 0.07, blur: fw * 0.04 }))
      .concat([{ shape: 'stroke', d: pathOf([172, 136, 150, 149, 176, 148, 152, 377, 400, 378, 379, 365, 397].map(at), false), width: fw * 0.045, blur: fw * 0.035 }]);
    R.highlight = [[117, 123], [346, 352]].map(([a, b]) => ({ shape: 'stroke', d: pathOf([at(a), at(b)], false), width: fw * 0.05, blur: fw * 0.03 }))
      .concat([{ shape: 'stroke', d: pathOf([168, 6, 197, 195].map(at), false), width: fw * 0.035, blur: fw * 0.02 }]);
    const lid = (side, t0, t1) => {
      const up = EYE_UP[side].map(at), b5 = BROW_LOW[side].map(at);
      const br = up.map((_, k) => { const t = k * (b5.length - 1) / (up.length - 1), j = Math.min(b5.length - 2, Math.floor(t)); return mix(b5[j], b5[j + 1], t - j); });
      const a = up.map((p, k) => mix(p, br[k], t0)), b = up.map((p, k) => mix(p, br[k], t1)).reverse();
      return pathOf(a.concat(b));
    };
    R.eyeshadow = ['r', 'l'].map((sd) => ({ shape: 'fill', d: lid(sd, 0.05, 0.55), blur: fw * 0.02 }));
    R.eyeshadow_outer = ['r', 'l'].map((sd) => {
      const o = at(sd === 'r' ? 33 : 263), br = at(sd === 'r' ? 46 : 276), c = mix(o, br, 0.35);
      return { shape: 'radial', cx: c[0], cy: c[1], r: fw * 0.09, blur: fw * 0.015 };
    });
    R.lid_shimmer = ['r', 'l'].map((sd) => ({ shape: 'fill', d: lid(sd, 0.08, 0.3), blur: fw * 0.012 }));
    R.liner = ['r', 'l'].map((sd) => ({ shape: 'stroke', d: pathOf(EYE_UP[sd].map(at), false), width: fw * 0.012, blur: fw * 0.004 }));
    return R;
  }

  function maskOf(item) {
    if (cur.masks[item]) return cur.masks[item];
    const { w, h } = cur, specs = regions()[item];
    if (!specs) throw new Error(__T('s0751', () => `沒有這一項妝：${item}`, () => `No such makeup item: ${item}`));
    const cv = document.createElement('canvas'); cv.width = w; cv.height = h;
    const g = cv.getContext('2d');
    let blur = 2;
    for (const sp of specs) {
      g.globalCompositeOperation = 'source-over';
      blur = Math.max(blur, Math.round(sp.blur || 2));
      if (sp.shape === 'fill') {
        g.fillStyle = '#fff'; g.fill(new Path2D(sp.d));
        if (sp.hole) { g.globalCompositeOperation = 'destination-out'; g.fill(new Path2D(sp.hole)); }
      } else if (sp.shape === 'stroke') {
        g.strokeStyle = '#fff'; g.lineWidth = sp.width; g.lineCap = 'round'; g.lineJoin = 'round'; g.stroke(new Path2D(sp.d));
      } else {
        g.save(); g.translate(sp.cx, sp.cy); g.rotate(sp.rot || 0); g.scale(1, (sp.ry || sp.r) / sp.r);
        const gr = g.createRadialGradient(0, 0, 0, 0, 0, sp.r);
        gr.addColorStop(0, 'rgba(255,255,255,1)'); gr.addColorStop(0.6, 'rgba(255,255,255,0.55)'); gr.addColorStop(1, 'rgba(255,255,255,0)');
        g.fillStyle = gr; g.beginPath(); g.arc(0, 0, sp.r, 0, Math.PI * 2); g.fill(); g.restore();
      }
    }
    const d = g.getImageData(0, 0, w, h).data, a = new Float32Array(w * h);
    for (let i = 0; i < w * h; i++) a[i] = d[i * 4 + 3] / 255 * (d[i * 4] / 255);
    const m = boxBlur(a, w, h, blur);
    for (let i = 0; i < w * h; i++) m[i] *= 1 - cur.protect[i];      // 白眉、眼球、內口永遠不上色
    cur.masks[item] = m;
    return m;
  }

  // SVG 外框：每一項的範圍（顯示用）
  function svgOverlay(items) {
    const R = regions(), col = { lip: '#e04870', blush: '#ff7aa8', contour: '#8a6f5e', highlight: '#ffe08a', eyeshadow: '#9a7fb8',
      eyeshadow_outer: '#6d5a80', lid_shimmer: '#c9c0e0', liner: '#3b3346' };
    const parts = [];
    for (const it of items) for (const sp of R[it] || []) {
      const c = col[it] || '#fff';
      if (sp.shape === 'radial') parts.push(`<ellipse cx="${f1(sp.cx)}" cy="${f1(sp.cy)}" rx="${f1(sp.r)}" ry="${f1(sp.ry || sp.r)}" transform="rotate(${f1((sp.rot || 0) * 180 / Math.PI)} ${f1(sp.cx)} ${f1(sp.cy)})" fill="none" stroke="${c}" stroke-width="2" stroke-dasharray="6 4"/>`);
      else parts.push(`<path d="${sp.d}" fill="none" stroke="${c}" stroke-width="${sp.shape === 'stroke' ? f1(Math.max(2, sp.width)) : 2}" stroke-opacity="${sp.shape === 'stroke' ? 0.45 : 0.9}" stroke-linecap="round"/>`);
      if (sp.hole) parts.push(`<path d="${sp.hole}" fill="none" stroke="${c}" stroke-width="1.5" stroke-dasharray="3 3"/>`);
    }
    return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${cur.w} ${cur.h}" preserveAspectRatio="none">${parts.join('')}</svg>`;
  }

  // 每一項怎麼混色（Lab）：亮度往目標靠多少、是否只會變亮（打亮）
  const BLEND = { lip: 0.75, blush: 0.35, contour: 0.8, highlight: 0.6, eyeshadow: 0.7, eyeshadow_outer: 0.8, lid_shimmer: 0.5, liner: 0.9 };

  // hex：底妝顏色（null＝不上底妝）；coverage 0–1；layers：[{item, hex, a}]；回傳 data URL
  function render(hex, coverage, layers = []) {
    if (!cur) throw new Error(__t('s0715', '先 prepare'));
    const okHex = (x) => typeof x === 'string' && /^#[0-9A-Fa-f]{6}$/.test(x);
    if (hex != null && !okHex(hex)) throw new Error(__T('s0752', () => `色碼格式不對：${hex}`, () => `Invalid color code format: ${hex}`));
    coverage = Math.min(1, Math.max(0, Number(coverage) || 0));
    const { w, h, px, L, A, B, lowL, lowA, lowB, fineL, fineA, fineB, mask, meanL } = cur;
    const nL = Float32Array.from(L), nA = Float32Array.from(A), nB = Float32Array.from(B);
    if (hex && coverage > 0) {
      const [tL, tA, tB] = srgbToLab(...hexToRgb(hex));
      for (let i = 0; i < w * h; i++) {
        const c = coverage * mask[i];
        if (c < 0.005) continue;
        const relief = KEEP_RELIEF - 0.35 * c;                      // 遮蓋越高，大塊深淺不一（淺色區塊、泛紅）越被壓平
        const gL = tL + (lowL[i] - meanL) * relief;                 // 色號＋原本的大範圍明暗
        const keep = 1 - MAX_TEXTURE_CUT * c;                       // 最細的一層（雀斑毛孔）保留比例
        nL[i] = lowL[i] + c * (gL - lowL[i]) + (fineL[i] - lowL[i]) * (1 - 0.25 * c) + (L[i] - fineL[i]) * keep;
        nA[i] = lowA[i] + c * (tA - lowA[i]) + (fineA[i] - lowA[i]) * (1 - c) + (A[i] - fineA[i]) * keep;
        nB[i] = lowB[i] + c * (tB - lowB[i]) + (fineB[i] - lowB[i]) * (1 - c) + (B[i] - fineB[i]) * keep;
      }
    }
    for (const ly of layers) {
      if (!ly || !okHex(ly.hex) || !(ly.a > 0)) continue;
      const m = maskOf(ly.item), k = BLEND[ly.item] ?? 0.6;
      const [tL, tA, tB] = srgbToLab(...hexToRgb(ly.hex));
      const hl = ly.item === 'highlight' || ly.item === 'lid_shimmer';
      for (let i = 0; i < w * h; i++) {
        const al = Math.min(1, ly.a) * m[i];
        if (al < 0.003) continue;
        const dL = tL - nL[i];
        nL[i] += al * k * (hl ? Math.max(0, dL) : dL);
        nA[i] += al * (hl ? 0.3 : 1) * (tA - nA[i]);
        nB[i] += al * (hl ? 0.3 : 1) * (tB - nB[i]);
      }
    }
    const out = new ImageData(new Uint8ClampedArray(px), w, h);
    const d = out.data;
    for (let i = 0; i < w * h; i++) {
      if (nL[i] === L[i] && nA[i] === A[i] && nB[i] === B[i]) continue;
      const o = labToSrgb(nL[i], nA[i], nB[i]);
      d[i * 4] = o[0]; d[i * 4 + 1] = o[1]; d[i * 4 + 2] = o[2];
    }
    const cv = document.createElement('canvas'); cv.width = w; cv.height = h;
    cv.getContext('2d').putImageData(out, 0, 0);
    return cv.toDataURL('image/png');        // 無損：沒上妝的地方（含白眉）像素和原圖完全一樣（JPEG 會重新壓縮改到）
  }

  // 吸管：點的位置（0–1）取低頻色。只接受臉上的皮膚（避開眉、眼、唇、頭髮、背景），取周圍多點的中位數；不合格回 null
  function sample(xRel, yRel) {
    if (!cur) throw new Error(__t('s0715', '先 prepare'));
    const { w, h, lowL, lowA, lowB, mask, fw } = cur;
    if (!(xRel >= 0 && xRel <= 1 && yRel >= 0 && yRel <= 1)) return null;
    const x0 = Math.round(xRel * w), y0 = Math.round(yRel * h), rad = Math.max(2, Math.round(fw * 0.02));
    const Ls = [], As = [], Bs = [];
    for (let dy = -rad; dy <= rad; dy += 2) for (let dx = -rad; dx <= rad; dx += 2) {
      const x = x0 + dx, y = y0 + dy;
      if (x < 0 || y < 0 || x >= w || y >= h) continue;
      const i = y * w + x;
      if (mask[i] < 0.6) continue;
      Ls.push(lowL[i]); As.push(lowA[i]); Bs.push(lowB[i]);
    }
    const total = Math.pow(Math.floor(rad / 2) * 2 + 1, 2);
    if (Ls.length < total * 0.6) return null;
    const med = (v) => v.sort((a, b) => a - b)[v.length >> 1];
    const Lm = med(Ls);
    if (Lm < 15 || Lm > 97) return null;     // 太暗或過曝
    return rgbToHex(...labToSrgb(Lm, med(As), med(Bs)));
  }

  // 量測用：臉部範圍內的高頻紋理強度（預覽前後比，驗證遮蓋力有作用）
  async function textureOf(src) {
    const c = cur, img = await loadImg(src);
    const cv = document.createElement('canvas'); cv.width = c.w; cv.height = c.h;
    const g = cv.getContext('2d'); g.drawImage(img, 0, 0, c.w, c.h);
    const p = g.getImageData(0, 0, c.w, c.h).data;
    const Lr = new Float32Array(c.w * c.h);
    for (let i = 0; i < c.w * c.h; i++) Lr[i] = srgbToLab(p[i * 4], p[i * 4 + 1], p[i * 4 + 2])[0];
    const low = boxBlur(Lr, c.w, c.h, Math.max(3, Math.round(c.fw * 0.03)));
    let s = 0, n = 0;
    for (let i = 0; i < c.w * c.h; i++) if (c.mask[i] > 0.95) { const v = Lr[i] - low[i]; s += v * v; n++; }
    return n ? Math.sqrt(s / n) : 0;
  }

  window.ShadePreview = { prepare, render, sample, textureOf, svgOverlay, regions: () => regions(), _debug: () => cur };   // _debug：只給量測用
})();
