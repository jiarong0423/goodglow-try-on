'use strict';
// 白斑局部遮瑕模擬（在手機上算，照片不上傳、不扣點）。
// 為什麼要自己做：YouCam 試妝的底妝只能全臉均勻上、遮瑕只能套在固定區域，沒辦法指定白斑位置；
// 實際做法（參考影片與遮蓋指南）是先把接近周圍膚色的粉底「點在白斑上」，再全臉推勻。
// 做法：
// 1. 臉部定位（MediaPipe）畫出臉的輪廓，挖掉眼睛和嘴巴。
// 2. 臉上每個像素和兩個取色（原本膚色、白斑）比：比較像白斑、而且比原本膚色亮的，算白斑。
// 3. 白斑範圍去雜點、邊緣羽化。
// 4. 在試妝結果上，把白斑範圍的「低頻底色」換成「試妝後受光的周圍膚色＋一點暖色」，保留高頻紋理與明暗：
//    白斑各處深淺不一（全脫色、半脫色），用整塊平均位移會讓亮的白斑留白、暗的被拉過頭；
//    改成逐處算白斑的局部底色再補到目標色（頻率分離）。暖色補償＝蜜桃／橘校色的作用，避免遮完偏灰。
// 畫面一律標明「手機上模擬的局部遮瑕」。

