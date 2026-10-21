"""依 presets.json 組 Makeup VTO 的 effects。前端只送「哪一項選第幾個 id」，不送任意參數。"""
from __future__ import annotations

import json
from pathlib import Path

PRESETS_PATH = Path(__file__).resolve().parent / "presets.json"


class PresetError(ValueError):
    pass


def load_presets(path: Path = PRESETS_PATH) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _pick(options: list[dict], option_id: str, item: str) -> dict:
    for opt in options:
        if opt["id"] == option_id:
            return opt
    raise PresetError(f"{item} 沒有選項 {option_id}")


def build_effects(selection: dict, presets: dict | None = None) -> list[dict]:
    """selection 例：{"undertone": "pink_cool", "blush": "soft", "lip": "natural_gloss",
    "brow": "taupe", "lash": "brown", "contour": "keep"}；沒給的項目視為 keep。"""
    p = presets or load_presets()
    tone_id = selection.get("undertone", "neutral")
    if tone_id not in p["undertones"]:
        raise PresetError(f"沒有膚色基調 {tone_id}")
    tone = p["undertones"][tone_id]
    effects: list[dict] = [dict(category="skin_smooth", **p["skin_smooth"])]  # 磨皮一律 0

    # 底妝先上（全臉、依皮膚範圍）；顏色＝膚色色卡自動對到的色號，再疊眉、唇、腮紅、睫毛
    found = _pick(p["foundation"], selection.get("foundation", "keep"), "foundation")
    if found["id"] != "keep":
        effects.append({"category": "foundation", "palettes": [{
            "color": render_foundation(selection, p), "colorIntensity": found["colorIntensity"],
            "glowIntensity": found["glowIntensity"], "coverageIntensity": found["coverageIntensity"]}]})
        if found.get("with_concealer"):
            # 全覆蓋：再疊官方 concealer（固定套在眼下、額頭、下巴；欄位照 makeup_vto.json），同一個色號
            c = found["with_concealer"]
            effects.append({"category": "concealer", "palettes": [{
                "color": foundation_color(selection), "colorIntensity": c["colorIntensity"],
                "colorUnderEyeIntensity": c["colorUnderEyeIntensity"], "coverageLevel": c["coverageLevel"]}]})

    # 修容（白化症五官立體）：底妝之後、打亮之前；冷調灰褐霧面，顏色由底妝色號算（contour_color）
    contour = _pick(p["contour"], selection.get("contour", "keep"), "contour")
    if contour["id"] != "keep":
        effects.append({"category": "contour", "pattern": {"name": contour["pattern"]},
                        "palettes": [{"color": contour_color(contour, selection, tone, p),
                                      "colorIntensity": contour["intensity"]}]})

    # 打亮：底妝之後、腮紅之前；顏色比底妝淺一階、低珠光，不放大紋理與白斑。
    # 選項自帶圖樣就用它（修容上方、T 字），否則依臉型選面中打亮圖樣
    hl = _pick(p["highlight"], selection.get("highlight", "keep"), "highlight")
    if hl["id"] != "keep":
        effects.append({"category": "highlighter",
                        "pattern": {"name": hl.get("pattern") or highlight_pattern(selection.get("face_shape"), p)},
                        "palettes": [{"color": highlight_color(selection, hl, p), "colorIntensity": hl["colorIntensity"],
                                      "glowIntensity": hl["glowIntensity"], "shimmerIntensity": hl["shimmerIntensity"],
                                      "shimmerDensity": hl["shimmerDensity"], "shimmerSize": hl["shimmerSize"]}]})

    dc = depth_colors(selection, tone_id, p)
    blush = _pick(p["blush"]["levels"], selection.get("blush", "keep"), "blush")
    if blush["id"] != "keep":
        effects.append({"category": "blush", "pattern": {"name": p["blush"]["pattern"]},
                        "palettes": [{"color": blush_hex(blush, tone, dc), "texture": "matte",
                                      "colorIntensity": blush_intensity(blush, dc)}]})

    lip = _pick(p["lip"], selection.get("lip", "keep"), "lip")
    if lip["id"] != "keep":
        palette = {"color": lip_hex(lip, tone, dc),
                   "texture": lip["texture"], "colorIntensity": lip_intensity(lip, dc)}
        if lip["texture"] in ("gloss", "sheer", "shimmer", "holographic", "metallic"):
            palette["gloss"] = lip["gloss"]
        if lip["texture"] in ("gloss", "sheer", "shimmer"):
            palette["transparencyIntensity"] = lip["transparencyIntensity"]
        effects.append({"category": "lip_color", "shape": {"name": "original"},
                        "palettes": [palette], "style": {"type": "full"}})

    brow = _pick(p["brow"], selection.get("brow", "keep"), "brow")
    if brow["id"] != "keep":
        color, intensity = brow_color(brow, selection)
        effects.append({"category": "eyebrows", "pattern": {"type": "color"},
                        "palettes": [{"color": color, "colorIntensity": intensity, "texture": "matte"}]})

    lash = _pick(p["lash"], selection.get("lash", "keep"), "lash")
    if lash["id"] != "keep":
        effects.append({"category": "eyelashes", "pattern": {"name": p["lash_pattern"]},
                        "palettes": [{"color": lash["color"], "colorIntensity": lash["intensity"]}]})

    return effects


