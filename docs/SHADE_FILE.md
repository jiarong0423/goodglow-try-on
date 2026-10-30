# Shade file: how a retailer's real shades replace the fictional demo catalogue

The demo uses `app/catalog.json` (fictional brands, no product links). If `app/partner_shades_official.json` exists, `app/color_engine.py` loads it (`OFFICIAL_CATALOG`, `load_catalogs`) and prefers it over older partner data for the same brand. The file is not in this repository and is not tracked.

## Format (the same keys as `app/catalog.json`)

| Field | Required | Meaning |
|---|---|---|
| `products[].brand` | yes | Brand name. The loader reads this key directly; a missing key raises a `KeyError` at load time. |
| `products[].name` | yes | Product name. |
| `products[].category` | yes | For example `foundation`. |
| `products[].source_url` | no | The brand's own product page, `https://` only. If present, the colour card links each matched shade to it; if absent, no link is shown. Nothing is guessed. |
| `products[].shades[].code` | yes | Shade code as printed on the product. |
| `products[].shades[].hex` | yes | Swatch colour, `#RRGGBB`. |
| `products[].shades[].undertone` | yes | `cool`, `neutral` or `warm`. |

A worked example with fictional values is in [`docs/examples/partner_shades_official.example.json`](examples/partner_shades_official.example.json).

## What is and is not tested

- Tested: official rows link to their product page and demo rows never do, using a placeholder brand file (`tests/test_judge_review_20261029.py`).
- Not tested: a retailer-scale file, lighting calibration at a counter, stock or basket integration. The app does not log shades tried or returns; a pilot has to record those itself.
