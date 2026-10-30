"""Brow color card: first tag every brow-pencil shade (6 depth levels x 5 color families), then match by tag (local, no points spent).

Request (10-16): brow shades looked wrong, so build the tags first and then match. Basis:
- Very-light-skin makeup notes supplied for this project: for very pale skin avoid black and dark grey; use light brown, golden brown, caramel, adjusting along the natural brow; for evening, caramel or grey-brown can deepen it.
- Videos of people with the condition (supplied for this project): some use taupe grey-brown brow gel, some use Blonde 02 light-gold brow pencil.
- A skincare brand's lighter-patch makeup guide: gold brow pencil for light skin, brown for deeper skin; fill light patches on the brow with the pencil and blend with a brush.
- Existing rule: if the brow is already visible, fill in the natural brow color or go one level deeper for definition; never shift toward lighter.
Color family prefers the cool / warm / neutral wording in the brand description (same approach as foundation); only if absent is it computed from the color.
"""
from __future__ import annotations

import math
import re

import color_engine as ce

LEVELS = [  # (level, name, lower bound of L*); light to dark
    (1, "淺金", 68.0), (2, "淺棕", 58.0), (3, "中棕", 45.0), (4, "深棕", 32.0), (5, "黑棕", 20.0), (6, "黑", -1.0)]
LEVEL_MID_L = {1: 72.0, 2: 63.0, 3: 51.0, 4: 38.0, 5: 26.0, 6: 13.0}
# representative color per family (Lab hue angle, chroma), used to compute the color for the target label's try-on
FAMILIES = {"金": (75.0, 18.0), "暖棕": (52.0, 16.0), "中性棕": (58.0, 10.0), "冷灰褐": (65.0, 5.0), "黑": (40.0, 2.0)}
PALE_SKIN_L = 72.0          # skin L* >= 72 counts as very pale: brow color goes at most to level 3, no black (light grey-brown is fine; people with the condition often use taupe)
VISIBLE_DL = 15.0           # skin-vs-brow lightness difference >= 15: the brow is already visible (same as color_engine.BROW_VISIBLE_DL)


def level_of(L: float) -> int:
    for lv, _, lo in LEVELS:
        if L >= lo:
            return lv
    return 6


def level_name(lv: int) -> str:
    return next(n for l, n, _ in LEVELS if l == lv)


def family_of(lab, text: str = "") -> str:
    """Color family: use what the description or name states (black / ash, grey, cool / warm, auburn, chocolate / neutral), otherwise look at the color."""
    L, a, b = lab
    t = text.lower()
    if L < 20 or re.search(r"\bblack black\b|^black$|自然黑", t):
        return "黑"
    # an explicit warm / cool wins (e.g. "warm ash-blonde" counts as warm); only ash or grey alone counts as cool grey
    if re.search(r"\b(warm|auburn|chocolate|caramel|honey|golden|red)\b|暖|焦糖", t):
        return "金" if (L >= 50 and "blonde" in t) or "金" in t else "暖棕"
    if re.search(r"\b(ash|ashy|grey|gray|cool|taupe)\b|灰|冷", t):
        return "冷灰褐"
    if re.search(r"\bneutral\b|中性", t):
        return "中性棕"
    c, h = math.hypot(a, b), math.degrees(math.atan2(b, a))
    if c < 8:
        return "冷灰褐"
    if c >= 14 and h >= 62 and L >= 55:
        return "金"
    if c >= 13:
        return "暖棕"
    return "中性棕"


def tag(hex_color: str, text: str = "") -> dict:
    lab = ce.hex_to_lab(hex_color)
    lv = level_of(lab[0])
    fam = family_of(lab, text)
    return {"level": lv, "level_name": level_name(lv), "family": fam, "label": f"第 {lv} 級{level_name(lv)}・{fam}",
            "pale_ok": lv <= 3 and fam != "黑"}


