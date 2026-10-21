"""把各入口的定案妝容（模擬）從 runs/ 匯出到 demo/final/，給 App「定案妝容」卡片用。
10-19 。
 → 加白斑女 U（S48），每款分三段：
本地逐層底妝 → 官方 makeup-vto 這一步（有錄好的才放；沒有就註明）→ 配方卡。
- 成品統一轉成 JPEG，長邊上限 1600。來源 sha256 不符就停（印 ERROR）。
- 配方（recipes.json）只放品項、位置、色塊 hex，不放品牌名或官方色號（App 對外畫面規則）。
- 每個檔都寫進 SHA256SUMS.txt。
runs/ 不進版控；在 worktree 裡跑時用 --runs 指到主工作樹的 runs/：
  python3 -B tools/export_final_looks.py --runs <runs dir>
"""
from __future__ import annotations
import argparse
import hashlib
import io
import json
from pathlib import Path
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "demo" / "final"

CREDIT = {
    "5273693": "照片：Pexels（Pexels License，可修改）https://www.pexels.com/photo/5273693/",
    "20551205": "照片：Pexels（Pexels License，可修改）https://www.pexels.com/photo/20551205/",
    "uCt9uA9r1Rw": "照片：Unsplash（Unsplash License，可修改）https://unsplash.com/photos/uCt9uA9r1Rw",
    "uCt9uA9r1Rw_frontal": "照片：模擬・由 AI 生成的轉向示意圖，非本人原照（原照來源：Unsplash，可修改授權）https://unsplash.com/photos/uCt9uA9r1Rw",
}

