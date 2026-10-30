"""Extract the Chinese strings users can see and build a Chinese-English map (docs/i18n/zh_en_map.csv). Changes no program code.

The English column of new strings is left blank; the author finalizes it, and it is then filled in verbatim and applied back (tools/build_i18n.py etc.).
Sources: web/index.html (text, alt, aria-label, title), web/app.js (string literals, comments skipped),
app/presets.json (label, description), app/full_looks.json (why, title, tip, source),
app/*.py (string literals that are not docstrings; only those containing Chinese). Each sentence is listed once, with all of its locations.

  python3 tools/extract_ui_strings.py            # write the CSV; rows that already have English keep it
"""
from __future__ import annotations

import ast
import csv
import io
import json
import re
import tokenize
from html.parser import HTMLParser
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from jslex import tokens  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "i18n" / "zh_en_map.csv"
CJK = re.compile(r"[㐀-鿿＀-￯　-〿]")
WEB_JS_EXTRA = ["web/camo_editor.js", "web/capture_guide.js", "web/makeup_layers.js", "web/shade_preview.js"]   # added by the stage 2 extra scan, 10-21
PY_FILES = ["app/color_engine.py", "app/recommend.py", "app/full_look.py", "app/looks.py", "app/skin_card.py",
            "app/server.py", "app/face_rules.py", "app/brow_card.py", "app/care_rules_albinism.py",
            "app/care_rules_vitiligo.py", "app/patch_detect.py"]       # added by the stage 3 extra scan


def has_cjk(s: str) -> bool:
    return bool(CJK.search(s))


class HtmlText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip += 1
        for k, v in attrs:
            if k in ("alt", "aria-label", "title", "placeholder") and v and has_cjk(v):
                self.rows.append((self.getpos()[0], f"<{tag} {k}>", v))

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.skip -= 1

    def handle_data(self, data):
        t = " ".join(data.split())
        if not self.skip and t and has_cjk(t):
            self.rows.append((self.getpos()[0], "html text", t))


def from_html(rel):
    p = HtmlText()
    p.feed((ROOT / rel).read_text(encoding="utf-8"))
    return [(rel, ln, ctx, s) for ln, ctx, s in p.rows]


JS_TOKEN = re.compile(r"//[^\n]*|/\*.*?\*/|'(?:\\.|[^'\\\n])*'|\"(?:\\.|[^\"\\\n])*\"|`(?:\\.|[^`\\])*`", re.S)


def from_js(rel):
    src = (ROOT / rel).read_text(encoding="utf-8")
    out = []
    for a_, b_, kind in tokens(src):          # tools/jslex.py: correctly handles templates nested inside template strings
        if kind not in ("str", "tpl"):
            continue
        body = src[a_ + 1:b_ - 1]
        if has_cjk(body):
            out.append((rel, src.count("\n", 0, a_) + 1, "js 字串", " ".join(body.split())))
    return out


def from_py(rel):
    src = (ROOT / rel).read_text(encoding="utf-8")
    toks = list(tokenize.generate_tokens(io.StringIO(src).readline))
    out = []
    for i, tk in enumerate(toks):
        if tk.type != tokenize.STRING or not has_cjk(tk.string):
            continue
        j = i - 1
        while j >= 0 and toks[j].type in (tokenize.NL, tokenize.COMMENT):
            j -= 1
        k = i + 1
        while k < len(toks) and toks[k].type in (tokenize.NL, tokenize.COMMENT):
            k += 1
        if (j < 0 or toks[j].type in (tokenize.INDENT, tokenize.NEWLINE, tokenize.DEDENT, tokenize.ENCODING)) and toks[k].type == tokenize.NEWLINE:
            continue                                   # docstring: internal documentation, not on-screen text
        s = tk.string
        s = re.sub(r"^[rbuRBUfF]*('''|\"\"\"|'|\")", "", s)
        s = re.sub(r"('''|\"\"\"|'|\")$", "", s)
        out.append((rel, tk.start[0], "py 字串", " ".join(s.split())))
    return out


