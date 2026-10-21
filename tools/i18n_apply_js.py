"""把 web 下 JS 檔裡使用者看得到的中文字串包成 t()／T()，英文逐字取自 docs/i18n/zh_en_map.csv。

  python3 tools/i18n_apply_js.py --dry     # 只報告：會改幾處、哪些略過（比對用字串、物件鍵、表外中文）
  python3 tools/i18n_apply_js.py           # 就地改寫；已包過的不重複包
  python3 tools/i18n_apply_js.py --check   # 只檢查：還有沒包的、表外的中文就 exit 1

改法（中文模式輸出與原本逐字相同）
- 一般字串  'XX'        →  __t('sNNNN', 'XX')                     英文取自 web/i18n/en.js 字典
- 樣板字串  `…${v}…`    →  __T('sNNNN', () => `…${v}…`, () => `<英文樣板>`)   兩個都是呼叫處的閉包，變數照常可用；
                                                                  英文樣板由本工具逐字從 CSV 複製進來
略過（回報給人工處理）：比對用字串（=== 、includes( 、case ）、物件鍵、表外中文、英文欄是空的列。
失敗路徑印 ERROR 並回非零。
"""
from __future__ import annotations

import csv
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from jslex import tokens  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
MAP = ROOT / "docs" / "i18n" / "zh_en_map.csv"
FILES = ["web/app.js", "web/capture_guide.js", "web/makeup_layers.js", "web/camo_editor.js", "web/shade_preview.js"]
CJK = re.compile(r"[㐀-鿿＀-￯　-〿]")
TOKEN = re.compile(r"//[^\n]*|/\*.*?\*/|'(?:\\.|[^'\\\n])*'|\"(?:\\.|[^\"\\\n])*\"|`(?:\\.|[^`\\])*`", re.S)
WRAPPED = re.compile(r"""(?:\b__t\(\s*'s\d+(?:@[^']*)?',\s*|\b__T\(\s*'s\d+',\s*\(\)\s*=>\s*)$""")
COMPARE_BEFORE = re.compile(r"(===?|!==?|\bcase)\s*$|\.(includes|indexOf|startsWith|endsWith|test|split|replace|replaceAll|match|search)\(\s*$|[\w\])]\[\s*$")
COMPARE_AFTER = re.compile(r"^\s*(===?|!==?)")
KEY_AFTER = re.compile(r"^\s*:")
KEY_BEFORE = re.compile(r"[{,]\s*$")


def norm(s: str) -> str:
    return " ".join(s.split())


EN_BODIES: set = set()


def load():
    rows = list(csv.DictReader(open(MAP, encoding="utf-8")))
    EN_BODIES.update(norm(r["en"]) for r in rows if r["en"].strip())
    return {r["zh"]: r for r in rows}


def scan(rel, by_zh):
    src = (ROOT / rel).read_text(encoding="utf-8")
    edits, skipped, wrapped = [], [], 0
    for a_, b_, kind in tokens(src):
        if kind not in ("str", "tpl"):
            continue
        tok = src[a_:b_]
        body = tok[1:-1]
        if not CJK.search(body):
            continue
        line = src.count("\n", 0, a_) + 1
        before, after = src[max(0, a_ - 40):a_], src[b_:b_ + 6]
        if WRAPPED.search(before):
            wrapped += 1
            continue
        if re.search(r"/\*i18n:keep\*/\s*$", before):
            skipped.append((rel, line, "標記保留（比對伺服器字串）", norm(body)[:50]))
            continue
        if norm(body) in EN_BODIES:                      # 本工具先前插進去的英文樣板，不是待處理的中文
            continue
        row = by_zh.get(norm(body))
        if row is None:
            skipped.append((rel, line, "表外中文", norm(body)[:50]))
            continue
        if COMPARE_BEFORE.search(before) or COMPARE_AFTER.search(after):
            skipped.append((rel, line, "比對用字串（保留中文）", norm(body)[:50]))
            continue
        if KEY_AFTER.search(after) and KEY_BEFORE.search(before):
            skipped.append((rel, line, "物件鍵（保留中文）", norm(body)[:50]))
            continue
        if not row["en"].strip() and row["id"] != "s0008":
            skipped.append((rel, line, "英文欄是空的", norm(body)[:50]))
            continue
        if tok[0] == "`":
            en = row["en"]
            wrapped_en = "`" + en + "`"
            if "\\" in en or tokens(wrapped_en) != [(0, len(wrapped_en), "tpl")]:   # 巢狀的反引號只能出現在 ${…} 裡
                skipped.append((rel, line, "英文樣板含不合法的反引號或反斜線", norm(body)[:50]))
                continue
            rep = f"__T('{row['id']}', () => {tok}, () => `{en}`)"
        else:
            rep = f"__t('{row['id']}', {tok})"
        edits.append((a_, b_, rep))
    return src, edits, skipped, wrapped


def main(mode: str) -> int:
    by_zh = load()
    total_edit, all_skipped = 0, []
    for rel in FILES:
        src, edits, skipped, wrapped = scan(rel, by_zh)
        all_skipped += skipped
        total_edit += len(edits)
        print(f"{rel}：待包 {len(edits)}、已包 {wrapped}、略過 {len(skipped)}")
        if mode == "apply" and edits:
            out = src
            for a, b, rep in sorted(edits, reverse=True):
                out = out[:a] + rep + out[b:]
            (ROOT / rel).write_text(out, encoding="utf-8")
    for rel, line, why, text in all_skipped:
        print(f"  略過 {rel}:{line} [{why}] {text}")
    if mode == "check":
        return 1 if total_edit or any(w in ("表外中文", "英文欄是空的") for _, _, w, _ in all_skipped) else 0
    print(f"合計{'已改' if mode == 'apply' else '待改'} {total_edit} 處")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main("check" if "--check" in sys.argv else "dry" if "--dry" in sys.argv else "apply"))
    except Exception as exc:
        print(f"ERROR: {exc!r}")
        sys.exit(2)
