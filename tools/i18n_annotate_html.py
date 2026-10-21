"""把 web/index.html 裡使用者看得到的中文標上 data-i18n（只加屬性或包 <span>，不改任何中文字、不改結構以外的東西）。

  python3 tools/i18n_annotate_html.py            # 就地改寫 web/index.html；已標過的不重複標
  python3 tools/i18n_annotate_html.py --check    # 只檢查：回報還沒標的列，有缺就 exit 1

規則
- 文字節點：該元素只有這一段文字 → 在開始標籤加 data-i18n="sNNNN"；元素裡還有別的標籤或文字 → 把這段文字包進 <span data-i18n="sNNNN">。
- 屬性：alt／aria-label／title 的值等於表內中文 → 加 data-i18n-attr="屬性:sNNNN;屬性:sNNNN"。
- 位置專屬鍵：docs/i18n/position_overrides.csv 列出的 (id, 檔:行) 改用鍵 "sNNNN@web/index.html:行"。
資料來源：docs/i18n/zh_en_map.csv（zh→id）。失敗路徑印 ERROR 並回非零。
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
        self.stack = []            # {'tag','s','e','kids'} ；kids = 有內容的子節點數
        self.edits = []            # (pos, insert_text) 或 (start, end, replacement)
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
                # 只有一段文字、沒有其他子節點 → 屬性標在開始標籤
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
    # 第一遍：解析，收集需要包 <span> 的節點
    sc = Scan(text, zh2id, overrides)
    sc.feed(text)
    sc.close()
    # 需要包 span 的文字節點：元素內有其他內容者
    wraps = []
    # 重新走一遍堆疊資訊較複雜，改用第二個掃描器專門找「混合內容」節點
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
    print(f"文字節點屬性標記 {sc.marked_text}、混合內容包 span {len(mx.mixed)}、屬性標記 {sc.marked_attr}、表外中文 {len(unmatched)}")
    for pos, t in unmatched[:20]:
        print(f"  表外：行 {text.count(chr(10), 0, pos) + 1}：{t[:40]}")
    if check_only:
        left = len(todo_edits)
        print(f"還需標記 {left} 處")
        return 1 if left or unmatched else 0
    if not todo_edits:
        print("沒有需要改的地方")
        return 0
    # 由後往前套用，位置才不會位移
    out = text
    for edit in sorted(todo_edits, key=lambda x: (x[0], x[1]), reverse=True):
        a, b, rep = edit
        out = out[:a] + rep + out[b:]
    HTML.write_text(out, encoding="utf-8")
    print(f"已寫入 {HTML}（{len(todo_edits)} 處）")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(run("--check" in sys.argv))
    except Exception as exc:  # 失敗必須留痕
        print(f"ERROR: {exc!r}")
        sys.exit(2)
