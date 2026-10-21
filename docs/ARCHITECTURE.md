# Architecture

How Good Glow Try-On is put together, which local tools are used to build and verify it, and how the mapping tables were used to find where our makeup simulation differs from real application technique. All diagrams are [Mermaid](https://mermaid.js.org/) and render on GitHub. Names in `code font` are files in this repository unless said otherwise.

![System architecture: the phone browser, one app server with a mode gate, saved results, the credit ledger and the YouCam API](assets/diagrams/00-system-architecture.png)

Contents: [1 Context and deployment](#1-context-and-deployment) · [2 Components](#2-components) · [3 Operating modes](#3-operating-modes) · [4 Try-on request](#4-try-on-request) · [5 Skin tone to shade](#5-skin-tone-to-shade) · [6 Uneven-tone pipeline](#6-uneven-tone-pipeline) · [7 Capture and photo checks](#7-capture-and-photo-checks) · [8 Credit ledger](#8-credit-ledger) · [9 Mapping tables](#9-mapping-tables) · [10 Real technique vs simulation](#10-real-technique-vs-simulation) · [11 Local tools](#11-local-tools) · [12 Evidence pipeline](#12-evidence-pipeline) · [13 Translation pipeline](#13-translation-pipeline)

## 1 Context and deployment

```mermaid
flowchart LR
  P["Phone or desktop browser (PWA)"]
  subgraph Host["Hosting: a VPS running Coolify"]
    CF["Cloudflare Tunnel"] --> TR["Traefik (port 80)"]
    TR --> C["Container: python app/server.py (non-root, health check /api/status)"]
    V[("Persistent volume /srv/app/runs: credit ledger")]
    C --- V
  end
  YC["YouCam S2S API (Makeup VTO, Face Attribute)"]
  CDN1["YouCam CDN: Camera Kit script"]
  CDN2["Public CDNs: MediaPipe models and scripts"]
  GH["GitHub (public repo, MIT)"]
  P -- "HTTPS" --> CF
  C -- "live mode only (key in server env)" --> YC
  P --> CDN1
  P --> CDN2
  GH -- "Coolify builds the Dockerfile" --> Host
```

- The image holds `app/`, `web/`, `demo/` and `tools/noise_test.py` only. It contains no API key, no brand data and no evidence page.
- The key, the judge sign-in and the call limits are environment variables set on the platform, never in the repository ([SECURITY.md](../SECURITY.md)).

## 2 Components

```mermaid
flowchart TB
  subgraph Browser["web/ (runs in the phone browser)"]
    APP["app.js: screens and flow"]
    CG["capture_guide.js, face_auto.js, face_check.js: photo checks and landmarks (MediaPipe)"]
    SP["shade_preview.js, makeup_layers.js, camouflage.js, camo_editor.js: local layers (OpenCV.js)"]
    I18N["i18n.js, i18n/en.js: English strings"]
    SW["sw.js: shell cache"]
  end
  subgraph Server["app/ (Python, standard library + numpy, scipy, Pillow)"]
    SRV["server.py: HTTP, request limits, routes"]
    JA["judge_auth.py: optional judge sign-in"]
    CE["color_engine.py: sRGB to Lab, CIEDE2000"]
    SK["skin_card.py: 10 depth levels x 3 undertones"]
    REC["recommend.py, full_look.py, brow_card.py, face_rules.py, care_rules_*.py: plans and rules"]
    LK["looks.py: option id to Makeup VTO body"]
    PD["patch_detect.py: lighter-area mask"]
    BG["budget.py: credit ledger"]
    TR["i18n_srv.py: English for server messages"]
    NT["tools/noise_test.py: YouCam REST client"]
  end
  subgraph Data["data files"]
    PR["presets.json, full_looks.json"]
    CAT["catalog.json: fictional demo shades"]
    DEMO["demo/: photos, saved real VTO results, final looks"]
  end
  APP --> SRV
  CG --> APP
  SP --> APP
  SRV --> CE --> SK
  SRV --> REC --> LK
  SRV --> PD
  SRV --> BG --> NT
  SRV --> JA
  SRV --> TR
  LK --> PR
  REC --> PR
  CE --> CAT
  SRV --> DEMO
```

## 3 Operating modes

The public site is open to everyone in demo mode. Real YouCam calls exist only for a signed-in judge, and only when the platform provides all four settings.

![Operating modes: server start, demo only or demo plus judge sign-in, anonymous, signed in, refused](assets/diagrams/03-operating-modes.png)

<details><summary>Mermaid source</summary>

```mermaid
stateDiagram-v2
  [*] --> Boot
  Boot --> Demo: GOODGLOW_LIVE, YOUCAM_API_KEY, GOODGLOW_JUDGE_USER or GOODGLOW_JUDGE_PASSWORD missing
  Boot --> DemoPlusJudge: all four set (server.py --judge-live)
  state Demo {
    [*] --> Replay
    Replay: saved real results, no key, no credits
  }
  state DemoPlusJudge {
    [*] --> Anonymous
    Anonymous --> SignedIn: POST /api/judge-login (signed HttpOnly cookie, 6 h)
    SignedIn --> Anonymous: sign out, cookie expires or server restarts
    Anonymous: demo mode (replay)
    SignedIn: live mode (real calls, capped)
    SignedIn --> Refused: max calls per start or credit cap reached
    Refused --> Anonymous: sign out
  }
```

</details>

Wrong passwords lock sign-in for a minute after six failures. Anonymous requests never reach the live state: `Handler._pick_state` chooses the state per request from the cookie.

## 4 Try-on request

![Try-on request sequence in demo mode and in live mode](assets/diagrams/04-tryon-request.png)

<details><summary>Mermaid source</summary>

```mermaid
sequenceDiagram
  participant B as Browser
  participant S as app/server.py
  participant L as Credit ledger
  participant Y as YouCam S2S API
  participant R as demo/replay
  B->>S: POST /api/makeup (photo, option ids)
  S->>S: heavy-request slot (503 busy after 8 s), size and format limits
  S->>S: looks.build_effects: option ids to a Makeup VTO body (smoothing 0)
  alt demo mode
    S->>R: exact key (condition, style, effects, colors)
    R-->>S: saved picture for exactly these choices, else the closest one
    S-->>B: image labelled "Demo mode | simulated"
  else live mode (signed-in judge)
    S->>S: orientation fix, resize to long side below 1920, re-encode without EXIF
    S->>L: reserve 1 credit
    S->>Y: POST /s2s/v2.0/file, then POST /task/makeup-vto
    loop poll every few seconds
      S->>Y: GET /task/makeup-vto/{id}
    end
    Y-->>S: result image
    S->>L: settle (record only on success)
    S-->>B: image labelled as live
  end
  Note over S,B: If YouCam cannot process a step, the app says so and shows a local simulation with its source labelled.
```

</details>

## 5 Skin tone to shade

```mermaid
flowchart TB
  A["Photo (camera or album)"] --> B["Landmarks and sample points in the browser (cheek, forehead, brow)"]
  B --> C["Light check and gray-world white balance (half strength, at most 8%)"]
  C --> D["sRGB to CIE Lab (D65)"]
  D --> E["Skin tone card: level by L*, cool/neutral/warm by hue"]
  E --> F{"User confirms or taps another cell"}
  F --> G["Catalog shades ranked by card level and undertone, then by CIEDE2000"]
  G --> H["One lighter, closest, one deeper test stripes"]
  G --> I["Care card with shades"]
  E -. "no shade matches the depth" .-> J["Suggest mixing one lighter and one deeper"]
```

Why a card first: photo colors are pulled grey and pink by light, so comparing them directly with swatches found no close match on our test photo. The card (10 levels by 3 undertones) puts the photo and the swatches on one scale.

## 6 Uneven-tone pipeline

```mermaid
flowchart TB
  P["Photo"] --> M["POST /api/patch-mask: patch_detect.py"]
  M --> M1["Three scales: fine, medium, coarse"]
  M1 --> M2["Per pixel: lightness gap / 4 + chroma gap / 3 against the surrounding skin"]
  M2 --> M3["Exclude dark hair, tiny specks, specular glints"]
  M3 --> MASK["Lighter-area mask (kept in memory, not stored)"]
  P -.->|"server unreachable"| FB["Browser fallback: camouflage.js"]
  MASK --> LAY["makeup_layers.js: corrector, concealer, concealer2, edge, foundation, powder, touch_up"]
  FB --> LAY
  LAY --> OUT["Preview labelled Simulated"]
  VTO["YouCam Makeup VTO: whole-face try-on, no region parameter"] --> OUT2["Look shown next to it"]
  OUT --- OUT2
```

Makeup VTO paints the whole face and has no region parameter, so concealing only one patch is our own local code. The measured comparison is reproducible with `tools/repro_region_gap.py` (section 12).

## 7 Capture and photo checks

```mermaid
flowchart TB
  K["YouCam Camera Kit (skincare mode, relaxed quality, 1080p video)"] -- "faceQualityChanged" --> H["Live hints: closer, center, face the camera, light"]
  K -- "faceDetectionCaptured" --> G["Capture guide: face size, angle, framing, light"]
  K -- "cameraFailed or unsupportedResolution" --> N["Fallback: browser camera, or album"]
  K -. "no event for 10 s" .-> T["Hint: close other tabs using the camera, or pick from album"]
  A["Album photo"] --> G
  G --> X{"Auto-crop to the face (3:4) leaves the short side at 640 px or more?"}
  X -- yes --> CROP["Crop and scale only, no retouching"]
  X -- no --> KEEP["Keep the original resolution"]
  CROP --> CHK["Check screen: pick tones, See my shade"]
  KEEP --> CHK
  CHK --> LOW{"Short side below 640 px?"}
  LOW -- yes --> RE["Ask for a retake (button stays locked)"]
  LOW -- no --> OK["Continue"]
```

The Camera Kit returns about 680 x 1080 on a phone, so the minimum short side is 640 px (the server enforces the same limit).

## 8 Credit ledger

```mermaid
stateDiagram-v2
  [*] --> Idle
  Idle --> Reserved: reserve(feature) at the official price
  Reserved --> Recorded: YouCam reports success, settle(success)
  Reserved --> Refunded: error, timeout or no result, settle(failure)
  Recorded --> Idle
  Refunded --> Idle
  Idle --> Refused: remaining credits below the price
  Idle --> Refused: calls this run at --max-calls
  Refused --> Idle
```

The ledger is `runs/budget_ledger.json` (not in version control; a persistent volume on the hosted site). `--max-calls` limits Makeup VTO calls per start, `--budget-cap` limits credits across starts. Prices: Makeup VTO 1, Face Attribute 10.

## 9 Mapping tables

The app is mostly lookup tables, so each behaviour can be read and tested without a model.

```mermaid
flowchart LR
  subgraph Input["User choices"]
    E["Entry point, look (grooming, natural, polished), option ids"]
  end
  E --> P["presets.json: option id to Makeup VTO effect (palette, pattern, intensity)"]
  E --> F["full_looks.json: entry x look base table, then adjusted by card level and undertone, lowered when skin is sensitive"]
  TONE["Skin tone card (skin_card.py): 10 levels x 3 undertones"] --> R["Shade ranking"]
  CAT["catalog.json: shade to reference color, depth, undertone"] --> R
  BR["brow_card.py: pencil shade to depth (6) x family (5) tags"] --> R2["Brow match"]
  FA["face_rules.py: Face Attribute result to blush placement and brow strength"] --> TIPS["Tips"]
  CARE["care_rules_*.py: care and sun-protection steps"] --> CARD["Care card"]
  P --> BODY["looks.build_effects: Makeup VTO request body"]
  F --> BODY
  R --> CARD
  R2 --> CARD
  TIPS --> CARD
  ZH["docs/i18n/zh_en_map.csv: Chinese UI text to reviewed English"] --> UI["Both languages from one table"]
```

| Table | File | Maps |
|---|---|---|
| Options | `app/presets.json` | option id to a Makeup VTO effect, so the browser never sends free-form parameters |
| Looks | `app/full_looks.json` | entry x look to a base set of options, with a one-sentence reason shown for every adjustment |
| Skin tone card | `app/skin_card.py` | L* bounds to 10 levels, hue to cool/neutral/warm |
| Shades | `app/catalog.json` | fictional demo shades to color, depth, undertone |
| Brows | `app/brow_card.py` | brow product to depth and family tags, matched by tag |
| Face tips | `app/face_rules.py` | Face Attribute output to blush and brow suggestions |
| Translation | `docs/i18n/zh_en_map.csv` | Chinese UI text to reviewed English (section 13) |
| Technique vs simulation | `docs/LAYERS_TECHNIQUE_VS_SIMULATION.md` | each layer to the real technique, our simulation and a verdict (section 10) |

## 10 Real technique vs simulation

We built a mapping table from each makeup layer to how the technique is really done, then read our own code against it to find where the simulation differs. The table is [docs/LAYERS_TECHNIQUE_VS_SIMULATION.md](LAYERS_TECHNIQUE_VS_SIMULATION.md).

```mermaid
flowchart TB
  R["Run records: what each layer changed, per example (tools/export_process_demo.py)"] --> T
  C["Code and parameters of the layer engines and browser layers"] --> T
  S["General technique notes (not brand specific)"] --> T
  T["Mapping table: layer, real technique, our simulation"]
  T --> V1["Reviewer 1: reads the real technique column"]
  T --> V2["Reviewer 2: reads the simulation column against code"]
  V1 --> D["Differences listed on purpose: Match / Differs / Partly"]
  V2 --> D
  D --> M["Measure what matters: lightness gap before and after (tools/repro_region_gap.py)"]
  D --> L["Limits of the simulation written next to every picture (evidence/)"]
  M --> F["Fix or label: e.g. patch concealing is local code and labelled Simulated"]
  L --> F
```

What the comparison found (details in the table):

| Where the simulation differs | Example |
|---|---|
| Order | highlight is applied before blush, real application usually does it last |
| Shape | nose shadow drawn as two vertical lines, real application blends along the sides of the nose |
| Tool | no corrector layer under the eyes, lightness is pulled toward the cheek instead |
| Width | the edge-blending band is far narrower than any width in our notes |
| Function | powder only pushes toward the foundation colour: no shine or oil model |
| Known gap | eyeshadow is one flat colour, real application uses two or three zones |

No physics for oxidation, shine, caking or wear-off; every opacity cap is an upper bound, not the real peak. The offline numbers are not independent tests: the engine was designed to shrink the gap we then measure.

## 11 Local tools

```mermaid
flowchart TB
  subgraph Run["Run (no key, no cost)"]
    SRVR["python app/server.py --mock --catalog demo"]
  end
  subgraph Verify["Verify and reproduce (no key, no cost)"]
    UT["python -m unittest discover -s tests"]
    RG["tools/repro_region_gap.py"]
    AUD["tools/audit_api_strings.py"]
    I18C["tools/build_i18n.py --check, tools/i18n_annotate_html.py --check, tools/i18n_apply_js.py --check"]
  end
  subgraph Evidence["Rebuild evidence (needs the original run folders)"]
    EPD["tools/export_process_demo.py"]
    EFL["tools/export_final_looks.py"]
  end
  subgraph Spend["Spend credits (explicit gates)"]
    NTR["tools/noise_test.py run ... --run"]
    PRB["tools/youcam_probe.py task ... --run"]
    REC2["tools/record_demo_replays.py --run --cap N"]
  end
  RG -->|"exit 1 if outside tolerance"| UT
  EPD --> EV["evidence/process.json and img/"]
  EFL --> FIN["demo/final/ and SHA256SUMS.txt"]
  REC2 --> RP["demo/replay/*/exact/"]
  RP --> SRVR
```

| Tool | What it does | How it works |
|---|---|---|
| `app/server.py` | serves the app, proxies YouCam, mock replay | standard-library HTTP server with bounded connections, streamed statics, per-request state selection |
| `tools/repro_region_gap.py` | the measured comparison | fixed inputs with checksums in `demo/repro/`; patch = inside the mask, surrounding skin = a 14 to 70 px ring outside it with skin-like pixels; prints the table and exits 1 outside tolerance |
| `tools/noise_test.py` | YouCam REST client and offline noise test | upload, task, poll, retry; 12-unit Skin Analysis runs only with `--run`; `check`, `summarize` and `credit` are free |
| `tools/youcam_probe.py` | one request against one feature | dry run by default; `--run` spends the price; request bodies live in `tools/bodies/` |
| `tools/record_demo_replays.py` | records the saved real results used by demo mode | dry run by default; `--run --cap N` spends 1 credit per option combination, ledger-checked |
| `tools/export_process_demo.py`, `tools/export_final_looks.py` | rebuild the evidence page data and the final looks | write `evidence/process.json`, `evidence/img/*`, `demo/final/*` and checksums |
| `tools/build_i18n.py`, `tools/extract_ui_strings.py`, `tools/i18n_annotate_html.py`, `tools/i18n_apply_js.py`, `tools/jslex.py`, `tools/audit_api_strings.py` | translation toolchain (section 13) | table-driven; each has a `--check` mode used by the tests |

Full command reference: [TOOLS_AND_USAGE.md](TOOLS_AND_USAGE.md).

## 12 Evidence pipeline

```mermaid
flowchart TB
  RUNS["Original run folders (not in this repository)"] --> EPD["tools/export_process_demo.py"]
  RUNS --> EFL["tools/export_final_looks.py"]
  EPD --> PJ["evidence/process.json + evidence/img/*"]
  PJ --> VIEW["evidence/index.html: slider, heat map, technique per layer"]
  EFL --> FIN["demo/final/* + SHA256SUMS.txt"]
  PH["demo/photos/*.jpg"] --> RG
  VF["demo/repro/: YouCam foundation results, local layers result, patch mask + SHA256SUMS.txt"] --> RG["tools/repro_region_gap.py"]
  RG --> TBL["Table: original 7.4, YouCam foundation about 5 to 6 % smaller, local fill about 42 % smaller"]
  TBL --> VIEW
```

The 42 % is a confirmation, not a blind test: the layers were designed to shrink exactly this gap. One photo, YouCam coverage 80.

## 13 Translation pipeline

```mermaid
flowchart TB
  SRC["Chinese UI text in web/*.js, web/index.html and server messages"] --> EX["tools/extract_ui_strings.py: assign stable ids (s0001...)"]
  EX --> CSV["docs/i18n/zh_en_map.csv"]
  CSV --> REV["Review: forward translation, blind back-translation, comparison"]
  REV --> BLD["tools/build_i18n.py"]
  BLD --> ENJS["web/i18n/en.js"]
  BLD --> ENJ["app/i18n_en.json"]
  ANN["tools/i18n_annotate_html.py, tools/i18n_apply_js.py: mark strings in HTML and JS"] --> SRC
  ENJS --> UI["Browser: ?lang=en or browser language"]
  ENJ --> API["Server replies in English when X-App-Lang is en"]
  API --> AUD["tools/audit_api_strings.py --strict: no Chinese left in English replies"]
```

The Chinese output stays byte-identical; tests pin the table's row count and checksum so a drift is caught.

## Rendered images

The same diagrams as image files (SVG and PNG, rendered from the Mermaid sources above), for places that do not render Mermaid.

| # | Diagram | Files |
|---|---|---|
| 0 | System architecture | [SVG](assets/diagrams/00-system-architecture.svg), [PNG](assets/diagrams/00-system-architecture.png) |
| 1 | Context and deployment | [SVG](assets/diagrams/01-context-and-deployment.svg), [PNG](assets/diagrams/01-context-and-deployment.png) |
| 2 | Components | [SVG](assets/diagrams/02-components.svg), [PNG](assets/diagrams/02-components.png) |
| 3 | Operating modes | [SVG](assets/diagrams/03-operating-modes.svg), [PNG](assets/diagrams/03-operating-modes.png) |
| 4 | Try-on request | [SVG](assets/diagrams/04-tryon-request.svg), [PNG](assets/diagrams/04-tryon-request.png) |
| 5 | Skin tone to shade | [SVG](assets/diagrams/05-skin-tone-to-shade.svg), [PNG](assets/diagrams/05-skin-tone-to-shade.png) |
| 6 | Uneven-tone pipeline | [SVG](assets/diagrams/06-uneven-tone-pipeline.svg), [PNG](assets/diagrams/06-uneven-tone-pipeline.png) |
| 7 | Capture and photo checks | [SVG](assets/diagrams/07-capture-and-photo-checks.svg), [PNG](assets/diagrams/07-capture-and-photo-checks.png) |
| 8 | Credit ledger | [SVG](assets/diagrams/08-credit-ledger.svg), [PNG](assets/diagrams/08-credit-ledger.png) |
| 9 | Mapping tables | [SVG](assets/diagrams/09-mapping-tables.svg), [PNG](assets/diagrams/09-mapping-tables.png) |
| 10 | Real technique vs simulation | [SVG](assets/diagrams/10-technique-vs-simulation.svg), [PNG](assets/diagrams/10-technique-vs-simulation.png) |
| 11 | Local tools | [SVG](assets/diagrams/11-local-tools.svg), [PNG](assets/diagrams/11-local-tools.png) |
| 12 | Evidence pipeline | [SVG](assets/diagrams/12-evidence-pipeline.svg), [PNG](assets/diagrams/12-evidence-pipeline.png) |
| 13 | Translation pipeline | [SVG](assets/diagrams/13-translation-pipeline.svg), [PNG](assets/diagrams/13-translation-pipeline.png) |