def from_py_ast(rel):
    """Stage 3 addition: scan again with the AST, covering f-strings (tokenize splits f-strings into fragments on 3.12 and misses them) and
    implicitly concatenated strings ("a" "b" becomes one sentence). f-strings are recorded as they appear in the source, e.g. the swatch-level sentence containing {level}."""
    src = (ROOT / rel).read_text(encoding="utf-8")
    tree = ast.parse(src)
    parent = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parent[child] = node
    out, skip_ids = [], set()
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            for sub in ast.walk(node):
                if sub is not node:
                    skip_ids.add(id(sub))
    for node in ast.walk(tree):
        if id(node) in skip_ids:
            continue
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and has_cjk(node.value):
            if isinstance(parent.get(node), ast.Expr):
                continue                                   # docstring
            out.append((rel, node.lineno, "py 字串", " ".join(node.value.split())))
        elif isinstance(node, ast.JoinedStr):
            seg = ast.get_source_segment(src, node) or ""
            m = re.match(r"^[rbuRBUfF]*('{3}|\"{3}|'|\")", seg)
            if not m:
                continue
            q = m.group(1)
            body = seg[m.end():-len(q)] if seg.endswith(q) else seg[m.end():]
            if has_cjk(body):
                out.append((rel, node.lineno, "py f-string", " ".join(body.split())))
    return out


def from_json(rel, keys):
    data = json.loads((ROOT / rel).read_text(encoding="utf-8"))
    out = []

    def walk(o, path):
        if isinstance(o, dict):
            for k, v in o.items():
                if k in keys and isinstance(v, str) and has_cjk(v):
                    out.append((rel, 0, f"{path}.{k}", v))
                else:
                    walk(v, f"{path}.{k}")
        elif isinstance(o, list):
            for n, v in enumerate(o):
                walk(v, f"{path}[{n}]")
    walk(data, "$")
    return out


def main() -> int:
    rows = from_html("web/index.html") + from_js("web/app.js")
    for f in WEB_JS_EXTRA:
        rows += from_js(f)
    rows += from_json("app/presets.json", {"label", "description", "limits"})
    rows += from_json("app/catalog.json", {"brand", "name", "code"})            # demo catalog (fictional brands); added by the stage 3 extra scan
    rows += from_json("demo/final/recipes.json", {"title", "credit", "item", "where"})   # finalized makeup recipe cards; added by the stage 3 extra scan
    rows += from_json("app/full_looks.json", {"why", "title", "tip", "source"})
    rows += from_json("demo/expected/vitiligo_20551205.json", {"how", "code"})      # expected makeup description and layer shade names for the lighter-patch demo photo (returned by /api/demo-photo)
    for f in PY_FILES:
        rows += from_py(f)
        rows += from_py_ast(f)
    old, old_id = {}, {}
    if OUT.exists():
        with OUT.open(encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                old[r["zh"]] = r.get("en", "")
                old_id[r["zh"]] = r["id"]
    en_echo = {" ".join(v.split()) for z, v in old.items() if v.strip() and v != z}   # If the English column's content appears in the source (English templates inserted by the tools), it is not Chinese original text; rows where English == Chinese (match strings used by the program) do not count
    rows = [r for r in rows if r[3] not in en_echo]
    merged: dict[str, dict] = {}
    for rel, ln, ctx, zh in rows:
        r = merged.setdefault(zh, {"zh": zh, "where": [], "context": ctx})
        r["where"].append(f"{rel}:{ln}" if ln else rel)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["id", "zh", "en", "context", "where"])
        nxt = max([int(i[1:]) for i in old_id.values()] + [0]) + 1
        for r in merged.values():
            if r["zh"] in old_id:
                rid = old_id[r["zh"]]
            else:
                rid, nxt = f"s{nxt:04d}", nxt + 1
            r["id"] = rid
        for r in sorted(merged.values(), key=lambda x: int(x["id"][1:])):
            w.writerow([r["id"], r["zh"], old.get(r["zh"], ""), r["context"], " ".join(r["where"][:6])])
    print(f"{len(merged)} sentences ({len(rows)} locations) → {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