def tag_color(lv: int, fam: str) -> str:
    h, c = FAMILIES[fam]
    return ce.lab_to_hex((LEVEL_MID_L[lv], c * math.cos(math.radians(h)), c * math.sin(math.radians(h))))


def target(brow_hex: str, skin_hex: str, strength: str = "soft") -> dict:
    """Own brow color -> target label and try-on color. strength: soft (fill a little) or defined (slightly deeper, adds definition)."""
    bL, ba, bb = ce.hex_to_lab(brow_hex)
    sL = ce.hex_to_lab(skin_hex)[0]
    own = tag(brow_hex)
    if sL - bL >= VISIBLE_DL:
        # brow already visible: same family, soft uses the own brow color, defined goes one level deeper; never shift toward lighter
        lv = own["level"] if strength == "soft" else min(6, own["level"] + 1)
        hex_ = brow_hex if strength == "soft" else ce.lab_to_hex((max(8.0, bL - 6), ba, bb))
        return {"level": lv, "family": own["family"], "hex": hex_, "own": own, "visible": True,
                "why": "照你原本的眉色補稀疏處" if strength == "soft" else "照你原本的眉色深一級，加強立體感"}
    if sL >= PALE_SKIN_L:
        # very pale skin, very faint brow: soft uses light golden brown, defined uses light grey-brown (taupe); no black or dark grey
        lv, fam = (2, "金") if strength == "soft" else (3, "冷灰褐")
        why = "眉毛很淡、膚色很淺：用淺金棕補，不用黑或深灰" if strength == "soft" else "眉毛很淡：用淺灰褐略深一點加立體，不用黑或深灰"
    else:
        # faint brow but deeper skin (e.g. a light patch sits on the brow): use the brown family
        lv = min(6, level_of(sL - (24 if strength == "soft" else 32)))
        fam = "暖棕" if own["family"] in ("金", "暖棕") else "中性棕"
        why = "眉毛顏色淡：用棕色系補到看得見"
    return {"level": lv, "family": fam, "hex": tag_color(lv, fam), "own": own, "visible": False, "why": why}


def match(tgt: dict, shades: list[dict], skin_hex: str | None = None, k: int = 3) -> list[dict]:
    """shades: [{brand, product, code, hex, description?, source}]. First same depth and same family, then relax to one level apart / nearby family;
    for very pale skin black and dark cool grey (level 4 and up) are excluded. Each entry returns fit (how well the label matches) and delta_e (against the target try-on color)."""
    pale = skin_hex is not None and ce.hex_to_lab(skin_hex)[0] >= PALE_SKIN_L and not tgt.get("visible")
    near = {"金": {"暖棕", "中性棕", "冷灰褐"}, "暖棕": {"金", "中性棕"}, "中性棕": {"暖棕", "冷灰褐"},
            "冷灰褐": {"中性棕", "暖棕", "金"}, "黑": {"冷灰褐", "中性棕"}}
    tl = ce.hex_to_lab(tgt["hex"])
    rows = []
    for s in shades:
        t = tag(s["hex"], f'{s.get("code", "")} {s.get("description", "")}')
        if pale and not t["pale_ok"]:
            continue
        dl = abs(t["level"] - tgt["level"])
        same_fam = t["family"] == tgt["family"]
        if dl == 0 and same_fam:
            rank, fit = 0, "同深淺、同色系"
        elif dl <= 1 and same_fam:
            rank, fit = 1, "深淺差一級、同色系"
        elif dl == 0 and t["family"] in near[tgt["family"]]:
            rank, fit = 2, "同深淺、色系相近"
        elif dl <= 1 and t["family"] in near[tgt["family"]]:
            rank, fit = 3, "深淺差一級、色系相近"
        else:
            continue
        de = round(ce.delta_e2000(tl, ce.hex_to_lab(s["hex"])), 1)
        rows.append(dict(s, tag=t, fit=fit, rank=rank, delta_e=de))
    rows.sort(key=lambda r: (r["rank"], r["delta_e"]))
    return rows[:k]
