import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
import budget as bg  # noqa: E402
import color_engine as ce  # noqa: E402
import recommend as rec  # noqa: E402
import skin_card as sc  # noqa: E402

# 品牌官網色號只在本機版（公開 repo 不含），沒有時底妝用示範型錄
BASE_SOURCE = "official" if ce.OFFICIAL_CATALOG.exists() else "demo"


class DeltaETests(unittest.TestCase):
    def test_ciede2000_reference_pairs(self):
        # Sharma, Wu, Dalal (2005) 測試資料
        pairs = [((50, 2.6772, -79.7751), (50, 0, -82.7485), 2.0425),
                 ((50, 3.1571, -77.2803), (50, 0, -82.7485), 2.8615),
                 ((50, 2.8361, -74.0200), (50, 0, -82.7485), 3.4412),
                 ((60.2574, -34.0099, 36.2677), (60.4626, -34.1751, 39.4387), 1.2644),
                 ((63.0109, -31.0961, -5.8663), (62.8187, -29.7946, -4.0864), 1.2630),
                 ((2.0776, 0.0795, -1.1350), (0.9033, -0.0636, -0.5514), 0.9082)]
        for a, b, expected in pairs:
            self.assertAlmostEqual(ce.delta_e2000(a, b), expected, places=4)

    def test_lab_roundtrip_and_white(self):
        L, a, b = ce.hex_to_lab("#FFFFFF")
        self.assertAlmostEqual(L, 100, places=2)
        for h in ("#C8827A", "#F5E2DC", "#1E1919"):
            self.assertEqual(ce.lab_to_hex(ce.hex_to_lab(h)), h)

    def test_bad_hex(self):
        with self.assertRaises(ValueError):
            ce.hex_to_lab("#12345")


class MatchTests(unittest.TestCase):
    def test_exact_demo_shade_is_first_and_very_close(self):
        res = ce.match_hex("#F5E2DC", "foundation", "cool", "demo")
        self.assertEqual(res[0]["code"], "C00 瓷粉")
        self.assertEqual(res[0]["verdict"], "很接近")

    def test_undertone_penalty_reorders_but_reports_true_delta(self):
        target = ce.lab_to_hex(tuple((a + b) / 2 for a, b in zip(ce.hex_to_lab("#F0D8CF"), ce.hex_to_lab("#F2DEC8"))))
        warm = ce.match_hex(target, "foundation", "warm", "demo")
        cool = ce.match_hex(target, "foundation", "cool", "demo")
        self.assertIn(warm[0]["undertone"], ("warm", "neutral"))
        self.assertIn(cool[0]["undertone"], ("cool", "neutral"))
        self.assertNotEqual([r["code"] for r in warm], [r["code"] for r in cool])
        for r in warm + cool:
            self.assertGreaterEqual(r["score"], r["delta_e"])



class VitiligoPlanTests(unittest.TestCase):
    def test_small_difference_single_shade(self):
        plan = ce.vitiligo_plan("#E6C6AF", "#E9CBB6", source="demo")
        self.assertEqual(plan["level"], "single")
        self.assertEqual(plan["edge"], [])

    def test_large_difference_two_shades_and_steps_warn_against_rubbing(self):
        plan = ce.vitiligo_plan("#9C6B4D", "#EBD3CB", source="demo")
        self.assertIn(plan["level"], ("two_shade", "two_shade_plus"))
        self.assertTrue(plan["edge"])
        self.assertTrue(any("點壓" in s for s in plan["steps"]))
        self.assertTrue(any("照光" in s for s in plan["steps"]))
        self.assertIn("deep_skin_note", plan)

    def test_pinkish_patch_still_gets_peach_not_green(self):
        # 白斑偏粉也不用綠：綠疊棕色粉底會發灰
        plan = ce.vitiligo_plan("#B07E5C", "#F2CFCF", source="demo")
        self.assertEqual(plan["level"], "two_shade_plus")
        self.assertTrue(plan["corrector"])
        self.assertTrue(all("綠" not in c["code"] for c in plan["corrector"]))
        self.assertTrue(any(w in plan["corrector"][0]["code"] for w in ("橘", "蜜桃")))