# 來源與盲評依據見 logs/decisions/log_20261016_foundation_layers.md（、）
# base＝本地逐層底妝（官方那一步之前）；official＝官方 makeup-vto 回傳圖（沒有就 None）；final＝最後成品
FINALS = {
    # 10-21
    # 三段同一個標準臉框（官方圖座標）：base＝本地逐層底妝（腮紅前一層，裁標準框左移 3px）；
    # official＝官方 makeup-vto（Oblique43 #DF9299 強度 100、磨皮 0；輸入已含 ⑤B 官方眼妝）；final＝官方底＋顴骨帶官方腮紅層 ×1.796
    "albinism_F": {"title": "粉嫩好氣色", "credit": CREDIT["5273693"],
                   "base": ("albinism_20261018/official_plan_FM_20261020/export/F_base_crop.jpg", "dff68e1dd1535032bd094c862a4a66c1059e02182281b5429238393469e0341b"),
                   "official": ("albinism_20261018/official_plan_FM_20261020/step9_oblique/F_Oblique43_i100_DF9299_raw.png", "5717debc030c487752212f4808bee63caa131647c6cd6d70679b6b04ae43de61"),
                   "final": ("albinism_20261018/official_plan_FM_20261020/results/F_final_crop.jpg", "fe1026c44555cbedfabcc51fa7b2c6862ba5978e901d9ff8a7fbd71e0bc6d220"),
                   "report": "albinism_20261018/approx_r14f/F_warm_apricot/report.json",
                   "hex_override": {"腮紅": "#DF9299"}},
    #：男生 base＝步 1 本地細修（標準框）；official＝官方唇試妝；final＝官方唇層 ×2＋磨皮強＋淡斑
    "albinism_M": {"title": "柔和・極簡", "credit": CREDIT["5273693"],
                   "base": ("albinism_20261018/official_plan_FM_20261020/M_C4_send.jpg", "ac181cd661fd31a490969568c722b206fd0d4af3c4b4b15373b8d730e0b2fa8c"),
                   "official": ("albinism_20261018/official_plan_FM_20261020/results/M_vtoA_raw.png", "d5966ab066e1b178341c360699241a10edeb615d510c9de22d5bccfe147a80f7"),
                   "final": ("albinism_20261018/official_plan_FM_20261020/results/M_final_crop.jpg", "c2ce9994c8ca516e68f6ef7535d1bfddb6b728cc0ed7c6a5626fd83ee8392eed"),
                   "report": "albinism_20261018/approx_r14f/M_v11_minimal/report.json",
                   "extra_steps": [{"item": "妝前柔焦", "where": "兩頰毛孔與痘疤處薄拍，鬍子區不碰", "hex": None}]},
    # 白斑症線 回覆：示範照男生（使用者「上次那個男生可以了」「示範照維持現狀」）；官方那一步＝官方試妝回傳圖
    "vitiligo_M": {"title": "膚色均勻・自然", "credit": CREDIT["20551205"],
                   "base": ("layer_rule_20261018/demo20551205_ai/layer_rule_full.png", "c9064386c8e71565102d7724a9001517ae507d33ac10c977f3570d261fc91072"),
                   "official": ("layer_rule_20261018/vto_male_natural/vto_male_natural_v2_raw.png", "a819ecd2164fbc10bb44b6ac1d9681aae496778d252886528425cc8afccf717c"),
                   "final": ("layer_rule_20261018/vto_male_natural/vto_male_natural_v2_raw.png", "a819ecd2164fbc10bb44b6ac1d9681aae496778d252886528425cc8afccf717c"),
                   "report": None},
    # → 選 A：
    # base＝本地逐層 C4；official＝C4 → 官方腮紅 → 官方眼影 2colors2（磨皮 0）回傳圖，也就是成品（跟白斑男一樣）
    # 回覆「模擬」：AI 轉向圖可沿用，畫面一律標模擬，並註明「由 AI 生成的轉向示意圖，非本人原照」
    "vitiligo_F": {"title": "局部輕遮瑕", "credit": CREDIT["uCt9uA9r1Rw_frontal"],
                   "source_note": "原照 Unsplash uCt9uA9r1Rw（側臉）→ AI 影像工具（AI）轉成正面示意圖（非本人原照，輪廓未驗證）→ 本地逐層 F_C4 → 官方腮紅＋眼影（磨皮 0）；畫面標「模擬」",
                   "base": ("layer_rule_20261018/F_compose/F_C4.png", "e6085ea0de4b0ab782771844c1a87306d97386347ad18aa25b38909aab0a9884"),
                   "official": ("layer_rule_20261018/frontal_F_20261020/official_v2/F4_eyeshadow_raw.png", "6c0c584ec55b091ec5b3e225e6637e7995c41550b809ee89736a499bbde0bc40"),
                   "final": ("layer_rule_20261018/frontal_F_20261020/official_v2/F4_eyeshadow_raw.png", "6c0c584ec55b091ec5b3e225e6637e7995c41550b809ee89736a499bbde0bc40"),
                   "report": None},
}

# 步驟名稱（runs 報告的步驟前綴）→ 對外品項與位置；不含品牌與色號
STEP_TEXT = [
    ("1 薄底妝", "粉底", "全臉薄薄一層，保留雀斑與毛孔"),
    ("2b 痘印", "遮瑕", "只點在泛紅的小點上，拍開邊緣"),
    ("2a 眼下", "眼下遮瑕", "靠下眼瞼最濃、往下淡出，不成一條帶"),
    ("2a' 淚溝", "淚溝遮瑕", "只補內眼角往外下那條凹線的暗處"),
    ("2c 修容", "修容", "顴骨下、太陽穴、下顎角，只上皮膚"),
    ("2f 打亮", "打亮", "顴骨上緣、額頭中間、鼻樑、下巴，輕輕帶過"),
    ("2d 鼻影", "鼻影", "鼻樑兩側兩條淡淡的平行線"),
    ("2 腮紅", "腮紅", "顴骨上、往太陽穴斜刷，避開眼下"),
    ("3 眼影", "眼影", "上眼皮薄薄一層"),
    ("4 睫毛", "睫毛膏", "深咖啡色，刷根部"),
    ("6 唇", "唇", "收在原本唇線內，保留唇紋"),
]
VITILIGO_STEPS = {
    "vitiligo_M": [{"item": "遮瑕", "where": "淺色區塊分區上色，交界拍開", "hex": None},
                   {"item": "粉底", "where": "全臉薄薄一層，讓膚色一致", "hex": None},
                   {"item": "淡妝", "where": "修容、腮紅一點點，看不出化妝", "hex": None}],
    "vitiligo_F": [{"item": "遮瑕", "where": "只在看得出的淺色區塊小範圍點蓋、拍開", "hex": None},
                   {"item": "粉底", "where": "其他地方維持原本膚色，不全臉厚塗", "hex": None}],
}


