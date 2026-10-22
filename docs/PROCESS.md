# Tools, methods and process

How the project was built during the hackathon, which methods we relied on, what each tool is for, and the friction we hit with the YouCam APIs. Diagrams are in [ARCHITECTURE.md](ARCHITECTURE.md); the command reference is [TOOLS_AND_USAGE.md](TOOLS_AND_USAGE.md).

## Principles

1. **Measure before claiming.** A number in the README or on a page must come from a command anyone can rerun. `tools/repro_region_gap.py` produces the lightness-gap table; `tools/check_claims.py` fails CI when a documented number stops matching the code or data.
2. **Replay real results instead of faking them.** Demo mode shows pictures saved from 80 real Makeup VTO calls (`tools/record_demo_replays.py`, dry run by default, 1 credit per recorded combination), exact for recorded choices and the closest otherwise, and says so on every screen.
3. **Lookup tables over models.** Option ids map to Makeup VTO effects (`app/presets.json`), entry points and looks map to a base set (`app/full_looks.json`), skin tone maps to a card of 10 depth levels by 3 undertones, shades map to reference colors. Every behaviour can be read and tested without a model.
4. **Keep real technique and simulation apart, and list where they differ.** [LAYERS_TECHNIQUE_VS_SIMULATION.md](LAYERS_TECHNIQUE_VS_SIMULATION.md) maps each makeup layer to the real technique and to our simulation, with a verdict (Match, Differs, Partly) read by two reviewers who each looked at one side.
5. **Spend credits only behind a gate.** Every tool that spends credits needs an explicit flag (`--run`) and most also a cap; the server has a per-start call limit and a persistent budget ledger.
6. **Test on the real device.** The camera path was debugged on a phone over wireless adb and Chrome DevTools: open and close cycles, timing, leftover camera tracks, and what the page does after a capture.

## Method, step by step

| Step | What we did | Tool |
|---|---|---|
| 1 | Called each YouCam feature once with a gated probe to learn request shapes and prices | `tools/youcam_probe.py`, `tools/noise_test.py` |
| 2 | Built the skin tone card and the shade ranking, checked CIEDE2000 against the Sharma 2005 reference data | `app/color_engine.py`, `app/skin_card.py`, `tests/test_color_engine.py` |
| 3 | Found that Makeup VTO cannot paint one patch, measured it, and built a local concealing path | `tools/repro_region_gap.py`, `app/patch_detect.py`, `web/makeup_layers.js` |
| 4 | Wrote the layer-by-layer mapping of real technique against simulation and had it read twice | `docs/LAYERS_TECHNIQUE_VS_SIMULATION.md`, `evidence/` |
| 5 | Recorded real results for demo mode | `tools/record_demo_replays.py` |
| 6 | Translated the interface and the server messages through one reviewed table | `tools/extract_ui_strings.py`, `tools/build_i18n.py`, `docs/i18n/zh_en_map.csv` |
| 7 | Hardened the server and wrote a threat model | [THREAT_MODEL.md](../THREAT_MODEL.md) |
| 8 | Tested capture on a phone and fixed what broke | see the friction log |
| 9 | Published a clean single-commit repository, scanned for secrets and brand data, deployed behind a sign-in for live mode | `.github/workflows/ci.yml`; secret scan by hand: `gitleaks detect --source . --redact` (history) and `gitleaks detect --source . --no-git --redact` (tree) |

## Review rounds

Before each release we ran independent reviews in parallel (security, key and API handling, claims against code, wording), compared their findings, fixed each with a test, and ran the whole suite again. A reviewer's report was never taken as proof: the finding was reproduced or the claim recomputed first.

## Friction log (YouCam APIs and tools)

| # | Task | Expected vs actual | Severity | What we did | Suggestion |
|---|---|---|---|---|---|
| 1 | Conceal only one lighter patch with Makeup VTO | **Expected:** an area or mask parameter. **Actual:** the try-on paints the whole face; on our photo whole-face foundation closed the lightness gap by only about 5 to 6 % | High for this use case | Local patch mask and layers, labelled *Simulated* | A region or mask input for Makeup VTO |
| 2 | Auto-capture on a phone | **Expected:** the Camera Kit opens and captures. **Actual:** with another browser tab holding the camera the Kit stays on "getting the camera ready" with no event | Medium | A hint after 10 seconds and an album fallback; checked with 12 open and close cycles on one Android phone | Emit `cameraFailed` when the camera cannot be opened |
| 3 | Take the photo | **Expected:** a manual shutter or a timer option. **Actual:** capture is automatic only and needs "Lighting Good", so a dim room can block it | Medium | Album fallback; the hint tells people to find more light | A manual capture method |
| 4 | Use the captured photo | **Expected:** a photo that meets the size our other tools need (short side 1080 px for HD analysis). **Actual:** about 680 x 1080 on a phone with 1080p video | Medium | Lowered the app's minimum to 640 px short side; Makeup VTO is fed the photo without enlarging it | Document the capture size for each `videoQuality` |
| 5 | Resize rules | **Expected:** one rule. **Actual:** two different wordings for the maximum image size of one feature (hair color: long side below 1920, or width below 1920 and height below 1080) | Low | Resize so both readings are satisfied | One rule per feature in the reference |
| 6 | Wait for a try-on | **Expected:** a quick answer. **Actual:** about 20 seconds per Makeup VTO call in our run | Low | Poll with a visible wait state; demo mode replays saved results | A faster preview tier |
| 7 | Count credits | **Expected:** the balance visible per request. **Actual:** the balance is read with a separate call | Low | A local ledger that reserves before a call and records on success | Return the remaining balance in each response |

## What did not work

- Splitting patches by brightness alone mistook shadow for skin; chroma divided by lightness worked on one photo and failed on another, so the rule uses an absolute chroma threshold.
- A fixed light brow pencil lightened dark brows; following the user's own brow color removed that.
- A first version of the capture helper cropped the photo so tightly that the result fell below the minimum size and blocked the next step; the crop is now skipped when it would.
- Browser speech synthesis for spoken guidance was unreliable across devices and languages, so it was removed; recorded audio is the planned replacement.