class CarePlanTests(unittest.TestCase):
    def test_skin_personalization_requires_complete_scores_and_three_normal_regions(self):
        scores = {
            "hd_moisture": 57, "hd_dark_circle": 65, "hd_redness": 72,
            "hd_radiance": 76, "hd_oiliness": 79, "hd_texture": 82, "hd_pore": 89,
        }
        skin = {"scores": scores, "skin_type": {
            "whole": "Normal", "t_zone": "Normal", "u_zone": "Normal",
        }}
        personalized = ce.care_plan("vitiligo", skin=skin)
        self.assertEqual(len(personalized), len(ce.care_plan("vitiligo")) + 1)
        self.assertIn("保濕要做足", personalized[0]["text"])
        self.assertIn("眼下：", personalized[6]["text"])
        self.assertNotIn("少量蜜粉按壓控油", personalized[7]["why"])
        self.assertIn("蜜粉只在淺色區塊輕按", personalized[7]["why"])
        self.assertEqual(ce.care_plan("albinism", skin=skin), ce.care_plan("albinism"))

        incomplete = {"scores": {k: v for k, v in scores.items() if k != "hd_pore"},
                      "skin_type": skin["skin_type"]}
        self.assertEqual(ce.care_plan("vitiligo", skin=incomplete), ce.care_plan("vitiligo"))
        missing_region = {"scores": scores, "skin_type": {"whole": "Normal", "t_zone": "Normal"}}
        self.assertIn("控油", ce.care_plan("vitiligo", skin=missing_region)[7]["why"])

    def test_protection_comes_before_makeup_for_both_conditions(self):
        for cond in ("albinism", "vitiligo"):
            texts = [s["text"] for s in ce.care_plan(cond)]
            sun = next(i for i, t in enumerate(texts) if "防曬" in t)
            prime = next(i for i, t in enumerate(texts) if "隔離" in t)
            makeup = next(i for i, t in enumerate(texts) if "上妝" in t or "遮瑕" in t)
            self.assertLess(sun, makeup)
            self.assertLess(prime, makeup)
            self.assertIn("卸妝", texts[-1])
            base = next(i for i, t in enumerate(texts) if "底妝" in t)
            self.assertLess(prime, base)                       # 隔離在底妝前
            self.assertLess(base, len(texts) - 1)

    def test_vitiligo_has_phototherapy_and_no_rubbing(self):
        texts = " ".join(s["text"] + s["why"] for s in ce.care_plan("vitiligo"))
        self.assertIn("照光", texts)
        self.assertIn("不搓", texts)


