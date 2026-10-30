# Shade file: how a retailer's real shades replace the fictional demo catalogue

The demo uses `app/catalog.json` (fictional brands, no product links). If `app/partner_shades_official.json` exists, `app/color_engine.py` loads it (`OFFICIAL_CATALOG`, `load_catalogs`) and prefers it over older partner data for the same brand. The file is not in this repository and is not tracked.

## Format (the same keys as `app/catalog.json`)

| Field | Required | Meaning |
|---|---|---|
| `products[].brand` | yes | Brand name. The loader reads this key directly; a missing key raises a `KeyError` at load time. |
| `products[].name` | yes | Product name. |
| `products[].category` | yes | The engine matches `foundation` and `concealer` (undertone can be inferred for these); other categories load but are not used by the shade card. |
| `products[].source_url` | no | The brand's own product page, `https://` only. If present, the colour card links each matched shade to it; if absent, no link is shown. Nothing is guessed. |
| `products[].shades[].code` | yes | Shade code as printed on the product. |
| `products[].shades[].hex` | yes | `#RRGGBB`. Use the colour of the foundation as worn on skin, not the bottle or cap colour; a swatch hex is only as good as how it was measured. |
| `products[].shades[].undertone` | no | `cool`, `neutral` or `warm`. If missing, the engine estimates it from `hex` (`app/color_engine.py`, `match_lab`). Supplying it is better. |

A worked example with fictional values is in [`docs/examples/partner_shades_official.example.json`](examples/partner_shades_official.example.json).

## Use it

1. Copy the example to `app/partner_shades_official.json` (not tracked by git) and replace the rows with your own.
2. Check it before starting the server: `python3 -I -B tools/check_shade_file.py app/partner_shades_official.json` (prints the number of products and shades, or the first problem with its location, and exits non-zero on a problem).
3. Start the server (`python3 -B app/server.py --mock`). The file is read from disk on each catalogue load, so a restart is the safe way to be sure. Open `/api/status`: the catalogue counts there come from the catalogue actually loaded, and the home page numbers follow it.

## What is and is not tested

- Tested: official rows link to their product page and demo rows never do, using a placeholder brand file (`tests/test_judge_review_20261029.py`).
- Not tested: a retailer-scale file, lighting calibration at a counter, stock or basket integration. The app does not log shades tried or returns; a pilot has to record those itself.