ITEMS = ("foundation", "contour", "highlight", "blush", "lip", "brow", "lash")


def depth_colors(selection: dict, tone_id: str, presets: dict) -> dict | None:
    """腮紅、唇色依膚色深淺帶×冷暖（presets.depth_colors；經驗建議）。沒有膚色就回 None（沿用冷暖固定色）。"""
    skin = selection.get("skin_hex")
    table = presets.get("depth_colors")
    if not (skin and table):
        return None
    import color_engine as ce
    import skin_card as sc
    try:
        lv = sc.level_of(ce.hex_to_lab(skin)[0])
    except ValueError:
        return None
    band = next((b for b in table["bands"] if lv in b["levels"]), None)
    return band.get(tone_id) if band else None


def blush_hex(blush: dict, tone: dict, dc: dict | None) -> str:
    """有自己顏色的選項（暖杏）照用；其他依膚色深淺帶×冷暖，沒有就用冷暖固定色。"""
    return blush.get("color") or (dc["blush"] if dc else tone["blush"])


def blush_intensity(blush: dict, dc: dict | None) -> int:
    """強度＝深淺帶的基準 ×（本選項強度 ÷「淡」55），例：一點血色 0.82、淡 1、中 1.27；有自己顏色的選項不縮放。"""
    if not dc or blush.get("color"):
        return blush["intensity"]
    return int(min(100, round(dc["blush_i"] * blush["intensity"] / 55)))


def lip_hex(lip: dict, tone: dict, dc: dict | None) -> str:
    if not lip.get("use_undertone_color"):
        return lip["color"]
    return dc["lip"] if dc else tone["lip"]


def lip_intensity(lip: dict, dc: dict | None) -> int:
    """跟著膚色走的唇色：強度依深淺帶縮放（基準 50）；固定色（紅唇、裸粉潤唇）不縮放。"""
    if not dc or not lip.get("use_undertone_color"):
        return lip["intensity"]
    return int(min(100, round(lip["intensity"] * dc["lip_i"] / 50)))


def highlight_pattern(face_shape: str | None, presets: dict) -> str:
    m = presets["highlight_patterns"]
    return m.get(face_shape or "", m["default"])


def highlight_color(selection: dict, hl: dict | None = None, presets: dict | None = None) -> str:
    """打亮色＝試妝底妝色淺一階（lift_L，預設 +10）、彩度乘 chroma（預設 0.7）；沒有底妝色號時用柔和香檳色。
    深膚色的 T 字打亮不降彩度（chroma 1.0），否則發灰。"""
    import color_engine as ce
    hl = hl or {}
    if not selection.get("foundation_hex"):
        return "#F3E3D3"
    try:
        L, a, b = ce.hex_to_lab(render_foundation(selection, presets))
    except (ValueError, PresetError):
        return "#F3E3D3"
    k = hl.get("chroma", 0.7)
    return ce.lab_to_hex((min(95.0, L + hl.get("lift_L", 10)), a * k, b * k))


