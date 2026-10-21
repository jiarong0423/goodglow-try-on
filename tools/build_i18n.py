"""由 docs/i18n/zh_en_map.csv 產生 web/i18n/en.js（英文字典）。英文逐字取自 CSV，不改寫。

  python3 tools/build_i18n.py            # 重新產生 web/i18n/en.js
  python3 tools/build_i18n.py --check    # 只檢查：現有檔案與重新產生的內容逐字相同、字典與 CSV 逐筆一致

字典分三塊
- I18N_EN       ：一般字串，鍵 sNNNN；位置專屬鍵 sNNNN@檔:行 也放在這裡（來源 docs/i18n/position_overrides.csv）。
- I18N_EN_TPL   ：含 ${…} 的樣板字串（原樣保留，第 2 階段改成函式）。
- 不收 NOT_UI 列出的正規表示式列（程式比對用，不是畫面文字）。
CSV 英文欄雜湊（id<TAB>en 逐行 sha256）寫在檔頭，測試會對照。失敗路徑印 ERROR 並回非零。
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
NOT_UI = {"s0889", "s0898", "s0899", "s0900", "s0901"}   # 程式比對用的正規表示式，不是畫面文字


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
    """伺服器端字典：exact（句子完全相同才換）＋ templates（Python f-string 樣式）。含 ${…} 的是前端樣板，不放。"""
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
            print("ERROR: web/i18n/en.js 與 CSV 不一致，請重跑 tools/build_i18n.py")
            return 1
        if not OUT_SRV.exists() or OUT_SRV.read_text(encoding="utf-8") != srv:
            print("ERROR: app/i18n_en.json 與 CSV 不一致，請重跑 tools/build_i18n.py")
            return 1
        # 逐筆比對：字典值＝CSV 英文欄
        by_id = {r["id"]: r["en"] for r in rows}
        bad = [k for k, v in {**plain, **tpl}.items() if by_id[k] != v]
        if bad:
            print(f"ERROR: 字典與 CSV 不符 {bad[:5]}")
            return 1
        print(f"OK：字典 {len(plain)}＋位置鍵 {len(overrides)}＋樣板 {len(tpl)}；CSV 英文欄 sha256={sha}")
        return 0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text, encoding="utf-8")
    OUT_SRV.write_text(srv, encoding="utf-8")
    print(f"已寫入 {OUT}：字典 {len(plain)}＋位置鍵 {len(overrides)}＋樣板 {len(tpl)}；sha256={sha}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main("--check" in sys.argv))
    except Exception as exc:
        print(f"ERROR: {exc!r}")
        sys.exit(2)
