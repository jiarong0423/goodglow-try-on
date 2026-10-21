'use strict';
// 化妝塗層模式（
// 「svg 其實就是面部上妝範圍」「opencv 是視覺定位」→ 核准 8 層設計）。
// 分工：OpenCV／MediaPipe 定位（camo_editor.js 的淺色區塊輪廓、臉部網格、分割）→ 每樣產品的上妝範圍（SVG path）
// → Canvas 照化妝順序一層一層塗（產品顏色＝推薦的實際色號）。不改五官、不生成像素。
// 層次與數字：美妝教學（白斑遮瑕：校色只點淺色區塊、遮瑕從中央往外推、只在看得出的地方補第二層、拍按不搓、蜜粉按壓；
// 打亮：顴骨上緣、鼻樑停在鼻頭前；修容：顴骨下緣、下顎、鼻側）＋外部建議 建議的不透明度與網格點。
// iPhone Safari 不支援 ctx.filter：羽化一律用方框模糊算好透明度，再用 globalCompositeOperation 疊色。
(function () {
  const LAYERS = [
    { id: 'corrector', name: __t('s0735', '校色'), product: __t('s0735', '校色') },
    { id: 'conceal', name: __t('s0736', '遮瑕'), product: __t('s0736', '遮瑕') },
    { id: 'conceal2', name: __t('s0737', '遮瑕第二層'), product: __t('s0736', '遮瑕') },
    { id: 'edge', name: __t('s0738', '修邊'), product: __t('s0739', '交界') },
    { id: 'foundation', name: __t('s0740', '粉底'), product: __t('s0740', '粉底') },
    { id: 'powder', name: __t('s0741', '蜜粉'), product: __t('s0741', '蜜粉') },
    // 補妝：上完底妝後重看一次，
    // 哪裡還看得出淺色（比周圍亮、比周圍不飽和）就只在那裡點同一支遮瑕、拍開邊緣；少量多次，每次重新找。
    // 依據：化妝師 pinpoint concealing（只點在瑕疵上、拍按、邊緣推到看不見）；美妝教學「一層遮不住就少量多次補第二層」；
    // 白斑遮蓋指南（交界對比最明顯，邊緣要特別處理）
    { id: 'touch1', name: __t('s0742', '補妝1'), product: __t('s0736', '遮瑕') },
    { id: 'touch2', name: __t('s0743', '補妝2'), product: __t('s0736', '遮瑕') },
    { id: 'touch3', name: __t('s0744', '補妝3'), product: __t('s0736', '遮瑕') },
    { id: 'contour', name: __t('s0160', '修容'), product: __t('s0160', '修容') },
    { id: 'highlight', name: __t('s0092', '打亮'), product: __t('s0092', '打亮') },
    { id: 'contour_fine', name: __t('s0745', '細修容'), product: __t('s0160', '修容') },          // 小範圍明暗：鼻側、眼窩、下唇下（3D 網格受光的細部）
    { id: 'highlight_fine', name: __t('s0746', '細打亮'), product: __t('s0092', '打亮') },
  ];
  // 每層不透明度（再乘上「濃淡」滑桿，預設 60%）。核准 A＋D：照 tools/fit_layers_to_target.py 對目標妝容的擬合
  // （遮瑕減、修容加重、加細修容；校色與修邊擬合為 0），換算到滑桿 60% 後，再用 tools/measure_layer_render.py 在瀏覽器實測調整：
  // 白斑示範照對目標圖的低頻色差中位數 6.23（舊值）→ 5.56（全臉）、7.99 → 6.76（淺色區塊）。細打亮實測會變差，先設 0。
  // 舊值（ 前）：校色 0.5、遮瑕 0.72、第二層 0.35、修邊 0.38、粉底 0.28、蜜粉 0.15、修容 0.2、打亮 0.2
  const FINE_GAIN = 0.1;                                             // 受光起伏多少算「滿」（diffuse 0–1；示範照 p10／p90 約 ±0.08）
  const OPACITY = { corrector: 0, conceal: 0.33, conceal2: 0.67, edge: 0, foundation: 0.5, powder: 0.15,
    contour: 0.75, highlight: 0.5, contour_fine: 0.3, highlight_fine: 0,
    // 補妝 瀏覽器實測（白斑示範照、擬合色號）：淺色區塊「可見度」（和周圍正常皮膚的色差）中位 7.81 → 4.74、p90 15.96 → 11.13；
    // 區塊 L* 52.7 → 49.6（目標 50.8）。0.3／0.5 較淡（5.69／5.27）
    touch1: 0.8, touch2: 0.8, touch3: 0.8 };
  // 修容、打亮的範圍：MediaPipe 網格點
  const CONTOUR = [[234, 93, 132, 58, 216, 207, 187, 50], [454, 323, 361, 288, 436, 427, 411, 280],           // 顴骨下緣（頰凹上方）
    [132, 58, 172, 136, 150, 149, 176, 148, 152], [361, 288, 397, 365, 379, 378, 400, 377, 152],               // 下顎線內側
    [193, 122, 196, 3, 51], [417, 351, 419, 248, 281]];                                                        // 鼻側
  const HL_CHEEK = [[116, 123, 147, 118, 101, 50], [345, 352, 376, 347, 330, 280]];
  const HL_NOSE_LINE = [168, 6, 197, 195, 5];                       // 鼻樑，停在鼻頭前
  const HL_NOSE_TIP = [4, 1];
  const HL_FOREHEAD = [10, 151, 9, 107, 336];
  const T_ZONE = [10, 109, 67, 107, 9, 336, 297, 338, 151, 168, 6, 197, 195, 5, 4];
  const SOFT_SPOTS = [33, 133, 362, 263, 64, 294, 61, 291];         // 眼角、鼻翼、嘴角：會動、容易卡粉 → 塗層減半

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
  function hull(pts) {                                               // 凸包（Andrew monotone chain）
    const p = pts.slice().sort((a, b) => a[0] - b[0] || a[1] - b[1]);
    const cross = (o, a, b) => (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]);
    const lo = [], up = [];
    for (const q of p) { while (lo.length >= 2 && cross(lo[lo.length - 2], lo[lo.length - 1], q) <= 0) lo.pop(); lo.push(q); }
    for (const q of p.slice().reverse()) { while (up.length >= 2 && cross(up[up.length - 2], up[up.length - 1], q) <= 0) up.pop(); up.push(q); }
    return lo.slice(0, -1).concat(up.slice(0, -1));
  }
  // 海綿拍按的顆粒：128×128 重複圖樣，70% 細孔＋30% 較大的不規則氣孔，透明度 0.65–1（固定種子，每次一樣）
  let NOISE = null;
  const DBG = {};                                                    // 量測用：最近一次 render 的補妝範圍（tools/measure_layer_render.py 對照）
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

  const MASK_FEATHER = 0.018;      // 伺服器遮罩的羽化（臉寬比例）： 柔邊對照 E4＝0.6%×3，額頭地圖線、下巴描邊最少
  function regionOf(S, Z) {          // 上妝範圍：臉部可上妝範圍＋脖子（MediaPipe 分割的身體皮膚）
    const n = S.W * S.H, region = new Float32Array(n);
    for (let i = 0; i < n; i++) {
      const body = S.segCls && S.segCls[i] === 2 && S.protect[i] < 0.1 && !(S.hairBg && S.hairBg[i] > 0.3) ? 1 : 0;
      region[i] = Math.max(Z.ok[i], body * (S.skin[i] > 0 || body ? 1 : 0));
    }
    return region;
  }

  // 範圍（每次 render 依目前區塊重算；結果快取到 Z）
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
    // 可上妝：皮膚、扣眉眼唇（protect），眼角鼻翼嘴角減半
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
  // 把一層（顏色＋透明度圖）用指定混合方式疊到畫布上。
  // lin：產品在「這張照片的光線下」的顏色（線性 RGB）；sh：每個位置的受光（1＝平均）→ 塗層跟著臉上原本的明暗變亮變暗
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

  // opt：{ colors:{corrector,concealer,edge,foundation,contour,highlight,normal}, upTo: 層 id（畫到這層為止）, off:{層 id:true}, strength:0–1.5 }
  // 每一層＝半透明薄膜（ 實測：產品顏色直接蓋上去，整張臉變成平平的面具，光影、毛孔都不見）：
  // 真的粉底、遮瑕是薄膜，皮膚原本的明暗與紋理會透出來 → 每層只把「範圍內的底色」往產品顏色推（推多少＝該層不透明度），
  // 紋理照舊、越厚稍微淡一點；產品顏色先換算成「在這張照片的光線下」的樣子，再跟著臉上原本的明暗變亮變暗
  async function render(opt) {
    const S = window.CamoEditor._debug(), CE = window.CamoEditor;
    if (!S) throw new Error(__t('s0747', '先找淺色區塊'));
    const { W, H, fw, spec } = S, col = opt.colors, k = opt.strength ?? 1, n = W * H;
    const Z = zones(S, opt), NZ = noise();
    const sponge = new Float32Array(n);
    for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) sponge[y * W + x] = NZ.a[(y % NZ.n) * NZ.n + (x % NZ.n)];
    // 試色在這個光線下：推薦是在「校正後的膚色」（col.normal）上比對色號的 → 照片裡實際膚色 − 校正後膚色＝這張照片的光線換算。
    // 照片裡實際膚色＝淺色區塊以外、正常皮膚的低頻顏色中位數（ 修：原本誤用「校正後膚色 − 粉底」當換算，
    // 等於假設粉底剛好等於本人膚色，產品全部被換算得偏紅偏黃 → 整臉橘、像假人）
    const sc = S.det.w / W, pl = [], pa = [], pb = [];
    for (let i = 0; i < n; i += 3) {
      if (S.skin[i] < 0.95 || S.protect[i] > 0.05) continue;
      const kk = Math.min(S.det.h - 1, Math.floor(Math.floor(i / W) * sc)) * S.det.w + Math.min(S.det.w - 1, Math.floor((i % W) * sc));
      if (S.core[kk] > 0.5) continue;
      pl.push(S.lowL[i]); pa.push(S.lowA[i]); pb.push(S.lowB[i]);
    }
    const med = (v) => (v.sort((x, y) => x - y), v[v.length >> 1]);
    const nLab = pl.length > 100 ? [med(pl), med(pa), med(pb)] : S.normal.slice();     // 照片裡的正常膚色
    const corr = CE.hexToLab(col.normal), off = nLab.map((v, j) => v - corr[j]);
    const cal = (hex) => CE.hexToLab(hex).map((v, j) => v + off[j]);
    // 遮瑕／修邊：美妝教學「一支對不準就兩支混」→ 從候選色號挑最接近照片膚色的一支或兩支混合（線性 RGB 混色、每 10% 一格）
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
    // 受光：周圍正常皮膚的明暗用泊松方式（薄膜內插）連續延伸到淺色區塊上；區塊外就是原本的低頻明暗
    const unk = new Float32Array(n), grow = boxBlur(Z.fillAll, W, H, fw * 0.015);
    for (let i = 0; i < n; i++) unk[i] = grow[i] > 0.02 ? 1 : 0;
    const M = CE.membrane(unk, opt.lift ?? 3), light = new Float32Array(n);   // lift：往受光側提亮（遮完不顯暗； 調亮 2 → 3）
    for (let i = 0; i < n; i++) light[i] = (M && M[3][i] && unk[i]) ? M[0][i] : S.lowL[i];
    // 淺色區塊裡加回臉型的明暗：周圍延伸過來的受光是平滑的，
    // 鼻子整個在區塊裡時，鼻樑亮面、鼻側陰影會被抹平 → 用 3D 臉部網格的受光（臉型決定，不受白斑本身亮度影響），
    // 減掉大範圍平均只留「形狀」的起伏，換算成 L* 加回去
    if (spec && spec.diffuse) {
      const dw = boxBlur(spec.diffuse, W, H, fw * 0.08), gainL = opt.shape ?? 32;
      // 網格邊緣（額頭上方、臉側）漸漸淡掉，不然在網格最上緣會出現一條橫線（ 實測）
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
    // 薄膜的「底色／紋理」分開：淺色區塊內外分開模糊，交界算底色（蓋得到），不算紋理（ A＋D）
    // 有伺服器算的淺色遮罩（opt.patchMask， 採用 E4）時不分界：分界會沿舊外框在額頭留下地圖線（隔離區柔邊對照 E0～E4）
    const lowOf = opt.patchMask ? (a) => boxBlur(a, W, H, r) : CE.splitBlur ? CE.splitBlur(Z.fillAll, r) : (a) => boxBlur(a, W, H, r);
    // 一層薄膜：alpha＝每個像素的不透明度；prod＝換算後的產品 Lab；mode：'film' 蓋色、'deepen' 修容（只往深）、'glow' 打亮（只往亮）
    const bright = opt.brighten ?? 3;                                  // 整體提亮（L*）：像選半號亮一點的粉底／提亮妝前乳
    // abRef：逐像素的顏色目標（補妝用「周圍已上好妝的皮膚」的 a/b，不用遮瑕本身的黃； 方案 B）
    function film(alpha, prod, mode, up = 0, lt = light, abRef = null) {
      const lL = lowOf(cL), lA = lowOf(cA), lB = lowOf(cB);
      for (let i = 0; i < n; i++) {
        const a = Math.min(1, alpha[i]);
        if (a < 0.003) continue;
        const tL = prod[0] + up + (lt[i] - nLab[0]);                    // 產品顏色跟著這個位置的受光變亮變暗
        let dL = tL - lL[i];
        if (mode === 'deepen') dL = Math.min(0, dL);
        if (mode === 'glow') dL = Math.max(0, dL);
        const keep = 1 - 0.1 * a;                                      // 塗得越厚，毛孔細紋稍微淡一點（「像假人、下手太重」：0.3 → 0.1）
        const dl = cL[i] - lL[i], da = cA[i] - lA[i], db = cB[i] - lB[i];
        cL[i] = lL[i] + a * dL + dl * keep;
        const pA = abRef ? abRef[0][i] : prod[1], pB = abRef ? abRef[1][i] : prod[2];
        cA[i] = lA[i] + a * (pA - lA[i]) * (mode === 'glow' ? 0.4 : 1) + da * keep;
        cB[i] = lB[i] + a * (pB - lB[i]) * (mode === 'glow' ? 0.4 : 1) + db * keep;
      }
    }
    // 補妝：找「還看得出來」的淺色——目前的底色比周圍皮膚亮、彩度低（缺色素的樣子），範圍＝臉＋脖子。
    // 周圍皮膚＝排除候選後的大範圍平均（兩輪）；補上去的亮度跟著周圍「已經上好妝的皮膚」，不是跟著淺色區塊本身。
    const region = regionOf(S, Z);
    DBG.region = region; DBG.touch = [];
    function touchUp(op) {
      if (!op) return null;
      // 看得出來的尺度：0.8% 臉寬（小斑點也抓得到；2% 會漏掉臉頰的細碎淺點， 離線比對）
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
      // 點在中央、邊緣往外拍開（羽化 1% 臉寬）；海綿顆粒
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
        // 只點在淺色區塊上、往內縮 0.5% 臉寬（不讓橘色沾到正常皮膚）：壓掉死白、補暖
        const er = boxBlur(Z.fill, W, H, fw * 0.005), m = new Float32Array(n);
        for (let i = 0; i < n; i++) m[i] = er[i] > 0.85 ? 1 : 0;
        const f = boxBlur(m, W, H, fw * 0.015);
        film(A((i) => f[i] * OP.corrector), cal(col.corrector), 'film');
      } else if (id === 'conceal' && col.concealer) {
        // 從中央往外推：中央最實（約 0.72）、邊界約 0.3、超出邊界 2% 臉寬內淡掉；海綿拍按的顆粒
        const d = opt.patchMask ? boxBlur(opt.patchMask, W, H, fw * MASK_FEATHER) : boxBlur(Z.fill, W, H, fw * 0.02);
        film(A((i) => OP.conceal * Math.min(1, d[i] * (opt.patchMask ? 1.3 : 1)) ** 1.2 * sponge[i]), concLab, 'film', bright);
      } else if (id === 'conceal2' && col.concealer) {
        // 第二層：重看一次，只補「還看得出來」的地方（底色比該處受光的正常膚色亮 3 以上，且在淺色區塊附近）
        const lL = lowOf(cL), near = boxBlur(Z.fillAll, W, H, fw * 0.03), m = new Float32Array(n);
        let cnt = 0;
        for (let i = 0; i < n; i++) if (near[i] > 0.05 && lL[i] > light[i] + 3) { m[i] = Math.min(1, (lL[i] - light[i] - 3) / 6); cnt++; }
        const f = boxBlur(m, W, H, fw * 0.012);
        film(A((i) => OP.conceal2 * Math.min(1, f[i] * 1.3) * sponge[i]), concLab, 'film', bright);
        done.push({ id, area: cnt / n });
        continue;
      } else if (id === 'edge' && col.edge) {
        // 交界內外各 1.5% 臉寬的一圈，用交界色號往外拍開
        const f = boxBlur(Z.ring, W, H, fw * 0.015);
        film(A((i) => OP.edge * Math.min(1, f[i] * 1.4) * sponge[i]), edgeLab, 'film', bright);
      } else if (id === 'foundation' && col.foundation) {
        // 全臉很薄一層統一色調；淺色區塊中央減半（已經有遮瑕，避免太厚）。
        // 美妝教學：粉底要往外推到下顎線、脖子、髮際、耳前，越外越薄，臉和脖子才不會有一條交界
        const d = boxBlur(Z.fill, W, H, fw * 0.02), seg = S.segCls, hair = S.hairBg;
        const near = boxBlur(S.skin, W, H, fw * 0.12), a = new Float32Array(n);
        const jawY = Math.max(...[172, 136, 150, 149, 176, 148, 152, 377, 400, 378, 379, 365, 397].map((q) => S.det.face[q].y * H));
        for (let i = 0; i < n; i++) {
          const inFace = Z.ok[i], face = OP.foundation * (1 - 0.5 * Math.min(1, d[i])) * inFace;
          let out = 0;
          if (seg && (seg[i] === 2 || seg[i] === 3) && S.protect[i] < 0.1) {
            // 臉以外的皮膚（脖子、耳朵、髮際）：離臉越遠越薄；脖子往下最多推到下顎線下方 25% 臉寬
            const y = (i / W) | 0, fade = y > jawY ? Math.max(0, 1 - (y - jawY) / (fw * 0.45)) : 1;
            out = OP.foundation * Math.min(1, near[i] * 2.5) * fade * (1 - (hair ? Math.min(1, hair[i] * 1.5) : 0));
          }
          a[i] = Math.max(face, out * (1 - inFace)) * k;
        }
        film(a, foundLab, 'film', bright);
      } else if (id === 'powder' && col.foundation) {
        // 透明蜜粉按壓：淺色區塊＋T 字，壓掉油亮、細紋柔一點（往粉底色靠、很淡）
        const u = new Float32Array(n);
        for (let i = 0; i < n; i++) u[i] = Math.max(Z.fill[i], Z.tzone[i]);
        const f = boxBlur(u, W, H, fw * 0.03);
        film(A((i) => OP.powder * f[i]), foundLab, 'film');
      } else if (id.startsWith('touch') && col.concealer) {
        let t = touchUp(OP[id]);
        // 有遮罩時：補妝 1 照伺服器遮罩點，補妝 3 照「上完妝後再抓一次」的遮罩（opt.patchMask2，二次抓取）
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
        // 顴骨下緣、下顎線、鼻側：只往深，邊緣大幅推開（7% 臉寬）
        const f = boxBlur(Z.contour, W, H, fw * 0.07);
        film(A((i) => OP.contour * Math.min(1, f[i] * 1.6)), cal(col.contour), 'deepen');
      } else if (id === 'highlight' && col.highlight) {
        // 顴骨上緣、鼻樑（停在鼻頭前）、鼻頭一點、額頭中央淡淡；強弱再乘 3D 網格的受光（高的地方才亮）
        const u = new Float32Array(n);
        for (let i = 0; i < n; i++) u[i] = Math.max(Z.hlCheek[i] * 0.8, Z.hlNose[i], Z.hlFore[i] * 0.7);
        const f = boxBlur(u, W, H, fw * 0.045), lt = spec ? spec.wide : null;
        film(A((i) => OP.highlight * Math.min(1, f[i] * 1.5) * (lt ? 0.4 + 0.6 * lt[i] : 1)), cal(lighten(col.highlight, 0.35)), 'glow');
      } else if ((id === 'contour_fine' || id === 'highlight_fine') && spec && spec.diffuse && (id === 'contour_fine' ? col.contour : col.highlight)) {
        // 細修容／細打亮：3D 臉部網格受光的「小範圍起伏」＝受光 − 大範圍平均（鼻側、眼窩、下唇下偏暗；鼻樑、眉骨、下巴偏亮）。
        // 對目標妝容的擬合：大範圍修容之外，小範圍明暗也要加深才像；瀏覽器實測細修容 0.3 最好、細打亮會變差
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
    // 高解析度輸出（核准送官方試妝前：「先把化妝塗層版改成原圖解析度輸出」）：
    // 塗層在 720 寬算好，改變量（妝後 − 原圖）幾乎都是平滑的底色變化 → 雙線性放大後疊回原圖，原圖的毛孔細節保留在高解析度
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

  // 從推薦清單挑「換算到這張照片的光線後，最接近周圍正常膚色」的一支（美妝教學：遮瑕對淺色區塊周圍的膚色）。
  // 實測：清單第一支換算後比本人膚色暗 4、偏紅（L 54／a* 24 對 58／18），第二支換算後＝本人膚色
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

  // 每層的上妝範圍（畫在編輯器上看用）：回傳 { id: 透明度圖 }
  function zoneMaps() { const S = window.CamoEditor._debug(); return S ? zones(S, {}) : null; }

  // 上妝範圍（臉＋脖子，已扣眼眉唇）：送給伺服器 /api/patch-mask 當偵測範圍
  function regionMask() { const S = window.CamoEditor._debug(); return S ? regionOf(S, zones(S, {})) : null; }

  window.MakeupLayers = { LAYERS, render, zoneMaps, regionMask, pickClosest, _debug: () => DBG };
})();