class RecommendTests(unittest.TestCase):
    def test_low_chroma_pale_skin_is_neutral_and_needs_confirm(self):
        prof = ce.skin_profile("#D9CBC1")          # 白化症受光側臉頰，彩度約 7
        self.assertTrue(prof["low_chroma"])
        self.assertEqual(prof["undertone"], "neutral")
        r = rec.recommend("albinism", "natural", skin_hex="#D9CBC1")
        self.assertEqual(r["undertone_id"], "neutral")
        self.assertTrue(r["undertone_confirm"])
        self.assertTrue(r["base"]["foundation"])
        self.assertEqual(set(r["looks"]), {"glow", "clear"})
        self.assertTrue(all(s["matches"] for s in r["looks"]["clear"]["shades"]))
        self.assertIn("防曬", " ".join(c["text"] for c in r["care"]))

    def test_vitiligo_foundation_matches_surrounding_skin(self):
        #  改回：整臉底妝對白斑周圍有色素的皮膚選色（不是平衡色）
        n, p = "#946D60", "#B49B96"
        plan = ce.vitiligo_plan(n, p)
        self.assertEqual(plan["card"]["hex"], n)
        self.assertEqual(plan["foundation"], ce.card_shades(n, "foundation", None, "all", 3)[1])
        self.assertNotIn("balance", plan)
        r = rec.recommend("vitiligo", "grooming", normal_hex=n, patch_hex=p, patch_ratio=0.4)
        self.assertEqual(r["looks"]["glow"]["selection"]["foundation_hex"], plan["foundation"][0]["hex"])
        self.assertTrue(any("局部校色" in st for st in plan["steps"]))
        L = lambda h: ce.hex_to_lab(h)[0]
        self.assertTrue(L(n) < L(ce.skin_balance(n, p, 0.2)["hex"]) < L(ce.skin_balance(n, p, 0.6)["hex"]) < L(p))

    def test_three_stripes_lighter_closest_deeper_same_line(self):
        for plan in (ce.albinism_base_plan("#CDB5AA"), ce.vitiligo_plan("#946D60", "#B49B96")):
            st = plan["stripes"]["stripes"]
            self.assertEqual([x["role"] for x in st], ["淺一階", "最接近", "深一階"])
            self.assertEqual(len({(x["brand"], x["product"]) for x in st}), 1)          # 同一條產品線
            nums = [re.search(r"\d+(?:\.\d+)?", x["code"]) for x in st]
            if all(nums):                                   # 有色號數字：照品牌編號，數字越大越深
                vals = [float(n.group()) for n in nums]
                self.assertTrue(vals[0] < vals[1] < vals[2], [x["code"] for x in st])
            else:
                Ls = [ce.hex_to_lab(x["hex"])[0] for x in st]
                self.assertTrue(Ls[0] > Ls[1] > Ls[2])
            self.assertIn("下顎線", plan["stripes"]["how"])

    def test_user_chosen_undertone_drives_skin_card(self):
        r = rec.recommend("vitiligo", "grooming", normal_hex="#946D60", patch_hex="#B49B96", undertone_override="neutral")
        self.assertEqual(r["base"]["card"]["tone"], "neutral")
        r = rec.recommend("vitiligo", "grooming", normal_hex="#946D60", patch_hex="#B49B96")
        self.assertEqual(r["base"]["card"]["tone"], "cool")                  # 沒選才用照片推估

    def test_vitiligo_needs_two_points_and_gives_full_set(self):
        with self.assertRaises(ValueError):
            rec.recommend("vitiligo", "grooming", normal_hex="#936A5C")
        r = rec.recommend("vitiligo", "grooming", normal_hex="#936A5C", patch_hex="#B69F9A")
        self.assertEqual(r["undertone_id"], "pink_cool")
        self.assertIn(r["base"]["level"], ("two_shade", "two_shade_plus"))
        found = r["base"]["foundation"]
        self.assertTrue(found and all(f["source"] == BASE_SOURCE for f in found))   # 底妝獨立一列；本機版用官網色號
        self.assertEqual(found[0]["level"], r["base"]["card"]["level"])
        self.assertTrue(any("照光" in c["text"] for c in r["care"]))
        self.assertNotIn("blush", r["looks"]["glow"]["selection"])   # 理容款不上腮紅


class BrowFromPhotoTests(unittest.TestCase):
    def test_dark_brows_keep_own_color_never_lighter(self):
        soft = ce.brow_from_photo("#242426", "#936A5C", "soft")
        deep = ce.brow_from_photo("#242426", "#936A5C", "defined")
        self.assertTrue(soft["brow_visible"])
        self.assertEqual(soft["hex"], "#242426")
        self.assertLessEqual(ce.hex_to_lab(deep["hex"])[0], ce.hex_to_lab("#242426")[0])

    def test_light_brows_get_deeper_but_capped_on_pale_skin(self):
        skin = "#D9CBC1"
        r = ce.brow_from_photo("#C8BCAD", skin, "defined")
        self.assertFalse(r["brow_visible"])
        sL, bL = ce.hex_to_lab(skin)[0], ce.hex_to_lab(r["hex"])[0]
        self.assertGreaterEqual(sL - bL, 25)                     # 看得見、有立體感
        self.assertLessEqual(sL - bL, ce.BROW_MAX_DL_PALE + 0.5)  # 不會太兇
        soft = ce.brow_from_photo("#C8BCAD", skin, "soft")
        self.assertGreater(ce.hex_to_lab(soft["hex"])[0], bL)    # 補一點比略深淺

    def test_quick_look_maps_fixed_brow_to_photo_and_builds(self):
        import looks
        p = looks.load_presets()
        sel = looks.quick_selection("grooming", "clear", "neutral", p, "#242426", "#936A5C")
        self.assertEqual(sel["brow"], "photo_soft")
        brow = next(e for e in looks.build_effects(sel, p) if e["category"] == "eyebrows")
        self.assertEqual(brow["palettes"][0]["color"], "#242426")
        nat = looks.quick_selection("natural", "clear", "neutral", p, "#C8BCAD", "#D9CBC1")
        self.assertEqual(nat["brow"], "photo_defined")
        self.assertEqual(looks.quick_selection("natural", "clear", "neutral", p)["brow"], "taupe")  # 沒取眉色才用固定色
        with self.assertRaises(looks.PresetError):
            looks.build_effects({"brow": "photo_soft"}, p)

    def test_recommend_reports_brow_levels(self):
        r = rec.recommend("vitiligo", "grooming", normal_hex="#936A5C", patch_hex="#B69F9A", brow_hex="#242426")
        self.assertEqual(r["brow"]["soft"]["hex"], "#242426")
        brow = next(s for s in r["looks"]["clear"]["shades"] if s["item"] == "brow")
        self.assertEqual(brow["hex"], "#242426")


