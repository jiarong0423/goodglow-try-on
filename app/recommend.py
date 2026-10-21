"""膚色匹配 → 整套推薦：保養保護流程＋底妝色號＋快速兩款妝容與色號。全部本機計算，不扣點。

流程：取色（白化症一點、白斑症兩點）→ 判冷暖與深淺 → 對色卡 → 依妝感給好氣色／清晰兩款 → 每項附最近色號 →
接上依症狀的保養保護步驟。冷暖只是推估，畫面要讓本人確認或改。
"""
from __future__ import annotations

import color_engine as ce
import full_look as fl
import looks
import skin_card as sc

TONE_ID = {"cool": "pink_cool", "neutral": "neutral", "warm": "warm_ivory"}
ENGINE_TONE = {v: k for k, v in TONE_ID.items()}
CARD_ORDER = ("brow", "lash", "blush", "lip")   # 配色卡由上到下的順序
ITEM_LABEL = {"brow": "眉毛", "lash": "睫毛", "blush": "腮紅", "lip": "唇"}


def shades_for(selection: dict, tone_id: str, presets: dict, source: str = "all") -> list[dict]:
    """把一組妝容選項換成每項的色碼＋最近型錄色號（保留原樣的項目不列）。"""
    tone = presets["undertones"].get(tone_id, presets["undertones"]["neutral"])
    dc = looks.depth_colors(selection, tone_id, presets)        # 腮紅、唇色依膚色深淺帶×冷暖
    items = []
    for item in CARD_ORDER:
        opt_id = selection.get(item, "keep")
        if opt_id == "keep":
            continue
        opts = presets["blush"]["levels"] if item == "blush" else presets[item]
        opt = next((o for o in opts if o["id"] == opt_id), None)
        if not opt:
            raise looks.PresetError(f"{item} 沒有選項 {opt_id}")
        if item == "brow":
            hex_ = looks.brow_color(opt, selection)[0]
        else:
            hex_ = looks.blush_hex(opt, tone, dc) if item == "blush" else (looks.lip_hex(opt, tone, dc) if item == "lip" else opt.get("color"))
        items.append({"item": item, "category": item, "option": opt_id, "label": opt.get("label", opt_id), "hex": hex_})
    return ce.card_matches(items, source)


def recommend(condition: str, style: str, skin_hex: str | None = None, normal_hex: str | None = None,
              patch_hex: str | None = None, undertone_override: str | None = None, source: str = "all",
              presets: dict | None = None, brow_hex: str | None = None,
              patch_ratio: float | None = None, sensitivity: dict | None = None) -> dict:
    p = presets or looks.load_presets()
    if condition == "vitiligo":
        if not (normal_hex and patch_hex):
            raise ValueError("要取兩個顏色：原本膚色、淺色區塊各一點")
        prof = ce.skin_profile(normal_hex)
    else:
        if not skin_hex:
            raise ValueError("要先取膚色：點一下臉頰或下顎")
        prof = ce.skin_profile(skin_hex)
    tone_id = undertone_override or TONE_ID[prof["undertone"]]
    # 本人自己選了冷暖（含「中性」）就照本人的；沒選才讓色卡用照片推估
    engine_tone = ENGINE_TONE.get(tone_id) if undertone_override else None
    if condition == "vitiligo":
        base = ce.vitiligo_plan(normal_hex, patch_hex, engine_tone, source, patch_ratio)
    else:
        base = ce.albinism_base_plan(skin_hex, engine_tone, source)
    found = base.get("foundation") or []
    foundation_hex = found[0]["hex"] if found else None          # 最接近膚色的色號，做好氣色覆蓋
    looks_out = {}
    for which in ("glow", "clear"):
        sel = looks.quick_selection(style, which, tone_id, p, brow_hex, skin_hex or normal_hex, foundation_hex,
                                    condition)
        looks.check_style_selection(style, sel, p)
        looks_out[which] = {"selection": sel, "shades": shades_for(sel, tone_id, p, source)}
    brow = None
    if brow_hex:
        brow = {lv: ce.brow_from_photo(brow_hex, skin_hex or normal_hex, lv) for lv in ("soft", "defined")}
        brow["sample"] = brow_hex
    level = sc.classify_photo(skin_hex or normal_hex)["level"]
    brow_visible = bool(brow_hex) and (ce.hex_to_lab(skin_hex or normal_hex)[0] - ce.hex_to_lab(brow_hex)[0]) >= ce.BROW_VISIBLE_DL
    full = fl.full_look(condition, style, level, tone_id, sensitivity, bool(brow_hex), p, brow_visible=brow_visible)
    color_ctx = {"skin_hex": skin_hex or normal_hex, **({"brow_hex": brow_hex} if brow_hex else {})}
    full["shades"] = shades_for({**full["selection"], **color_ctx}, tone_id, p, source)   # 照原眉色要眉色＋膚色才算得出顏色
    return {"condition": condition, "style": style, "profile": prof, "undertone_id": tone_id, "brow": brow,
            "skin_level": level, "full_look": full,
            "depth_colors": looks.depth_colors({"skin_hex": skin_hex or normal_hex}, tone_id, p),
            "foundation_shade": found[0] if found else None,
            "undertone_confirm": prof["low_chroma"] or undertone_override is None,
            "base": base, "looks": looks_out, "care": ce.care_plan(condition),
            "sensitive": ce.sensitive_notes(condition),
            "note": "冷暖是照片推估，請本人確認；色號以專櫃自然光試色為準。"}
