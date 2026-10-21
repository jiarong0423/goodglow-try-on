"""Wrap the user-visible Chinese strings in the JS files under web in t()/T(); the English is taken verbatim from docs/i18n/zh_en_map.csv.

  python3 tools/i18n_apply_js.py --dry     # report only: how many places would change and which are skipped (match strings, object keys, Chinese not in the table)
  python3 tools/i18n_apply_js.py           # rewrite in place; already-wrapped strings are not wrapped again
  python3 tools/i18n_apply_js.py --check   # check only: exit 1 if anything is still unwrapped or Chinese outside the table remains

How it rewrites (Chinese-mode output is identical to the original, character for character)
- Plain string  'XX'     →  __t('sNNNN', 'XX')                     English comes from the web/i18n/en.js dictionary
- Template string  `…${v}…`    →  __T('sNNNN', () => `…${v}…`, () => `<English template>`)   both are closures at the call site, so variables work as usual;
                                                                  the English template is copied verbatim from the CSV by this tool
Skipped (reported for manual handling): match strings (=== , includes( , case ), object keys, Chinese not in the table, and rows whose English column is empty.
Failure paths print ERROR and return non-zero.
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
        if norm(body) in EN_BODIES:                      # an English template this tool inserted earlier, not Chinese awaiting processing
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
            if "\\" in en or tokens(wrapped_en) != [(0, len(wrapped_en), "tpl")]:   # nested backticks may appear only inside ${…}
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
        print(f"{rel}: to wrap {len(edits)}, already wrapped {wrapped}, skipped {len(skipped)}")
        if mode == "apply" and edits:
            out = src
            for a, b, rep in sorted(edits, reverse=True):
                out = out[:a] + rep + out[b:]
            (ROOT / rel).write_text(out, encoding="utf-8")
    for rel, line, why, text in all_skipped:
        print(f"  skipped {rel}:{line} [{why}] {text}")
    if mode == "check":
        return 1 if total_edit or any(w in ("表外中文", "英文欄是空的") for _, _, w, _ in all_skipped) else 0
    print(f"Total {'changed' if mode == 'apply' else 'to change'}: {total_edit} places")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main("check" if "--check" in sys.argv else "dry" if "--dry" in sys.argv else "apply"))
    except Exception as exc:
        print(f"ERROR: {exc!r}")
        sys.exit(2)