def contour_color(contour: dict, selection: dict, tone: dict, presets: dict | None = None) -> str:
    """修容色：試妝底妝色暗 dL（預設 −14）、a*、b* 各乘 ka、kb＝灰褐（白皙膚不用古銅，會變橘）。
    沒有底妝色號就用膚色基調的修容色。"""
    if not contour.get("color_from_foundation"):
        return tone["contour"] if contour.get("use_undertone_color") else contour["color"]
    import color_engine as ce
    if not selection.get("foundation_hex"):
        return tone["contour"]
    try:
        L, a, b = ce.hex_to_lab(render_foundation(selection, presets))
    except (ValueError, PresetError):
        return tone["contour"]
    return ce.lab_to_hex((max(5.0, L + contour.get("dL", -14)), a * contour.get("ka", 0.4), b * contour.get("kb", 0.3)))


def render_foundation(selection: dict, presets: dict | None = None) -> str:
    """試妝用的底妝顏色。預設＝色卡對到的色號色碼；選項有 toward_skin 時（白斑「薄透水光」），
    在 Lab 裡往本人周圍膚色（skin_hex）靠這個比例，模擬薄塗後的實際顏色，色號推薦不變。"""
    hex_ = foundation_color(selection)
    p = presets or load_presets()
    opt = next((o for o in p["foundation"] if o["id"] == selection.get("foundation", "keep")), {})
    w, skin = opt.get("toward_skin"), selection.get("skin_hex")
    import color_engine as ce
    if w and skin:
        try:
            f, s = ce.hex_to_lab(hex_), ce.hex_to_lab(skin)
            hex_ = ce.lab_to_hex(tuple(fv + (sv - fv) * w for fv, sv in zip(f, s)))
        except ValueError:
            pass
    return neutralize_redness(hex_, skin)


REDNESS_HUE = 45.0        # 膚色色相 < 45°（偏粉紅）且彩度夠，視為泛紅
REDNESS_SHIFT = 4.0       # 底妝色相往黃（中性）轉 4°（經驗建議 3–5°，經驗建議）


def neutralize_redness(found_hex: str, skin_hex: str | None) -> str:
    """泛紅的皮膚：底妝顏色色相往中性微調，自然中和泛紅，不用綠色修色、不做局部遮瑕。"""
    if not skin_hex:
        return found_hex
    import math
    import color_engine as ce
    try:
        sL, sa, sb = ce.hex_to_lab(skin_hex)
        L, a, b = ce.hex_to_lab(found_hex)
    except ValueError:
        return found_hex
    if math.degrees(math.atan2(sb, sa)) >= REDNESS_HUE or math.hypot(sa, sb) < 6:
        return found_hex
    c, h = math.hypot(a, b), math.atan2(b, a) + math.radians(REDNESS_SHIFT)
    return ce.lab_to_hex((L, c * math.cos(h), c * math.sin(h)))


def foundation_color(selection: dict) -> str:
    """底妝顏色：用膚色色卡對到的專櫃色號色碼（foundation_hex）。沒有就不能試底妝。"""
    hex_ = selection.get("foundation_hex") or ""
    if not (len(hex_) == 7 and hex_.startswith("#")):
        raise PresetError("試底妝要先有對應的底妝色號（先取膚色）")
    try:
        int(hex_[1:], 16)
    except ValueError:
        raise PresetError(f"底妝色號色碼錯誤：{hex_}") from None
    return hex_.upper()


def brow_color(brow: dict, selection: dict) -> tuple[str, int]:
    """固定色選項直接用；照原眉色的選項由照片取到的眉色、膚色算（color_engine.brow_from_photo）。"""
    if not brow.get("from_photo"):
        return brow["color"], brow["intensity"]
    import color_engine as ce
    if not (selection.get("brow_hex") and selection.get("skin_hex")):
        raise PresetError("「照原眉色」要先在照片上點眉毛和臉頰取色")
    try:
        res = ce.brow_from_photo(selection["brow_hex"], selection["skin_hex"], brow["from_photo"])
    except ValueError as exc:
        raise PresetError(f"眉色或膚色色碼錯誤：{exc}") from None
    return res["hex"], res["intensity"]


