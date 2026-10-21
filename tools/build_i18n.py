"""Generate web/i18n/en.js (the English dictionary) from docs/i18n/zh_en_map.csv. The English is taken verbatim from the CSV, not rewritten.

  python3 tools/build_i18n.py            # regenerate web/i18n/en.js
  python3 tools/build_i18n.py --check    # check only: the existing file is byte-identical to the regenerated content, and the dictionary matches the CSV entry by entry

The dictionary has three parts
- I18N_EN       : plain strings, key sNNNN; position-specific keys sNNNN@file:line also go here (source docs/i18n/position_overrides.csv).
- I18N_EN_TPL   : template strings containing ${…} (kept as-is; turned into functions in stage 2).
- Regular-expression rows listed in NOT_UI are excluded (used for program matching, not on-screen text).
The CSV English-column hash (sha256 over id<TAB>en lines) is written in the file header, and the tests compare against it. Failure paths print ERROR and return non-zero.
"""
from __future__ import annotations

import csv
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAP = ROOT / "docs" / "i18n" / "zh_en_map.csv"
OVR = ROOT / "docs" / "i18n" / "position_overrides.csv"
OUT = ROOT / "web" / "i18n" / "en.js"
OUT_SRV = ROOT / "app" / "i18n_en.json"
NOT_UI = {"s0889", "s0898", "s0899", "s0900", "s0901"}   # regular expressions used for program matching, not on-screen text


def load():
    rows = list(csv.DictReader(open(MAP, encoding="utf-8")))
    sha = hashlib.sha256("\n".join(f"{r['id']}\t{r['en']}" for r in rows).encode()).hexdigest()
    plain, tpl = {}, {}
    for r in rows:
        if r["id"] in NOT_UI:
            continue
        (tpl if "${" in r["zh"] else plain)[r["id"]] = r["en"]
    overrides = {}
    if OVR.exists():
        for r in csv.DictReader(open(OVR, encoding="utf-8")):
            overrides[r["key"]] = r["en"]
    return rows, sha, plain, tpl, overrides


def render_server(rows):
    """Server-side dictionary: exact (replaced only when the sentence is identical) + templates (Python f-string patterns). Entries containing ${…} are front-end templates and are not included."""
    exact, templates = {}, []
    for r in rows:
        if r["id"] in NOT_UI or not r["en"].strip() or "${" in r["zh"]:
            continue
        if r["context"] == "py f-string":
            templates.append([r["zh"], r["en"]])
        else:
            exact[r["zh"]] = r["en"]
    return json.dumps({"exact": exact, "templates": templates}, ensure_ascii=False, indent=1, sort_keys=True) + "\n"


def render(sha, plain, tpl, overrides):
    d = {**plain, **overrides}
    dump = lambda o: json.dumps(o, ensure_ascii=False, indent=1, sort_keys=True)
    return (
        "'use strict';\n"
        "// 由 tools/build_i18n.py 產生，勿手改。來源 docs/i18n/zh_en_map.csv（英文欄雜湊見下）＋ docs/i18n/position_overrides.csv\n"
        f"// CSV 英文欄 sha256={sha}\n"
        f"window.I18N_EN = {dump(d)};\n"
        f"window.I18N_EN_TPL = {dump(tpl)};   // 含 ${{…}} 的樣板字串：第 2 階段改成函式\n"
    )


def main(check: bool) -> int:
    rows, sha, plain, tpl, overrides = load()
    text = render(sha, plain, tpl, overrides)
    srv = render_server(rows)
    if check:
        if not OUT.exists() or OUT.read_text(encoding="utf-8") != text:
            print("ERROR: web/i18n/en.js does not match the CSV; rerun tools/build_i18n.py")
            return 1
        if not OUT_SRV.exists() or OUT_SRV.read_text(encoding="utf-8") != srv:
            print("ERROR: app/i18n_en.json does not match the CSV; rerun tools/build_i18n.py")
            return 1
        # entry-by-entry comparison: dictionary value == CSV English column
        by_id = {r["id"]: r["en"] for r in rows}
        bad = [k for k, v in {**plain, **tpl}.items() if by_id[k] != v]
        if bad:
            print(f"ERROR: dictionary does not match the CSV {bad[:5]}")
            return 1
        print(f"OK: dictionary {len(plain)} + position keys {len(overrides)} + templates {len(tpl)}; CSV English column sha256={sha}")
        return 0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text, encoding="utf-8")
    OUT_SRV.write_text(srv, encoding="utf-8")
    print(f"Wrote {OUT}: dictionary {len(plain)} + position keys {len(overrides)} + templates {len(tpl)}; sha256={sha}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main("--check" in sys.argv))
    except Exception as exc:
        print(f"ERROR: {exc!r}")
        sys.exit(2)
