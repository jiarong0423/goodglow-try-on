"""Show aliases on the surface while keeping real data internally: for recordings, screenshots and public pages (--brand-alias).

Design decision (10-21): replace brand names with a capital letter on the surface, map internally; real shade codes are needed to be useful; keep only the code, drop the shade name.
- Internal: shade matching, ranking and color-difference computation still use the real brand data, so results do not change.
- Surface: before the server returns JSON, real brand names are replaced with a letter alias (English "Brand M", with an equivalent "M" form in Chinese),
    and product names (also trademarks) are replaced with a category (Foundation, Concealer, ... in English, the Chinese category names in Chinese);
    shades keep only the code and drop the shade name ("Pearl (X-004)" -> "X-004", "WX 64 Honeyed" -> "WX 64");
    shades that have a name but no code become "No.<n>" (n = position in that product's shade list, stable); color codes (#RRGGBB) are left alone.
The mapping table lives in app/brand_alias.json (local file, not version-controlled); see app/brand_alias.example.json for the format.
If the mapping table or brand data is missing, the mode is not enabled and this is reported; it never pretends to have aliased.
Images (data: URLs, very long base64) pass through untouched with no text substitution, so they are not corrupted.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
ALIAS_FILE = HERE / "brand_alias.json"
PARTNER_FILES = [HERE / "partner_shades_official.json", HERE / "partner_shades.json"]   # current official site + 2018 public swatches (all that exist are read)
CATEGORY_LABEL = {
    "zh": {"foundation": "粉底", "concealer": "遮瑕", "corrector": "校色", "blush": "腮紅", "lip": "唇彩",
           "brow": "眉筆", "lash": "睫毛膏"},
    "en": {"foundation": "Foundation", "concealer": "Concealer", "corrector": "Corrector", "blush": "Blush",
           "lip": "Lip color", "brow": "Brow product", "lash": "Mascara"},
}
SHADE_KEYS = ("code", "lighter", "deeper")      # these fields hold full shade names (the sibling product field is needed to know which one)
LONG_PLAIN = 2000                                # strings with no whitespace longer than this are treated as encoded data (images etc.) and skipped

_PAREN_CODE = re.compile(r"\(([^()]*\d[^()]*)\)")                           # Pearl (X-004) -> X-004; Mild (204) Tinted -> 204
_LEAD_CODE = re.compile(r"^(?:[A-Z]{1,3}\s*)?[#.]?\d[\w.\-]*(?=\s|$)")       # AB5, WX 64 Honeyed, 3D1 Cool Bone, .5X, #01
_NUM_TONE = re.compile(r"^[A-Za-z][A-Za-z ]*?\s(\d+[A-Za-z]?)\s(Cool|Warm|Neutral)$")   # Light 100 Cool → 100 Cool


def shade_alias(label: str, index: int) -> str:
    """Full shade name -> surface form with the code only; index is the position in the product's shade list (1-based), used as the number when there is no code."""
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
    """Images / long encoded strings: no text substitution and no audit comparison."""
    return s.startswith("data:") or (len(s) > LONG_PLAIN and not any(c.isspace() for c in s))


class BrandAlias:
    def __init__(self, aliases: dict, products: dict, shades: dict | None = None):
        """aliases: real brand name -> letter alias; products: real product name -> category;
        shades: real product name -> list of full shade names for that product (order is fixed)."""
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
                m[lab] = a if n == 0 else f"{a}-{n + 1}"        # on a name clash within one product add a suffix so the surface stays unambiguous
            self.shade_map[prod] = m
        # Full shade names in free text: only replace compound names that have a code, and the same way for every product; plain shade names (Peach, Stud...) are not replaced globally,
        # because that would hit ordinary words; such shades are replaced only in structured fields (code / lighter / deeper).
        glob: dict[str, set] = {}
        for m in self.shade_map.values():
            for lab, a in m.items():
                glob.setdefault(lab, set()).add(a)
        self._text_shades = sorted(((lab, next(iter(a))) for lab, a in glob.items()
                                    if len(a) == 1 and next(iter(a)) != lab and not next(iter(a)).startswith("No.")),
                                   key=lambda t: -len(t[0]))
        self._text_rx = [(re.compile(r"(?<![\w.#-])" + re.escape(lab) + r"(?![\w-])"), a) for lab, a in self._text_shades]

    # ------------------------------------------------------------ replace
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
                if k == "url" and isinstance(v, str):
                    continue                                   # a product link names the brand's domain: dropped on the surface, like the brand name
                if k in SHADE_KEYS and isinstance(v, str) and ctx and v in self.shade_map[ctx]:
                    out[k] = self.shade_map[ctx][v]
                else:
                    out[k] = self.scrub(v, lang, ctx)
            return out
        return obj

    # ------------------------------------------------------------ audit
    def real_shade_names(self) -> list:
        """Full shade names that were changed (those containing a shade name). For audit: the returned content must no longer contain them."""
        names = set()
        for m in self.shade_map.values():
            for lab, a in m.items():
                if a != lab:
                    names.add(lab)
        return sorted(names, key=lambda x: -len(x))

    def leaks(self, obj) -> list:
        """For checks: real brand names / product names / (in structured fields) full shade names still visible in the returned object."""
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
                    if k == "url" and isinstance(v, str) and v:
                        found.add(f"url:{v}")
                    elif k in SHADE_KEYS and isinstance(v, str) and ctx and v in self.shade_map[ctx]:
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
    for f in present:                                   # every partner brand file is included: the client can pick the data source via source=partner / official
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