(function () {
  // MediaPipe Face Mesh 輪廓與五官（點編號）
  const FACE_OVAL = [10, 338, 297, 332, 284, 251, 389, 356, 454, 323, 361, 288, 397, 365, 379, 378, 400, 377, 152, 148,
    176, 149, 150, 136, 172, 58, 132, 93, 234, 127, 162, 21, 54, 103, 67, 109];
  const LEFT_EYE = [33, 7, 163, 144, 145, 153, 154, 155, 133, 173, 157, 158, 159, 160, 161, 246];
  const RIGHT_EYE = [362, 382, 381, 380, 374, 373, 390, 249, 263, 466, 388, 387, 386, 385, 384, 398];
  const LIPS = [61, 146, 91, 181, 84, 17, 314, 405, 321, 375, 291, 409, 270, 269, 267, 0, 37, 39, 40, 185];
  const WORK_W = 480;              // 運算解析度（寬）
  const T_LINE = [151, 9, 168, 6, 197, 195, 5];   // 額頭中央→眉心→鼻樑→鼻頭

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
    // groups：[[點編號…, 是否挖掉]]；回傳 Uint8Array（1＝臉上可遮的皮膚）
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
    // 可分離方框模糊（兩次）＝近似高斯，用來去雜點與羽化
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
   * 判別白斑範圍（在原圖上）：回傳 { mask: Float32Array(0–1，已羽化), w, h, ratio }，失敗回 null。
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
      // 白斑（含半脫色、交界）：比原本膚色亮到「兩色亮度差的三成」以上，或顏色本來就比較像白斑
      //
      const dn = (L - nLab[0]) ** 2 + 2 * ((a - nLab[1]) ** 2 + (b - nLab[2]) ** 2);
      const dp = (L - pLab[0]) ** 2 + 2 * ((a - pLab[1]) ** 2 + (b - pLab[2]) ** 2);
      const lighter = L > nLab[0] + Math.max(4, 0.3 * (pLab[0] - nLab[0]));
      if (lighter || (dp < dn && L > nLab[0] + 3)) { raw[i] = 1; patchN++; }
    }
    const r = Math.max(2, Math.round(w * 0.006));
    let soft = boxBlur(raw, w, h, r);                       // 去雜點：低於一半的孤立點拿掉
    for (let i = 0; i < soft.length; i++) soft[i] = soft[i] > 0.45 && skin[i] ? 1 : 0;
    const core = Float32Array.from(soft);                  // 白斑本體（未外擴）
    if (extend > 0) {
      // 邊界外擴：白斑輪廓往外多蓋 extend 像素（運算解析度），再羽化，消除白斑邊緣那圈亮線（外部建議）
      const grown = boxBlur(soft, w, h, Math.max(1, Math.round(extend)));
      for (let i = 0; i < soft.length; i++) soft[i] = grown[i] > 0.12 && skin[i] ? 1 : 0;
    }
    // 邊緣羽化；blend > 1 時過渡帶加寬，白斑到周圍膚色漸進銜接
    soft = boxBlur(soft, w, h, Math.max(2, Math.round(w * (extend > 0 ? 0.016 : 0.012) * blend)));
    return { mask: soft, core, w, h, ratio: faceN ? patchN / faceN : 0, face };
  }

  /**
   * 在試妝結果上做局部遮瑕：把白斑範圍的局部底色補到周圍膚色（＋暖色），保留紋理。回傳 dataURL。
   * 預設＝V8：V7 參數＋中央加亮改成 T 字形漸進（centerMode 'tband'、+9）。
   *   「目標是自然」；本機量測白斑色差 8.2 → 4.3、鼻樑比外頰亮 13 → 21。
   *   V7 可用 { centerMode: 'gauss' } 重現；V6 可用 { centerMode: 'ellipse', center: 8, warm: [0, 0], extend: 0, darkSpot: 0 } 與強度 0.85 重現。
   * strength：0–1（預設 0.88：色差降到約 5.5–6.5 就好，不硬蓋到 0）
   * opts.center＝臉中央遮完保留比周圍亮多少（L*，預設 9：外部建議建議 +6，但本機量到中央比外頰只亮 12，
   *   目標是 18–20，+9／範圍 0.20 才到；見 <本機紀錄>）；opts.centerMode＝'gauss'（預設：以鼻樑、兩側蘋果肌為中心、
   *   往白斑邊緣遞減到 0）或 'ellipse'（V6：鼻子為中心的橢圓＋T 字帶）。這位使用者的白斑長在天然高光區，
   *   整片拉成外輪廓的深膚色會又平又髒
   * opts.warm＝[Δa*, Δb*] 暖色補償（預設 [1.8, 1.2]，紅多於黃；V5 的 [3, 4] 偏橘髒、V6 的 [0, 0] 偏冷灰）
   * opts.sigma＝中央高斯範圍（臉寬倍數，預設 0.20）；opts.extend＝白斑邊界外擴（原圖像素，預設 10）；opts.darkSpot＝臉頰深色斑提亮（L*，預設 4）
   * opts.blend＝邊緣過渡帶寬度倍數（預設 1）；opts.neckFade＝嘴角以下到下巴，遮瑕權重漸減到多少（預設 1＝不減）
   * opts.fill＝'local'（就近取色：白斑填成附近有色素皮膚的低頻顏色，T 字亮部自然銜接到白斑，往外頰漸深；
   *   附近取不到周圍膚色的大塊白斑才退回整體目標色＋中央加亮）或 'global'（V7：整體目標色＋中央加亮）。
   *   實測這張照片白斑旁邊的皮膚偏深，就近取色會整片變暗，不採用。
   *   'tzone'：量 T 字（額頭中央到鼻頭、下巴）有色素皮膚的實際亮度當亮端、外頰受光膚色當暗端，
   *   白斑依離 T 字的距離在兩端之間漸進填色（opts.tzoneSigma＝範圍，臉寬倍數，預設 0.18）。實測鼻樑本來就亮、
   *   被算進白斑，量到的亮端偏暗，也不採用。
   *   opts.centerMode＝'tband'：中央加亮的權重改用 T 字形（額頭中央到鼻頭＋下巴）往外漸進遞減，加亮量＝opts.center
   * opts.transition＝白斑中心保留多少遮瑕（0–1，預設不設＝整塊同強度）：邊緣維持 strength 接平周圍膚色，
   *   越往白斑中心遮得越少，中心只到 transition 倍，留下較淺的中間色——過渡而不是整塊壓成深色
   *   opts.transitionWidth＝深度模糊半徑（臉寬倍數，預設 0.035）、
   *   opts.transitionStart＝深度多少才開始漸淡（預設 0.5；調高＝邊緣遮滿的帶加寬）
   * opts.split＝顏色和明度分開遮：{ chroma: 0.90, edgeL: strength, centerL: 0.52, rim: 15, falloff: 35,
   *   seam: 8, seamSigma: 4 }（距離單位＝原圖像素）。顏色（a*、b*＋暖色）整塊白斑都遮 chroma；只有明度 L* 從白斑邊緣往內
   *   rim 像素維持 edgeL，再用 falloff 像素的餘弦漸層降到 centerL——中心亮但顏色已是暖膚色，看起來像受光、不像沒遮到。
   *   漸淡只在臉中央（眼下、鼻樑、內頰、下巴）；額角髮際、下顎角的白斑中心照樣遮到 edgeL。
   *   seam＝白斑邊界內外各多少像素做跨邊界平滑（低頻 σ≈seamSigma、保留毛孔），去掉邊界那條淺色細線。
   *   opts.centerWarm＝白斑中心的暖色 [Δa*, Δb*]（預設同 warm；邊緣到中心漸變）。
   *   V10：split.lumaExtend＝明度遮蓋只到原始白斑邊界（設 0；外側比目標色暗的正常皮膚
   *   不動明度，只壓暗比目標亮的像素），顏色仍跟 opts.extend 外擴；split.hfBand／hfKeep＝原始邊界內側 hfBand 像素內，
   *   「像素減低頻」的亮邊只保留 hfKeep 倍（低頻在邊界會混到外側深膚色，差值被當紋理印回來成亮邊）。
   * opts.tWidth＝T 字加亮帶寬（臉寬倍數，預設 0.12；V10 收窄 25% → 0.09）
   * opts.darkSpotAB＝深色斑提亮時一起調的 [Δa*, Δb*]（預設 [0, 0]；V9 建議 [-1, -1]）
   * opts.darkSpotGrow＝深色斑遮罩往 [右, 下] 外擴多少原圖像素（預設不擴；V10 [8, 8]，蓋過下緣暗溝）
   * opts.makeup＝第二段「化妝」：
   *   遮瑕補平之後，對全臉皮膚（不只白斑）做 T 字打亮與外圍修容，往色卡顏色靠、保留紋理：
   *   { hi: '#hex'（T 字目標色，色卡淺一級）, hiAmt: 0–1, co: '#hex'（修容目標色，色卡深一級）, coAmt: 0–1, tWidth: 0.12, coWidth: 0.09（修容帶寬，臉寬倍數）}。
   *   T 字＝額頭中央到鼻頭＋下巴；修容＝臉輪廓內側一圈（太陽穴、顴骨下、下顎），臉中央不碰。
   * opts.mode＝'lowfreq'（預設）或 'mean'（舊做法）
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
    const px2w = det.w / (original.naturalWidth || det.w);   // 原圖像素 → 運算解析度
    const W = result.naturalWidth, H = result.naturalHeight;
    const c = document.createElement('canvas'); c.width = W; c.height = H;
    const g = c.getContext('2d', { willReadFrequently: true });
    g.drawImage(result, 0, 0);
    const img = g.getImageData(0, 0, W, H), d = img.data;
    // 試妝結果裡「白斑範圍」與「周圍皮膚」各自的平均色（在臉上、遠離交界的像素）
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
        if (L < nRef - 25) continue;                           // 周圍皮膚不要算到陰影、眉毛、鬍渣
        normals.push([L, d[i], d[i + 1], d[i + 2]]);
      }
    }
    if (!sum.p[3] || normals.length < 20) return null;
    // 目標色＝試妝後「受光的」周圍膚色（亮度較高的一半）：用整體平均會把陰影算進去，白斑被拉成暗沉一塊
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
      // 低頻底色：只用白斑像素（遮罩加權）做模糊，交界不會混到周圍膚色
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
      // 外擴到周圍皮膚的那一圈：用「全部皮膚」的低頻底色，免得把白斑的位移套到正常皮膚、壓出一圈暗邊
      const sw = new Float32Array(w * h), sch = [0, 1, 2].map(() => new Float32Array(w * h));
      for (let i = 0; i < w * h; i++) { if (skin[i]) { sw[i] = 1; for (let k = 0; k < 3; k++) sch[k][i] = sp[i * 4 + k]; } }
      const bsw = boxBlur(sw, w, h, r), bsc = sch.map((a) => boxBlur(a, w, h, r));
      const coreSoft = boxBlur(det.core, w, h, Math.max(2, Math.round(w * 0.006)));
      // 白斑「深度」：離白斑邊緣越遠越接近 1（大半徑模糊白斑本體，邊緣約 0.5）
      const depthMap = transition === undefined ? null : boxBlur(det.core, w, h, Math.max(4, Math.round(w * (opts.transitionWidth || 0.035))));
      // 就近取色：只用「不是白斑、不是陰影眉毛」的皮膚，兩種半徑模糊出附近膚色的低頻顏色
      let tz = null;
      if (fill === 'tzone' || centerMode === 'tband') {
        // T 字遮罩：額頭中央→鼻頭的粗帶＋下巴；量其中「不是白斑」皮膚的平均色（YouCam T 字打亮也在這裡）
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
          // 往外漸進的權重：T 字帶本身＝1，用多次大半徑模糊做出平滑遞減
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
      // 臉中央權重：鼻子為中心的橢圓（含雙頰內側、眼下、嘴周）＋額頭到鼻頭的 T 字帶，大半徑羽化
      let cm = null, lift = [0, 0, 0];
      const f = det.face, P = (i) => [f[i].x * w, f[i].y * h];
      const fw = Math.hypot(P(234)[0] - P(454)[0], P(234)[1] - P(454)[1]);
      if (center > 0 && centerMode === 'gauss') {
        // 以鼻樑（6）、鼻樑中段（195）、兩側蘋果肌（205、425）為中心的高斯權重，再乘「離白斑邊緣多遠」，邊緣遞減到 0
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
      // 臉頰深色斑：兩頰範圍內、不是白斑、比受光膚色暗 10 以上的像素，局部提亮 darkSpot（遮完白斑後黑斑會像髒污）
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
          // 往右、往下外擴（原圖像素 → 運算解析度），蓋過深色斑下緣與白斑交界的暗溝
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
      // V9：白斑內「離邊緣多遠」（運算解析度像素，3-4 倒角距離）＋臉中央權重＋邊界接縫帶
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
        // 臉中央：鼻子為中心的橢圓（眼下、內頰、嘴周、下巴）＋額頭中央到鼻頭的 T 字帶，羽化；額角、下顎角不在內
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
          // 存到 det 給最後一步（全解析度接縫平滑）用
          const rb = Math.max(1, Math.round(split.seam * px2w / 2));
          const cb = boxBlur(det.core, w, h, rb), sIn = boxBlur(skin, w, h, rb * 2);
          seamBand = new Float32Array(w * h);
          for (let i = 0; i < w * h; i++) {
            seamBand[i] = Math.max(0, 1 - Math.abs(2 * cb[i] - 1)) * Math.min(1, Math.max(0, (sIn[i] - 0.9) / 0.1));
          }
          det.seamBand = seamBand;
        }
        if (split.lumaExtend !== null) {
          // 明度只遮到原始白斑邊界：白斑本體小半徑羽化後放大，邊界上已是 1、往外約 2 格內歸零
          const rr = Math.max(1, Math.round(split.lumaExtend * px2w) + 2), cbL = boxBlur(det.core, w, h, rr);
          lumaMask = new Float32Array(w * h);
          for (let i = 0; i < w * h; i++) lumaMask[i] = Math.min(1, 2 * cbL[i]);
        }
        if (split.hfBand > 0 && split.hfKeep < 1) {
          // 原始邊界內側環狀帶（往內 hfBand 像素）
          const rh = Math.max(1, Math.round(split.hfBand * px2w / 2)), ch2 = boxBlur(det.core, w, h, rh);
          hfBand = new Float32Array(w * h);
          for (let i = 0; i < w * h; i++) hfBand[i] = det.core[i] > 0.5 ? Math.max(0, 1 - Math.abs(2 * ch2[i] - 1)) : 0;
        }
      }
      if (opts.makeup) {
        // V11 化妝層的權重（運算解析度）：T 字帶＋下巴、臉輪廓內側一圈
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
            const dep = Math.min(1, Math.max(0, (depthMap[j] - (opts.transitionStart || 0.5)) / 0.35));   // transitionStart 越高，邊緣遮滿的帶越寬
            a *= 1 - (1 - transition) * dep;
          }
          if (neckFade < 1) {
            // 下臉漸減：嘴角下方（17）到下巴（152）之間，權重從 1 漸進降到 neckFade，下顎到脖子不會一刀切成面具
            const t = Math.min(1, Math.max(0, (y / H - f[17].y) / Math.max(1e-3, f[152].y - f[17].y)));
            a *= 1 - (1 - neckFade) * t;
          }
          const cw = cm ? cm[j] : 0, core = coreSoft[j];
          // 就近取色的信心：小半徑附近有足夠周圍膚色就用小半徑，否則大半徑，再不夠就退回整體目標色＋中央加亮
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
            // 顏色整塊遮 chroma；明度從邊緣 edgeL 漸進到中心 centerL（a 已含遮罩羽化與下臉漸減，換算成比例）
            const t = lumT[j], scale = a / strength;
            // V10：明度用「只到原始邊界」的遮罩（a 裡的下臉漸減換算回來：a / (m × strength)）
            const scaleL = lumaMask ? lumaMask[j] * a / (m * strength) : scale;
            const aL = scaleL * (split.edgeL - (split.edgeL - split.centerL) * t), aC = scale * split.chroma;
            const pl = srgbToLab(d[i], d[i + 1], d[i + 2]), ll = srgbToLab(...lowV), tl = srgbToLab(...tgtV);
            tl[1] += (centerWarm[0] - warm[0]) * t; tl[2] += (centerWarm[1] - warm[1]) * t;
            let L2 = pl[0] + (tl[0] - ll[0]) * aL;
            if (hfBand && hfBand[j] > 0 && pl[0] > ll[0]) L2 -= (1 - split.hfKeep) * hfBand[j] * (pl[0] - ll[0]);   // 邊界內側亮邊只留 hfKeep
            if (lumaMask && core < 0.5) L2 = pl[0] > tl[0] ? Math.max(tl[0], Math.min(pl[0], L2)) : pl[0];       // 邊界外：只壓暗比目標亮的像素
            const o = labToSrgb(L2, pl[1] + (tl[1] - ll[1]) * aC, pl[2] + (tl[2] - ll[2]) * aC);
            d[i] = o[0]; d[i + 1] = o[1]; d[i + 2] = o[2];
          }
        }
      }
    }
    if (split && split.seam > 0 && det.seamBand) {
      // 接縫平滑：邊界帶內把「中低頻」換成 σ≈seamSigma 的模糊結果，保留最細的毛孔紋理（外部建議：海岸線）
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
      // 第二段化妝：低頻底色往 T 字／修容目標色移，像素加同樣位移（紋理不變）
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
