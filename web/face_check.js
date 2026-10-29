'use strict';
// Cross-check of face landmarks:
// a second, independent face detector, YuNet (OpenCV Zoo, MIT, a YOLO-style single-stage detector: face box + 5 points), runs on the phone (ONNX Runtime Web),
// and its distance is compared (in units of eye distance) with the 5 corresponding MediaPipe face-mesh points (two eye centers, nose tip, two mouth corners).
// If the two models differ too much -> the makeup area may be drawn crooked; prompt the user to retake or drag the points manually. The photo is not uploaded.
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

  // img: HTMLImageElement / Canvas; faceW: estimated face width (original-image pixels, used to scale to the 10-300px faces YuNet handles well)
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
    for (let i = 0; i < n; i++) { data[i] = px[i * 4 + 2]; data[n + i] = px[i * 4 + 1]; data[2 * n + i] = px[i * 4]; }   // BGR, 0-255, NCHW
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

  // det.face: MediaPipe normalized coordinates; returns { ok, level, meanErr, maxErr, score, points } (errors in units of eye distance)
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
    // Thresholds (measured on the demo photo: normal 6.5%; mesh deliberately shifted right by 2% of face width -> 9%, 4% -> 15%): mean < 8% of eye distance = consistent; < 12% = roughly consistent; above = inconsistent
    const level = meanErr < 0.08 ? 'agree' : meanErr < 0.12 ? 'close' : 'disagree';
    return { ok: level !== 'disagree', level, meanErr, maxErr, errs, score: y.score, yunet: y, mediapipe: mp };
  }

  window.FaceCheck = { yunet, crossCheck };
})();