def check_style_selection(style: str, selection: dict, presets: dict | None = None) -> None:
    """妝感決定可選的選項（例如紅唇只在「精緻」）；不在清單內就拒絕。"""
    p = presets or load_presets()
    if style not in p["styles"]:
        raise PresetError(f"沒有妝感 {style}")
    allowed = p["styles"][style]["options"]
    for item in ITEMS:
        value = selection.get(item, "keep")
        if value not in allowed[item]:
            raise PresetError(f"妝感「{p['styles'][style]['label']}」不提供 {item}={value}")


def quick_selection(style: str, which: str, undertone: str, presets: dict | None = None,
                    brow_hex: str | None = None, skin_hex: str | None = None,
                    foundation_hex: str | None = None, condition: str = "albinism") -> dict:
    """快速三選的「好氣色」（glow）或「清晰」（clear）；保留原樣不呼叫 API。
    有照片眉色時，固定眉色改成「照原眉色」（brow_photo_map），不套固定淺色。"""
    p = presets or load_presets()
    if style not in p["styles"]:
        raise PresetError(f"沒有妝感 {style}")
    quick = p["styles"][style]["quick"]
    if which not in quick:
        raise PresetError(f"快速選項只有 {list(quick)}")
    sel = dict(quick[which], undertone=undertone)
    # 底妝顏色＝最接近膚色的色號；還沒取到膚色（沒有色號）就先不上底妝
    if foundation_hex:
        sel["foundation_hex"] = foundation_hex
        if condition == "vitiligo" and sel.get("foundation", "keep") != "keep":
            # 白斑：白斑由手機上的局部遮瑕蓋住，全臉底妝只要「自然」讓膚色一致
            sel["foundation"] = "medium"
        if sel.get("highlight", "keep") != "keep":
            #  兩個方向：白化症修容＋修容上方打亮（五官立體）；白斑症 T 字打亮
            sel.update(p.get("condition_quick", {}).get(condition, {}).get(style, {}))
    elif sel.get("foundation", "keep") != "keep":
        sel["foundation"] = "keep"
    if skin_hex:
        sel["skin_hex"] = skin_hex                   # 白斑「薄透水光」底妝往周圍膚色靠要用
    if brow_hex and skin_hex:
        sel["brow_hex"] = brow_hex
        if sel.get("brow", "keep") != "keep":
            sel["brow"] = p["brow_photo_map"].get(sel["brow"], sel["brow"])
    elif sel.get("brow", "keep") != "keep" and _pick(p["brow"], sel["brow"], "brow").get("from_photo"):
        sel["brow"] = "fill_light"                   # 「照原眉色」要有照片眉色；沒有就退回固定淺色只補一點
    return sel


def build_body(selection: dict, presets: dict | None = None) -> dict:
    return {"version": "1.0", "effects": build_effects(selection, presets)}


def balance_hint(selection: dict, presets: dict | None = None) -> str | None:
    """紅唇很深時建議（不自動）加深眉、睫毛來平衡。"""
    p = presets or load_presets()
    lip = _pick(p["lip"], selection.get("lip", "keep"), "lip")
    if lip.get("suggest_balance") and selection.get("brow", "keep") in ("keep", "light_brown", "taupe"):
        return "深色紅唇比較搶眼，可以試試眉毛「灰褐深一階」和棕色睫毛來平衡。"
    return None


LAYER_ORDER = ("foundation", "contour", "highlight", "blush", "lip", "brow", "lash")   # 逐層疊加：底妝→修容→打亮→腮紅→唇→眉→睫毛
LAYER_LABEL = {"foundation": "底妝", "contour": "修容", "highlight": "打亮", "blush": "腮紅", "lip": "唇", "brow": "眉毛", "lash": "睫毛"}


def layers(selection: dict) -> list[tuple[str, dict]]:
    """把一套妝拆成逐層累加的選項：第 1 層只有底妝，第 2 層＝底妝＋腮紅……保留原樣的項目跳過。"""
    base = {k: v for k, v in selection.items() if k not in ITEMS}
    out, acc = [], {}
    for item in LAYER_ORDER:
        if selection.get(item, "keep") == "keep":
            continue
        acc[item] = selection[item]
        out.append((item, dict(base, **acc)))
    return out
