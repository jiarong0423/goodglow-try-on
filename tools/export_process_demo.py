"""把四位示範對象「一層一層做出來」的逐步圖匯出成 App 的示範頁資料（evidence/index.html 用）。

  python3 -B tools/export_process_demo.py --runs <runs dir>

輸入：本地逐層引擎每一步的累積圖（frames／steps）、官方 YouCam Makeup VTO 回傳圖、定案圖。
輸出：
- evidence/img/<對象>/NN.jpg      每一步的累積圖（寬 720）
- evidence/img/<對象>/NN_d.jpg    這一層改了哪裡（和上一步的差異熱圖，放大顯示，寬 360）
- evidence/process.json                   每一步的中英文說明、做法標籤、色差數字、來源 sha256
規則：不放品牌名、產品名、色號、人名、病名；每張圖標「模擬」；來源 sha256 記進 process.json 方便重現；
失敗路徑印 ERROR 並回非零；原圖不覆寫。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
OUT_IMG = ROOT / "evidence" / "img"
OUT_JSON = ROOT / "evidence" / "process.json"
W_IMG, W_DIFF = 720, 360

# 做法標籤（畫面上的小標籤）：local＝在本機算（不扣點）、youcam＝YouCam Makeup VTO
LOCAL, YOUCAM, START, FINAL = "local", "youcam", "start", "final"

ALB_STEPS = [  # (報告步驟前綴, zh 位置, en 位置, zh 做法, en 做法)
    ("1 薄底妝", "全臉薄薄一層，保留雀斑與毛孔", "A thin base over the whole face; freckles and pores stay",
     "Lab 色彩空間混色，不透明度上限 0.22（示範 B 為 0.30），臉緣淡出", "Lab color blend, opacity cap 0.22 (0.30 in Example B), edges faded out"),
    ("2b 痘印", "只點在泛紅的小點上，拍開邊緣", "Concealer only on small red spots, edges patted out",
     "依色差找出小點遮罩，不透明度上限 0.30，點內柔化", "Spot mask from color difference, opacity cap 0.30, soft blend inside the spot"),
    ("2a 眼下", "靠下眼瞼最濃、往下淡出，不成一條帶", "Under the eye: strongest at the lash line, fading downward, never a band",
     "漸層遮罩，不透明度上限 0.45", "Gradient mask, opacity cap 0.45"),
    ("2a' 淚溝", "只補內眼角往外下那條凹線的暗處", "Fills only the dark hollow running down from the inner corner",
     "低頻亮度拉回臉頰（保留毛孔），不透明度上限 0.75", "Low-frequency lightness pulled toward the cheek (pores kept), opacity cap 0.75"),
    ("2c 修容", "顴骨下、太陽穴、下顎角，只上皮膚", "Under the cheekbones, at the temples and jaw angles; skin only",
     "分區遮罩，不透明度上限 0.30", "Zone mask, opacity cap 0.30"),
    ("2f 打亮", "顴骨上緣、額頭中間、鼻樑、下巴，輕輕帶過", "Top of the cheekbones, mid forehead, nose bridge, chin; a light touch",
     "寬而柔的漸層，不成一條帶，不透明度上限 0.22", "Wide soft gradient, never a band, opacity cap 0.22"),
    ("2d 鼻影", "鼻樑兩側兩條淡淡的平行線", "Two faint parallel lines at the sides of the nose",
     "平行線遮罩，不透明度上限 0.22", "Parallel-line mask, opacity cap 0.22"),
    ("2 腮紅", "顴骨上、往太陽穴斜刷，避開眼下", "Over the cheekbones, swept toward the temples, away from the eyes",
     "形狀遮罩，不透明度上限 0.45（定案版的腮紅改由 YouCam 試妝）", "Shape mask, opacity cap 0.45 (the final look takes its blush from YouCam)"),
    ("3 眼影", "上眼皮薄薄一層", "A thin wash on the upper lid", "指定色，不透明度上限 0.22", "Chosen color, opacity cap 0.22"),
    ("4 睫毛", "深咖啡色，刷根部", "Dark brown at the lash roots, tips left clear",
     "睫毛遮罩，根深尖透，不透明度上限 0.70", "Lash mask, deeper at the root and sheer at the tip, opacity cap 0.70"),
    ("6 唇", "收在原本唇線內，保留唇紋", "Kept inside the natural lip line; lip texture stays",
     "Lab 換色（唇遮罩內），不透明度上限 0.50（示範 B 為 0.45）", "Lab color swap inside the lip mask, opacity cap 0.50 (0.45 in Example B)"),
]
ALB_M_TEXT = {  # 男生那組只用到這幾層
    "1 薄底妝": 0, "2b 痘印": 1, "2a 眼下": 2, "6 唇": 10,
}
VIT_M_STEPS = [  # 白斑男：本地逐層 17 步（報告順序）；zh、en
    ("校色：淺色區塊核心先用校色色號", "A corrector first, only on the core of the lighter area"),
    ("遮瑕：淺色區塊用較深色號（只上比目標淺處）", "Concealer on the lighter area, only where it is lighter than the target"),
    ("遮瑕第二層：還看得出來的淺色處再點一次", "A second concealer pass where the lighter area still shows"),
    ("修邊：交界兩側偏淺處沿邊補色", "Edge work: color along the border where it is still lighter"),
    ("修邊：交界外側棕邊薄拍較淺色號", "Edge work: a lighter shade patted thinly on the brown rim outside the border"),
    ("修邊：棕色小島薄拍較淺色號", "Edge work: a lighter shade patted thinly on the small brown islands"),
    ("粉底：整臉一層薄粉底", "Foundation: one thin layer over the whole face"),
    ("粉底：往下帶到脖子薄薄一層", "Foundation: carried down the neck in a thin layer"),
    ("修容：臉周圍薄掃暖色修容", "Contour: a thin warm sweep around the edge of the face"),
    ("補拍：淺色殘留點較深色號", "Touch-up: a deeper shade dabbed on the lighter leftovers"),
    ("補拍：偏深殘留薄拍較淺色號", "Touch-up: a lighter shade patted thinly on the slightly too deep spots"),
    ("細修：脖子淺色處點遮", "Fine work: spot-concealing the lighter areas on the neck"),
    ("細修：下顎陰影帶薄拍提亮", "Fine work: lightly brightening the shadow band along the jaw"),
    ("細修：眼下薄拍校色（桃杏）", "Fine work: a thin peach-apricot corrector under the eyes"),
    ("細修：細亮紋細刷點遮", "Fine work: fine-brush concealing of thin light lines"),
    ("第1輪補：遮瑕（偏淺處）", "Round 1 touch-up: concealer where it is still lighter"),
    ("第1輪補：偏深處薄拍較淺色號", "Round 1 touch-up: a lighter shade patted thinly where it is too deep"),
]


BRUSH_EN = {"點刷": "Dab brush", "扁頭遮瑕刷（沿交界）": "Flat concealer brush along the border", "細刷": "Fine brush", "大粉底刷": "Large foundation brush"}


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def load_rgb(p: Path) -> Image.Image:
    return Image.open(p).convert("RGB")


def save_img(im: Image.Image, dst: Path, width: int) -> None:
    h = round(im.height * width / im.width)
    im.resize((width, h), Image.LANCZOS).save(dst, "JPEG", quality=82)


def diff_map(prev: Image.Image, cur: Image.Image, width: int) -> Image.Image:
    """這一層改了哪裡：和上一步（同尺寸）的逐像素差，放大成熱圖。上一步尺寸不同時回傳空白圖。"""
    a = np.asarray(prev.resize(cur.size, Image.BILINEAR), dtype=float)
    b = np.asarray(cur, dtype=float)
    d = np.abs(a - b).sum(axis=2)
    d = np.clip(d / 24.0, 0, 1)                      # 差 24（三通道和）以上就滿格
    heat = np.zeros(d.shape + (3,), dtype=np.uint8)
    heat[..., 0] = (255 * d).astype(np.uint8)
    heat[..., 1] = (140 * d * d).astype(np.uint8)
    base = np.asarray(cur.convert("L").resize(cur.size), dtype=float)[..., None] * 0.35
    out = np.clip(base + heat * 0.9, 0, 255).astype(np.uint8)
    return Image.fromarray(out).resize((width, round(cur.height * width / cur.width)), Image.LANCZOS)


class Builder:
    def __init__(self, runs: Path, repo: Path):
        self.runs, self.repo = runs, repo
        self.subjects = []

    def add(self, sid: str, meta: dict, frames: list[dict]) -> None:
        d = OUT_IMG / sid
        d.mkdir(parents=True, exist_ok=True)
        for old in d.glob("*.jpg"):
            old.unlink()
        prev = None
        steps = []
        for i, f in enumerate(frames):
            src = f.pop("src")
            if not src.is_file():
                raise SystemExit(f"ERROR 找不到來源圖：{src}")
            im = load_rgb(src)
            save_img(im, d / f"{i:02d}.jpg", W_IMG)
            has_diff = prev is not None and f["kind"] != START
            if has_diff:
                diff_map(prev, im, W_DIFF).save(d / f"{i:02d}_d.jpg", "JPEG", quality=80)
            steps.append({"n": i, "img": f"img/{sid}/{i:02d}.jpg", "diff": f"img/{sid}/{i:02d}_d.jpg" if has_diff else None,
                          "source_sha256": sha(src), **f})
            prev = im
        self.subjects.append({"id": sid, **meta, "steps": steps})

    def write(self) -> None:
        data = {"version": "process-20261021", "note": "每一步的累積圖都是模擬；說明為草稿，英文定稿由 owner。不含品牌名、色號、人名、病名。",
                "subjects": self.subjects}
        OUT_JSON.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        root = OUT_JSON.parent                                  # 證據頁的校驗清單：每張圖與 process.json 的 sha256
        sums = [f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.relative_to(root).as_posix()}"
                for p in sorted(list((root / "img").rglob("*.jpg")) + [OUT_JSON])]
        (root / "SHA256SUMS.txt").write_text("\n".join(sums) + "\n", encoding="utf-8")


def step(kind, zh, en, tech_zh="", tech_en="", delta=None, **extra):
    return {"kind": kind, "zh": zh, "en": en, "tech_zh": tech_zh, "tech_en": tech_en, "delta": delta, **extra}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", default=str(ROOT / "runs"))
    a = ap.parse_args()
    runs = Path(a.runs)
    if not runs.is_dir():
        print(f"ERROR 找不到 runs 資料夾：{runs}", file=sys.stderr)
        return 2
    b = Builder(runs, ROOT)
    alb = runs / "albinism_20261018"
    plan = alb / "official_plan_FM_20261020"

    # ---- 白化症 F：本地 11 層（累積）→ YouCam 試妝（底＋腮紅）→ 定案
    rep = json.loads((alb / "approx_r14f/F_warm_apricot/report.json").read_text(encoding="utf-8"))
    frames = [dict(src=alb / "approx_r14f/F_warm_apricot/frames/00.png", **step(START, "你的照片（起點）", "Your photo (the starting point)"))]
    for k, (prefix, zh, en, tz, te) in enumerate(ALB_STEPS, 1):
        rs = next(s for s in rep["步驟"] if s["步驟"].startswith(prefix))
        frames.append(dict(src=alb / f"approx_r14f/F_warm_apricot/frames/{k:02d}.png",
                           **step(LOCAL, zh, en, tz, te, rs.get("範圍內平均色差（上一層→這一層）"))))
    frames.append(dict(src=plan / "export/F_base_crop.jpg", **step(LOCAL, "本機逐層做完的底（裁到臉部標準框）", "The locally layered base, cropped to the standard face frame",
                                                                    "同一組層，裁成固定臉框，送去 YouCam 之前的樣子", "The same layers, cropped to a fixed face frame, as sent to YouCam")))
    frames.append(dict(src=plan / "step9_oblique/F_Oblique43_i100_DF9299_raw.png", **step(YOUCAM, "YouCam 試妝：官方腮紅（輸入已含先前的官方眼妝）", "YouCam try-on: official blush (input already carries an earlier official eye-makeup pass)", "YouCam Makeup VTO，腮紅強度 100，磨皮固定 0", "YouCam Makeup VTO, blush intensity 100, smoothing fixed at 0")))
    frames.append(dict(src=plan / "results/F_final_crop.jpg", **step(FINAL, "定案（模擬）：官方眼妝與腮紅＋顴骨帶腮紅", "Final look (simulated): YouCam eye makeup and blush plus a cheekbone blush band",
                                                                      "官方腮紅層放大 1.8 倍疊回", "The official blush layer, scaled up 1.8×, laid back on")))
    b.add("light_a", {"title_zh": "膚色很淺 · 示範 A", "title_en": "Very light skin · Example A", "credit": "Pexels", "local_layers": 11, "youcam_steps": 1}, frames)

    # ---- 白化症 M：本地 4 層 → YouCam 唇 → 定案
    repm = json.loads((alb / "approx_r14f/M_v11_minimal/report.json").read_text(encoding="utf-8"))
    frames = [dict(src=alb / "approx_r14f/M_v11_minimal/frames/00.png", **step(START, "你的照片（起點）", "Your photo (the starting point)"))]
    for k, name in enumerate(("1 薄底妝", "2b 痘印", "2a 眼下", "6 唇"), 1):
        prefix, zh, en, tz, te = ALB_STEPS[ALB_M_TEXT[name]]
        rs = next(s for s in repm["步驟"] if s["步驟"].startswith(prefix))
        frames.append(dict(src=alb / f"approx_r14f/M_v11_minimal/frames/{k:02d}.png", **step(LOCAL, zh, en, tz, te, rs.get("範圍內平均色差（上一層→這一層）"))))
    frames.append(dict(src=plan / "M_C4_send.jpg", **step(LOCAL, "本機細修後送去 YouCam 的圖", "The locally refined image sent to YouCam")))
    frames.append(dict(src=plan / "results/M_vtoA_raw.png", **step(YOUCAM, "YouCam 試妝：唇", "YouCam try-on: lips", "YouCam Makeup VTO，磨皮固定 0", "YouCam Makeup VTO, smoothing fixed at 0")))
    frames.append(dict(src=plan / "results/M_final_crop.jpg", **step(FINAL, "定案（模擬）：唇層加強、柔焦、淡化小斑", "Final look (simulated): stronger lip layer, soft focus, faded small marks",
                                                                      "官方唇層 ×2，柔焦只在兩頰（鬍子區不碰）", "The official lip layer ×2, soft focus on the cheeks only (beard area untouched)")))
    b.add("light_b", {"title_zh": "膚色很淺 · 示範 B", "title_en": "Very light skin · Example B", "credit": "Pexels", "local_layers": 4, "youcam_steps": 1, "extra_zh": "另有一道兩頰柔焦", "extra_en": "plus a soft-focus pass on the cheeks"}, frames)

    # ---- 白斑 M：本地 17 步 → 合併 → YouCam 試妝
    lr = runs / "layer_rule_20261018"
    repv = json.loads((lr / "demo20551205_ai/report.json").read_text(encoding="utf-8"))
    photo = ROOT / "demo/photos/vitiligo_20551205.jpg"
    frames = [dict(src=photo, **step(START, "你的照片（起點）", "Your photo (the starting point)"))]
    for k, (zh, en) in enumerate(VIT_M_STEPS, 1):
        rs = repv["步驟"][k - 1]
        de = rs.get("範圍內色差（前→後）")
        frames.append(dict(src=lr / f"demo20551205_ai/steps/{k:02d}.png",
                           **step(LOCAL, zh, en, f"{rs.get('刷具', '')}；{rs.get('層數', 1)} 層", f"{BRUSH_EN.get(rs.get('刷具'), rs.get('刷具'))}; {rs.get('層數', 1)} layer(s)",
                                  de, metric="patch")))
    frames.append(dict(src=lr / "demo20551205_ai/layer_rule_full.png", **step(LOCAL, "本機逐層做完（含一輪補拍與整臉粉底、修容）", "All local layers done (one touch-up round, whole-face foundation and contour included)")))
    frames.append(dict(src=lr / "vto_male_natural/vto_male_natural_v2_raw.png", **step(FINAL, "定案（模擬）：YouCam 試妝，淡妝", "Final look (simulated): YouCam try-on, light makeup",
                                                                                         "YouCam Makeup VTO，磨皮固定 0", "YouCam Makeup VTO, smoothing fixed at 0")))
    b.add("uneven_a", {"title_zh": "膚色深淺不一 · 示範 A", "title_en": "Uneven skin tone · Example A", "credit": "Pexels", "local_layers": 17, "youcam_steps": 1,
                                 "note_zh": "這組的本機逐層是對一張 AI 生成的目標圖擬合出來的，不是照著真人上妝照片。", "note_en": "The local layers of this example were fitted to an AI-generated target image, not copied from a photo of real makeup.", "extra_zh": "另有一輪補拍，整臉粉底與修容合併", "extra_en": "plus one touch-up round, with whole-face foundation and contour merged"}, frames)

    # ---- 白斑 F：本機逐層（四個部位）→ YouCam 腮紅 → YouCam 眼影（逐部位中間圖沒有留，只放階段圖）
    ff = lr / "frontal_F_20261020"
    frames = [dict(src=ff / "F0_original_AI_frontal.png", **step(START, "起點（AI 生成的正面示意圖，非本人原照）", "Starting point (an AI-generated frontal illustration, not an original photo)")),
              dict(src=lr / "F_compose/F_C4.png", **step(LOCAL, "本機逐層做完：眼周 → 嘴與下巴 → 耳 → 額，只在看得出的淺色區塊小範圍點蓋、拍開",
                                                         "All local layers done: around the eyes, mouth and chin, ear, forehead; only small dabs on lighter areas that really show, patted out",
                                                         "連通色差成長出遮罩（不畫直線），分部位逐層，每步放大檢查", "Masks grown by connected color difference (no straight lines), one body area at a time, each step checked zoomed in")),
              dict(src=ff / "official_v2/F4_blush_raw.png", **step(YOUCAM, "YouCam 試妝：腮紅", "YouCam try-on: blush", "YouCam Makeup VTO，磨皮固定 0", "YouCam Makeup VTO, smoothing fixed at 0")),
              dict(src=ff / "official_v2/F4_eyeshadow_raw.png", **step(FINAL, "定案（模擬）：再加 YouCam 眼影", "Final look (simulated): plus a YouCam eyeshadow", "YouCam Makeup VTO，眼影兩色", "YouCam Makeup VTO, two-color eyeshadow"))]
    b.add("uneven_b", {"title_zh": "膚色深淺不一 · 示範 B", "title_en": "Uneven skin tone · Example B", "credit": "Unsplash", "credit_zh": "Unsplash（AI 轉向示意圖）", "credit_en": "Unsplash (AI-generated frontal illustration)", "local_layers": None, "youcam_steps": 2,
                       "note_zh": "本機逐層的部位中間圖沒有留存，這裡只放階段圖。感謝願意公開自己照片的人與攝影師。膚色很淺或深淺不一的人的清晰照片本來就少，正面照更少；這一組只有側臉照，所以用 AI 生成的正面示意圖來示範方法，它不是本人原照，畫面標示「模擬」。照片中的人沒有參與本作品，也不代表背書。", "note_en": "The per-area intermediate images of the local layers were not kept, so only the stage images are shown. Thank you to the people and photographers who make photos publicly available. Clear photos of people with very light skin or uneven skin tone are scarce, and frontal ones scarcer; here the only suitable photo was a side view, so an AI-generated frontal illustration demonstrates the method. It is not the person's original photo and is labelled Simulated. The people in these photos did not take part in this project and do not endorse it."}, frames)
    b.write()
    total = sum(len(s["steps"]) for s in b.subjects)
    print(f"OK {len(b.subjects)} 組、{total} 張 → {OUT_JSON}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as exc:                                   # 失敗路徑印 ERROR 並回非零
        print(f"ERROR {exc!r}", file=sys.stderr)
        sys.exit(1)
