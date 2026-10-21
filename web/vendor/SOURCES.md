# Third-party libraries (web/vendor)

Konva, OpenCV.js and ONNX Runtime Web were downloaded from jsDelivr (original files of the npm packages) on 2026-10-16; the sha256 values were compared with jsDelivr's official hashes. The full licence texts are in each folder's LICENSE (the ONNX Runtime Web package ships no LICENSE file; its package.json says MIT, and the text is at https://github.com/microsoft/onnxruntime/blob/main/LICENSE).

| File | Source | Licence | bytes | sha256 | Matches official |
|---|---|---|---|---|---|
| `konva/konva.min.js` | konva@10.6.0 `/konva.min.js` | MIT | 187,271 | `c03625663b3f4b79c64aecd5671f1a83a37aa2d1e276005b186e42e7d8dba5a1` | yes |
| `konva/LICENSE` | konva@10.6.0 `/LICENSE` | MIT | 1,172 | `7190132c82df30243dc2ba512c9509ea3b629329b4266e9f2c3b5dbd04029613` | yes |
| `opencv/opencv.js` | @techstark/opencv-js@4.12.0-release.1 `/dist/opencv.js` | Apache-2.0 | 10,872,779 | `bd0c3e6448043de04f6a64a12cb7b759f78c3ab8f7c35c9f2e0f71c88bb17103` | yes |
| `opencv/LICENSE` | @techstark/opencv-js@4.12.0-release.1 `/LICENSE` | Apache-2.0 | 11,357 | `c71d239df91726fc519c6eb72d318ec65820627232b2f796219e87dcf35d0ab4` | yes |
| `onnxruntime/ort.wasm.min.js` | onnxruntime-web@1.30.0 `/dist/ort.wasm.min.js` | MIT | 50,196 | `faece07e84faa9001f5104bbe4e5c70861d06cb2376a0dea1fa0ad33a67adc81` | yes |
| `onnxruntime/ort-wasm-simd-threaded.mjs` | onnxruntime-web@1.30.0 `/dist/ort-wasm-simd-threaded.mjs` | MIT | 24,381 | `e13f7f94fc51b4ca72b12faeb1ee95f4ace6dfbc8939bc718aabdc0a27c4299b` | yes |
| `onnxruntime/ort-wasm-simd-threaded.wasm` | onnxruntime-web@1.30.0 `/dist/ort-wasm-simd-threaded.wasm` | MIT | 14,239,897 | `3398c10d07d229bd91b364548e130e0e51a8e5704b88c7c083ebbeb78842dee2` | yes |
| `onnxruntime/package.json` | onnxruntime-web@1.30.0 `/package.json` | MIT | 4,593 | `16f69f755509990539aeb1c8445884960e91adcacab2e8dca8e44ee43601df5c` | yes |

What they are used for: Konva = editable vector paths on the canvas (manual fine-tuning of the lighter-patch boundary); OpenCV.js = contour extraction and vectorization (findContours + approxPolyDP); ONNX Runtime Web = on-phone inference for a segmentation model, should one be added later (there is no model at present).

## YuNet face detection (2026-10-17, used to cross-check face positions against MediaPipe)

| File | Source | Size | sha256 |
|---|---|---|---|
| yunet/face_detection_yunet_2023mar.onnx | https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet (Git LFS) | 232,589 | 8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4 |
| yunet/LICENSE | same (MIT, Copyright (c) 2020 Shiqi Yu) | 1,085 | c83b8120c50ccbd4c4f96edf53141bdd566ebb8f8e9227e415326aa1b1aba958 |

Check: the model's sha256 equals the oid recorded in the GitHub LFS pointer file (oid sha256:8f2383e4…2552fa4, size 232589).
