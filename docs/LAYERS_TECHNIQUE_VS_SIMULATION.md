# Layers: real application technique vs. how we simulate it

Built from our own run records and code, cross-read by two independent reviewers (one for real technique, one for the simulation), with differences listed on purpose. No brand, product or shade names.

**How to read it.** *Match* = the simulation follows the technique. *Differs* = the simulation does something else (the useful rows). Tags: V = read in code or in a run report, U = not verified.
The simulation exists in three places: (1) an **offline layer engine** (pre-computed frames shown on the GitHub evidence page (`evidence/`); not part of the public online version), (2) a lighter **in-browser version** (`web/makeup_layers.js` plus `app/patch_detect.py`, runs on the user's own photo, no YouCam call), and (3) **YouCam Makeup VTO**, which replaces some layers in the final images. Only one public source on technique is peer-reviewed (cosmetic camouflage for vitiligo, Indian J Dermatol 2010); the rest are our own notes and consumer beauty guides, so treat "real technique" as practice, not as evidence.

## Example 1 (very light skin), offline engine

| Layer | Real technique | Our simulation | Result |
|---|---|---|---|
| `base_thin` | Thin even base, strongest at face centre, faded at the edges | Blend with opacity cap 0.22 (0.30 in the second very-light-skin example), edges faded; no sponge-pressed texture | Match |
| `blemish` | Dab concealer only on small spots, pat the edges | Each spot takes the colour of the skin around it (not a concealer shade), cap 0.30; a cheek low-contrast smoothing comes with it (a side effect real dabbing does not have) | Differs (side effect) |
| `under_eye` | Peach corrector first, then concealer, avoiding outer-corner creases | No corrector layer: lightness pulled 70% toward the cheek, plus a thin concealer colour | Differs (order, tool) |
| `tear_trough` | Dab only the deepest line, one or two light taps | Low-frequency lightness pulled 75% toward the cheek (a*b* 50%) over a wide area; cannot tell a bony hollow from a shadow, so it can flatten | Partly; risk |
| `contour` | Under cheekbones, temples, jaw angles, blended | Landmark-drawn diagonal band plus Gaussian blur, cool grey-brown (L −10); no powder/cream difference | Match |
| `highlight` | Top of cheekbones, mid-forehead, nose bridge, chin; usually applied last | Matte L +3 lift only (no shimmer); applied **before** blush | Differs (order) |
| `nose_shadow` | Along the sides of the nose, blended out | Two **vertical** parallel lines at the midline ±0.06 face widths, drawn after the nose-bridge highlight | Differs (shape, order) |
| `blush` | Over the cheekbones toward the temples | 15° ellipse, kept off the under-eye area, fading toward the nose; the cap is an upper bound, the peak is lower. In the final looks YouCam's blush replaces it | Match |
| `eyeshadow` | Two or three zones with a gradient | One flat colour, α 0.22; the final look also uses a one-colour YouCam shadow | Differs (known gap) |
| `lashes` | Wiggle at the roots, heavier outer third; brown for very light lashes | Narrow upper band, dark root and clear tip, uniform weight, no lower lashes; the final look uses a YouCam colour plus liner | Differs |
| `lips` | Stay inside the lip line; blot, balm | Colour swap inside the lip mask, texture kept, cap 0.50 (0.45 in the second example); no blot or gloss. The final male look adds YouCam's lip layer at ×2 | Match (limited) |
| `soft_focus` | A pore-blurring primer **before** foundation; it does not fade spots | Image-domain bilateral filter plus mid-scale spot subtraction, applied to the **finished** image after the YouCam lip; mid-frequency retention 0.38 (1.0 = unchanged). It is strong smoothing, not a light pat | Differs (order, effect, strength) |

## Example 2 (uneven skin tone), offline engine and in-browser version

| Layer | Real technique | Our simulation | Result |
|---|---|---|---|
| `corrector` | Thin, only inside the patch, before concealer | Same offline; **off by default (α 0) in the browser**. The warm-colour limit is our own assumption; the peer-reviewed source we read lists a brown undercover foundation for depigmentation and does not support an orange or peach corrector for white patches | Partly; disputed |
| `concealer` | Thin pressed layers of one shade, centre outwards | Pulls the patch 75% toward a target tone, up to two shades mixed; browser version centre-weighted (d^1.2) | Partly |
| `concealer2` | Second pass only on what still shows | Same | Match |
| `edge` | Blend the border | Offline default band about 1 mm inside and 1.3 mm outside, far narrower than any width in our notes (3 mm to 2 cm, all unverified); browser default off (α 0) | Differs (width) |
| `foundation` | Thin, centre outwards, thinner on the neck | Face 0.08 and neck 0.2 offline; 0.5× in the browser | Match |
| `powder` | Press to set | Pushes toward the foundation colour by 0.15; no shine or oil model; **browser only** | Differs (function) |
| `touch_up` | Look again and fix both too-light and too-dark spots | Offline does both; the browser only fixes too-light leftovers; round 1 uses the original mask, only a later round re-detects after makeup | Partly |
| `detail_fix` | Neck, jaw, fine lines | Offline only; fine-line detection (15 px top-hat) is our own algorithm with no public source | Partly (no source) |
| `contour_warm` | A stronger, warmer contour on deeper skin | Not a true contour: a thin sweep with a foundation shade (ΔL* −2.2); it improves ΔE by only 0.03. The final look adds YouCam's real contour layer | Differs |

## Limits of the simulation (read this before trusting any picture)

- No physics for oxidation, specular shine, caking, wear-off or real product coverage. Powder and lip finish are alpha shapes or colour pushes. Texture is restored by adding the original's high-frequency detail back.
- Each "opacity cap" is an upper bound, not the real peak.
- The process page and the final images are not identical in what YouCam did: in the first example YouCam added only blush on an input that already had a YouCam eye pass, and the local eyeshadow and lashes layers do not survive into the final. Only blemish, lip and base caps above were re-read against the audit; the exact cap per variant can differ.
- Recipe cards show some local-simulation colours that differ from the final images (lashes, the male lip, the blemish concealer colour). The final image is the source of truth.
- The two offline engines and the soft-focus script are not in this repository. They produced the frames; the public repository contains the browser version, the patch mask (`app/patch_detect.py`) and the reproducible comparison (`python3 -B tools/repro_region_gap.py`).
- The offline numbers are not independent tests: the engine was designed to shrink exactly the gaps we then measure. Independent recounts of some stage targets (eye area, texture retention) were **not met**; see the process page.

## Where each piece lives

| Piece | Where | Online? |
|---|---|---|
| In-browser layers (`corrector` to `touch_up` for the uneven-tone example) | `web/makeup_layers.js` | Yes |
| Foundation fill preview on any photo | `web/shade_preview.js` | Yes |
| Lighter-area mask (rule-based, no model) | `app/patch_detect.py`, `web/camouflage.js` | Yes |
| Offline layer engines and soft-focus script | outside the repository | No (pre-computed frames) |
| Brows, lashes, blush, lips, contour in the final images | YouCam Makeup VTO | Live mode only; demo mode replays saved results |
