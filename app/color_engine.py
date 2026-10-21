"""色卡比對引擎：sRGB → CIE Lab、CIEDE2000 色差、冷暖推估、對型錄找最近色號、淺色區塊兩點方案。

全部在本機計算，不呼叫 YouCam、不扣點。
注意：相機拍到的顏色受光線影響，只在光線指紋合格時給結果；螢幕與實物有差異，以自然光試色為準（Lavedo）。
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path

APP = Path(__file__).resolve().parent
DEMO_CATALOG = APP / "catalog.json"
PARTNER_CATALOG = APP / "partner_shades.json"            # The Pudding 2018 公開色票
OFFICIAL_CATALOG = APP / "partner_shades_official.json"  # 品牌官網現行色號（有就優先）

# ΔE2000 判讀門檻（一般色彩管理慣例：<2 幾乎看不出、<4 接近、<7 看得出差異）
VERDICTS = [(2.0, "很接近"), (4.0, "接近"), (7.0, "有差")]
NO_MATCH = "沒有接近色"
UNDERTONE_PENALTY = 2.0      # 冷暖不合時加在色差上的懲罰（中性只加一半）
VITILIGO_SINGLE = 5.0        # 原膚色與白斑色差低於此值：一色即可
VITILIGO_CORRECT = 12.0      # 高於此值：兩色加校色考量


# ---------------------------------------------------------------- 色彩換算

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
    """CIEDE2000（Sharma, Wu, Dalal 2005 的公式）。"""
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


# 前端用代碼比對判定（畫面文字才能跟著語言換；伺服器內部仍用中文字串比較）
VERDICT_CODES = {"很接近": "very_close", "接近": "close", "有差": "gap", NO_MATCH: "no_match"}


def verdict_code(label: str) -> str:
    return VERDICT_CODES.get(label, "")


def _verdict_short(de: float) -> str:
    return "很接近" if de < 2 else "接近" if de < 5 else "有差"


LOW_CHROMA = 8.0


def skin_profile(hex_color: str) -> dict:
    """冷暖、深淺推估（啟發式：色相角越小越偏粉＝冷，越大越偏黃＝暖）。只當參考，使用者可改。"""
    L, a, b = hex_to_lab(hex_color)
    hue = math.degrees(math.atan2(b, a))
    chroma = math.hypot(a, b)
    # 極淺膚色彩度很低，色相受照片白平衡影響大：彩度 < 8 一律判中性，請本人確認
    low_chroma = chroma < LOW_CHROMA
    undertone = "neutral" if low_chroma else ("cool" if hue < 52 else ("warm" if hue > 64 else "neutral"))
    depth = "very_light" if L >= 85 else "light" if L >= 72 else "medium" if L >= 55 else "deep"
    return {"lab": [round(L, 1), round(a, 1), round(b, 1)], "hue": round(hue, 1), "chroma": round(chroma, 1),
            "undertone": undertone, "low_chroma": low_chroma, "depth": depth, "method": "heuristic"}


# ---------------------------------------------------------------- 型錄

def load_catalogs(source: str = "all") -> list[dict]:
    """source：demo（虛構示範型錄）、official（合作品牌官網現行色號）、partner（合作品牌 2018 公開色票）、all。
    all 時同一品牌有官網現行資料就不用 2018 舊資料。"""
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


# ---------------------------------------------------------------- 方案

def skin_catalog_source(source: str) -> str:
    """膚色類（粉底、遮瑕）推薦專櫃色號：all 時只用官網現行色號，沒有官網資料才退回全部。"""
    if source == "all" and OFFICIAL_CATALOG.exists():
        return "official"
    return source


def card_shades(skin_hex: str, category: str, undertone: str | None, source: str, top: int = 4) -> tuple[dict, list]:
    """照片膚色 → 膚色色卡格子 → 各產品線同一格的專櫃色號（skin_card）。"""
    import skin_card as sc
    m = sc.match_skin(skin_hex, load_catalogs(skin_catalog_source(source)), category, undertone)
    near = [s for s in m["shades"] if s["level_diff"] <= 2]       # 差三級以上的產品線不列，免得像在推薦
    return m["card"], (near or m["shades"][:1])[:top]


def three_stripes(skin_hex: str, category: str = "foundation", undertone: str | None = None,
                  source: str = "all") -> dict:
    """三條試色：
    取最接近的那條產品線，列出比它淺一階、它本身、比它深一階的色號（優先同冷暖）。"""
    import skin_card as sc
    card, shades = card_shades(skin_hex, category, undertone, source, 1)
    if not shades:
        return {"card": card, "stripes": []}
    best = shades[0]
    rows = [r for r in sc.classify_catalog(load_catalogs(skin_catalog_source(source)))
            if r["category"] == category and r["brand"] == best["brand"] and r["product"] == best["product"]]
    me = next(r for r in rows if r["code"] == best["code"])

    def num(code):
        # 品牌色號裡的數字（例如 3C、12W、N 5、Fair 05）：同一條產品線數字越大越深
        m = re.search(r"\d+(?:\.\d+)?", code)
        return float(m.group()) if m else None

    numbered = num(me["code"]) is not None and all(num(r["code"]) is not None for r in rows)

    def pick(cands, ref):
        cands = sorted(cands, key=lambda r: (r["tone"] != me["tone"], abs(ref(r) - ref(me))))
        return cands[0] if cands else None
    if numbered:
        # 有色號數字就照品牌自己的深淺順序（官網色票的網頁顯示色有時和編號順序不一致）
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


GAMUT_DE = 8.0       # 最接近的色號 ΔE00 還 > 8：目錄（官網色票）沒有夠近的顏色
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
    """沒有預期妝容時，依膚色推算打亮、修容的目標色。"""
    L, a, b = hex_to_lab(skin_hex)
    return {"highlight": {"hex": lab_to_hex((min(L + 6, 96), a * 0.9, b * 0.9))},
            "contour": {"hex": lab_to_hex((max(L - 14, 15), a * 0.8, b * 0.6))}}


def face_shades(skin_hex: str, zones: dict | None = None, undertone: str | None = None, source: str = "all",
                items: tuple = ("concealer", "highlight", "contour")) -> list[dict]:
    """臉部色號：遮瑕（對膚色）、打亮（淺一階的遮瑕）、修容（深兩階、偏冷的粉底）。

    參考影片：遮瑕用最淺號、只點泛紅與黑眼圈；
    型錄沒有打亮、修容產品 → 用遮瑕／粉底的淺一階、深兩階代替。
    zones：預期妝容的分區色（有就用，沒有就依膚色推算）。"""
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
            # 修容要低彩度的灰褐：色卡同一格的粉底常偏橘 → 改用實際顏色最接近的粉底／遮瑕，偏橘就明說
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
    """白斑的膚色平衡點（參考用；改回整臉底妝對周圍膚色，這裡不再用來選底妝）：
    原本膚色與白斑依臉上面積比例混合（Lab 加權平均）。
    patch_ratio：白斑約佔臉部取色點的比例（自動取色時由分群算出）；沒有就用 0.5。"""
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
    # 整臉底妝對「白斑周圍有色素的皮膚」選色；
    # 白斑本身用局部校色＋遮瑕＋修邊處理
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
        # 白斑缺色素：一律只點在白斑上的蜜桃／橘色校色；膚色越深選越深的橘紅（依原本膚色深淺挑）。
        # 白斑偏粉（p_hue < n_hue）也不用綠：綠會把白斑提亮、抽掉紅，再疊棕色粉底變灰（外部建議
        # 先前「偏粉用 Mint 綠」的規則已移除；綠色只適合淺膚泛紅）
        warm = [r for r in match_lab(n_lab, "corrector", None, csrc, 40)
                if re.search(r"peach|orange|ochre|red|bisque|橘|蜜桃", r["code"], re.I)]
        plan["corrector"] = sorted(warm, key=lambda r: abs(hex_to_lab(r["hex"])[0] - n_lab[0]))[:2]
        if plan["corrector"]:
            plan["corrector_reason"] = ("淺色區塊缺色素：先用指尖只在淺色區塊上薄點蜜桃／橘色校色（深膚色用偏紅的深橘），"
                                        "粉底才不會泛灰；淺色區塊偏粉也不用綠色，綠疊棕色粉底會發灰。"
                                        "")
        if n_lab[0] < 60:
            plan["deep_skin_note"] = "原膚色較深：校色選偏紅的深橘，粉底對周圍膚色。"
    plan["steps"] = [
        "防曬與隔離先上；淺色區塊處對紫外線特別敏感。",
        "局部校色：只在淺色區塊上薄點蜜桃／橘色（不用綠色），分小區塊，不要整臉上。",
        "粉底對淺色區塊周圍的膚色選色：面中最多、往外推薄，T 字平刷、眼周最薄；用海綿**點壓**，不要來回搓：摩擦可能在該處長出新的淺色區塊。（化妝師分區上法）",
        "面中打亮：額頭中央、鼻樑、下巴、眼下三角，霧面或微光；T 字少量蜜粉按壓控油。",
        "需要的地方再疊一層遮瑕；淺色區塊邊緣色差最明顯，點上淺一點的遮瑕修邊、往外淡化。",
        "少量蜜粉輕壓定妝。",
        "照光治療當天不上妝。",
        "選色時在下顎線試三條（淺一階、最接近、深一階），到窗邊自然光看、等 15–30 分鐘再決定：底妝和皮脂混合、變乾後會稍微變深，"
        "猶豫時選略淺半階的。",
    ]
    plan["gamut_note"] = gamut_note(plan["foundation"]) or gamut_note(plan["concealer"])
    plan["base"] = plan["concealer"] + plan["foundation"]   # 舊欄位：遮瑕在前
    return plan


def category_source(category: str, source: str) -> str:
    """all 時：這個類別有官網現行色號就只用官網，沒有才用全部（含虛構示範型錄）。"""
    if source == "all" and any(p["category"] == category for p in load_catalogs("official")):
        return "official"
    return source


def brow_matches(hex_color: str, source: str = "all", skin_hex: str | None = None) -> list[dict]:
    """眉筆：先替每個色號貼眉色卡標籤（深淺×色系），只推薦同深淺同色系、或差一級／色系相近的（標籤先建立再匹配）。"""
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
    """items：[{"item": "brow", "category": "brow", "hex": "#7F6A5D"}, …] → 每項附最近的專櫃色號。
    眉、唇、腮紅是設計好的顏色對產品色票（同一種顏色空間），直接比色差即可。"""
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


# 保養與保護步驟（依內部設計紀錄的上妝流程與兩套規劃表）
_CARE_COMMON = [
    ("溫和清潔、保濕", "皮膚薄、容易紅；動作輕，不搓不擦，乾燥的地方上妝會浮粉。"),
    ("防曬 SPF50+，出門前 10–15 分鐘", "黑色素較少時，紫外線防護比較弱。"),
    ("隔離（溫和、無香料的妝前乳）", "在皮膚和彩妝之間多一層保護，也讓後面的妝服貼。"),
]
_CARE = {
    "albinism": _CARE_COMMON[:2] + [
        ("眼睛也要防曬：寬簷帽、包覆式抗 UV 墨鏡", "眼睛怕光，防曬不只擦臉。"),
        ("隔離：溫和、無香料的妝前乳；要修色只修泛紅（綠色系）", "多一層保護、讓妝服貼；不選美白提亮的紫色系，目標是調和膚色，不是更白。"),
        ("新產品先在耳後或下顎小範圍試用", "選溫和、無香料，降低過敏機會。"),
        ("底妝（想要時）：顏色接近原本膚色，面中最多往外推薄，T 字平刷、眼周最薄", "照配色卡的底妝色號，往外推到脖子一致；T 字少量蜜粉按壓。保留特色的妝可以不上底妝，隔離一樣要上。"),
        ("面中打亮（想要時）：額頭中央、鼻樑、下巴、眼下三角，霧面或微光", "膚色很淺的人常用粉色系、珠光打亮；量少一點、輕拍。"),
        ("上妝：眉、睫毛、腮紅、唇，少量多次", "照配色卡的色號；保留原樣也可以。"),
        ("卸妝：溫和卸妝，指尖輕打圈、紙巾輕壓", "不用力擦。"),
    ],
    "vitiligo": [
        ("溫和清潔、保濕，不搓不刷", "摩擦、抓、用力刷都可能在該處長出新的淺色區塊。"),
        ("防曬 SPF50+，大範圍擦", "淺色區塊處容易曬傷；周圍皮膚不曬黑，色差也比較小。"),
        _CARE_COMMON[2],
        ("避開可能誘發淺色區塊的成分", "染髮劑（PPD）、香水體香劑（PTBP）、部分唇眼彩染料、美白產品；在哪裡用、哪裡出現淺色區塊就停用並看醫師。"),
        ("照光治療當天不上妝", "照光前 2 小時不擦乳霜、化妝品、香水；一般保濕可以。"),
        ("局部校色：只在淺色區塊上薄點蜜桃／橘色（不用綠色）", "淺色區塊缺色素，先校色粉底才不會泛灰；不要整臉上。"),
        ("底妝：對淺色區塊周圍的膚色選色，面中最多、往外推薄；T 字平刷、眼周最薄；海綿點壓", "照配色卡的底妝色號；往外推到脖子一致。T 字少量蜜粉按壓控油。不想上可以跳過。"),
        ("面中打亮：額頭中央、鼻樑、下巴、眼下三角，選霧面或微光", "讓臉中間亮一點、更立體；珠光太多會讓紋理和淺色區塊更明顯。"),
        ("遮瑕用點壓，不推不擦；邊緣用淺一點的修邊", "照配色卡的遮瑕打底色、修邊色；不想遮也完全可以。"),
        ("卸妝：溫和卸妝，紙巾輕壓", "同樣不搓。"),
    ],
}


def care_plan(condition: str = "albinism", skin: dict | None = None) -> list[dict]:
    """回傳保養保護步驟；有膚質分析時交由對應入口調整配方卡。"""
    steps = _CARE.get(condition, _CARE["albinism"])
    if skin is not None:
        if condition == "vitiligo":
            from care_rules_vitiligo import personalize
            steps = personalize(steps, skin)
        elif condition == "albinism":
            from care_rules_albinism import personalize
            steps = personalize(steps, skin)
    return [{"step": i + 1, "text": t, "why": w} for i, (t, w) in enumerate(steps)]


# 眉色：依照片取到的原眉色選色（KB：貼近原本眉色或略深、只補稀疏處；太深的眉在極淺膚色上會很兇；
# 雅漾：淺膚色用金色、深膚色用棕色）。「略深」用來加強立體感。
BROW_VISIBLE_DL = 15.0     # 膚色與眉色亮度差 ≥ 15：眉毛本來就看得到（多數白斑症、一般人）
BROW_MAX_DL_PALE = 38.0    # 淺膚色（L ≥ 72）眉色最多比膚色深 38，避免太兇


def brow_from_photo(brow_hex: str, skin_hex: str, level: str = "defined") -> dict:
    """level：soft（照原眉色補一點）或 defined（照原眉色略深，加強立體感）。回傳色碼、強度與理由。"""
    if level not in ("soft", "defined"):
        raise ValueError(f"眉色層級只有 soft／defined：{level}")
    import brow_card as bcard
    tg = bcard.target(brow_hex, skin_hex, level)          # 起：眉色卡標籤（深淺×色系）決定目標色
    if tg:
        return {"hex": tg["hex"], "intensity": (35 if level == "soft" else 50) if tg["visible"] else (45 if level == "soft" else 55),
                "brow_visible": tg["visible"], "level": level, "why": tg["why"],
                "tag": {"level": tg["level"], "family": tg["family"],
                        "label": f'第 {tg["level"]} 級{bcard.level_name(tg["level"])}・{tg["family"]}'}}
    bL, ba, bb = hex_to_lab(brow_hex)
    sL, _, _ = hex_to_lab(skin_hex)
    visible = (sL - bL) >= BROW_VISIBLE_DL
    if visible:
        # 原本就有眉色：同色補稀疏，或深一階加立體；不往淺的方向改
        L = bL if level == "soft" else max(8.0, bL - 6)
        lab, intensity = (L, ba, bb), (35 if level == "soft" else 50)
        why = "照你原本的眉色補稀疏處" if level == "soft" else "照你原本的眉色深一階，加強立體感"
    else:
        # 眉毛很淡（白化症常見）：沿用原眉色的色相，加深到看得見；淺膚色有深度上限
        chroma = math.hypot(ba, bb)
        hue = math.atan2(bb, ba) if chroma >= 3 else math.radians(55)   # 幾乎無彩時用灰褐的色相
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


# 皮膚比較敏感的提醒
# 來源：內部研究筆記（敏感肌）
_SENSITIVE = {
    "albinism": {
        "title": "你的皮膚比較敏感，上妝前先看這幾點",
        "items": [
            ("黑色素較少時，強光下很快就可能曬傷", "防曬、遮陽、長袖、抗 UV 墨鏡一起用。"),
            ("皮膚薄、容易紅，手指一壓就可能留紅印", "上妝用輕拍，不要來回推。"),
            ("新產品先在耳後或下顎小範圍試，選溫和、無香料", "不要一開始就買大罐。"),
            ("定期給皮膚科檢查", "長期日曬會讓皮膚慢慢出現變化；新長的斑點、顏色不均或形狀改變，請給皮膚科醫師檢查。"),
        ],
    },
    "vitiligo": {
        "title": "你的皮膚比較敏感，上妝前先看這幾點",
        "items": [
            ("淺色區塊的黑色素較少，特別容易曬傷", "大範圍擦防曬；周圍皮膚不曬黑，色差也比較小。"),
            ("不要搓、抓、用力刷", "摩擦可能讓該處長出新的淺色區塊。上妝、卸妝都用點壓。"),
            ("留意可能誘發淺色區塊的成分", "染髮劑（PPD）、香水體香劑（PTBP）、部分唇眼彩染料、美白成分（如杜鵑醇）；在哪裡用、哪裡出現淺色區塊就停用並看醫師。"),
            ("新產品先小範圍試，不要急著買大罐", "出現刺激、過敏就停用。"),
            ("照光治療當天不上妝；不確定是什麼、或淺色區塊範圍變大，請皮膚科確認", "以醫師或衛教人員的說明為準。"),
        ],
    },
}


def sensitive_notes(condition: str = "albinism") -> dict:
    d = _SENSITIVE.get(condition, _SENSITIVE["albinism"])
    return {"title": d["title"], "items": [{"text": t, "why": w} for t, w in d["items"]]}
