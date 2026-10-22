# Local tools and how to use them

All commands run from the repository root. `-B` keeps Python from writing `.pyc` files. Costs are YouCam "API units" (Makeup VTO 1, Face Attribute 10, Skin Analysis HD 12, Facial Color Tones 20). Install: `pip install -r requirements.txt` (numpy, scipy, Pillow).

## 1. Run the app (no key, no cost)

```bash
python3 -B app/server.py --mock --catalog demo      # http://127.0.0.1:8787/
```

| Flag | Meaning |
|---|---|
| `--mock` | Replay saved results; never calls YouCam; 0 units |
| `--catalog demo` | Fictional shade catalog (use this for anything public) |
| `--brand-alias` | Local check only: real brand names become letters, shade names dropped |
| `--simulate-youcam-error [CODE]` | With `--mock` only: make YouCam steps fail so the local assist layer shows |
| `--max-heavy N` / `GOODGLOW_MAX_HEAVY` | Concurrent heavy requests (default 2); more wait up to 8 s, then HTTP 503 `busy` |
| `--port`, `--host` | Defaults 8787, 127.0.0.1 |
| `--max-calls`, `--budget-cap` | Live mode ceilings (defaults 20 Makeup VTO calls, 60 units in total; Face Attribute counts against the unit budget only); the ledger is `runs/budget_ledger.json` |

Live mode (needs your own key in the environment, never in the repo): `python3 -B app/server.py --max-calls 20 --budget-cap 60`. It exits with code 2 when no key is found.

Container: `docker build -t goodglow .` then `docker run -p 8787:8787 -e PORT=8787 goodglow` (runs `--mock --catalog demo`, non-root, health check on `/api/status`).

## 2. Verify and reproduce (no key, no cost)

| Goal | Command |
|---|---|
| All tests | `python3 -B -m unittest discover -s tests` |
| Lighter-area brightness gap: original vs YouCam foundation vs local layers | `python3 -I -B tools/repro_region_gap.py [--json]` (exit 1 if outside tolerance) |
| Translation tables consistent | `python3 -B tools/build_i18n.py --check`, `tools/i18n_annotate_html.py --check`, `tools/i18n_apply_js.py --check` |
| No Chinese left in English API replies | start the server with `--mock`, then `python3 -B tools/audit_api_strings.py http://localhost:8787 --lang en --strict` |

## 3. Rebuild the evidence (needs the original run folders, which are not in this repository)

| Tool | Output |
|---|---|
| `tools/export_process_demo.py --runs <runs dir>` | `evidence/process.json` and `evidence/img/*` |
| `tools/export_final_looks.py --runs <runs dir>` | `demo/final/*` with `SHA256SUMS.txt` |
| `tools/record_demo_replays.py --payloads <file>` | Dry run by default; `--run --cap N` spends 1 unit per option combination |

## 4. Tools that spend units

| Tool | Gate | Note |
|---|---|---|
| `tools/noise_test.py run ... --run` | needs `--run`; bypasses the ledger | Skin Analysis, 12 units per photo; `check` and `summarize` are free; `credit` reads the balance |
| `tools/youcam_probe.py task FEATURE PHOTO BODY.json --run` | needs `--run` (without it: dry run); no ledger | Spends the feature price; `cost` is free. Use only with the bodies in `tools/bodies/` |
| `tools/record_demo_replays.py --run` | `--cap` | Ledger-checked |

Never point these at someone else's photo. Use your own or the licensed sample photos in `demo/photos`.

## 5. Not in the public release

About 30 experiment scripts (hair colour, mask experiments, one-off live cases) are kept out of the public export; they hold local paths and run notes and some spend units without a gate. The two offline layer engines and the black-box service (`mcp_blackbox/`) also stay outside the repository. The black-box service sends numeric metrics to an external language model unless its AI review is switched off, so it is not part of the public online version.

`requirements.txt` lists only what the shipped app and tools need (numpy, scipy, Pillow); the experiment tools and offline engines that are not shipped need more (opencv, mediapipe, mcp).
