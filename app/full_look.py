"""Full makeup look: base table of entry x look style (full_looks.json) -> adjust by shade-card level and undertone -> downgrade when sensitivity is high. All computed locally, no points spent.

The rules come from an AI-suggested idea (, source to be verified) and use only options already in presets.json; every adjustment returns a plain-language reason, which the screen lists as is.
Order: base table -> shade-card rules -> sensitivity downgrade (safety first, applied last) -> options not in the look's allowed list fall back to "keep as is".
"""
from __future__ import annotations

import json
from pathlib import Path

import looks

FULL_LOOKS_PATH = Path(__file__).resolve().parent / "full_looks.json"
ITEMS = ("foundation", "contour", "highlight", "brow", "lash", "blush", "lip")


def load_full_looks(path: Path = FULL_LOOKS_PATH) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def is_high_sensitivity(sens: dict | None) -> bool:
    """Three sensitivity questions: stinging/redness "often", or allergy to fragrance/ingredients "yes", counts as high sensitivity."""
    s = sens or {}
    return s.get("irritation") == "often" or s.get("fragrance") == "yes"


def full_look(condition: str, style: str, level: int, tone_id: str, sensitivity: dict | None = None,
              has_brow_sample: bool = False, presets: dict | None = None, data: dict | None = None,
              brow_visible: bool | None = None) -> dict:
    p = presets or looks.load_presets()
    d = data or load_full_looks()
    base = d["base"][condition][style]
    sel = {k: base[k] for k in ITEMS}
    notes: list[str] = []
    allowed = p["styles"][style]["options"]

    def setv(item, value, why):
        if sel[item] == value or value not in allowed[item]:
            return
        sel[item] = value
        notes.append(why)

    alb, vit = condition == "albinism", condition == "vitiligo"
    on = lambda item: sel[item] != "keep"     # noqa: E731  only adjust items that are applied; never add an item on the person's behalf

    # ---- shade-card level and undertone
    if level <= 2:
        if sel["contour"] == "defined":
            setv("contour", "soft", "膚色很淺：修容只用極淡灰階，避免髒感")
        if sel["blush"] in ("medium", "apricot"):
            setv("blush", "soft", "膚色很淺：腮紅用淡的，避免大面積色塊")
        if sel["brow"] in ("taupe_deep", "taupe"):
            setv("brow", "photo_soft" if has_brow_sample else "greige", "眉色很淺：不用深灰褐，照原眉色補或用冷燕麥灰")
        if alb and sel["lash"] in ("black", "brown"):
            setv("lash", "champagne", "睫毛很淺：用香檳淡金，避開粗重深色")
    if level <= 3 and tone_id == "pink_cool":
        if style == "polished" and on("lip"):
            setv("lip", "red2", "粉冷調淺膚色：唇色用冷調櫻桃，避開橘黃")
        if sel["blush"] == "apricot":
            setv("blush", "soft", "粉冷調淺膚色：腮紅避開暖杏")
    if level <= 3 and tone_id == "warm_ivory":
        if on("blush"):
            setv("blush", "apricot", "暖象牙淺膚色：腮紅用暖杏")
        if on("brow") and sel["brow"] not in ("photo_soft", "photo_defined"):
            setv("brow", "light_brown", "暖象牙淺膚色：眉色用淺金棕")
    if vit and 4 <= level <= 6 and tone_id == "neutral":
        if on("brow") and sel["brow"] not in ("fill_light",):
            setv("brow", "taupe", "中性中等膚色：眉色用灰褐，色調穩定")
        if on("lash"):
            setv("lash", "brown", "中性中等膚色：睫毛以棕色為準")
    if vit and level >= 7 and style == "polished":
        setv("contour", "defined", "深膚色：修容要明顯一點，太淺會顯得灰白")
        if on("lip"):
            setv("lip", "red3", "深膚色：精緻款用霧面深紅做對比")
    if vit and 4 <= level <= 7 and tone_id == "warm_ivory" and on("blush"):
        setv("blush", "apricot", "暖調膚色：腮紅用暖杏")

    # ---- high sensitivity: reduce friction, powder and pigment irritation (applied last, safety first)
    high = is_high_sensitivity(sensitivity)
    if high:
        if sel["foundation"] in ("max", "full"):
            setv("foundation", "medium", "容易敏感：底妝降為自然，避免厚重")
        if on("contour"):
            sel["contour"] = "keep"
            notes.append("容易敏感：不上修容，減少來回刷")
        if sel["highlight"] in ("tzone", "lift"):
            setv("highlight", "nose", "容易敏感：打亮只留鼻樑微光")
        if sel["blush"] not in ("keep", "flush"):
            if "flush" in allowed["blush"]:
                setv("blush", "flush", "容易敏感：腮紅只點一點血色")
            else:
                sel["blush"] = "keep"
                notes.append("容易敏感：不上腮紅")
        if sel["lash"] not in ("keep", "translucent_brown"):
            setv("lash", "translucent_brown", "容易敏感：睫毛用淡棕灰，卸妝負擔小")
        if sel["brow"] not in ("keep", "fill_light"):
            setv("brow", "fill_light", "容易敏感：眉只補一點")
        if sel["lip"] == "red3":
            setv("lip", "natural_gloss", "容易敏感：霧面唇容易乾，改水光自然色")

    # ---- brow already visible (e.g. black brows): the fixed colors are all lighter than the natural brow, so always switch to the natural brow color (, owner: the brow shades looked wrong)
    if brow_visible and has_brow_sample and sel["brow"] in ("fill_light", "light_brown", "taupe", "taupe_deep", "greige"):
        new = "photo_defined" if sel["brow"] == "taupe_deep" else "photo_soft"
        if new in allowed["brow"]:
            sel["brow"] = new
            notes.append("眉毛本來就看得見：照原眉色補，不換成較淺的顏色")

    # ---- very faint brow (white brow): the official try-on cannot locate a white brow, and in three people's tests the brow was drawn above the natural brow (, owner: do not draw white brows)
    brow_pale = has_brow_sample and brow_visible is False
    if brow_pale and sel["brow"] != "keep":
        sel["brow"] = "keep"
        notes.append("眉毛顏色很淡：自動試妝抓不準眉毛位置，眉毛保留原樣")

    # ---- options that follow the natural brow color need a brow color sample; without one, switch to "fill a little only"
    if sel["brow"] in ("photo_soft", "photo_defined") and not has_brow_sample:
        sel["brow"] = "fill_light"
    for item in ITEMS:                       # safeguard: any option not in the allowed list falls back to keep as is
        if sel[item] not in allowed[item]:
            sel[item] = "keep"
    looks.check_style_selection(style, sel, p)
    steps = [s for s in d["steps"] if s.get("only") in (None, condition)]
    return {"selection": sel, "why": base["why"], "adjustments": notes, "high_sensitivity": high, "brow_pale": brow_pale,
            "phototherapy": bool((sensitivity or {}).get("phototherapy")), "steps": steps,
            "source_note": "這些妝容規則參考化妝師常見做法與 AI 建議整理，尚未經專業審核；以你自己舒適為準。"}
