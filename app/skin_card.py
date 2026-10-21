"""膚色色卡：先把膚色對到自己的色卡（深淺 10 級 × 冷暖 3 種），再從色卡找專櫃色號。

為什麼不直接用色差（ΔE）比：照片裡的皮膚受光線、相機影響，比產品色票灰、偏粉（ 實測：照片膚色彩度 19、
色相 43°；同亮度的產品色票彩度 33–49、色相 58–70°），直接比會全部「沒有接近色」。
所以兩邊各自分級：
- 照片膚色：深淺用亮度 L*，冷暖用色相（彩度太低時判中性，請本人確認）。
- 專櫃色號：深淺用色票亮度 L*；冷暖優先用品牌自己的標示（官網說明、色號裡的 C/N/W 等字母），
  沒有標示時才用「同深淺色號之間的相對色相」排名（低的三分之一偏冷、高的三分之一偏暖）。
兩邊落在同一格就是同深淺同冷暖。同深淺沒有色號時，給一淺一深兩色混合。
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path

import color_engine as ce

# 深淺 10 級的 L* 下界（第 1 級最淺）；色卡顯示色只是示意
LEVEL_BOUNDS = [88, 82, 76, 70, 64, 57, 50, 42, 34, -1]
LEVEL_NAMES = ["極淺", "很淺", "淺", "淺中", "中", "中深", "深", "很深", "極深", "最深"]
TONES = ("cool", "neutral", "warm")
# 排序權重（ 推薦排序 C，隔離區比較 <本機紀錄>）：色差 ΔE00 每 3 約等於差一級；
# 比膚色淺的色號級差算兩倍（白化症規則「不選更白」；彩妝教學：太淺會顯灰）
DE_WEIGHT = 3.0
LIGHTER_PENALTY = 2.0
# 現行排序（R0：品牌冷暖＋ΔE/40）的第 1 名色差已小於這個值就保留，不被新排序換掉
KEEP_DE = 1.5
R0_DE_WEIGHT = 40.0
TONE_LABEL = {"cool": "冷調", "neutral": "中性", "warm": "暖調"}
_TONE_HUE = {"cool": 40.0, "neutral": 55.0, "warm": 70.0}



_VERDICT_CODES = {"很接近": "very_close", "接近": "close", "有差": "gap"}   # 前端用代碼比對，畫面文字才能跟著語言換


def _verdict(ld, tg) -> str:
    return "很接近" if ld == 0 and tg == 0 else ("接近" if ld == 0 or (ld == 1 and tg == 0) else "有差")

def level_of(L: float) -> int:
    for i, bound in enumerate(LEVEL_BOUNDS):
        if L >= bound:
            return i + 1
    return len(LEVEL_BOUNDS)


def level_mid_L(level: int) -> float:
    hi = 100 if level == 1 else LEVEL_BOUNDS[level - 2]
    lo = max(LEVEL_BOUNDS[level - 1], 20)
    return (hi + lo) / 2


def card() -> list[dict]:
    """30 格色卡（示意色）：前端畫色卡、配色卡顯示用。"""
    cells = []
    for lv in range(1, len(LEVEL_BOUNDS) + 1):
        L = level_mid_L(lv)
        chroma = 10 + 12 * min(1.0, (95 - L) / 40)
        for t in TONES:
            h = math.radians(_TONE_HUE[t])
            cells.append({"level": lv, "level_name": LEVEL_NAMES[lv - 1], "tone": t, "tone_label": TONE_LABEL[t],
                          "hex": ce.lab_to_hex((L, chroma * math.cos(h), chroma * math.sin(h)))})
    return cells


def classify_photo(hex_color: str) -> dict:
    """照片取到的膚色 → 色卡格子。"""
    prof = ce.skin_profile(hex_color)
    lv = level_of(prof["lab"][0])
    return {"hex": hex_color, "level": lv, "level_name": LEVEL_NAMES[lv - 1], "tone": prof["undertone"],
            "tone_label": TONE_LABEL[prof["undertone"]], "tone_confirm": prof["low_chroma"], "profile": prof}


# ---------------------------------------------------------------- 專櫃色號分級

# 品牌自己的色號冷暖寫法：品牌色號資料只留本機（app/brand_codes_local.json，不進公開 repo），沒有就只用通用規則
_BRAND_CODE_FILE = Path(__file__).resolve().parent / "brand_codes_local.json"
_BRAND_CODE = ({b: [tuple(x) for x in v] for b, v in json.loads(_BRAND_CODE_FILE.read_text(encoding="utf-8"))["codes"].items()}
               if _BRAND_CODE_FILE.exists() else {})
_GENERIC_CODE = [(r"^\.?\d+(\.\d+)?\s*C\d?", "cool"), (r"^\.?\d+(\.\d+)?\s*W\d?", "warm"),
                 (r"^\.?\d+(\.\d+)?\s*N\d?", "neutral")]


def brand_tone(shade: dict, brand: str | None = None) -> str | None:
    """品牌自己的冷暖標示。順序：明寫的 undertone 欄位 → 說明裡「cool/warm/neutral undertone」→
    品牌色號字母 → 色號名稱裡的 Cool/Warm/Neutral → 說明裡的 pink/yellow 描述。都沒有就回 None。"""
    if shade.get("undertone") in TONES:
        return shade["undertone"]
    code, desc = shade.get("code", ""), shade.get("description", "").lower()
    m = re.search(r"\b(cool|warm|neutral)\b[\w\s-]{0,30}undertone", desc)
    if m:
        return m.group(1)
    for rx, tone in _BRAND_CODE.get(brand or "", []) + _GENERIC_CODE:
        if re.search(rx, code, re.I):
            return tone
    m = re.search(r"\b(cool|warm|neutral)\b", code, re.I)
    if m:
        return m.group(1).lower()
    pinkish = re.search(r"\b(pink|rosy|red)", desc)
    yellowish = re.search(r"\b(yellow|golden|olive)", desc)
    if "balance" in desc or (pinkish and yellowish):
        return "neutral"
    if pinkish:
        return "cool"
    if yellowish:
        return "warm"
    return None


# 色號名稱裡的冷暖字（只看名稱與編碼，不看官網說明句）；Honey、Beige、Sand 是顏色名不是冷暖標示（有的品牌 Honey 官網寫 Cool）
_NAME_WORDS = [(r"\b(cool|warm|neutral)\b", None), (r"\b(golden|olive)\b", "warm"), (r"\b(rosy|rose|pink)\b", "cool")]


def label_tone(code: str, brand: str | None = None) -> str | None:
    """色號名稱／品牌編碼本身標的冷暖。
    有的品牌編碼字母和一般說法相反（例如某組字母偏黃、另一組偏粉），對照表只放本機品牌檔 brand_codes_local.json。"""
    for rx, tone in _BRAND_CODE.get(brand or "", []) + _GENERIC_CODE:
        if re.search(rx, code, re.I):
            return tone
    for rx, tone in _NAME_WORDS:
        m = re.search(rx, code, re.I)
        if m:
            return tone or m.group(1).lower()
    return None


def _hue(lab) -> float:
    return math.degrees(math.atan2(lab[2], lab[1]))


def classify_catalog(products: list[dict]) -> list[dict]:
    """把型錄每個膚色類色號（粉底、遮瑕）標上深淺級與冷暖。冷暖來源記在 tone_source。"""
    rows = []
    for p in products:
        if p["category"] not in ("foundation", "concealer"):
            continue
        for s in p["shades"]:
            lab = ce.hex_to_lab(s["hex"])
            rows.append({"brand": p["brand"], "product": p["name"], "category": p["category"], "source": p["source"],
                         "code": s["code"], "hex": s["hex"], "lab": lab, "level": level_of(lab[0]),
                         "tone": brand_tone(s, p["brand"]), "tone_source": "brand"})
    # 排序用的相對冷暖（tone_rel）：同品牌、同類別、±1 級內依色相角的百分位分三等分（低＝偏紅＝cool、高＝偏黃＝warm）。
    # 品牌文字標示（tone）只留給畫面顯示：同樣標 cool，各品牌的網頁色票色相差很多（ 偏黃調查）
    for r in rows:
        peers = [_hue(x["lab"]) for x in rows if x["brand"] == r["brand"] and x["category"] == r["category"] and abs(x["level"] - r["level"]) <= 1]
        rank = sum(h < _hue(r["lab"]) for h in peers) / max(1, len(peers) - 1)
        r["tone_rel"] = "cool" if rank < 1 / 3 else ("warm" if rank > 2 / 3 else "neutral")
        # 排序用冷暖：名稱／編碼有標就用標示，沒標才用相對位置
        r["tone_rank"] = label_tone(r["code"], r["brand"]) or r["tone_rel"]
    # 沒有品牌標示的：和同深淺（±1 級）所有色號比相對色相
    for r in rows:
        if r["tone"]:
            continue
        peers = sorted(_hue(x["lab"]) for x in rows if abs(x["level"] - r["level"]) <= 1)
        rank = sum(h < _hue(r["lab"]) for h in peers) / max(1, len(peers) - 1)
        r["tone"] = "cool" if rank < 1 / 3 else ("warm" if rank > 2 / 3 else "neutral")
        r["tone_source"] = "relative_hue"
    return rows


def _tone_gap(a: str, b: str) -> float:
    if a == b:
        return 0.0
    return 0.5 if "neutral" in (a, b) else 1.0


def fit_text(level_diff: int, tone_gap: float) -> str:
    if level_diff == 0 and tone_gap == 0:
        return "同深淺、同冷暖"
    if level_diff == 0:
        return "同深淺、冷暖相近" if tone_gap < 1 else "同深淺、冷暖不同"
    if level_diff == 1:
        return "差一級"
    return f"差 {level_diff} 級"


def counter_shades(target: dict, rows: list[dict], category: str = "foundation", per_product: int = 1) -> list[dict]:
    """色卡格子 → 每條產品線最適合的色號；同深淺沒有時附一淺一深兩色混合。"""
    out = []
    lines = {}
    for r in rows:
        if r["category"] == category:
            lines.setdefault((r["brand"], r["product"], r["source"]), []).append(r)
    tL = level_mid_L(target["level"]) if "L" not in target else target["L"]
    # 同一格裡挑「顏色最接近膚色」的那支；
    # 色差只當格內排序用，因為照片和色票整體有落差，不拿來判斷接不接近
    t_lab = tuple(target["profile"]["lab"]) if target.get("profile") else None
    # 保留門檻：現行排序（品牌冷暖＋ΔE/40）的第 1 名若色差 < KEEP_DE，就固定排第 1
    keep = None
    if t_lab:
        r0 = [(abs(s["level"] - target["level"]) + _tone_gap(s["tone"], target["tone"]) + ce.delta_e2000(t_lab, s["lab"]) / R0_DE_WEIGHT, i, s)
              for i, s in enumerate(r for r in rows if r["category"] == category)]
        if r0:
            s0 = min(r0, key=lambda x: (x[0], x[1]))[2]
            keep = s0 if ce.delta_e2000(t_lab, s0["lab"]) < KEEP_DE else None
    for (brand, product, source), shades in lines.items():
        scored = []
        for s in shades:
            ld = abs(s["level"] - target["level"])
            tg = _tone_gap(s.get("tone_rank") or s.get("tone_rel") or s["tone"], target["tone"])
            de = ce.delta_e2000(t_lab, s["lab"]) if t_lab else None
            tie = de / DE_WEIGHT if t_lab else abs(s["lab"][0] - tL) / 50
            pen = ld * (LIGHTER_PENALTY if s["level"] < target["level"] else 1.0)
            score = -1.0 if s is keep else pen + tg + tie
            scored.append((score, ld, tg, s, de))
        scored.sort(key=lambda x: x[0])
        best = []
        for score, ld, tg, s, de in scored[:per_product]:
            best.append({"brand": brand, "product": product, "source": source, "code": s["code"], "hex": s["hex"],
                         "level": s["level"], "tone": s["tone"], "tone_source": s["tone_source"],
                         "level_diff": ld, "fit": fit_text(ld, tg), "score": round(score, 3),
                         "delta_e": round(de, 1) if de is not None else None, "tone_rel": s.get("tone_rel"),
                         "tone_rank": s.get("tone_rank"), "kept": s is keep,
                         "verdict": _verdict(ld, tg), "verdict_code": _VERDICT_CODES[_verdict(ld, tg)]})
        top = best[0]
        if top["level_diff"] > 0:
            lighter = [x for x in scored if x[3]["lab"][0] > tL and x[3]["level"] < target["level"]]
            deeper = [x for x in scored if x[3]["lab"][0] < tL and x[3]["level"] > target["level"]]
            if lighter and deeper:
                a, b = min(lighter, key=lambda x: x[0])[3], min(deeper, key=lambda x: x[0])[3]
                top["mix"] = {"lighter": a["code"], "deeper": b["code"], "ratio": "1:1",
                              "why": "這條產品線沒有同深淺的色號：一淺一深各取一半混合。"}
        out.extend(best)
    out.sort(key=lambda r: r["score"])
    return out


def match_skin(hex_color: str, products: list[dict], category: str = "foundation",
               tone_override: str | None = None) -> dict:
    """照片膚色 → 色卡格子 → 各產品線色號。"""
    cell = classify_photo(hex_color)
    if tone_override in TONES:
        cell = dict(cell, tone=tone_override, tone_label=TONE_LABEL[tone_override], tone_confirm=False)
    rows = classify_catalog(products)
    return {"card": cell, "shades": counter_shades(cell, rows, category)}
