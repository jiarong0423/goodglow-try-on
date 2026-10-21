"""Build the effects of a Makeup VTO request from presets.json. The frontend sends only "which id was chosen for each item", never arbitrary parameters."""
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
    """selection example: {"undertone": "pink_cool", "blush": "soft", "lip": "natural_gloss",
    "brow": "taupe", "lash": "brown", "contour": "keep"}; items not given are treated as keep."""
    p = presets or load_presets()
    tone_id = selection.get("undertone", "neutral")
    if tone_id not in p["undertones"]:
        raise PresetError(f"沒有膚色基調 {tone_id}")
    tone = p["undertones"][tone_id]
    effects: list[dict] = [dict(category="skin_smooth", **p["skin_smooth"])]  # skin smoothing is always 0

    # foundation goes on first (whole face, within the skin region); color = the shade the skin-tone card matched automatically, then brow, lip, blush, lash are layered on top
    found = _pick(p["foundation"], selection.get("foundation", "keep"), "foundation")
    if found["id"] != "keep":
        effects.append({"category": "foundation", "palettes": [{
            "color": render_foundation(selection, p), "colorIntensity": found["colorIntensity"],
            "glowIntensity": found["glowIntensity"], "coverageIntensity": found["coverageIntensity"]}]})
        if found.get("with_concealer"):
            # full coverage: also layer the official concealer (fixed under the eyes, forehead, chin; fields per makeup_vto.json), same shade
            c = found["with_concealer"]
            effects.append({"category": "concealer", "palettes": [{
                "color": foundation_color(selection), "colorIntensity": c["colorIntensity"],
                "colorUnderEyeIntensity": c["colorUnderEyeIntensity"], "coverageLevel": c["coverageLevel"]}]})

    # contour (albinism facial definition): after foundation, before highlight; cool grey-brown matte, color computed from the foundation shade (contour_color)
    contour = _pick(p["contour"], selection.get("contour", "keep"), "contour")
    if contour["id"] != "keep":
        effects.append({"category": "contour", "pattern": {"name": contour["pattern"]},
                        "palettes": [{"color": contour_color(contour, selection, tone, p),
                                      "colorIntensity": contour["intensity"]}]})

    # highlight: after foundation, before blush; one level lighter than the foundation, low shimmer, so it does not magnify texture or patches.
    # if the option brings its own pattern (above the contour, T-zone) use it, otherwise choose the mid-face highlight pattern by face shape
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
    """Blush and lip colors by skin depth band x undertone (presets.depth_colors; per a 10-16 suggestion). Returns None when there is no skin color (keeping the fixed undertone colors)."""
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
    """Options with their own color (warm apricot) are used as is; others follow skin depth band x undertone, falling back to the fixed undertone color when there is none."""
    return blush.get("color") or (dc["blush"] if dc else tone["blush"])


def blush_intensity(blush: dict, dc: dict | None) -> int:
    """Strength = the depth band's base x (this option's strength / the "light" strength 55), e.g. a hint of flush 0.82, light 1, medium 1.27; options with their own color are not scaled."""
    if not dc or blush.get("color"):
        return blush["intensity"]
    return int(min(100, round(dc["blush_i"] * blush["intensity"] / 55)))


def lip_hex(lip: dict, tone: dict, dc: dict | None) -> str:
    if not lip.get("use_undertone_color"):
        return lip["color"]
    return dc["lip"] if dc else tone["lip"]


def lip_intensity(lip: dict, dc: dict | None) -> int:
    """Lip color that follows the skin: strength is scaled by depth band (base 50); fixed colors (red lip, nude-pink tinted balm) are not scaled."""
    if not dc or not lip.get("use_undertone_color"):
        return lip["intensity"]
    return int(min(100, round(lip["intensity"] * dc["lip_i"] / 50)))


def highlight_pattern(face_shape: str | None, presets: dict) -> str:
    m = presets["highlight_patterns"]
    return m.get(face_shape or "", m["default"])


def highlight_color(selection: dict, hl: dict | None = None, presets: dict | None = None) -> str:
    """Highlight color = the try-on foundation color one level lighter (lift_L, default +10) with chroma multiplied by chroma (default 0.7); with no foundation shade, a soft champagne.
    On deep skin the T-zone highlight does not reduce chroma (chroma 1.0), otherwise it turns grey (image review, 10-16)."""
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
    """Contour color: the try-on foundation color darkened by dL (default -14), a* and b* multiplied by ka and kb = grey-brown (fair skin should not get bronze, which turns orange).
    With no foundation shade, use the contour color of the skin undertone."""
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
    """Foundation color for the try-on. Default = the color code of the shade the card matched; when the option has toward_skin (vitiligo "thin sheer glow"),
    it moves toward the person's surrounding skin color (skin_hex) in Lab by that ratio, simulating the actual color after thin application; the shade recommendation is unchanged."""
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


