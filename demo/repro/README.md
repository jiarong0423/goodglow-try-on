# demo/repro: reproduce "YouCam alone cannot do it, our local fill closes the gap"



Rerun: `python3 -I -B tools/repro_region_gap.py` (prints the table; exits 1 if a number is outside the expected range). File checksums are in `SHA256SUMS.txt`.

| File | What it is |
|---|---|
| `../photos/vitiligo_20551205.jpg` | The original photo (public Pexels photo) |
| `vto_foundation_dark.jpg` / `vto_foundation_light.jpg` | YouCam Makeup VTO whole-face foundation (coverage 80, smoothing 0) with the darker / lighter shade; images returned by the real call (scaled to a 1438×1918 JPEG) |
| `local_layers.jpg` | Result of our offline layer-by-layer fill (not part of this repository) |
| `patch_mask.png` | The lighter-patch mask, a multi-scale rule-based computation (`app/patch_detect.py`), no model involved |

Expected output (lightness L* gap between the lighter patch and the surrounding skin; smaller is closer):

| Input | Gap | Shrunk vs. original |
|---|---|---|
| Original photo | 7.4 | — |
| YouCam whole-face foundation (darker shade) | 6.9 | about 6% |
| YouCam whole-face foundation (lighter shade) | 7.0 | about 5% |
| Our layered local fill | 4.3 | about 42% |

Reproduce "when YouCam cannot process a step, a local simulation takes over":

```bash
python3 -B app/server.py --mock --simulate-youcam-error --port 8806   # try-on requests return error_face_angle_invalid
# open http://localhost:8787/?lang=en in a browser and walk to the "Try-on" page with a demo photo
```

Expected: the try-on page says "YouCam could not process this step…", the source line reads "Local simulation (YouCam could not process this step)", and the picture is the local fill. Automated check: `python3 -B -m unittest tests.test_assist_layer`.

Note: the local layers were designed to shrink exactly this gap, and the layers of this run were fitted to an AI-generated target image, so this is a confirmation, not a blind test. One photo, YouCam coverage 80. An earlier "about 11%" came from a different measure; only numbers you can rerun are published here.
