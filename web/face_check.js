'use strict';
// 臉部定位交叉核對：
// 第二個、獨立的臉部偵測器 YuNet（OpenCV Zoo，MIT，YOLO 類單階段：臉框＋5 點）在手機上跑（ONNX Runtime Web），
// 跟 MediaPipe 臉部網格對應的 5 點（兩眼中心、鼻頭、兩側嘴角）比距離（以眼距為單位）。
// 兩個模型差太多 → 上妝範圍可能畫歪，提示使用者重拍或手動拖點。照片不上傳。
(function () {
  const MODEL = 'vendor/yunet/face_detection_yunet_2023mar.onnx', SIZE = 640, STRIDES = [8, 16, 32];
  let sessionP = null;
  function session() {
    if (!sessionP) {
      sessionP = window.Vendor.ort()
        .then((ort) => ort.InferenceSession.create(MODEL, { executionProviders: ['wasm'] }).then((s) => ({ ort, s })))
        .catch((e) => { sessionP = null; throw e; });
    }
    return sessionP;
  }

  // img：HTMLImageElement／Canvas；faceW：預估臉寬（原圖像素，用來縮放到 YuNet 擅長的 10–300px 臉）
  async function yunet(img, faceW) {
    const { ort, s } = await session();
    const iw = img.naturalWidth || img.width, ih = img.naturalHeight || img.height;
    let sc = SIZE / Math.max(iw, ih);
    if (faceW) sc = Math.min(sc, 220 / faceW);
    const c = document.createElement('canvas'); c.width = SIZE; c.height = SIZE;
    const g = c.getContext('2d', { willReadFrequently: true });
    g.fillStyle = '#000'; g.fillRect(0, 0, SIZE, SIZE);
    g.drawImage(img, 0, 0, iw * sc, ih * sc);
    const px = g.getImageData(0, 0, SIZE, SIZE).data, n = SIZE * SIZE, data = new Float32Array(3 * n);
    for (let i = 0; i < n; i++) { data[i] = px[i * 4 + 2]; data[n + i] = px[i * 4 + 1]; data[2 * n + i] = px[i * 4]; }   // BGR、0–255、NCHW
    const out = await s.run({ input: new ort.Tensor('float32', data, [1, 3, SIZE, SIZE]) });
    let best = null;
    STRIDES.forEach((st) => {
      const cols = SIZE / st, rows = SIZE / st, cls = out[`cls_${st}`].data, obj = out[`obj_${st}`].data;
      const bb = out[`bbox_${st}`].data, kp = out[`kps_${st}`].data;
      for (let r = 0; r < rows; r++) for (let col = 0; col < cols; col++) {
        const k = r * cols + col;
        const score = Math.sqrt(Math.min(1, Math.max(0, cls[k])) * Math.min(1, Math.max(0, obj[k])));
        if (best && score <= best.score) continue;
        const cx = (col + bb[k * 4]) * st, cy = (r + bb[k * 4 + 1]) * st, w = Math.exp(bb[k * 4 + 2]) * st, h = Math.exp(bb[k * 4 + 3]) * st;
        const kps = [];
        for (let q = 0; q < 5; q++) kps.push([(kp[k * 10 + 2 * q] + col) * st / sc, (kp[k * 10 + 2 * q + 1] + r) * st / sc]);
        best = { score, box: [(cx - w / 2) / sc, (cy - h / 2) / sc, w / sc, h / sc], kps };
      }
    });
    return best;
  }

  // det.face：MediaPipe 正規化座標；回傳 { ok, level, meanErr, maxErr, score, points }（誤差以眼距為單位）
  async function crossCheck(img, face) {
    const iw = img.naturalWidth || img.width, ih = img.naturalHeight || img.height;
    const P = (i) => [face[i].x * iw, face[i].y * ih];
    const avg = (ids) => { const ps = ids.map(P); return [ps.reduce((a, p) => a + p[0], 0) / ps.length, ps.reduce((a, p) => a + p[1], 0) / ps.length]; };
    const eyeA = face.length > 473 ? P(468) : avg([33, 133]), eyeB = face.length > 473 ? P(473) : avg([362, 263]);
    const mp = { eyes: [eyeA, eyeB].sort((u, v) => u[0] - v[0]), nose: P(1), mouth: [P(61), P(291)].sort((u, v) => u[0] - v[0]) };
    const ow = mp.eyes[1][0] - mp.eyes[0][0], iod = Math.hypot(ow, mp.eyes[1][1] - mp.eyes[0][1]);
    const xs = face.map((p) => p.x * iw);
    const y = await yunet(img, Math.max(...xs) - Math.min(...xs));
    if (!y || y.score < 0.5) return { ok: false, level: 'noface', score: y ? y.score : 0 };
    const ye = [y.kps[0], y.kps[1]].sort((u, v) => u[0] - v[0]), ym = [y.kps[3], y.kps[4]].sort((u, v) => u[0] - v[0]);
    const pairs = [[mp.eyes[0], ye[0]], [mp.eyes[1], ye[1]], [mp.nose, y.kps[2]], [mp.mouth[0], ym[0]], [mp.mouth[1], ym[1]]];
    const errs = pairs.map(([a, b]) => Math.hypot(a[0] - b[0], a[1] - b[1]) / iod);
    const meanErr = errs.reduce((a, b) => a + b, 0) / errs.length, maxErr = Math.max(...errs);
    // 門檻（示範照實測：正常 6.5%；網格故意右移 2% 臉寬 → 9%、4% → 15%）：平均 < 8% 眼距＝一致；< 12%＝大致一致；以上＝不一致
    const level = meanErr < 0.08 ? 'agree' : meanErr < 0.12 ? 'close' : 'disagree';
    return { ok: level !== 'disagree', level, meanErr, maxErr, errs, score: y.score, yunet: y, mediapipe: mp };
  }

  window.FaceCheck = { yunet, crossCheck };
})();
