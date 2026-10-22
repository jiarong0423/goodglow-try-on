"""Skin-tone card: first match the skin color to its own card cell (10 depth levels x 3 undertones), then find counter shades from the card.

Why not compare directly by color difference (ΔE): skin in a photo is affected by lighting and camera, and is greyer and pinker than product swatches (10-15 measurement: photo skin chroma 19,
hue 43°; product swatches of the same lightness chroma 33-49, hue 58-70°), so a direct comparison would give "no close shade" for everything.
So each side is graded on its own:
- Photo skin: depth by lightness L*, undertone by hue (when chroma is too low it is judged neutral and the person is asked to confirm).
- Counter shades: depth by swatch lightness L*; undertone prefers the brand's own marking (official description, C/N/W letters in the shade code),
  and only when unmarked uses the ranking of "relative hue among shades of the same depth" (the lowest third is cool, the highest third is warm).
Both sides landing in the same cell means the same depth and undertone. When no shade of the same depth exists, mix one lighter and one deeper.
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path

import color_engine as ce

# lower L* bound of each of the 10 depth levels (level 1 is the lightest); the card's display colors are only illustrative
LEVEL_BOUNDS = [88, 82, 76, 70, 64, 57, 50, 42, 34, -1]
LEVEL_NAMES = ["極淺", "很淺", "淺", "淺中", "中", "中深", "深", "很深", "極深", "最深"]
TONES = ("cool", "neutral", "warm")
# ranking weights (10-17 ranking C, isolation comparison local run notes (not shipped)): every 3 of ΔE00 is about one level;
# a shade lighter than the skin counts its level gap double (albinism rule "do not pick whiter"; makeup tutorials: too light looks grey)
DE_WEIGHT = 3.0
LIGHTER_PENALTY = 2.0
# if the current ranking's (R0: brand undertone + ΔE/40) first-place color difference is already below this, keep it and do not let the new ranking replace it
KEEP_DE = 1.5
R0_DE_WEIGHT = 40.0
TONE_LABEL = {"cool": "冷調", "neutral": "中性", "warm": "暖調"}
_TONE_HUE = {"cool": 40.0, "neutral": 55.0, "warm": 70.0}



_VERDICT_CODES = {"很接近": "very_close", "接近": "close", "有差": "gap"}   # the frontend compares by code, so on-screen text can follow the language


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
    """30-cell card (illustrative colors): used by the frontend to draw the card and show the shade card."""
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
    """Skin color from the photo -> card cell."""
    prof = ce.skin_profile(hex_color)
    lv = level_of(prof["lab"][0])
    return {"hex": hex_color, "level": lv, "level_name": LEVEL_NAMES[lv - 1], "tone": prof["undertone"],
            "tone_label": TONE_LABEL[prof["undertone"]], "tone_confirm": prof["low_chroma"], "profile": prof}


# ---------------------------------------------------------------- counter-shade grading

# how brands write undertone in their shade codes: brand shade-code data stays local only (app/brand_codes_local.json, not in the public repo); without it only the generic rules are used
_BRAND_CODE_FILE = Path(__file__).resolve().parent / "brand_codes_local.json"
_BRAND_CODE = ({b: [tuple(x) for x in v] for b, v in json.loads(_BRAND_CODE_FILE.read_text(encoding="utf-8"))["codes"].items()}
               if _BRAND_CODE_FILE.exists() else {})
_GENERIC_CODE = [(r"^\.?\d+(\.\d+)?\s*C\d?", "cool"), (r"^\.?\d+(\.\d+)?\s*W\d?", "warm"),
                 (r"^\.?\d+(\.\d+)?\s*N\d?", "neutral")]


def brand_tone(shade: dict, brand: str | None = None) -> str | None:
    """A brand's own undertone marking. Order: an explicit undertone field -> "cool/warm/neutral undertone" in the description ->
    the brand shade-code letter -> Cool/Warm/Neutral in the shade name -> pink/yellow wording in the description. None if there is none."""
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


# undertone words in the shade name (only the name and code are read, not the official description sentence); Honey, Beige, Sand are color names, not undertone markings (for some brands Honey is described as Cool on the official site)
_NAME_WORDS = [(r"\b(cool|warm|neutral)\b", None), (r"\b(golden|olive)\b", "warm"), (r"\b(rosy|rose|pink)\b", "cool")]


def label_tone(code: str, brand: str | None = None) -> str | None:
    """The undertone marked by the shade name / brand code itself (owner's rule,: if the brand name already marks undertone (C/W/Golden etc.), use the brand marking; use relative position only when unmarked).
    Some brands' code letters are the opposite of the usual reading (e.g. one set of letters leans yellow and another leans pink); the lookup table lives only in the local brand file brand_codes_local.json."""
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
    """Tag every complexion-category shade (foundation, concealer) in the catalog with a depth level and undertone. The undertone source is recorded in tone_source."""
    rows = []
    for p in products:
        if p["category"] not in ("foundation", "concealer"):
            continue
        for s in p["shades"]:
            lab = ce.hex_to_lab(s["hex"])
            rows.append({"brand": p["brand"], "product": p["name"], "category": p["category"], "source": p["source"],
                         "code": s["code"], "hex": s["hex"], "lab": lab, "level": level_of(lab[0]),
                         "tone": brand_tone(s, p["brand"]), "tone_source": "brand"})
    # relative undertone for ranking (tone_rel): within the same brand and category, within ±1 level, split into three equal groups by the hue-angle percentile (low = reddish = cool, high = yellowish = warm).
    # the brand's text marking (tone) is kept only for display: even when labeled cool, the web swatch hues differ a lot between brands (10-17 yellowness survey)
    for r in rows:
        peers = [_hue(x["lab"]) for x in rows if x["brand"] == r["brand"] and x["category"] == r["category"] and abs(x["level"] - r["level"]) <= 1]
        rank = sum(h < _hue(r["lab"]) for h in peers) / max(1, len(peers) - 1)
        r["tone_rel"] = "cool" if rank < 1 / 3 else ("warm" if rank > 2 / 3 else "neutral")
        # undertone for ranking: use the marking if the name/code has one, otherwise the relative position
        r["tone_rank"] = label_tone(r["code"], r["brand"]) or r["tone_rel"]
    # for shades without a brand marking: compare relative hue against all shades of the same depth (±1 level)
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
    """Card cell -> the best shade for each product line; when none of the same depth exists, attach a one-lighter and one-deeper mix."""
    out = []
    lines = {}
    for r in rows:
        if r["category"] == category:
            lines.setdefault((r["brand"], r["product"], r["source"]), []).append(r)
    tL = level_mid_L(target["level"]) if "L" not in target else target["L"]
    # within the same cell pick the one whose color is closest to the skin ;
    # the color difference is used only for ordering inside the cell, because photos and swatches differ overall, so it is not used to judge whether something is close
    t_lab = tuple(target["profile"]["lab"]) if target.get("profile") else None
    # keep threshold: if the current ranking's (brand undertone + ΔE/40) first place has color difference < KEEP_DE, it is pinned at first
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
    """Photo skin color -> card cell -> shades per product line."""
    cell = classify_photo(hex_color)
    if tone_override in TONES:
        cell = dict(cell, tone=tone_override, tone_label=TONE_LABEL[tone_override], tone_confirm=False)
    rows = classify_catalog(products)
    return {"card": cell, "shades": counter_shades(cell, rows, category)}
