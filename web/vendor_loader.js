'use strict';
// Third-party libraries: loaded only when needed .
// OpenCV.js (10.9 MB) and ONNX Runtime Web (14 MB) are large and are not downloaded when the app opens; they load on first use,
// and the service worker stores /vendor/ in a separate cache (version pinned), so they work offline afterward. Sources and sha256 are in vendor/SOURCES.md.
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
  function once(key, make) {           // Load each library only once; if it fails, it can be retried next time
    if (!loaded[key]) loaded[key] = make().catch((e) => { loaded[key] = null; throw e; });
    return loaded[key];
  }
  window.Vendor = {
    konva: () => once('konva', () => script('vendor/konva/konva.min.js').then(() => window.Konva)),
    opencv: () => once('cv', async () => {
      await script('vendor/opencv/opencv.js');
      let C = window.cv;
      if (C instanceof Promise) C = await C;            // Some builds return a Promise; an object with its own then must not be awaited (it would wait forever)
      if (!(C && C.Mat)) {
        await new Promise((ok, bad) => {
          let n = 0;
          const t = setInterval(() => {
            if (window.cv && window.cv.Mat) { clearInterval(t); ok(); } else if (++n > 600) { clearInterval(t); bad(new Error('OpenCV 初始化逾時')); }
          }, 50);
        });
      }
      const cvObj = window.cv && window.cv.Mat ? window.cv : C;
      // The OpenCV (Emscripten) object has its own then: returning it directly from an async function treats it as "unfinished work" and waits forever, freezing the whole page (measured 10-16)
      if (cvObj && typeof cvObj.then === 'function') delete cvObj.then;
      return cvObj;
    }),
    ort: () => once('ort', () => script('vendor/onnxruntime/ort.wasm.min.js').then(() => {
      window.ort.env.wasm.wasmPaths = new URL('vendor/onnxruntime/', location.href).href;   // A full URL is required
      window.ort.env.wasm.numThreads = 1;
      return window.ort;
    })),
  };
})();
