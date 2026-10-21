"""伺服器端回傳文字的英文化（第 3 階段）。英文逐字取自 app/i18n_en.json（tools/build_i18n.py 由 docs/i18n/zh_en_map.csv 產生）。

做法：在回傳 JSON 之前（server.py 的 _json）把每個字串值換成英文；只在請求帶 X-App-Lang: en（或 ?lang=en）時才動，
中文模式完全不經過這裡，回傳與原本位元組相同。
- 完全相同的句子（空白壓成一格後比對）→ 直接換。
- f-string 句子（例如「差 {level_diff} 級」）→ 以樣式比對，再把抓到的值放進英文樣板；抓到的值若本身是中文（例如級別名稱）也會再換一次。
- 連串多句（用「。」「；」接起來的字串）→ 每一句都在表裡才拆開換，英文以一個空格相接；有任何一句不在表裡就維持原樣（由稽核工具 tools/audit_api_strings.py 抓出來補表）。
表裡沒有的字串一律原樣回傳，不猜、不改寫。
防護（紅隊 F1）：用戶端送來的字串也可能原樣回波到回應裡，樣式比對的 regex 有多個「任意字」群組，對惡意長字串會變成多項式時間。
所以：超過 MAX_TEMPLATE 字的字串不做樣式比對（伺服器自己產生的句子都很短，最長一句 133 字）；
超過 MAX_TOTAL 字的字串完全不處理；樣式比對前先確認樣式裡的固定文字依序都出現過（不成立就不必跑 regex）。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

DATA = Path(__file__).with_name("i18n_en.json")
CJK = re.compile(r"[㐀-鿿＀-￯　-〿]")
PLACEHOLDER = re.compile(r"\{[^{}]*\}")
SENTENCE_END = re.compile(r"(?<=[。；！？])")
MAX_TEMPLATE = 400        # 超過就不跑樣式比對
MAX_TOTAL = 3000          # 超過就完全不處理（原樣回傳）


def norm(s: str) -> str:
    return " ".join(s.split())


class Localizer:
    def __init__(self, data: dict):
        self.exact = {norm(k): v for k, v in data.get("exact", {}).items()}
        self.templates = []
        for zh, en in data.get("templates", []):
            parts = PLACEHOLDER.split(zh)
            holders = PLACEHOLDER.findall(zh)
            if not holders:
                continue
            rx = "".join(re.escape(p) + (f"(?P<g{i}>.+?)" if i < len(holders) else "") for i, p in enumerate(parts))
            literals = [p for p in parts if p]
            self.templates.append((re.compile(rx + r"\Z", re.S), holders, en, literals))
        self.templates.sort(key=lambda t: -len(t[0].pattern))      # 較具體（較長）的樣式先試

    @staticmethod
    def _literals_in_order(key: str, literals: list) -> bool:
        pos = 0
        for lit in literals:
            i = key.find(lit, pos)
            if i < 0:
                return False
            pos = i + len(lit)
        return True

    def _fill(self, m: re.Match, holders: list, en: str) -> str:
        values = {}
        for i, h in enumerate(holders):
            values.setdefault(h, []).append(self.tr(m.group(f"g{i}")))
        used = {}

        def sub(mm: re.Match) -> str:
            h = mm.group(0)
            lst = values.get(h)
            if not lst:
                return h
            k = used.get(h, 0)
            used[h] = k + 1
            return lst[min(k, len(lst) - 1)]
        return PLACEHOLDER.sub(sub, en)

    def tr(self, s: str, _depth: int = 0) -> str:
        if not isinstance(s, str) or not CJK.search(s):
            return s
        if len(s) > MAX_TOTAL:
            return s
        key = norm(s)
        hit = self.exact.get(key)
        if hit is not None:
            return hit
        for rx, holders, en, literals in (self.templates if len(key) <= MAX_TEMPLATE else ()):
            if not self._literals_in_order(key, literals):
                continue
            m = rx.match(key)
            if m:
                return self._fill(m, holders, en)
        bare = key.rstrip("。；！？")                                # 句尾標點不影響比對（表裡有的句子可能沒帶句尾標點）
        if bare != key and bare:
            t = self.tr(bare, 1)
            if not CJK.search(t):
                return t
        if _depth == 0:                                           # 多句相接：每一句都在表裡才換
            pieces = [p for p in SENTENCE_END.split(key) if p.strip()]
            if len(pieces) > 1:
                out = []
                for p in pieces:
                    t = self.tr(p, 1)
                    if CJK.search(t):
                        return s
                    out.append(t)
                return " ".join(out)
        return s

    def localize(self, obj):
        if isinstance(obj, str):
            return self.tr(obj)
        if isinstance(obj, list):
            return [self.localize(x) for x in obj]
        if isinstance(obj, tuple):
            return [self.localize(x) for x in obj]
        if isinstance(obj, dict):
            return {k: self.localize(v) for k, v in obj.items()}
        return obj


_CACHE: dict = {}


def get() -> Localizer:
    if "l" not in _CACHE:
        data = json.loads(DATA.read_text(encoding="utf-8")) if DATA.exists() else {}
        _CACHE["l"] = Localizer(data)
    return _CACHE["l"]


def localize(obj):
    return get().localize(obj)