class SkinCardTests(unittest.TestCase):
    def test_photo_levels_and_low_chroma(self):
        self.assertEqual(sc.classify_photo("#D9CBC1")["level"], 2)
        self.assertTrue(sc.classify_photo("#D9CBC1")["tone_confirm"])
        self.assertEqual(sc.classify_photo("#AF8679")["level"], 6)
        self.assertEqual(len(sc.card()), 30)

    def test_generic_undertone_codes(self):
        # 沒有品牌專屬規則時：色號字母、名稱裡的 Cool/Warm、說明裡的冷暖或粉／黃描述
        cases = [("3C", "", "cool"), ("12W", "", "warm"), ("4N", "", "neutral"), ("Light 10 Warm", "", "warm"),
                 ("Ivory", "light with cool undertone", "cool"), ("Sand", "beige with yellow tones", "warm"),
                 ("Beige", "balance of yellow & pink", "neutral")]
        for code, desc, want in cases:
            self.assertEqual(sc.brand_tone({"code": code, "description": desc}, "示範品牌"), want, code)

    def test_mix_when_line_has_no_same_level(self):
        rows = [{"brand": "X", "product": "P", "category": "foundation", "source": "official", "code": c,
                 "hex": h, "lab": ce.hex_to_lab(h), "level": sc.level_of(ce.hex_to_lab(h)[0]), "tone": "neutral",
                 "tone_source": "brand"} for c, h in (("light", "#E8CBB0"), ("deep", "#8A6248"))]
        out = sc.counter_shades({"level": 5, "tone": "neutral"}, rows)
        self.assertGreater(out[0]["level_diff"], 0)
        self.assertEqual({out[0]["mix"]["lighter"], out[0]["mix"]["deeper"]}, {"light", "deep"})


class SensitiveNotesTests(unittest.TestCase):
    def test_both_conditions_have_soft_worded_sensitivity_notes(self):
        alb, vit = ce.sensitive_notes("albinism"), ce.sensitive_notes("vitiligo")
        self.assertIn("敏感", alb["title"]); self.assertIn("敏感", vit["title"])
        a = " ".join(x["text"] + x["why"] for x in alb["items"])
        v = " ".join(x["text"] + x["why"] for x in vit["items"])
        for word in ("曬傷", "小範圍", "皮膚科"):
            self.assertIn(word, a)
        for word in ("曬傷", "不要搓", "PPD", "小範圍", "照光"):
            self.assertIn(word, v)
        for banned in ("缺陷", "蒼白", "改造", "危險"):                       # 柔性字眼
            self.assertNotIn(banned, a + v + alb["title"] + vit["title"])
        r = rec.recommend("vitiligo", "grooming", normal_hex="#946D60", patch_hex="#B49B96")
        self.assertEqual(r["sensitive"]["title"], vit["title"])


class BudgetTests(unittest.TestCase):
    def test_reserve_settle_and_cap(self):
        with tempfile.TemporaryDirectory() as td:
            b = bg.Budget(2, Path(td) / "ledger.json")
            c = b.reserve("makeup-vto")
            b.settle("makeup-vto", c, success=False)       # 失敗不記帳
            self.assertEqual(b.spent, 0)
            for _ in range(2):
                c = b.reserve("makeup-vto")
                b.settle("makeup-vto", c, success=True)
            with self.assertRaises(bg.BudgetExceeded):
                b.reserve("makeup-vto")
            self.assertEqual(bg.Budget(5, Path(td) / "ledger.json").spent, 2)  # 跨次啟動累計


if __name__ == "__main__":
    unittest.main()