def steps_from_report(report: Path, hex_override: dict | None = None) -> list[dict]:
    """hex_override：成品某品項換了色（例如 女生腮紅改官方粉色），用品項名覆寫色塊 hex。"""
    hex_override = hex_override or {}
    rep = json.loads(report.read_text(encoding="utf-8"))
    out = []
    for s in rep["步驟"]:
        for prefix, item, where in STEP_TEXT:
            if s["步驟"].startswith(prefix):
                c = s.get("色號") or {}
                hexv = c.get("hex") or s.get("色")
                hexv = hex_override.get(item, hexv)
                out.append({"item": item, "where": where, "hex": hexv if isinstance(hexv, str) and hexv.startswith("#") else None})
                break
    return out


def save_jpeg(runs: Path, rel_sha: tuple[str, str], dst: Path) -> None:
    raw = (runs / rel_sha[0]).read_bytes()
    got = hashlib.sha256(raw).hexdigest()
    if got != rel_sha[1]:
        raise SystemExit(f"ERROR 來源 sha256 不符：{rel_sha[0]} {got} != {rel_sha[1]}")
    im = Image.open(io.BytesIO(raw)).convert("RGB")
    s = 1600 / max(im.size)
    if s < 1:
        im = im.resize((round(im.width * s), round(im.height * s)), Image.LANCZOS)
    im.save(dst, "JPEG", quality=90)


def main() -> None:
    ap = argparse.ArgumentParser(); ap.add_argument("--runs", default=str(ROOT / "runs")); a = ap.parse_args()
    runs = Path(a.runs)
    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("*.jpg"):
        old.unlink()
    recipes = {}
    for key, f in FINALS.items():
        imgs = {}
        for stage in ("base", "official", "final"):
            if f[stage] is None:
                imgs[stage] = None
                continue
            same = next((st for st in ("base", "official") if st != stage and f[st] == f[stage] and imgs.get(st)), None)
            if same:                                   # 同一張來源不重複存（例如官方回傳圖就是成品）
                imgs[stage] = imgs[same]
                continue
            name = f"{key}_{stage}.jpg"
            save_jpeg(runs, f[stage], OUT / name)
            imgs[stage] = name
        recipes[key] = {"title": f["title"], "label": "模擬", "credit": f["credit"], "images": imgs,
                        "steps": (steps_from_report(runs / f["report"], f.get("hex_override")) + f.get("extra_steps", [])) if f["report"] else VITILIGO_STEPS[key],
                        "source_sha256": {k: (v[1] if v else None) for k, v in ((s, f[s]) for s in ("base", "official", "final"))}}
        if f.get("source_note"):
            recipes[key]["source_note"] = f["source_note"]
    (OUT / "recipes.json").write_text(json.dumps(recipes, ensure_ascii=False, indent=1), encoding="utf-8")
    sums = [f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}" for p in sorted(OUT.glob("*")) if p.name != "SHA256SUMS.txt"]
    (OUT / "SHA256SUMS.txt").write_text("\n".join(sums) + "\n", encoding="utf-8")
    print("\n".join(sums))


if __name__ == "__main__":
    main()
