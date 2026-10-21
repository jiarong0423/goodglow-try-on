"""檯面上換代稱、內部維持真實資料：錄影、截圖、公開頁面用（--brand-alias）。


- 內部：色號比對、排序、色差計算仍用真實品牌資料，結果不變。
- 檯面：伺服器回傳 JSON 之前，把真實品牌名換成字母代稱（中文「M 牌」、英文「Brand M」），
  產品名（也是商標）換成品類（粉底、遮瑕…／Foundation、Concealer…）；
  色號只留代碼、去掉色名（「Pearl (X-004)」→「X-004」、「WX 64 Honeyed」→「WX 64」）；
  沒有代碼只有色名的色號，改成「No.序號」（序號＝該產品色號清單裡的位置，固定不變）；色碼（#RRGGBB）不動。
對照表放 app/brand_alias.json（本機檔，不進版控）；格式見 app/brand_alias.example.json。
找不到對照表或品牌資料時不啟用並回報，不會假裝已經代稱。
圖片（data: 網址、很長的 base64）一律原樣放行，不做文字代換，避免把圖壓壞。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
ALIAS_FILE = HERE / "brand_alias.json"
PARTNER_FILES = [HERE / "partner_shades_official.json", HERE / "partner_shades.json"]   # 官網現行＋2018 公開色票（有的都讀）
CATEGORY_LABEL = {
    "zh": {"foundation": "粉底", "concealer": "遮瑕", "corrector": "校色", "blush": "腮紅", "lip": "唇彩",
           "brow": "眉筆", "lash": "睫毛膏"},
    "en": {"foundation": "Foundation", "concealer": "Concealer", "corrector": "Corrector", "blush": "Blush",
           "lip": "Lip color", "brow": "Brow product", "lash": "Mascara"},
}
SHADE_KEYS = ("code", "lighter", "deeper")      # 這些欄位裡放的是色號全名（要搭配同一層的 product 才知道是哪一個）
LONG_PLAIN = 2000                                # 沒有空白又超過這個長度的字串視為編碼資料（圖片等），不處理

_PAREN_CODE = re.compile(r"\(([^()]*\d[^()]*)\)")                           # Pearl (X-004) → X-004；Mild (204) Tinted → 204
_LEAD_CODE = re.compile(r"^(?:[A-Z]{1,3}\s*)?[#.]?\d[\w.\-]*(?=\s|$)")       # AB5、WX 64 Honeyed、3D1 Cool Bone、.5X、#01
_NUM_TONE = re.compile(r"^[A-Za-z][A-Za-z ]*?\s(\d+[A-Za-z]?)\s(Cool|Warm|Neutral)$")   # Light 100 Cool → 100 Cool


def shade_alias(label: str, index: int) -> str:
    """色號全名 → 只留代碼的檯面寫法；index 為該產品色號清單中的位置（1 起算），無代碼時當序號用。"""
    s = " ".join(str(label).split())
    m = _PAREN_CODE.search(s)
    if m:
        return m.group(1).strip()
    m = _LEAD_CODE.match(s)
    if m:
        return m.group(0).strip()
    m = _NUM_TONE.match(s)
    if m:
        return f"{m.group(1)} {m.group(2)}"
    return f"No.{index}"


def is_opaque(s: str) -> bool:
    """圖片／長編碼字串：不做文字代換也不做稽核比對。"""
    return s.startswith("data:") or (len(s) > LONG_PLAIN and not any(c.isspace() for c in s))


class BrandAlias:
    def __init__(self, aliases: dict, products: dict, shades: dict | None = None):
        """aliases：真實品牌名 → 字母代稱；products：真實產品名 → 品類；
        shades：真實產品名 → 該產品色號全名清單（順序固定）。"""
        letters = list(aliases.values())
        if len(set(letters)) != len(letters):
            raise ValueError("代稱必須互不相同")
        self.aliases = dict(aliases)
        self.products = dict(products)
        self._prod_order = sorted(self.products, key=len, reverse=True)
        self._brand_rx = {b: re.compile(r"(?<![A-Za-z])" + re.escape(b) + r"(?![A-Za-z])") for b in self.aliases}
        self.shade_map: dict[str, dict[str, str]] = {}
        for prod, labels in (shades or {}).items():
            used: dict[str, int] = {}
            m: dict[str, str] = {}
            for i, lab in enumerate(labels, 1):
                a = shade_alias(lab, i)
                n = used.get(a, 0)
                used[a] = n + 1
                m[lab] = a if n == 0 else f"{a}-{n + 1}"        # 同一產品內撞名時加尾碼，保證檯面上不混淆
            self.shade_map[prod] = m
        # 自由文字裡的色號全名：只換「有代碼的複合名稱」，且各產品換法一致；單純的色名（Peach、Stud…）不做全域代換，
        # 因為會誤傷一般用語，這類色號只在結構化欄位（code／lighter／deeper）換。
        glob: dict[str, set] = {}
        for m in self.shade_map.values():
            for lab, a in m.items():
                glob.setdefault(lab, set()).add(a)
        self._text_shades = sorted(((lab, next(iter(a))) for lab, a in glob.items()
                                    if len(a) == 1 and next(iter(a)) != lab and not next(iter(a)).startswith("No.")),
                                   key=lambda t: -len(t[0]))
        self._text_rx = [(re.compile(r"(?<![\w.#-])" + re.escape(lab) + r"(?![\w-])"), a) for lab, a in self._text_shades]

    # ------------------------------------------------------------ 換
    def scrub_text(self, s: str, lang: str = "zh") -> str:
        if is_opaque(s):
            return s
        labels = CATEGORY_LABEL["en" if lang == "en" else "zh"]
        for name in self._prod_order:
            if name in s:
                s = s.replace(name, labels.get(self.products[name], labels["foundation"]))
        for rx, a in self._text_rx:
            s = rx.sub(a, s)
        for brand, letter in self.aliases.items():
            s = self._brand_rx[brand].sub(f"Brand {letter}" if lang == "en" else f"{letter} 牌", s)
        return s

    def scrub(self, obj, lang: str = "zh", _prod: str | None = None):
        if isinstance(obj, str):
            return self.scrub_text(obj, lang)
        if isinstance(obj, (list, tuple)):
            return [self.scrub(x, lang, _prod) for x in obj]
        if isinstance(obj, dict):
            prod = obj.get("product")
            ctx = prod if isinstance(prod, str) and prod in self.shade_map else _prod
            out = {}
            for k, v in obj.items():
                if k in SHADE_KEYS and isinstance(v, str) and ctx and v in self.shade_map[ctx]:
                    out[k] = self.shade_map[ctx][v]
                else:
                    out[k] = self.scrub(v, lang, ctx)
            return out
        return obj

    # ------------------------------------------------------------ 稽核
    def real_shade_names(self) -> list:
        """被改掉的色號全名（含「色名」的那些）。稽核用：回傳內容不應再出現它們。"""
        names = set()
        for m in self.shade_map.values():
            for lab, a in m.items():
                if a != lab:
                    names.add(lab)
        return sorted(names, key=lambda x: -len(x))

    def leaks(self, obj) -> list:
        """檢查用：回傳物件裡還看得到的真實品牌名／產品名／（結構化欄位中的）色號全名。"""
        found = set()

        def walk(o, prod):
            if isinstance(o, str):
                if is_opaque(o):
                    return
                for name in self._prod_order:
                    if name in o:
                        found.add(name)
                for brand, rx in self._brand_rx.items():
                    if rx.search(o):
                        found.add(brand)
                for lab, _a in self._text_shades:
                    if re.search(r"(?<![\w.#-])" + re.escape(lab) + r"(?![\w-])", o):
                        found.add(lab)
            elif isinstance(o, (list, tuple)):
                for x in o:
                    walk(x, prod)
            elif isinstance(o, dict):
                p = o.get("product")
                ctx = p if isinstance(p, str) and p in self.shade_map else prod
                for k, v in o.items():
                    if k in SHADE_KEYS and isinstance(v, str) and ctx and v in self.shade_map[ctx]:
                        if self.shade_map[ctx][v] != v:
                            found.add(v)
                    else:
                        walk(v, ctx)
        walk(obj, None)
        return sorted(found)


def load(alias_file: Path = ALIAS_FILE, partner_files=None) -> BrandAlias:
    partner_files = PARTNER_FILES if partner_files is None else (
        [partner_files] if isinstance(partner_files, Path) else list(partner_files))
    if not alias_file.is_file():
        raise FileNotFoundError(f"找不到代稱對照表 {alias_file.name}（範例見 brand_alias.example.json）")
    present = [f for f in partner_files if f.is_file()]
    if not present:
        raise FileNotFoundError(f"找不到品牌資料 {partner_files[0].name}；沒有真實品牌資料就不需要代稱模式")
    aliases = json.loads(alias_file.read_text(encoding="utf-8"))
    products: dict = {}
    shades: dict = {}
    brands: set = set()
    for f in present:                                   # 所有合作品牌檔都納入：用戶端可用 source=partner／official 選資料來源
        data = json.loads(f.read_text(encoding="utf-8"))
        for p in data.get("products", []):
            if p.get("brand"):
                brands.add(p["brand"])
            if p.get("name"):
                products[p["name"]] = p.get("category", "foundation")
                labels = [s["code"] for s in p.get("shades", []) if s.get("code")]
                seen = shades.setdefault(p["name"], [])
                for lab in labels:
                    if lab not in seen:
                        seen.append(lab)
    missing = sorted(brands - set(aliases))
    if missing:
        raise ValueError(f"對照表缺少品牌：{missing}")
    return BrandAlias(aliases, products, shades)
