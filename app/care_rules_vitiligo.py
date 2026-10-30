"""Skin-analysis recipe-card rules for the light-patch entry."""

import math


def personalize(steps, skin):
    scores = skin.get("scores") if isinstance(skin, dict) else None
    skin_type = skin.get("skin_type") if isinstance(skin, dict) else None
    score_keys = ("hd_moisture", "hd_dark_circle", "hd_redness", "hd_radiance",
                  "hd_oiliness", "hd_texture", "hd_pore")
    valid_scores = isinstance(scores, dict) and all(
        isinstance(scores.get(key), (int, float))
        and not isinstance(scores[key], bool)
        and math.isfinite(scores[key])
        for key in score_keys
    )
    low_two = set(sorted(score_keys, key=scores.get)[:2]) if valid_scores else set()
    normal_regions = isinstance(skin_type, dict) and all(
        skin_type.get(region) == "Normal" for region in ("whole", "t_zone", "u_zone")
    )
    no_oil_control = valid_scores and normal_regions and scores["hd_oiliness"] >= 75

    personalized = []
    for text, why in steps:
        if text.startswith("溫和清潔") and "hd_moisture" in low_two:
            text = "溫和清潔，保濕要做足再上妝；不搓不刷"
            why = "這次膚質分析裡，保濕是分數最低的一項；乾的地方上妝會浮粉。摩擦、抓、用力刷都會刺激皮膚。"
        elif text.startswith("底妝") and no_oil_control:
            text = "底妝：對淺色區塊周圍的膚色選色，面中最多、往外推薄；眼周最薄；海綿點壓"
            why = "照配色卡的底妝色號；往外推到脖子一致。這次分析 T 字、U 字都是中性膚質，不需要特別控油；蜜粉只在淺色區塊輕按，固定遮瑕。不想上可以跳過。"
        personalized.append((text, why))
        if text.startswith("局部校色") and "hd_dark_circle" in low_two:
            personalized.append((
                "眼下：先薄點一層桃杏色校色，再上遮瑕；量少、點壓",
                "這次膚質分析裡，眼下暗沉是第二弱的一項；桃杏色能中和偏暗，讓眼周看起來有精神。內眼角、下眼瞼最薄，避免卡紋。",
            ))
    return personalized