REDNESS_HUE = 45.0        # skin hue < 45° (pinkish) with enough chroma counts as redness
REDNESS_SHIFT = 4.0       # shift the foundation hue toward yellow (neutral) by 4° (suggested 3-5°,, rule of thumb)


def neutralize_redness(found_hex: str, skin_hex: str | None) -> str:
    """Reddish skin: nudge the foundation hue toward neutral to cancel the redness naturally; no green correcting, no local concealing."""
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
    """Foundation color: the color code of the counter shade matched via the skin-tone card (foundation_hex). Without it, foundation cannot be tried."""
    hex_ = selection.get("foundation_hex") or ""
    if not (len(hex_) == 7 and hex_.startswith("#")):
        raise PresetError("試底妝要先有對應的底妝色號（先取膚色）")
    try:
        int(hex_[1:], 16)
    except ValueError:
        raise PresetError(f"底妝色號色碼錯誤：{hex_}") from None
    return hex_.upper()


def brow_color(brow: dict, selection: dict) -> tuple[str, int]:
    """Fixed-color options are used directly; "natural brow color" options are computed from the brow color and skin color taken from the photo (color_engine.brow_from_photo)."""
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
    """The look style decides which options are selectable (e.g. red lip only in "polished"); options outside the list are refused."""
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
    """Quick three-way choice, "glow" or "clear"; items kept as is do not call the API.
    When a photo brow color exists, the fixed brow color becomes "natural brow color" (brow_photo_map) instead of the fixed light color."""
    p = presets or load_presets()
    if style not in p["styles"]:
        raise PresetError(f"沒有妝感 {style}")
    quick = p["styles"][style]["quick"]
    if which not in quick:
        raise PresetError(f"快速選項只有 {list(quick)}")
    sel = dict(quick[which], undertone=undertone)
    # foundation color = the shade closest to the skin; if no skin color was taken yet (no shade), do not apply foundation
    if foundation_hex:
        sel["foundation_hex"] = foundation_hex
        if condition == "vitiligo" and sel.get("foundation", "keep") != "keep":
            # vitiligo: patches are covered by local concealing on the phone; whole-face foundation only needs to be "natural" so the skin tone is even
            sel["foundation"] = "medium"
        if sel.get("highlight", "keep") != "keep":
            # two directions from: albinism contour + highlight above the contour (facial definition); vitiligo T-zone highlight
            sel.update(p.get("condition_quick", {}).get(condition, {}).get(style, {}))
    elif sel.get("foundation", "keep") != "keep":
        sel["foundation"] = "keep"
    if skin_hex:
        sel["skin_hex"] = skin_hex                   # needed for the vitiligo "thin sheer glow" foundation to move toward the surrounding skin
    if brow_hex and skin_hex:
        sel["brow_hex"] = brow_hex
        if sel.get("brow", "keep") != "keep":
            sel["brow"] = p["brow_photo_map"].get(sel["brow"], sel["brow"])
    elif sel.get("brow", "keep") != "keep" and _pick(p["brow"], sel["brow"], "brow").get("from_photo"):
        sel["brow"] = "fill_light"                   # "natural brow color" needs a photo brow color; without it fall back to the fixed light color, fill a little only
    return sel


def build_body(selection: dict, presets: dict | None = None) -> dict:
    return {"version": "1.0", "effects": build_effects(selection, presets)}


def balance_hint(selection: dict, presets: dict | None = None) -> str | None:
    """When the red lip is very deep, suggest (not automatically) deepening brow and lashes to balance."""
    p = presets or load_presets()
    lip = _pick(p["lip"], selection.get("lip", "keep"), "lip")
    if lip.get("suggest_balance") and selection.get("brow", "keep") in ("keep", "light_brown", "taupe"):
        return "深色紅唇比較搶眼，可以試試眉毛「灰褐深一階」和棕色睫毛來平衡。"
    return None


LAYER_ORDER = ("foundation", "contour", "highlight", "blush", "lip", "brow", "lash")   # layered one on top of another: foundation -> contour -> highlight -> blush -> lip -> brow -> lash
LAYER_LABEL = {"foundation": "底妝", "contour": "修容", "highlight": "打亮", "blush": "腮紅", "lip": "唇", "brow": "眉毛", "lash": "睫毛"}


def layers(selection: dict) -> list[tuple[str, dict]]:
    """Split a look into cumulative layered options: layer 1 is foundation only, layer 2 = foundation + blush ...; items kept as is are skipped."""
    base = {k: v for k, v in selection.items() if k not in ITEMS}
    out, acc = [], {}
    for item in LAYER_ORDER:
        if selection.get(item, "keep") == "keep":
            continue
        acc[item] = selection[item]
        out.append((item, dict(base, **acc)))
    return out
