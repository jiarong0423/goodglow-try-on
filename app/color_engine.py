"""Shade-matching engine: sRGB -> CIE Lab, CIEDE2000 color difference, undertone estimation, nearest catalog shade, two-point plan for light patches.

Everything is computed locally; no YouCam calls, no points spent.
Note: colors captured by a camera depend on lighting, so results are given only when the lighting fingerprint passes; screens differ from the real product, so trying shades in natural light is the reference (Lavedo).
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path

APP = Path(__file__).resolve().parent
DEMO_CATALOG = APP / "catalog.json"
PARTNER_CATALOG = APP / "partner_shades.json"            # The Pudding 2018 public swatches
OFFICIAL_CATALOG = APP / "partner_shades_official.json"  # current shades from the brand's official site (preferred when present)

# ΔE2000 reading thresholds (common color-management convention: <2 barely visible, <4 close, <7 visible difference)
VERDICTS = [(2.0, "很接近"), (4.0, "接近"), (7.0, "有差")]
NO_MATCH = "沒有接近色"
UNDERTONE_PENALTY = 2.0      # penalty added to the color difference when undertones disagree (neutral adds only half)
VITILIGO_SINGLE = 5.0        # color difference between own skin and the patch below this: a single shade is enough
VITILIGO_CORRECT = 12.0      # above this: two shades plus a corrector are considered


# ---------------------------------------------------------------- color conversion

def hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.strip().lstrip("#")
    if len(h) != 6:
        raise ValueError(f"色碼格式錯誤：{hex_color}")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def rgb_to_lab(rgb: tuple[int, int, int]) -> tuple[float, float, float]:
    def lin(c):
        c /= 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (lin(float(c)) for c in rgb)
    x = (0.4124564 * r + 0.3575761 * g + 0.1804375 * b) / 0.95047
    y = (0.2126729 * r + 0.7151522 * g + 0.0721750 * b) / 1.00000
    z = (0.0193339 * r + 0.1191920 * g + 0.9503041 * b) / 1.08883

    def f(t):
        return t ** (1 / 3) if t > 216 / 24389 else (24389 / 27 * t + 16) / 116
    fx, fy, fz = f(x), f(y), f(z)
    return (116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz))


def hex_to_lab(hex_color: str) -> tuple[float, float, float]:
    return rgb_to_lab(hex_to_rgb(hex_color))


def lab_to_hex(lab: tuple[float, float, float]) -> str:
    L, a, b = lab
    fy = (L + 16) / 116
    fx, fz = fy + a / 500, fy - b / 200

    def finv(t):
        return t ** 3 if t ** 3 > 216 / 24389 else (116 * t - 16) / (24389 / 27)
    x, y, z = finv(fx) * 0.95047, finv(fy), finv(fz) * 1.08883
    r = 3.2404542 * x - 1.5371385 * y - 0.4985314 * z
    g = -0.9692660 * x + 1.8760108 * y + 0.0415560 * z
    bb = 0.0556434 * x - 0.2040259 * y + 1.0572252 * z

    def gam(c):
        c = 12.92 * c if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055
        return max(0, min(255, round(c * 255)))
    return "#{:02X}{:02X}{:02X}".format(gam(r), gam(g), gam(bb))


def delta_e2000(lab1, lab2) -> float:
    """CIEDE2000 (formula of Sharma, Wu, Dalal 2005)."""
    L1, a1, b1 = lab1
    L2, a2, b2 = lab2
    C1, C2 = math.hypot(a1, b1), math.hypot(a2, b2)
    Cm = (C1 + C2) / 2
    G = 0.5 * (1 - math.sqrt(Cm ** 7 / (Cm ** 7 + 25 ** 7)))
    a1p, a2p = (1 + G) * a1, (1 + G) * a2
    C1p, C2p = math.hypot(a1p, b1), math.hypot(a2p, b2)
    h1p = math.degrees(math.atan2(b1, a1p)) % 360 if (a1p or b1) else 0.0
    h2p = math.degrees(math.atan2(b2, a2p)) % 360 if (a2p or b2) else 0.0
    dLp, dCp = L2 - L1, C2p - C1p
    if C1p * C2p == 0:
        dhp = 0.0
    elif abs(h2p - h1p) <= 180:
        dhp = h2p - h1p
    elif h2p - h1p > 180:
        dhp = h2p - h1p - 360
    else:
        dhp = h2p - h1p + 360
    dHp = 2 * math.sqrt(C1p * C2p) * math.sin(math.radians(dhp / 2))
    Lmp, Cmp = (L1 + L2) / 2, (C1p + C2p) / 2
    if C1p * C2p == 0:
        hmp = h1p + h2p
    elif abs(h1p - h2p) <= 180:
        hmp = (h1p + h2p) / 2
    elif h1p + h2p < 360:
        hmp = (h1p + h2p + 360) / 2
    else:
        hmp = (h1p + h2p - 360) / 2
    T = (1 - 0.17 * math.cos(math.radians(hmp - 30)) + 0.24 * math.cos(math.radians(2 * hmp))
         + 0.32 * math.cos(math.radians(3 * hmp + 6)) - 0.20 * math.cos(math.radians(4 * hmp - 63)))
    dtheta = 30 * math.exp(-(((hmp - 275) / 25) ** 2))
    Rc = 2 * math.sqrt(Cmp ** 7 / (Cmp ** 7 + 25 ** 7))
    Sl = 1 + 0.015 * (Lmp - 50) ** 2 / math.sqrt(20 + (Lmp - 50) ** 2)
    Sc = 1 + 0.045 * Cmp
    Sh = 1 + 0.015 * Cmp * T
    Rt = -math.sin(math.radians(2 * dtheta)) * Rc
    return math.sqrt((dLp / Sl) ** 2 + (dCp / Sc) ** 2 + (dHp / Sh) ** 2 + Rt * (dCp / Sc) * (dHp / Sh))


def verdict(de: float) -> str:
    for limit, label in VERDICTS:
        if de < limit:
            return label
    return NO_MATCH


# the frontend compares by code (so on-screen text can follow the language; the server still compares the Chinese strings internally)
VERDICT_CODES = {"很接近": "very_close", "接近": "close", "有差": "gap", NO_MATCH: "no_match"}


def verdict_code(label: str) -> str:
    return VERDICT_CODES.get(label, "")


def _verdict_short(de: float) -> str:
    return "很接近" if de < 2 else "接近" if de < 5 else "有差"


LOW_CHROMA = 8.0


def skin_profile(hex_color: str) -> dict:
    """Undertone and depth estimate (heuristic: smaller hue angle = pinker = cool, larger = yellower = warm). Reference only; the user can override."""
    L, a, b = hex_to_lab(hex_color)
    hue = math.degrees(math.atan2(b, a))
    chroma = math.hypot(a, b)
    # very pale skin has very low chroma and the hue is heavily affected by the photo's white balance: chroma < 8 is always judged neutral, ask the person to confirm
    low_chroma = chroma < LOW_CHROMA
    undertone = "neutral" if low_chroma else ("cool" if hue < 52 else ("warm" if hue > 64 else "neutral"))
    depth = "very_light" if L >= 85 else "light" if L >= 72 else "medium" if L >= 55 else "deep"
    return {"lab": [round(L, 1), round(a, 1), round(b, 1)], "hue": round(hue, 1), "chroma": round(chroma, 1),
            "undertone": undertone, "low_chroma": low_chroma, "depth": depth, "method": "heuristic"}


# ---------------------------------------------------------------- catalog

def load_catalogs(source: str = "all") -> list[dict]:
    """source: demo (fictional demo catalog), official (partner brands' current official shades), partner (partner brands' 2018 public swatches), all.
    With all, if a brand has current official data the old 2018 data is not used for it."""
    out = []
    if source in ("demo", "all"):
        for p in json.loads(DEMO_CATALOG.read_text(encoding="utf-8"))["products"]:
            out.append(dict(p, source="demo"))
    official_brands = set()
    if source in ("official", "all") and OFFICIAL_CATALOG.exists():
        for p in json.loads(OFFICIAL_CATALOG.read_text(encoding="utf-8"))["products"]:
            out.append(dict(p, source="official"))
            official_brands.add(p["brand"])
    if source in ("partner", "all") and PARTNER_CATALOG.exists():
        for p in json.loads(PARTNER_CATALOG.read_text(encoding="utf-8"))["products"]:
            if source == "all" and p["brand"] in official_brands:
                continue
            out.append(dict(p, source="partner"))
    return out


def product_urls() -> dict[tuple[str, str], str]:
    """(brand, product name) -> the brand's own product page, taken from the catalog field source_url (official data only; the demo catalog has none).
    Nothing is guessed: a product without source_url has no link."""
    out = {}
    for p in load_catalogs("all"):
        url = p.get("source_url")
        if isinstance(url, str) and url.startswith("https://"):
            out[(p["brand"], p["name"])] = url
    return out


def attach_urls(obj, urls: dict | None = None):
    """Walk a response and add "url" to every shade row ({"brand", "product", "code"}) whose product has a source_url. Rows without one are left as they are."""
    if urls is None:
        urls = product_urls()
    if not urls:
        return obj
    if isinstance(obj, list):
        return [attach_urls(x, urls) for x in obj]
    if isinstance(obj, dict):
        out = {k: attach_urls(v, urls) for k, v in obj.items()}
        if "url" not in out and isinstance(out.get("brand"), str) and isinstance(out.get("product"), str) and "code" in out:
            url = urls.get((out["brand"], out["product"]))
            if url:
                out["url"] = url
        return out
    return obj


def match_lab(lab, category: str, undertone: str | None = None, source: str = "all", top: int = 3) -> list[dict]:
    results = []
    for p in load_catalogs(source):
        if p["category"] != category:
            continue
        for s in p["shades"]:
            s_lab = hex_to_lab(s["hex"])
            de = delta_e2000(lab, s_lab)
            s_tone = s.get("undertone") or (skin_profile(s["hex"])["undertone"] if category in ("foundation", "concealer") else None)
            penalty = 0.0
            if undertone and s_tone and s_tone != undertone:
                penalty = UNDERTONE_PENALTY / 2 if "neutral" in (s_tone, undertone) else UNDERTONE_PENALTY
            results.append({"brand": p["brand"], "product": p["name"], "code": s["code"], "hex": s["hex"],
                            "source": p["source"], "undertone": s_tone, "delta_e": round(de, 2),
                            "score": round(de + penalty, 2), "verdict": verdict(de),
                            "verdict_code": verdict_code(verdict(de))})
    results.sort(key=lambda r: r["score"])
    return results[:top]


def match_hex(hex_color: str, category: str, undertone: str | None = None, source: str = "all", top: int = 3) -> list[dict]:
    return match_lab(hex_to_lab(hex_color), category, undertone, source, top)


# ---------------------------------------------------------------- plan

def skin_catalog_source(source: str) -> str:
    """Complexion products (foundation, concealer): recommend counter shades; with all only the current official shades are used, falling back to everything only when there is no official data."""
    if source == "all" and OFFICIAL_CATALOG.exists():
        return "official"
    return source


def card_shades(skin_hex: str, category: str, undertone: str | None, source: str, top: int = 4) -> tuple[dict, list]:
    """Photo skin color -> skin-tone card cell -> counter shade in the same cell for each product line (skin_card)."""
    import skin_card as sc
    m = sc.match_skin(skin_hex, load_catalogs(skin_catalog_source(source)), category, undertone)
    near = [s for s in m["shades"] if s["level_diff"] <= 2]       # product lines three or more levels away are not listed, so it does not read as a recommendation
    return m["card"], (near or m["shades"][:1])[:top]


def three_stripes(skin_hex: str, category: str = "foundation", undertone: str | None = None,
                  source: str = "all") -> dict:
    """Three try-on shades:
    take the closest product line and list the shade one level lighter, the shade itself and one level deeper (same undertone preferred)."""
    import skin_card as sc
    card, shades = card_shades(skin_hex, category, undertone, source, 1)
    if not shades:
        return {"card": card, "stripes": []}
    best = shades[0]
    rows = [r for r in sc.classify_catalog(load_catalogs(skin_catalog_source(source)))
            if r["category"] == category and r["brand"] == best["brand"] and r["product"] == best["product"]]
    me = next(r for r in rows if r["code"] == best["code"])

    def num(code):
        # digits in brand shade codes (e.g. 3C, 12W, N 5, Fair 05): within one product line a larger digit is deeper
        m = re.search(r"\d+(?:\.\d+)?", code)
        return float(m.group()) if m else None

    numbered = num(me["code"]) is not None and all(num(r["code"]) is not None for r in rows)

    def pick(cands, ref):
        """The adjacent step first (smallest distance in depth), then the same undertone among steps about as close.
        Never jump past nearer shades just to keep the undertone: that put a deep brown next to an ivory."""
        if not cands:
            return None
        d0 = min(abs(ref(r) - ref(me)) for r in cands)
        group = [r for r in cands if abs(ref(r) - ref(me)) <= d0 * 1.5]
        pool = [r for r in group if r["tone"] == me["tone"]] or group
        return min(pool, key=lambda r: abs(ref(r) - ref(me)))
    if numbered:
        # when shade digits exist, follow the brand's own depth order (the web display colors of official swatches sometimes disagree with the numbering)
        n0 = num(me["code"])
        lighter = pick([r for r in rows if num(r["code"]) < n0], lambda r: num(r["code"]))
        deeper = pick([r for r in rows if num(r["code"]) > n0], lambda r: num(r["code"]))
    else:
        lighter = pick([r for r in rows if r["lab"][0] > me["lab"][0] + 1.5], lambda r: r["lab"][0])
        deeper = pick([r for r in rows if r["lab"][0] < me["lab"][0] - 1.5], lambda r: r["lab"][0])
    out = []
    for role, role_key, r in (("淺一階", "lighter", lighter), ("最接近", "closest", me), ("深一階", "deeper", deeper)):
        if r:
            out.append({"role": role, "role_key": role_key, "brand": r["brand"], "product": r["product"], "code": r["code"],
                        "hex": r["hex"], "level": r["level"], "tone": r["tone"], "source": r["source"]})
    return {"card": card, "stripes": out,
            "how": "在下顎線（臉和脖子交界）各試一條，到窗邊自然光看，等 15–30 分鐘（底妝和皮脂混合、變乾後會稍微變深）"
                   "再選最「看不見」的那條；兩條之間猶豫時選略淺半階的。"}


GAMUT_DE = 8.0       # closest shade still has ΔE00 > 8: the catalog (official swatches) has no close enough color
GAMUT_NOTE = ("官網色票普遍比真人皮膚黃：目錄裡沒有很接近你膚色的色號，以下是明度最合、相對偏紅的候選。"
              "請在下顎線試三條、自然光下看；需要時底妝可加一點紅色調色液，淺色區塊先用紅橘色校色。")


def gamut_note(shades: list) -> str | None:
    d = shades[0].get("delta_e") if shades else None
    return GAMUT_NOTE if d is not None and d > GAMUT_DE else None


def albinism_base_plan(skin_hex: str, undertone: str | None = None, source: str = "all") -> dict:
    prof = skin_profile(skin_hex)
    tone = undertone or prof["undertone"]
    card, found = card_shades(skin_hex, "foundation", undertone, source)
    best = found[0] if found else None
    gap = best is None or best["verdict"] == "有差"
    return {"skin": skin_hex, "profile": prof, "undertone_used": tone, "card": card, "foundation": found, "gap": gap,
            "gamut_note": gamut_note(found),
            "stripes": three_stripes(skin_hex, "foundation", undertone, source),
            "notes": ["顏色以原膚色為準：不選更白、也不選更深的色號。",
                      "若最接近的色號仍「有差」，可混兩色（彩妝師常見做法）或請專櫃以自然光試色。"]}


def derive_zones(skin_hex: str) -> dict:
    """With no expected makeup look, derive the highlight and contour target colors from the skin color."""
    L, a, b = hex_to_lab(skin_hex)
    return {"highlight": {"hex": lab_to_hex((min(L + 6, 96), a * 0.9, b * 0.9))},
            "contour": {"hex": lab_to_hex((max(L - 14, 15), a * 0.8, b * 0.6))}}


def face_shades(skin_hex: str, zones: dict | None = None, undertone: str | None = None, source: str = "all",
                items: tuple = ("concealer", "highlight", "contour")) -> list[dict]:
    """Face shades: concealer (matched to skin), highlight (one level lighter concealer), contour (two levels deeper, cooler foundation).

    Owner's question (10-16): had the face shades been thought through, given the many videos just reviewed. Videos of people with the condition: concealer uses the lightest shade and is dabbed only on redness and dark circles;
    the catalog has no highlight or contour products -> substitute concealer / foundation one level lighter and two levels deeper.
    zones: zone colors of the expected makeup look (used if present, otherwise derived from the skin color)."""
    got = zones if zones and zones.get("highlight") and zones.get("contour") else None
    z = got or derive_zones(skin_hex)
    src_label = "預期妝容的打亮／修容區取色" if got else "依膚色推算"
    plan = {
        "concealer": (skin_hex, "concealer", undertone, "只點在泛紅、黑眼圈，同深淺或淺半階；用指腹點開，不要全臉。", "膚色"),
        "highlight": (z["highlight"]["hex"], "concealer", undertone,
                      "打亮：比膚色淺一階的遮瑕，點在鼻樑、顴骨上緣，拍開。", src_label),
        "contour": (z["contour"]["hex"], "foundation", "cool",
                    "修容：深兩階、偏冷（灰褐）的粉底，刷在顴骨下方和下顎線，邊界拍開；不要用偏橘的。", src_label),
    }
    out = []
    for item in items:
        hex_, cat, tone, how, basis = plan[item]
        cell, ms = card_shades(hex_, cat, tone, source, 3)
        row = {"item": item, "category": cat, "hex": hex_, "card": cell, "matches": ms, "how": how, "basis": basis}
        if item == "contour":
            # contour needs a low-chroma grey-brown: the foundation in the same card cell is often orange -> use the foundation / concealer whose actual color is closest, and say so plainly if it is orange
            src = skin_catalog_source(source)
            t_lab = hex_to_lab(hex_)
            ms = sorted(match_lab(t_lab, "foundation", None, src, 6) + match_lab(t_lab, "concealer", None, src, 6),
                        key=lambda r: r["delta_e"])[:3]
            row["matches"] = ms
            t_c = math.hypot(t_lab[1], t_lab[2])
            if not ms or ms[0]["delta_e"] > 8:
                row["matches"] = []
                row["warn"] = "型錄沒有適合的修容色（粉底不是偏橘就是太淺）：照色塊選灰褐色的修容粉，刷很淡、只在顴骨下方。"
            elif math.hypot(*hex_to_lab(ms[0]["hex"])[1:]) - t_c > 8:
                row["warn"] = "型錄裡最接近的粉底仍比目標偏橘：刷很淡、只在顴骨下方，或選灰褐色的修容產品。"
        out.append(row)
    return out


def skin_balance(normal_hex: str, patch_hex: str, patch_ratio: float | None = None) -> dict:
    """Skin-color balance point for vitiligo (reference only; since  decided to return to whole-face foundation matched to the surrounding skin, so this is no longer used to pick foundation):
    the natural skin color and the patch color mixed by their area share on the face (Lab weighted average).
    patch_ratio: approximate share of face sampling points on the patch (computed by clustering on auto-sampling); 0.5 if absent."""
    known = patch_ratio is not None
    w = min(0.9, max(0.1, patch_ratio if known else 0.5))
    n_lab, p_lab = hex_to_lab(normal_hex), hex_to_lab(patch_hex)
    lab = tuple(n * (1 - w) + p * w for n, p in zip(n_lab, p_lab))
    return {"hex": lab_to_hex(lab), "patch_ratio": round(w, 2), "ratio_source": "auto" if known else "default"}


def vitiligo_plan(normal_hex: str, patch_hex: str, undertone: str | None = None, source: str = "all",
                  patch_ratio: float | None = None) -> dict:
    n_lab, p_lab = hex_to_lab(normal_hex), hex_to_lab(patch_hex)
    de = delta_e2000(n_lab, p_lab)
    prof = skin_profile(normal_hex)
    tone = undertone or prof["undertone"]
    # whole-face foundation is matched to the pigmented skin around the patches;
    # the patches themselves are handled with local corrector + concealer + edge blending
    n_card, n_found = card_shades(normal_hex, "foundation", undertone, source, 3)
    plan = {"normal": normal_hex, "patch": patch_hex, "delta_e": round(de, 2), "profile": prof,
            "undertone_used": tone, "card": n_card, "foundation": n_found,
            "stripes": three_stripes(normal_hex, "foundation", undertone, source),
            "concealer": card_shades(normal_hex, "concealer", undertone, source, 3)[1],
            "edge": [], "corrector": [], "steps": []}
    if de < VITILIGO_SINGLE:
        plan["level"] = "single"
        plan["summary"] = "色差不大：用一個接近周圍膚色的色號，點壓在淺色區塊上即可。"
    else:
        mid = tuple((n + p) / 2 for n, p in zip(n_lab, p_lab))
        plan["edge_target"] = lab_to_hex(mid)
        plan["edge_card"], plan["edge"] = card_shades(plan["edge_target"], "concealer", undertone, source, 3)
        plan["level"] = "two_shade" if de < VITILIGO_CORRECT else "two_shade_plus"
        plan["summary"] = "色差明顯：淺色區塊先局部校色，粉底對周圍膚色，邊緣再用淺一點的遮瑕修邊。"
        n_hue = math.degrees(math.atan2(n_lab[2], n_lab[1]))
        p_hue = math.degrees(math.atan2(p_lab[2], p_lab[1]))
        csrc = category_source("corrector", source)
        # the patches are lighter than the surrounding skin: always dab only a peach / orange corrector on the patch; the deeper the skin, the deeper the orange-red (picked by the natural skin depth).
        # Pinkish patches (p_hue < n_hue) also do not get green: green brightens the patch and removes red, and a brown foundation on top turns grey (sample review, 10-16;
        # the earlier rule "pinkish -> Mint green" has been removed; green only suits light skin with redness)
        warm = [r for r in match_lab(n_lab, "corrector", None, csrc, 40)
                if re.search(r"peach|orange|ochre|red|bisque|橘|蜜桃", r["code"], re.I)]
        plan["corrector"] = sorted(warm, key=lambda r: abs(hex_to_lab(r["hex"])[0] - n_lab[0]))[:2]
        if plan["corrector"]:
            plan["corrector_reason"] = ("淺色區塊顏色較淺：先用指尖只在淺色區塊上薄點蜜桃／橘色校色（深膚色用偏紅的深橘），"
                                        "粉底才不會泛灰；淺色區塊偏粉也不用綠色，綠疊棕色粉底會發灰。"
                                        "")
        if n_lab[0] < 60:
            plan["deep_skin_note"] = "原膚色較深：校色選偏紅的深橘，粉底對周圍膚色。"
    plan["steps"] = [
        "防曬與隔離先上。",
        "局部校色：只在淺色區塊上薄點蜜桃／橘色（不用綠色），分小區塊，不要整臉上。",
        "粉底對淺色區塊周圍的膚色選色：面中最多、往外推薄，T 字平刷、眼周最薄；用海綿**點壓**，不要來回搓：用力摩擦會刺激皮膚。（化妝師分區上法）",
        "面中打亮：額頭中央、鼻樑、下巴、眼下三角，霧面或微光；T 字少量蜜粉按壓控油。",
        "需要的地方再疊一層遮瑕；淺色區塊邊緣色差最明顯，點上淺一點的遮瑕修邊、往外淡化。",
        "少量蜜粉輕壓定妝。",
        "照光治療日是否上妝，請依醫師指示。",
        "選色時在下顎線試三條（淺一階、最接近、深一階），到窗邊自然光看、等 15–30 分鐘再決定：底妝和皮脂混合、變乾後會稍微變深，"
        "猶豫時選略淺半階的。",
    ]
    plan["gamut_note"] = gamut_note(plan["foundation"]) or gamut_note(plan["concealer"])
    plan["base"] = plan["concealer"] + plan["foundation"]   # legacy field: concealer first
    return plan


def category_source(category: str, source: str) -> str:
    """With all: if this category has current official shades use only those, otherwise use everything (including the fictional demo catalog)."""
    if source == "all" and any(p["category"] == category for p in load_catalogs("official")):
        return "official"
    return source


def brow_matches(hex_color: str, source: str = "all", skin_hex: str | None = None) -> list[dict]:
    """Brow pencil: first tag every shade with a brow-card label (depth x family), then recommend only same depth and family, or one level apart / nearby family (: build the tags first, then match)."""
    import brow_card as bcard
    t = bcard.tag(hex_color)
    tgt = {"level": t["level"], "family": t["family"], "hex": hex_color, "visible": True}
    shades = [{"brand": c["brand"], "product": c["name"], "code": s["code"], "hex": s["hex"],
               "description": s.get("description", ""), "source": c["source"]}
              for c in load_catalogs(source) if c["category"] == "brow" for s in c["shades"]]
    out = []
    for r in bcard.match(tgt, shades, skin_hex):
        out.append({"brand": r["brand"], "product": r["product"], "code": r["code"], "hex": r["hex"], "source": r["source"],
                    "fit": r["fit"], "delta_e": r["delta_e"], "tag": r["tag"]["label"],
                    "verdict": _verdict_short(r["delta_e"]),
                    "verdict_code": verdict_code(_verdict_short(r["delta_e"]))})
    return out


def card_matches(items: list[dict], source: str = "all") -> list[dict]:
    """items: [{"item": "brow", "category": "brow", "hex": "#7F6A5D"}, ...] -> each item gets its nearest counter shade.
    Brow, lip and blush are designed colors matched against product swatches (same color space), so the color difference is compared directly."""
    out = []
    for it in items:
        if not it.get("hex"):
            out.append(dict(it, matches=[]))
            continue
        src = category_source(it["category"], source)
        if it["category"] == "brow":
            out.append(dict(it, matches=brow_matches(it["hex"], src, it.get("skin_hex"))))
            continue
        out.append(dict(it, matches=match_hex(it["hex"], it["category"], it.get("undertone"), src, 3)))
    return out


# Skincare and protection steps (from the internal design notes' makeup routine and two planning tables)
_CARE_COMMON = [
    ("溫和清潔、保濕", "皮膚薄、容易紅；動作輕，不搓不擦，乾燥的地方上妝會浮粉。"),
    ("防曬 SPF50+，出門前 10–15 分鐘", "防曬是上妝前的第一步。"),
    ("隔離（溫和、無香料的妝前乳）", "在皮膚和彩妝之間多一層保護，也讓後面的妝服貼。"),
]
_CARE = {
    "albinism": _CARE_COMMON[:2] + [
        ("眼睛也要防曬：寬簷帽、包覆式抗 UV 墨鏡", "眼睛也要防曬，防曬不只擦臉。"),
        ("隔離：溫和、無香料的妝前乳；要修色只修泛紅（綠色系）", "多一層保護、讓妝服貼；不選美白提亮的紫色系，目標是調和膚色，不是更白。"),
        ("新產品先在耳後或下顎小範圍試用", "選溫和、無香料，降低過敏機會。"),
        ("底妝（想要時）：顏色接近原本膚色，面中最多往外推薄，T 字平刷、眼周最薄", "照配色卡的底妝色號，往外推到脖子一致；T 字少量蜜粉按壓。保留特色的妝可以不上底妝，隔離一樣要上。"),
        ("面中打亮（想要時）：額頭中央、鼻樑、下巴、眼下三角，霧面或微光", "膚色很淺的人常用粉色系、珠光打亮；量少一點、輕拍。"),
        ("上妝：眉、睫毛、腮紅、唇，少量多次", "照配色卡的色號；保留原樣也可以。"),
        ("卸妝：溫和卸妝，指尖輕打圈、紙巾輕壓", "不用力擦。"),
    ],
    "vitiligo": [
        ("溫和清潔、保濕，不搓不刷", "摩擦、抓、用力刷都會刺激皮膚。"),
        ("防曬 SPF50+，大範圍擦", "等吸收後再上妝。"),
        _CARE_COMMON[2],
        ("避開可能刺激皮膚的成分", "染髮劑（PPD）、香水體香劑（PTBP）、部分唇眼彩染料、美白產品都可能刺激皮膚；若發現新的或有變化的斑點，請看皮膚科醫師。"),
        ("照光治療日是否上妝，請依醫師指示", "是否上妝、塗什麼，請依醫師或治療單位的指示；沒有特別指示時，一般保濕可以。"),
        ("局部校色：只在淺色區塊上薄點蜜桃／橘色（不用綠色）", "淺色區塊顏色較淺，先校色粉底才不會泛灰；不要整臉上。"),
        ("底妝：對淺色區塊周圍的膚色選色，面中最多、往外推薄；T 字平刷、眼周最薄；海綿點壓", "照配色卡的底妝色號；往外推到脖子一致。T 字少量蜜粉按壓控油。不想上可以跳過。"),
        ("面中打亮：額頭中央、鼻樑、下巴、眼下三角，選霧面或微光", "讓臉中間亮一點、更立體；珠光太多會讓紋理和淺色區塊更明顯。"),
        ("遮瑕用點壓，不推不擦；邊緣用淺一點的修邊", "照配色卡的遮瑕打底色、修邊色；不想遮也完全可以。"),
        ("卸妝：溫和卸妝，紙巾輕壓", "同樣不搓。"),
    ],
}


def care_plan(condition: str = "albinism", skin: dict | None = None) -> list[dict]:
    """Return the skincare / protection steps; with a skin analysis, the matching entry adjusts the recipe card."""
    steps = _CARE.get(condition, _CARE["albinism"])
    if skin is not None:
        if condition == "vitiligo":
            from care_rules_vitiligo import personalize
            steps = personalize(steps, skin)
        elif condition == "albinism":
            from care_rules_albinism import personalize
            steps = personalize(steps, skin)
    return [{"step": i + 1, "text": t, "why": w} for i, (t, w) in enumerate(steps)]


# Brow color: choose by the natural brow color taken from the photo (KB: stay close to the natural brow or slightly deeper, fill sparse areas only; a very dark brow looks harsh on very pale skin;
# a skincare brand: gold for light skin, brown for deeper skin). "Slightly deeper" adds definition.
BROW_VISIBLE_DL = 15.0     # skin-vs-brow lightness difference >= 15: the brow is already visible (most people with vitiligo, and the general population)
BROW_MAX_DL_PALE = 38.0    # for light skin (L >= 72) the brow is at most 38 deeper than the skin, to avoid looking harsh


def brow_from_photo(brow_hex: str, skin_hex: str, level: str = "defined") -> dict:
    """level: soft (fill a little in the natural brow color) or defined (slightly deeper than the natural brow, adds definition). Returns the color code, strength and reason."""
    if level not in ("soft", "defined"):
        raise ValueError(f"眉色層級只有 soft／defined：{level}")
    import brow_card as bcard
    tg = bcard.target(brow_hex, skin_hex, level)          # since: the brow-card label (depth x family) decides the target color
    if tg:
        return {"hex": tg["hex"], "intensity": (35 if level == "soft" else 50) if tg["visible"] else (45 if level == "soft" else 55),
                "brow_visible": tg["visible"], "level": level, "why": tg["why"],
                "tag": {"level": tg["level"], "family": tg["family"],
                        "label": f'第 {tg["level"]} 級{bcard.level_name(tg["level"])}・{tg["family"]}'}}
    bL, ba, bb = hex_to_lab(brow_hex)
    sL, _, _ = hex_to_lab(skin_hex)
    visible = (sL - bL) >= BROW_VISIBLE_DL
    if visible:
        # natural brow present: fill sparse areas in the same color, or one level deeper for definition; never shift toward lighter
        L = bL if level == "soft" else max(8.0, bL - 6)
        lab, intensity = (L, ba, bb), (35 if level == "soft" else 50)
        why = "照你原本的眉色補稀疏處" if level == "soft" else "照你原本的眉色深一階，加強立體感"
    else:
        # very faint brow (common with albinism): keep the hue of the natural brow and deepen until visible; light skin has a depth cap
        chroma = math.hypot(ba, bb)
        hue = math.atan2(bb, ba) if chroma >= 3 else math.radians(55)   # nearly achromatic: use a grey-brown hue
        c = min(14.0, max(7.0, chroma * 1.5))
        L = sL - (24 if level == "soft" else 32)
        if sL >= 72:
            L = max(L, sL - BROW_MAX_DL_PALE)
        L = max(L, 30.0)
        lab, intensity = (L, c * math.cos(hue), c * math.sin(hue)), (45 if level == "soft" else 55)
        why = ("眉毛很淡：用原眉色的色調加深一點，讓眉毛看得見" if level == "soft"
               else "眉毛很淡：用原眉色的色調加深，加強立體感；淺膚色不會深到太兇")
    return {"hex": lab_to_hex(lab), "intensity": intensity, "brow_visible": visible,
            "level": level, "why": why}


# Reminder that the skin is more sensitive
# Source: internal research notes (sensitive skin)
_SENSITIVE = {
    "albinism": {
        "title": "你的回答顯示皮膚偏敏感，上妝前先看這幾點",
        "items": [
            ("強光下請做好防曬", "防曬、遮陽、長袖、抗 UV 墨鏡一起用。"),
            ("動作放輕，手指用力一壓可能留紅印", "上妝用輕拍，不要來回推。"),
            ("新產品先在耳後或下顎小範圍試，選溫和、無香料", "不要一開始就買大罐。"),
            ("定期給皮膚科檢查", "新長的斑點、顏色不均或形狀改變，請給皮膚科醫師檢查。"),
        ],
    },
    "vitiligo": {
        "title": "你的回答顯示皮膚偏敏感，上妝前先看這幾點",
        "items": [
            ("出門前先擦好防曬", "大範圍擦；等吸收後再上妝，妝會更服貼。"),
            ("不要搓、抓、用力刷", "用力摩擦會刺激皮膚。上妝、卸妝都用點壓。"),
            ("留意可能刺激皮膚的成分", "染髮劑（PPD）、香水體香劑（PTBP）、部分唇眼彩染料、美白成分都可能刺激皮膚；若發現新的或有變化的斑點，請看皮膚科醫師。"),
            ("新產品先小範圍試，不要急著買大罐", "出現刺激、過敏就停用。"),
            ("照光治療日是否上妝，請依醫師指示；不確定是什麼、或發現新的或有變化的斑點，請皮膚科確認", "以醫師或衛教人員的說明為準。"),
        ],
    },
}


def sensitive_notes(condition: str = "albinism") -> dict:
    d = _SENSITIVE.get(condition, _SENSITIVE["albinism"])
    return {"title": d["title"], "items": [{"text": t, "why": w} for t, w in d["items"]]}
