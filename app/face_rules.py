"""依五官給建議（YouCam Face Attribute 的類別 → 白話建議＋選項建議）。

只「建議」不自動改：本人看完再決定要不要套。規則維度照內部知識庫（白化症妝容研究筆記）第 I 節
（臉型→腮紅形狀與位置、眉毛濃淡→補眉強度、眼型→睫毛、唇形→唇妝質地；性別不當規則輸入），內容是一般美妝原則。
欄位名稱照 實測回應：注意 cheekbone 的整體欄位 API 拼成 "overrall"。
"""
from __future__ import annotations

FEATURES = ["faceShape", "eyebrowThickness", "eyeShape", "lipShape", "cheekbones"]   # 1–5 項 = 10 點

_FACE = {
    "Round": "臉型偏圓：腮紅從蘋果肌斜斜往太陽穴刷（目前試妝就是斜刷）。",
    "Square": "臉型偏方：腮紅斜刷、往太陽穴暈開，讓線條柔和（目前試妝就是斜刷）。",
    "Oblong": "臉型偏長：腮紅橫向、停在蘋果肌，不要往上拉太長。",
    "Oval": "臉型橢圓：腮紅位置照喜好，蘋果肌或斜刷都適合。",
    "Heart": "臉型上寬下窄：腮紅放在顴骨下緣、往耳朵方向，不要太靠近鼻子。",
    "InvTriangle": "臉型上寬下窄：腮紅放在顴骨下緣、往耳朵方向，不要太靠近鼻子。",
    "Diamond": "顴骨較寬：腮紅放在顴骨中央往外輕拍。",
    "Triangle": "下顎較寬：腮紅稍微往上、靠近顴骨高處。",
}
_CHEEK = {
    "High Cheekbone": "顴骨較高：腮紅刷在顴骨上方往外。",
    "Low Cheekbone": "顴骨較低：腮紅放在蘋果肌稍高的位置。",
    "Flat Cheekbone": "顴骨較平：腮紅放在蘋果肌稍高的位置，輕拍就好。",
    "Round Cheeks": "臉頰圓潤：腮紅輕拍在蘋果肌。",
}
_EYE = {
    "Almond": "杏仁眼：睫毛用自然款就很好看。",
    "Round": "圓眼：睫毛刷在眼尾多一點，眼型會拉長一點。",
    "Narrow": "細長眼：睫毛從根部往上刷，讓眼睛看起來比較開。",
}
_LIP = {
    "Thin": ("唇形較薄：水光、淺色的唇看起來比較豐潤。", "natural_gloss"),
    "Narrow": ("唇形較窄：水光、淺色的唇看起來比較豐潤。", "natural_gloss"),
    "Full": ("唇形豐潤：潤色護唇或霧面都適合，顏色可以淡一點。", "tinted_balm"),
    "Heavy Lower Lip": ("下唇較厚：上唇中央多擦一點點，上下比較平衡。", None),
    "Heavy Upper Lip": ("上唇較厚：下唇中央多擦一點點，上下比較平衡。", None),
}


def _pick(d: dict, key: str | None):
    return d.get(key) if key else None


def advise(results: dict, style: str = "natural") -> dict:
    """results：Face Attribute 回應的 results。回傳 {"tips": [...], "suggest": {item: option_id}, "facts": {...}}。"""
    tips, suggest, facts = [], {}, {}
    face = results.get("faceshape")
    if face and face != "Unknown":
        facts["face_shape"] = face
        if _pick(_FACE, face):
            tips.append(_FACE[face])
    cheek = results.get("cheekbone") or {}
    overall = cheek.get("overall") or cheek.get("overrall") or cheek.get("left")
    if overall:
        facts["cheekbones"] = overall
        if _pick(_CHEEK, overall):
            tips.append(_CHEEK[overall])
    brow = results.get("eyebrow") or {}
    bodies = [brow.get(k) for k in ("left_body_thickness", "right_body_thickness") if brow.get(k)]
    tails = [brow.get(k) for k in ("left_tail_thickness", "right_tail_thickness") if brow.get(k)]
    if bodies:
        facts["brow_body"] = bodies
        if "Sparse" in bodies:
            tips.append("眉毛較稀：照原眉色從「補一點」開始，順著毛流畫短線。")
            suggest["brow"] = "photo_soft"
        elif all(b == "Dense" for b in bodies) or "Dense" in bodies:
            tips.append("眉毛本身濃：只要順毛、照原眉色補一點，不需要加深。")
            suggest["brow"] = "photo_soft"
    if tails and all(t == "Sparse" for t in tails):
        facts["brow_tail"] = tails
        tips.append("眉尾比較稀：只補眉尾就好，眉形不用改。")
    eyes = [results.get("eyelid", {}).get(k) for k in ("left_shape", "right_shape")]
    eye = next((e for e in eyes if e), None)
    if eye:
        facts["eye_shape"] = eye
        if _pick(_EYE, eye):
            tips.append(_EYE[eye])
    lips = results.get("lipshape") or []
    for lip in lips:
        facts.setdefault("lip_shape", []).append(lip)
        hit = _pick(_LIP, lip)
        if hit:
            tips.append(hit[0])
            if hit[1] and style != "grooming":
                suggest["lip"] = hit[1]
            break
    if not tips:
        tips.append("五官沒有特別需要調整的地方，照喜歡的妝感就好。")
    return {"tips": tips, "suggest": suggest, "facts": facts,
            "note": "依五官的一般美妝原則；只是建議，保留原樣也很好。"}
