'use strict';
// 第三方程式庫：用到才載入。
// OpenCV.js（10.9 MB）、ONNX Runtime Web（14 MB）很大，不在 App 開啟時下載；第一次用到才載入，
// service worker 會把 /vendor/ 存進獨立快取（版本固定），之後離線也能用。來源與 sha256 見 vendor/SOURCES.md。
(function () {
  const loaded = {};
  function script(src) {
    return new Promise((ok, bad) => {
      const s = document.createElement('script');
      s.src = src; s.async = true;
      s.onload = ok; s.onerror = () => bad(new Error(`載入失敗：${src}`));
      document.head.appendChild(s);
    });
  }
  function once(key, make) {           // 同一個程式庫只載一次；失敗了下次可以再試
    if (!loaded[key]) loaded[key] = make().catch((e) => { loaded[key] = null; throw e; });
    return loaded[key];
  }
  window.Vendor = {
    konva: () => once('konva', () => script('vendor/konva/konva.min.js').then(() => window.Konva)),
    opencv: () => once('cv', async () => {
      await script('vendor/opencv/opencv.js');
      let C = window.cv;
      if (C instanceof Promise) C = await C;            // 有的版本給 Promise；自帶 then 的物件不要 await（會無限等待）
      if (!(C && C.Mat)) {
        await new Promise((ok, bad) => {
          let n = 0;
          const t = setInterval(() => {
            if (window.cv && window.cv.Mat) { clearInterval(t); ok(); } else if (++n > 600) { clearInterval(t); bad(new Error('OpenCV 初始化逾時')); }
          }, 50);
        });
      }
      const cvObj = window.cv && window.cv.Mat ? window.cv : C;
      // OpenCV（Emscripten）物件自帶 then：直接從 async 函式回傳會被當成「還沒完成的工作」無限等待、整個頁面卡死（ 實測）
      if (cvObj && typeof cvObj.then === 'function') delete cvObj.then;
      return cvObj;
    }),
    ort: () => once('ort', () => script('vendor/onnxruntime/ort.wasm.min.js').then(() => {
      window.ort.env.wasm.wasmPaths = new URL('vendor/onnxruntime/', location.href).href;   // 要完整網址
      window.ort.env.wasm.numThreads = 1;
      return window.ort;
    })),
  };
})();
