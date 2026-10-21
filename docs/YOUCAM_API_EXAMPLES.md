# YouCam API: what the app sends and reads

Everything here is read from the code in this repository (`tools/noise_test.py` for the client, `app/server.py` for the calls, `app/looks.py` for the request bodies). Nothing below is invented sample output.

## Call sequence (Makeup VTO, live mode)

1. `POST /s2s/v2.0/file` with `{"files":[{"content_type":"image/jpeg","file_name":"selfie.jpg","file_size":N}]}`. The answer lists a pre-signed upload request and a `file_id`; the client `PUT`s the JPEG to that URL.
2. `POST /s2s/v2.0/task/makeup-vto` with the body below plus `"src_file_id": "<file_id>"`. The answer carries `data.task_id`.
3. `GET /s2s/v2.0/task/makeup-vto/{task_id}` every few seconds until `data.task_status` is `success` or `error`.
4. On `success` the code reads the result image URL from `data.results` and downloads it. On `error` it reads `data.error` (or `error_code`) and shows a friendly reason; if YouCam cannot process the step (for example `error_face_angle_invalid`), the app falls back to a local simulation and says so.

All calls carry `Authorization: Bearer <key>`, where the key comes from the `YOUCAM_API_KEY` environment variable on the server and never reaches the browser. Every try-on is reserved in a credit ledger first (`app/budget.py`) and recorded only when YouCam reports success.

## A real request body (built by `app/looks.py`)

This is what the app sends for a soft blush and a tinted lip, smoothing fixed at zero:

```json
{
 "version": "1.0",
 "effects": [
  {"category": "skin_smooth", "skinSmoothStrength": 0, "skinSmoothColorIntensity": 0},
  {"category": "blush", "pattern": {"name": "Oblique1"},
   "palettes": [{"color": "#DF988B", "texture": "matte", "colorIntensity": 55}]},
  {"category": "lip_color", "shape": {"name": "original"},
   "palettes": [{"color": "#C8827A", "texture": "satin", "colorIntensity": 20}], "style": {"type": "full"}}
 ]
}
```

The same three calls with curl (`$YOUCAM_API_KEY` is your own key; `FILE_ID` comes from step 1 and `TASK_ID` from step 2):

```bash
API=https://yce-api-01.makeupar.com
# 2. create the try-on task
curl -sS -X POST "$API/s2s/v2.0/task/makeup-vto" \
  -H "Authorization: Bearer $YOUCAM_API_KEY" -H "Content-Type: application/json" \
  -d '{"version":"1.0","src_file_id":"FILE_ID","effects":[{"category":"skin_smooth","skinSmoothStrength":0,"skinSmoothColorIntensity":0},{"category":"blush","pattern":{"name":"Oblique1"},"palettes":[{"color":"#DF988B","texture":"matte","colorIntensity":55}]}]}'
# 3. poll until data.task_status is success or error
curl -sS "$API/s2s/v2.0/task/makeup-vto/TASK_ID" -H "Authorization: Bearer $YOUCAM_API_KEY"
```

More bodies, one per effect, are in `tools/bodies/` (lashes, blush, brows, foundation, whole looks).

## Face Attribute (optional tips)

`POST /s2s/v2.0/task/face-attr-analysis` with `{"src_file_id": ..., "features": [...], "face_angle_strictness_level": "medium"}`, polled at `GET /s2s/v2.0/task/face-attr-analysis/{task_id}`. The result feeds `app/face_rules.py`, which turns face shape and brow density into blush and brow suggestions. It runs only when the user taps the button (10 credits).

## Evidence that the saved demo pictures come from real calls

`demo/replay/<entry>/exact/index.json` lists every recorded Makeup VTO call: the option set sent, the time it was recorded (`recorded_at`), the size of the raw result (`raw_bytes`) and sha256 checksums of the raw and the saved picture. 80 combinations are recorded on the demo photos; demo mode serves exactly these, or the closest one when your choice was not recorded.

## Credits

Makeup VTO costs 1 credit per call and Face Attribute 10 (`app/budget.py`, checked against YouCam's feature price list). The ledger file is `runs/budget_ledger.json`; `--max-calls` and `--budget-cap` limit a run.
