"""眉色卡：先替每支眉筆色號建標籤（深淺 6 級 × 色系 5 種），再用標籤匹配（本機，不扣點）。

依據：
- 白化症化妝重點（參考資料）：極白膚色避免黑色、深灰；用淺棕、金棕、焦糖色，順著原眉微調；晚宴可焦糖或灰褐加深。
- 參考影片：有人用 taupe 灰褐眉膏、有人用 Blonde 02 淺金眉筆。
- 保養品牌白斑化妝指南：淺膚色用金色眉筆、深膚色用棕色；眉毛上的淺色區塊用眉筆補、刷子暈開。
- 既有規則：眉毛本來看得見就照原眉色補，或深一階加立體，不往淺的方向改。
色系優先用品牌說明裡的冷／暖／中性字樣（同底妝做法），沒寫才看顏色算。
"""
from __future__ import annotations

import math
import re

import color_engine as ce

LEVELS = [  # (級, 名稱, L* 下限)；由淺到深
    (1, "淺金", 68.0), (2, "淺棕", 58.0), (3, "中棕", 45.0), (4, "深棕", 32.0), (5, "黑棕", 20.0), (6, "黑", -1.0)]
LEVEL_MID_L = {1: 72.0, 2: 63.0, 3: 51.0, 4: 38.0, 5: 26.0, 6: 13.0}
# 色系代表色（Lab 色相角、彩度），用來算「目標標籤」的試妝顏色
FAMILIES = {"金": (75.0, 18.0), "暖棕": (52.0, 16.0), "中性棕": (58.0, 10.0), "冷灰褐": (65.0, 5.0), "黑": (40.0, 2.0)}
PALE_SKIN_L = 72.0          # 膚色 L* ≥ 72 視為很淺：眉色最深到第 3 級、不用黑（淺的灰褐可以，常見用 taupe）
VISIBLE_DL = 15.0           # 膚色與眉色亮度差 ≥ 15：眉毛本來就看得見（同 color_engine.BROW_VISIBLE_DL）


def level_of(L: float) -> int:
    for lv, _, lo in LEVELS:
        if L >= lo:
            return lv
    return 6


def level_name(lv: int) -> str:
    return next(n for l, n, _ in LEVELS if l == lv)


def family_of(lab, text: str = "") -> str:
    """色系：說明或名稱寫了就照寫的（black／ash、grey、cool／warm、auburn、chocolate／neutral），否則看顏色。"""
    L, a, b = lab
    t = text.lower()
    if L < 20 or re.search(r"\bblack black\b|^black$|自然黑", t):
        return "黑"
    # 明寫 warm／cool 優先（例如「warm ash-blonde」算暖），只寫 ash、grey 才算冷灰
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
    """本人眉色 → 目標標籤與試妝顏色。strength：soft（補一點）或 defined（略深、加立體）。"""
    bL, ba, bb = ce.hex_to_lab(brow_hex)
    sL = ce.hex_to_lab(skin_hex)[0]
    own = tag(brow_hex)
    if sL - bL >= VISIBLE_DL:
        # 眉毛本來就看得見：同色系，soft 用原眉色、defined 深一級；不往淺的方向改
        lv = own["level"] if strength == "soft" else min(6, own["level"] + 1)
        hex_ = brow_hex if strength == "soft" else ce.lab_to_hex((max(8.0, bL - 6), ba, bb))
        return {"level": lv, "family": own["family"], "hex": hex_, "own": own, "visible": True,
                "why": "照你原本的眉色補稀疏處" if strength == "soft" else "照你原本的眉色深一級，加強立體感"}
    if sL >= PALE_SKIN_L:
        # 膚色很淺、眉毛很淡：補一點用淺金棕，略深用淺灰褐（taupe）；不用黑與深灰
        lv, fam = (2, "金") if strength == "soft" else (3, "冷灰褐")
        why = "眉毛很淡、膚色很淺：用淺金棕補，不用黑或深灰" if strength == "soft" else "眉毛很淡：用淺灰褐略深一點加立體，不用黑或深灰"
    else:
        # 眉毛很淡但膚色較深（例如淺色區塊長在眉毛上）：用棕色系
        lv = min(6, level_of(sL - (24 if strength == "soft" else 32)))
        fam = "暖棕" if own["family"] in ("金", "暖棕") else "中性棕"
        why = "眉毛顏色淡：用棕色系補到看得見"
    return {"level": lv, "family": fam, "hex": tag_color(lv, fam), "own": own, "visible": False, "why": why}


def match(tgt: dict, shades: list[dict], skin_hex: str | None = None, k: int = 3) -> list[dict]:
    """shades：[{brand, product, code, hex, description?, source}]。先同深淺同色系，再放寬到深淺差一級、色系相近；
    膚色很淺時排除黑與深冷灰（第 4 級以上）。每筆回 fit（標籤吻合程度）與 delta_e（對目標試妝色）。"""
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
