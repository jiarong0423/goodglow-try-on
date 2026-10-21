'use strict';
// 淺色區塊局部遮瑕：AI 找邊界 → SVG 向量路徑（可編輯）→ Canvas 分層渲染（手機上算、不上傳、不扣點）。
// 貼了「AI 影像分析＋SVG 向量路徑＋Canvas 像素級渲染」的架構與 PoC，「你把程式寫好」。
// 1. 找邊界：沿用 camouflage.js 的淺色區塊判斷（彩度÷亮度分群），OpenCV.js findContours＋approxPolyDP 抓每一塊輪廓。
// 2. SVG 路徑：輪廓點用二次貝茲曲線（取相鄰點中點）平滑成 path 字串；使用者可開關每一塊、拖拉節點調整邊界。
// 3. Canvas 分層（Lab）：校色層（暖橘中和，避免遮完發灰）→ 遮瑕層（取「每一塊周圍」的正常膚色）→ 紋理層（高頻細節照原圖）。
//    羽化用方框模糊（iPhone Safari 不支援 ctx.filter）；白眉、眼睛、內口另做保護區，永遠不改。
// 畫面一律標明「手機上模擬的局部遮瑕」。

(function () {
  const FACE_OVAL = [10, 338, 297, 332, 284, 251, 389, 356, 454, 323, 361, 288, 397, 365, 379, 378, 400, 377, 152, 148,
    176, 149, 150, 136, 172, 58, 132, 93, 234, 127, 162, 21, 54, 103, 67, 109];
  const PROTECT = [
    [70, 63, 105, 66, 107, 55, 65, 52, 53, 46], [300, 293, 334, 296, 336, 285, 295, 282, 283, 276],           // 眉
    [33, 7, 163, 144, 145, 153, 154, 155, 133, 173, 157, 158, 159, 160, 161, 246],                               // 眼
    [362, 382, 381, 380, 374, 373, 390, 249, 263, 466, 388, 387, 386, 385, 384, 398],
    [61, 146, 91, 181, 84, 17, 314, 405, 321, 375, 291, 409, 270, 269, 267, 0, 37, 39, 40, 185],                // 整個嘴唇（外唇線； 原本只保護內口，大塊區塊會把嘴唇遮淡）
  ];
  const BROW_TOP = [70, 63, 105, 66, 107, 336, 296, 334, 293, 300];   // 眉毛上緣（額頭分區的下界）
  const ALPHA_CORE = 80, ALPHA_WIDE = 24, SIDE_W = 0.6;   // 高光集中度（皮膚常用 10–60）、側燈強度
  const LIPS_OUTER = [61, 146, 91, 181, 84, 17, 314, 405, 321, 375, 291, 409, 270, 269, 267, 0, 37, 39, 40, 185];
  const RENDER_W = 720;
  const RING_L_PCT = 0.7;                // 目標亮度取外圍膚色的第幾百分位（受光側）
  const HI_L = 10;                       // 打亮 100% 時臉中央最多提亮多少 L*（ 審查建議亮度 +10–15%、飽和 −5%；原 8）
  const HI_WIDE = 1.3;                   // 光斑放寬倍數（審查：打亮羽化要很高、往外自然淡出）
  // 打亮（化妝的 highlight）：額頭中央、鼻樑、眼下顴骨（C 字區）、下巴，各一個高斯光斑 [網格點, 寬(臉寬倍), 高(臉寬倍), 強度]
  const HI_SPOTS = [[151, 0.13, 0.07, 1], [9, 0.05, 0.05, 0.7], [6, 0.03, 0.06, 0.9], [195, 0.025, 0.05, 0.9], [4, 0.025, 0.025, 0.6],
    [117, 0.07, 0.04, 0.8], [346, 0.07, 0.04, 0.8], [199, 0.05, 0.035, 0.7]];
  const WARM = [3.0, 2.8];               // 校色層：蜜桃／暖橘 Δa*、Δb*

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
  // 區塊內外分開模糊：淺色區塊裡只和區塊裡平均、外面只和外面平均，
  // 交界（色素的邊）因此歸在低頻，化妝塗層蓋得到；整片一起模糊的話，交界留在紋理裡，怎麼疊都看得到輪廓。
  // tools/fit_layers_to_target.py 量：交界歸低頻後逼近誤差 1.62 → 1.59（顏色也擬合時）。
  // 回傳 blur(src)：遮罩的模糊只算一次，之後每個通道多一次模糊。
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
        out[i] = Number.isFinite(own) ? own : (ba[i] + bb[i]);       // 自己那邊沒有像素（不會發生在正常大小的區塊）：退回整片平均
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

  // 二次貝茲平滑（PoC 做法）：每段以原點為控制點、走到相鄰兩點的中點 → 輪廓平滑、沒有鋸齒
  function pathD(pts) {
    if (pts.length < 3) return '';
    const f = (v) => v.toFixed(1), n = pts.length;
    const mid = (i) => [(pts[i][0] + pts[(i + 1) % n][0]) / 2, (pts[i][1] + pts[(i + 1) % n][1]) / 2];
    const m0 = mid(n - 1);
    let d = `M${f(m0[0])} ${f(m0[1])}`;
    for (let i = 0; i < n; i++) { const m = mid(i); d += ` Q${f(pts[i][0])} ${f(pts[i][1])} ${f(m[0])} ${f(m[1])}`; }
    return d + ' Z';
  }

  // Delaunay 三角剖分（Bowyer-Watson；本版 OpenCV.js 沒有 Subdiv2D）。點數 468，O(n²) 夠快
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

  // 3D 打亮：MediaPipe 網格 468 點有深度 z，
  // 三角剖分 → 頂點法向量（相鄰三角面平均）→ 逐像素內插成法向量圖 → Blinn-Phong 鏡面高光（光源在正前上方）。
  // 額頭、鼻樑、顴骨依骨骼形狀亮起來，臉側一點高光也跟著移動。不用灰階梯度推法向量：白斑邊緣會被當成凸起而亮起來。
  // 回傳 core（集中高光，α=24）、wide（擴散，α=8，邊緣混一點暖色模擬透光）
  function meshSpecular(det, W, H, skin, protect) {
    const N = 468, P3 = det.face.slice(0, N).map((p) => [p.x * W, p.y * H, p.z * W]);
    const tris = delaunay(P3.map((p) => [p[0], p[1]]));
    if (tris.length < 300) return null;
    const vn = P3.map(() => [0, 0, 0]);
    for (const [a, b, c] of tris) {
      const u = [P3[b][0] - P3[a][0], P3[b][1] - P3[a][1], P3[b][2] - P3[a][2]], v = [P3[c][0] - P3[a][0], P3[c][1] - P3[a][1], P3[c][2] - P3[a][2]];
      let nx = u[1] * v[2] - u[2] * v[1], ny = u[2] * v[0] - u[0] * v[2], nz = u[0] * v[1] - u[1] * v[0];
      if (nz > 0) { nx = -nx; ny = -ny; nz = -nz; }                 // 朝向鏡頭（MediaPipe z 越小越靠近鏡頭）
      for (const k of [a, b, c]) { vn[k][0] += nx; vn[k][1] += ny; vn[k][2] += nz; }
    }
    for (const v of vn) { const l = Math.hypot(...v) || 1; v[0] /= l; v[1] /= l; v[2] /= l; }
    // 三盞燈：正前上方＋左右斜上方（化妝打光常用；只有正前一盞時鼻樑側面、顴骨接不到光）
    const half = (L) => { const l = Math.hypot(...L), h = [L[0] / l, L[1] / l, L[2] / l - 1], hl = Math.hypot(...h); return h.map((v) => v / hl); };
    const HS = [half([0, -0.6, -1]), half([-0.55, -0.45, -1]), half([0.55, -0.45, -1])], HW = [1, SIDE_W, SIDE_W];
    // 嘴唇不打亮（外唇線多邊形）
    const lip = document.createElement('canvas'); lip.width = W; lip.height = H;
    const lg = lip.getContext('2d', { willReadFrequently: true }); lg.fillStyle = '#fff'; lg.beginPath();
    LIPS_OUTER.forEach((k, j) => (j ? lg.lineTo(P3[k][0], P3[k][1]) : lg.moveTo(P3[k][0], P3[k][1]))); lg.closePath(); lg.fill();
    const ld = lg.getImageData(0, 0, W, H).data, lipM = new Float32Array(W * H);
    for (let i = 0; i < W * H; i++) lipM[i] = ld[i * 4 + 3] / 255;
    const lipB = boxBlur(lipM, W, H, Math.max(3, Math.round(W * 0.008)));
    const core = new Float32Array(W * H), wide = new Float32Array(W * H), diffuse = new Float32Array(W * H), cover = new Float32Array(W * H);
    const LF = [0, -0.6, -1].map((v, _, a) => v / Math.hypot(...a));   // 漫射光：正前上方（化妝塗層補立體感用）
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

  // 準備：照片 Lab、低頻、臉部皮膚範圍、保護區、淺色區塊核心遮罩（camouflage.js）
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
    // 額頭、下巴分區：
    // 臉部網格外框只到額頭中段（第 10 點），額頭上半到髮際線、下巴邊緣用 MediaPipe 多類別分割的「臉部皮膚」補進來。
    const xs0 = Math.min(...xs), xs1 = Math.max(...xs);
    const browPts = BROW_TOP.map(P).sort((u, v) => v[0] - u[0]);
    const zoneF = raster((cg) => {                                  // 額頭：眉毛上緣以上、臉寬範圍內
      cg.beginPath(); cg.moveTo(xs0, 0); cg.lineTo(xs1, 0); cg.lineTo(xs1, browPts[0][1]);
      browPts.forEach((q) => cg.lineTo(q[0], q[1])); cg.lineTo(xs0, browPts[browPts.length - 1][1]); cg.closePath(); cg.fill();
    });
    const chinY = P(17)[1] + fw * 0.02, jx0 = P(172)[0], jx1 = P(397)[0];
    const zoneC = raster((cg) => cg.fillRect(Math.min(jx0, jx1), chinY, Math.abs(jx1 - jx0), H - chinY));   // 下巴：下唇以下、兩側下顎角之間
    // 分割用另一張一般畫布（willReadFrequently 的畫布送進 MediaPipe 實測慢 6 倍：1.4 秒 vs 0.2 秒）
    const segCv = document.createElement('canvas'); segCv.width = W; segCv.height = H; segCv.getContext('2d').drawImage(img, 0, 0, W, H);
    const S0normalL = hexToLab(normalHex)[0];
    const seg = window.FaceAuto && window.FaceAuto.segmentCached ? await window.FaceAuto.segmentCached(src, segCv) : null;
    let hairBg = null, segAdded = 0, segCls = null;
    if (seg) {
      const sx = seg.w / W, sy = seg.h / H, colIdx = new Int32Array(W);
      for (let x = 0; x < W; x++) colIdx[x] = Math.min(seg.w - 1, Math.floor(x * sx));
      hairBg = new Float32Array(W * H); segCls = new Uint8Array(W * H);   // segCls：化妝塗層要知道脖子、耳朵（粉底推開）
      for (let y = 0, i = 0; y < H; y++) for (let x = 0, row = Math.min(seg.h - 1, Math.floor(y * sy)) * seg.w; x < W; x++, i++) {
        const k = seg.arr[row + colIdx[x]];
        segCls[i] = k;
        // 頭髮、背景：遮瑕羽化不要蓋上去。頭髮只排除「暗的」像素（髮絲）：髮際被分成頭髮的區域裡，
        // 髮絲之間的淺色皮膚照樣可以遮（ 診斷：右上捲髮旁那塊淺色整塊被當頭髮排除）
        if (k === 0 || (k === 1 && L[i] < S0normalL - 8)) hairBg[i] = 1;
        if (k === 3 && (zoneF[i] > 0.5 || zoneC[i] > 0.5) && skinRaw[i] < 0.5) { skinRaw[i] = 1; segAdded++; }
      }
      hairBg = boxBlur(hairBg, W, H, Math.max(2, Math.round(fw * 0.01)));
    }
    const sb = boxBlur(skinRaw, W, H, Math.max(3, Math.round(fw * 0.04)));     // 臉部範圍邊緣柔化，額頭上緣不要一條硬線
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
    // 低頻：淺色區塊（偵測核心，換到這個解析度）內外分開模糊
    const coreW = new Float32Array(W * H), csx = det.w / W, csy = det.h / H;
    for (let y = 0, i = 0; y < H; y++) for (let x = 0; x < W; x++, i++) coreW[i] = det.core[Math.min(det.h - 1, Math.floor(y * csy)) * det.w + Math.min(det.w - 1, Math.floor(x * csx))];
    const split = makeSplitBlur(coreW, W, H, r);
    S = { src, normalHex, patchHex, W, H, px, L, A, B, lowL: split(L), lowA: split(A), lowB: split(B),
      skin, protect, fw, det, normal: hexToLab(normalHex), patches: [] };
    S.core = Float32Array.from(det.core);
    S.segCls = segCls; S.hairBg = hairBg;
    const hi = new Float32Array(W * H);                              // 打亮權重圖（0–1），只算一次
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
    S.hi = hi;                                                       // 舊版高斯光斑（3D 打亮失敗時退回）
    try { S.spec = meshSpecular(det, W, H, skin, protect); } catch (e) { S.spec = null; }
    const rr = Math.max(1, Math.round(fw * 0.006));
    const sm = { L: boxBlur(L, W, H, rr), A: boxBlur(A, W, H, rr), B: boxBlur(B, W, H, rr) };   // 兩區共用
    const zoneAll = Float32Array.from(skin, (v) => (v > 0.9 ? 1 : 0));   // 全臉也跑一次局部參考（臉頰、下顎的細碎淺點）
    S.zones = { seg: !!seg, segAdded, forehead: zoneDetect(__t('s0712', '額頭'), zoneF, sm), chin: zoneDetect(__t('s0713', '下巴'), zoneC, sm), face: zoneDetect(__t('s0714', '全臉'), zoneAll, sm) };
    return S;
  }

  // 分區判別：額頭常有油光、受光比臉頰亮，下巴常在陰影、有鬍渣——整臉同一個亮度門檻會漏抓或亂抓。
  // 每區各自算「有色素皮膚」的亮度當參考（受光差異抵掉），再用彩度÷亮度（淺色區塊彩度低）判斷，結果併進淺色區塊核心。
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
    // 局部參考（ 殘留點診斷：額頭中央受光 57.6、太陽穴較暗，整區一個參考會漏掉太陽穴、髮際兩角的淺色區塊）：
    // 每個像素跟「附近 6% 臉寬內的有色素皮膚」比；附近沒有有色素皮膚時退回整區參考
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
    const dn = boxBlur(raw, W, H, Math.max(2, Math.round(fw * 0.008)));   // 去雜點（油光小亮點）
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

  // 輪廓：淺色區塊核心遮罩 → （外擴 expand 像素）→ OpenCV 抓外輪廓 → approxPolyDP 化簡 → 換到渲染座標
  async function findPatches(expand = 3) {
    if (!S) throw new Error(__t('s0715', '先 prepare'));
    const cvx = await window.Vendor.opencv();
    const { det, W } = S, w = det.w, h = det.h, sc = W / w;
    const bin = new Uint8Array(w * h);
    for (let i = 0; i < w * h; i++) bin[i] = S.core[i] > 0.5 ? 255 : 0;       // 含額頭、下巴分區判別的結果
    const m = cvx.matFromArray(h, w, cvx.CV_8UC1, bin);
    const k = cvx.getStructuringElement(cvx.MORPH_ELLIPSE, new cvx.Size(3, 3));
    cvx.morphologyEx(m, m, cvx.MORPH_CLOSE, k);                      // 補小洞、接斷線
    const e = Math.max(0, Math.round(expand));
    if (e > 0) { const ke = cvx.getStructuringElement(cvx.MORPH_ELLIPSE, new cvx.Size(2 * e + 1, 2 * e + 1)); cvx.dilate(m, m, ke); ke.delete(); }
    const cs = new cvx.MatVector(), hi = new cvx.Mat();
    cvx.findContours(m, cs, hi, cvx.RETR_EXTERNAL, cvx.CHAIN_APPROX_NONE);
    const minArea = w * h * 0.0003, out = [];                       // 原 0.0006；降一半讓臉頰細碎淺點也成塊
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

  // 每一塊的目標色：外圍一圈（邊界外 1–4% 臉寬）、在臉上、不在其他淺色區塊、不在保護區的低頻色中位數
  // src（第 2 輪起）：從「目前遮完的結果」取周圍顏色，排除還沒遮好的留白（src.excl），已遮好的舊區塊可以取
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
      if (det.mask[k480] > 0.05 || S.core[k480] > 0.5) continue;                                // 已判成淺色區塊（含交界）的不算
      Ls.push(L[i]); As.push(A[i]); Bs.push(B[i]);                        // 原始像素：模糊後的顏色會混進淺色區塊
    }
    const pct = (v, q) => v.sort((a, b) => a - b)[Math.min(v.length - 1, Math.floor(v.length * q))];
    // 亮度取外圍「受光的」七成位、顏色取中位數（：中位數混進臉側陰影，
    // 最大塊蓋在臉中央 T 字卻填成 44.3，比有色素皮膚中位數 47.2 還暗；camouflage.js 早先也是取受光的一半）
    p.ring = Ls.length >= 30 ? [pct(Ls, RING_L_PCT), pct(As, 0.5), pct(Bs, 0.5)] : S.normal.slice();     // 周圍取不到就用整體的原本膚色
    p.ringN = Ls.length;
    return p.ring;
  }

  // 渲染：coverage 0–1、feather 羽化（渲染像素）、corrector 校色層、keepLight 保留明暗（只遮一部分亮度）
  // 把一塊的目標色填進 tL/tA/tB（區塊＋外擴到羽化範圍）；只讀這塊的外框範圍，不整張讀畫布（區塊多時差十倍）
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

  // 只在 mask 範圍內做模糊（加權平均）：範圍外的顏色不混進來
  function normBlur(arr, mask, rad) {
    const { W, H } = S, n = W * H, am = new Float32Array(n);
    for (let i = 0; i < n; i++) am[i] = arr[i] * mask[i];
    const num = boxBlur(am, W, H, rad), den = boxBlur(mask, W, H, rad), out = new Float32Array(n);
    for (let i = 0; i < n; i++) out[i] = den[i] > 1e-3 ? num[i] / den[i] : arr[i];
    return out;
  }
  // 目標色平滑：每塊各自一個目標色，相鄰兩塊交界會跳一階 → 遮完出現塊狀硬邊。
  // 只在有目標色的範圍內模糊，交界變成漸層；範圍外維持整體原本膚色。
  function smoothTargets(tL, tA, tB, wm, rad) {
    const sL = normBlur(tL, wm, rad), sA = normBlur(tA, wm, rad), sB = normBlur(tB, wm, rad);
    for (let i = 0; i < tL.length; i++) if (wm[i] > 0) { tL[i] = sL[i]; tA[i] = sA[i]; tB[i] = sB[i]; }
  }
  // 「保留光澤」：亮度改用「區塊內大範圍平均」和一般低頻混合當底色 → 額頭、顴骨那片大範圍的亮光不會被當成底色抹平
  // 只在區塊內平均，外圍深膚色不混進來（不會出淺色邊）。
  const shineLow = (low, cur, mask, s, wideR) => {
    if (s <= 0) return low;
    const wide = normBlur(cur, mask, wideR), out = new Float32Array(low.length);
    for (let i = 0; i < low.length; i++) out[i] = mask[i] > 0.02 ? (1 - s) * low[i] + s * wide[i] : low[i];
    return out;
  };

  // 光照連續目標色（泊松／薄膜內插，OpenCV seamlessClone 的核心；本版 OpenCV.js 沒編進 seamlessClone，自己解）：
  // 斑塊內部是未知區，周圍正常皮膚當邊界，解拉普拉斯方程式 → 顏色從四周平滑延伸進來（亮側亮、暗側暗，中間漸變），
  // 不再整塊同一個目標色。頭髮、眉眼、背景不當邊界（自然邊界）。在 1/4 解析度用 SOR 解，再雙線性放大。
  //  貼「泊松融合…把周圍正常皮膚的光照連續性和漸變計算進來」→「應該是可以做到」。
  function membraneTargets(unk, lift) {
    const { W, H, L, A, B, skin, protect } = S, f = 4, cw = Math.ceil(W / f), ch = Math.ceil(H / f), n = cw * ch;
    const sum = [new Float32Array(n), new Float32Array(n), new Float32Array(n)], kn = new Float32Array(n), un = new Float32Array(n), tot = new Float32Array(n);
    for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
      const i = y * W + x, c = ((y / f) | 0) * cw + ((x / f) | 0);
      tot[c]++;
      if (unk[i] > 0.5) { un[c]++; continue; }
      if (skin[i] > 0.9 && protect[i] < 0.1) { kn[c]++; sum[0][c] += L[i]; sum[1][c] += A[i]; sum[2][c] += B[i]; }
    }
    const state = new Int8Array(n);                                  // 1 未知、2 已知（邊界）、0 不參與
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
    // 放大回全解析度（雙線性，只用參與的格子加權；眉眼、頭髮、背景的格子沒有值，不能混進來，否則會出現亮點、橘色條紋）
    const out = [new Float32Array(W * H), new Float32Array(W * H), new Float32Array(W * H), new Uint8Array(W * H)];
    for (let y = 0; y < H; y++) {
      const gy = Math.min(ch - 1, Math.max(0, (y + 0.5) / f - 0.5)), y0 = gy | 0, y1 = Math.min(ch - 1, y0 + 1), fy = gy - y0;
      for (let x = 0; x < W; x++) {
        const gx = Math.min(cw - 1, Math.max(0, (x + 0.5) / f - 0.5)), x0 = gx | 0, x1 = Math.min(cw - 1, x0 + 1), fx = gx - x0, i = y * W + x;
        const cs = [y0 * cw + x0, y0 * cw + x1, y1 * cw + x0, y1 * cw + x1], ws = [(1 - fx) * (1 - fy), fx * (1 - fy), (1 - fx) * fy, fx * fy];
        let wt = 0, v0 = 0, v1 = 0, v2 = 0;
        for (let q = 0; q < 4; q++) { if (!state[cs[q]]) continue; const wq = ws[q]; wt += wq; v0 += F[0][cs[q]] * wq; v1 += F[1][cs[q]] * wq; v2 += F[2][cs[q]] * wq; }
        if (wt < 1e-4) continue;                                      // 周圍都沒有值：沿用原本的目標色
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
    // 目標色：每一塊各自周圍的膚色（外擴到羽化範圍），沒被任何一塊蓋到的地方用整體原本膚色
    const tL = new Float32Array(W * H).fill(S.normal[0]), tA = new Float32Array(W * H).fill(S.normal[1]), tB = new Float32Array(W * H).fill(S.normal[2]);
    g.fillStyle = '#fff'; g.strokeStyle = '#fff'; g.lineJoin = 'round';
    const base = S.patches.filter((p) => !p.round);
    const wm = new Float32Array(W * H);
    for (const p of base) if (p.on && p.ring) paintTarget(g, p, feather, tL, tA, tB, wm);
    smoothTargets(tL, tA, tB, wm, tR);
    const fill = opt.fill || 'membrane';
    // 遮罩：開著的區塊填滿 → 羽化（內部維持 1、邊緣往外漸淡）→ 只在臉上、扣保護區
    g.clearRect(0, 0, W, H);
    for (const p of base) if (p.on) g.fill(new Path2D(pathD(p.pts)));
    const d = g.getImageData(0, 0, W, H).data, raw = new Float32Array(W * H);
    for (let i = 0; i < W * H; i++) raw[i] = d[i * 4 + 3] / 255;
    const bl = boxBlur(raw, W, H, feather);
    if (fill === 'membrane') {
      // 未知區＝斑塊（含所有輪次的區塊）外擴一點，避免邊界取到交界的淺色；lift＝往受光側提亮多少 L*
      const all = new Float32Array(W * H);
      g.clearRect(0, 0, W, H); g.lineWidth = Math.max(4, S.fw * 0.015); g.lineJoin = 'round';
      for (const p of S.patches) if (p.on) { const pa = new Path2D(pathD(p.pts)); g.fill(pa); g.stroke(pa); }
      const da = g.getImageData(0, 0, W, H).data;
      for (let i = 0; i < W * H; i++) all[i] = da[i * 4 + 3] / 255;
      const M = membraneTargets(all, Number(opt.lift ?? 3));
      if (M) for (let i = 0; i < W * H; i++) if (M[3][i] && (wm[i] > 0 || all[i] > 0)) { tL[i] = M[0][i]; tA[i] = M[1][i]; tB[i] = M[2][i]; }
    }
    const kL = keepLight ? 0.6 : 1;                                   // 保留明暗：亮度只往周圍膚色靠六成，中心像受光、不像沒遮到
    // 校色層：越白的地方中和越多（像化妝師只在最白處多點一點校色）；遮瑕越厚，校色透出越少
    const decay = concealer ? 1 - 0.25 * coverage : 1;
    // 多次薄擦：第 1 次整塊上（含校色）；之後每次只補「還看得出差異」的地方，
    // 差多少補多少（殘差 ÷ 6 當權重），已經融合的地方不再動 → 比一次蓋厚自然、臉不會變平。
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
        const cw = corrector && pass === 0 ? m * decay * Math.min(1, Math.max(0.3, (lowL[i] - tL[i]) / 12)) : 0;   // 校色只在第 1 次
        // 只換低頻底色（校色＋遮瑕），高頻紋理（毛孔、細紋）照原圖
        cL[i] += cc * kL * (tL[i] - lL[i]) - cw * 1.5;          // 校色帶一點點壓暗（相乘的效果）
        cA[i] += cc * (tA[i] - lA[i]) + cw * WARM[0];
        cB[i] += cc * (tB[i] - lB[i]) + cw * WARM[1];
      }
    }
    // 第 2 輪起：
    // findResidual 抓到的留白各自是一塊新的 SVG 區塊（round ≥ 1），目標色＝它周圍「已經遮好」的膚色；
    // 每輪在上一輪結果上再蓋一層，差多少補多少（同多次薄擦），一樣保留紋理、扣保護區。
    const maxRound = Math.max(0, ...S.patches.map((p) => p.round || 0));
    for (let k = 1; concealer && k <= maxRound; k++) {      // 只校色（不遮瑕）時不疊
      const list = S.patches.filter((p) => p.round === k && p.on && p.ring);
      if (!list.length) continue;
      g.clearRect(0, 0, W, H);
      const kT = { L: Float32Array.from(tL), A: Float32Array.from(tA), B: Float32Array.from(tB) };
      const wk = new Float32Array(W * H);
      if (fill !== 'membrane') {                                     // 光照連續模式：輪次區塊沿用同一個連續目標色
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
    // 留白：遮完後低頻顏色還比目標亮、色差 > 3 的皮膚；只看已遮區塊和它外圍 4% 臉寬內（不去動臉上天然的高光）
    const fL = boxBlur(cL, W, H, r), fA = boxBlur(cA, W, H, r), fB = boxBlur(cB, W, H, r);
    const near = boxBlur(raw, W, H, Math.max(3, Math.round(S.fw * 0.04)));
    const rL = shineLow(fL, cL, raw, shine, wideR);                  // 判斷留白也用同一個底色：保留的光澤不算留白
    const resid = new Uint8Array(W * H);
    let rN = 0, sN = 0;
    for (let i = 0; i < W * H; i++) {
      if (skin[i] < 0.9 || protect[i] > 0.05) continue;
      sN++;
      if (near[i] < 0.01) continue;
      const dE = Math.hypot(rL[i] - tL[i], fA[i] - tA[i], fB[i] - tB[i]);
      if (dE > 3 && rL[i] > tL[i] + 1.5) { resid[i] = 1; rN++; }
    }
    // 打亮層（opt.highlight 0–1）：臉中央高點整體提亮、稍降飽和（米白），只加低頻、紋理照舊；評分在這之前算，不影響色差
    const hiAmt = Math.min(1, Math.max(0, Number(opt.highlight ?? 0)));
    if (hiAmt > 0 && S.spec && opt.hiMode !== 'spots') {
      // 3D 打亮：核心米白（稍降飽和）、外圍一圈帶微量暖色（光線透進皮膚的透光感）
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
        cL[i] += w * HI_L; cA[i] *= 1 - 0.05 * w; cB[i] *= 1 - 0.08 * w;   // 稍降飽和、偏米白，不是純白
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
    // 結果評分：淺色區塊核心（低頻）和各自周圍膚色的平均色差（Lab ΔE76）
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

  // 抓留白：用目前設定遮一次 → 還看得出差異的地方用 OpenCV 抓輪廓 → 變成下一輪的 SVG 區塊（可開關、可拖點）
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
    cvx.morphologyEx(m, m, cvx.MORPH_OPEN, k);                       // 去掉零星雜點
    cvx.morphologyEx(m, m, cvx.MORPH_CLOSE, k);
    const ke = cvx.getStructuringElement(cvx.MORPH_ELLIPSE, new cvx.Size(5, 5)); cvx.dilate(m, m, ke);
    const excl = new Uint8Array(m.data);                             // 留白（外擴後）：取周圍色時不算
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
