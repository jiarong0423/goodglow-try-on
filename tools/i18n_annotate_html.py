"""Annotate the user-visible Chinese in web/index.html with data-i18n (only adds attributes or wraps text in <span>; no Chinese character and nothing beyond that structure is changed).

  python3 tools/i18n_annotate_html.py            # rewrite web/index.html in place; already-annotated text is not annotated again
  python3 tools/i18n_annotate_html.py --check    # check only: report rows not yet annotated; exit 1 if any are missing

Rules
- Text nodes: if the element holds only this one piece of text, add data-i18n="sNNNN" to the opening tag; if the element also holds other tags or text, wrap this text in <span data-i18n="sNNNN">.
- Attributes: if the value of alt/aria-label/title equals a Chinese string in the table, add data-i18n-attr="attribute:sNNNN;attribute:sNNNN".
- Position-specific keys: the (id, file:line) pairs listed in docs/i18n/position_overrides.csv use the key "sNNNN@web/index.html:line".
Data source: docs/i18n/zh_en_map.csv (zh→id). Failure paths print ERROR and return non-zero.
"""
from __future__ import annotations

import csv
import sys
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HTML = ROOT / "web" / "index.html"
MAP = ROOT / "docs" / "i18n" / "zh_en_map.csv"
OVR = ROOT / "docs" / "i18n" / "position_overrides.csv"
FILE_KEY = "web/index.html"
ATTRS = ("alt", "aria-label", "title")
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}


def load_maps():
    zh2id, text_ids = {}, {}
    for r in csv.DictReader(open(MAP, encoding="utf-8")):
        zh2id[r["zh"]] = r["id"]
    overrides = {}
    if OVR.exists():
        for r in csv.DictReader(open(OVR, encoding="utf-8")):
            overrides[r["key"]] = r
    return zh2id, overrides


class Scan(HTMLParser):
    def __init__(self, text, zh2id, overrides):
        super().__init__(convert_charrefs=False)
        self.text, self.zh2id, self.overrides = text, zh2id, overrides
        starts = [0]
        for line in text.split("\n"):
            starts.append(starts[-1] + len(line) + 1)
        self.line_start = starts
        self.stack = []            # {'tag','s','e','kids'} ; kids = number of child nodes with content
        self.edits = []            # (pos, insert_text) or (start, end, replacement)
        self.marked_text, self.marked_attr, self.unmatched = 0, 0, []

    def off(self):
        line, col = self.getpos()
        return self.line_start[line - 1] + col

    def key_for(self, id_, pos):
        line = self.text.count("\n", 0, pos) + 1
        k = f"{id_}@{FILE_KEY}:{line}"
        return k if k in self.overrides else id_

    def handle_starttag(self, tag, attrs):
        s = self.off()
        e = s + len(self.get_starttag_text())
        raw = self.get_starttag_text()
        have = dict(attrs)
        if "data-i18n-attr" not in have:
            pairs = []
            for a in ATTRS:
                v = have.get(a)
                if v and v in self.zh2id:
                    pairs.append(f"{a}:{self.key_for(self.zh2id[v], s)}")
            if pairs:
                ins = e - (2 if raw.endswith("/>") else 1)
                self.edits.append((ins, ins, f' data-i18n-attr="{";".join(pairs)}"'))
                self.marked_attr += len(pairs)
        if self.stack:
            self.stack[-1]["kids"] += 1
        if tag not in VOID and not raw.endswith("/>"):
            self.stack.append({"tag": tag, "s": s, "e": e, "kids": 0, "have": have, "texts": []})

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        while self.stack:
            top = self.stack.pop()
            if top["tag"] == tag:
                # only one piece of text and no other child nodes → the attribute goes on the opening tag
                if top["kids"] == len(top["texts"]) == 1 and "data-i18n" not in top["have"]:
                    pos, id_ = top["texts"][0]
                    ins = top["e"] - 1
                    self.edits.append((ins, ins, f' data-i18n="{self.key_for(id_, pos)}"'))
                    self.marked_text += 1
                break

    def handle_data(self, data):
        t = data.strip()
        if not t or not self.stack:
            return
        top = self.stack[-1]
        if top["tag"] in ("script", "style"):
            return
        top["kids"] += 1
        pos = self.off() + data.index(t)
        if t in self.zh2id:
            top.setdefault("nodes", []).append((pos, self.zh2id[t], t, data))
            top["texts"].append((pos, self.zh2id[t]))
        elif any("㐀" <= ch <= "鿿" for ch in t):
            self.unmatched.append((pos, t))


def run(check_only: bool) -> int:
    zh2id, overrides = load_maps()
    text = HTML.read_text(encoding="utf-8")
    # first pass: parse and collect the nodes that need a <span> wrapper
    sc = Scan(text, zh2id, overrides)
    sc.feed(text)
    sc.close()
    # text nodes that need a span: those whose element has other content
    wraps = []
    # re-walking the stack information is more complicated, so use a second scanner dedicated to finding mixed-content nodes
    class Mixed(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=False)
            self.stack = []
            self.mixed = []
        def off(self):
            line, col = self.getpos()
            return sc.line_start[line - 1] + col
        def handle_starttag(self, tag, attrs):
            raw = self.get_starttag_text()
            if self.stack:
                self.stack[-1]["kids"].append(("tag", self.off()))
            if tag not in VOID and not raw.endswith("/>"):
                self.stack.append({"tag": tag, "have": dict(attrs), "kids": []})
        def handle_startendtag(self, tag, attrs):
            if self.stack:
                self.stack[-1]["kids"].append(("tag", self.off()))
        def handle_endtag(self, tag):
            while self.stack:
                top = self.stack.pop()
                if top["tag"] == tag:
                    kinds = top["kids"]
                    if len(kinds) > 1 or (kinds and kinds[0][0] == "text" and "data-i18n" in top["have"]):
                        for k in kinds:
                            if k[0] == "text" and k[2] in zh2id and "data-i18n" not in top["have"]:
                                self.mixed.append(k)
                    break
        def handle_data(self, data):
            t = data.strip()
            if t and self.stack and self.stack[-1]["tag"] not in ("script", "style"):
                self.stack[-1]["kids"].append(("text", self.off() + data.index(t), t))
    mx = Mixed()
    mx.feed(text)
    mx.close()
    todo_edits = [e for e in sc.edits]
    for _, pos, t in mx.mixed:
        id_ = sc.key_for(zh2id[t], pos)
        todo_edits.append((pos, pos + len(t), f'<span data-i18n="{id_}">{t}</span>'))
    unmatched = [u for u in sc.unmatched]
    print(f"text-node attribute marks {sc.marked_text}, mixed-content span wraps {len(mx.mixed)}, attribute marks {sc.marked_attr}, Chinese not in the table {len(unmatched)}")
    for pos, t in unmatched[:20]:
        print(f"  not in table: line {text.count(chr(10), 0, pos) + 1}: {t[:40]}")
    if check_only:
        left = len(todo_edits)
        print(f"{left} more places need marking")
        return 1 if left or unmatched else 0
    if not todo_edits:
        print("nothing to change")
        return 0
    # apply from back to front so positions do not shift
    out = text
    for edit in sorted(todo_edits, key=lambda x: (x[0], x[1]), reverse=True):
        a, b, rep = edit
        out = out[:a] + rep + out[b:]
    HTML.write_text(out, encoding="utf-8")
    print(f"Wrote {HTML} ({len(todo_edits)} places)")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(run("--check" in sys.argv))
    except Exception as exc:  # failures must leave a trace
        print(f"ERROR: {exc!r}")
        sys.exit(2)
