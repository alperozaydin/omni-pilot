from __future__ import annotations

import pytest

from omni_pilot.analyzer import analyze, determine_status
from tests.helpers import enrichment


class TestDetermineStatus:
    def test_ok_when_above_rda_below_ul(self):
        assert determine_status(100.0, 90.0, 2000.0) == "ok"

    def test_ok_when_above_rda_no_ul(self):
        assert determine_status(100.0, 90.0, None) == "ok"

    def test_low_when_between_50_and_100_pct(self):
        assert determine_status(60.0, 90.0, None) == "low"

    def test_deficient_when_below_50_pct(self):
        assert determine_status(30.0, 90.0, None) == "deficient"

    def test_high_when_above_ul(self):
        assert determine_status(3500.0, 90.0, 2000.0) == "high"

    def test_ok_at_exact_rda(self):
        assert determine_status(90.0, 90.0, None) == "ok"

    def test_low_at_exact_50_pct(self):
        assert determine_status(45.0, 90.0, None) == "low"

    def test_ok_at_exact_ul(self):
        assert determine_status(2000.0, 90.0, 2000.0) == "ok"


class TestAnalyze:
    def _make_entry(self, food_name: str, date: str, total_weight_g: float) -> dict:
        return {
            "date": date,
            "time": "12:00",
            "food_name": food_name,
            "serving_size": "g",
            "serving_qty": total_weight_g,
            "serving_weight_g": 1.0,
            "total_weight_g": total_weight_g,
            "calories_kcal": 100.0,
            "protein_g": 10.0,
            "fat_g": 5.0,
            "carbs_g": 15.0,
        }

    def _make_ref_ranges(self) -> dict:
        return {
            "demographic": {"sex": "male", "age_group": "19-50", "body_weight_kg": 75},
            "nutrients": {
                "vitamin_a_mcg": {
                    "name": "Vitamin A", "unit": "mcg", "type": "rda",
                    "rda": 900, "ul": 3000, "source": "NIH",
                },
                "calcium_mg": {
                    "name": "Calcium", "unit": "mg", "type": "rda",
                    "rda": 1000, "ul": 2500, "source": "NIH",
                },
            },
        }

    def test_daily_average_calculation(self):
        entries = [
            self._make_entry("Eggs", "2026-07-13", 200.0),
            self._make_entry("Eggs", "2026-07-14", 200.0),
        ]
        enriched = {
            "Eggs": {"vitamin_a_mcg": 149.0, "calcium_mg": 50.0},
        }
        ref_ranges = self._make_ref_ranges()
        result = analyze(entries, enrichment(enriched), ref_ranges)

        assert result["period"]["days"] == 2
        # 200g of eggs per day -> 149 * (200/100) = 298 mcg vitamin A per day
        # Average over 2 days = 298
        vit_a = result["nutrients"]["vitamin_a_mcg"]
        assert vit_a["daily_avg"] == pytest.approx(298.0)
        # 298/900 = 33.1% which is < 50%, so deficient
        assert vit_a["status"] == "deficient"

    def test_skipped_foods_excluded(self):
        entries = [
            self._make_entry("Eggs", "2026-07-13", 200.0),
            self._make_entry("Quick Add", "2026-07-13", 100.0),
        ]
        enriched = {
            "Eggs": {"vitamin_a_mcg": 149.0, "calcium_mg": 50.0},
        }
        ref_ranges = self._make_ref_ranges()
        result = analyze(entries, enrichment(enriched, skipped={"Quick Add"}), ref_ranges)

        assert result["coverage"]["mapped_entries"] == 1
        assert result["coverage"]["skipped_entries"] == 1

    def test_failed_lookup_reported_as_unresolved_not_skipped(self):
        entries = [
            self._make_entry("Eggs", "2026-07-13", 200.0),
            self._make_entry("Lachs", "2026-07-13", 300.0),
            self._make_entry("Water", "2026-07-13", 500.0),
        ]
        result = analyze(
            entries,
            enrichment(
                {"Eggs": {"vitamin_a_mcg": 149.0, "calcium_mg": 50.0}},
                skipped={"Water"},
                unresolved={"Lachs"},
            ),
            self._make_ref_ranges(),
        )

        coverage = result["coverage"]
        # "You told it to ignore this" and "your data has a hole" are different
        # report lines; a failed lookup must not read as an intentional skip.
        assert coverage["mapped_entries"] == 1
        assert coverage["skipped_entries"] == 1
        assert coverage["skipped_foods"] == ["Water"]
        assert coverage["unresolved_entries"] == 1
        assert coverage["unresolved_foods"] == ["Lachs"]

    def test_unmeasured_nutrient_reports_zero_coverage(self):
        entries = [self._make_entry("Eggs", "2026-07-13", 200.0)]
        enriched = {
            "Eggs": {"vitamin_a_mcg": 149.0, "calcium_mg": None},
        }
        ref_ranges = self._make_ref_ranges()
        result = analyze(entries, enrichment(enriched), ref_ranges)

        vit_a = result["nutrients"]["vitamin_a_mcg"]
        assert vit_a["daily_avg"] == pytest.approx(298.0)
        assert vit_a["coverage_pct"] == 100.0
        # USDA never measured calcium for this food: it contributes nothing,
        # and the 200g is disclosed as unmeasured rather than as a measured zero.
        calcium = result["nutrients"]["calcium_mg"]
        assert calcium["daily_avg"] == pytest.approx(0.0)
        assert calcium["coverage_pct"] == 0.0

    def test_multiple_foods_same_day(self):
        entries = [
            self._make_entry("Eggs", "2026-07-13", 200.0),
            self._make_entry("Milk", "2026-07-13", 300.0),
        ]
        enriched = {
            "Eggs": {"vitamin_a_mcg": 149.0, "calcium_mg": 50.0},
            "Milk": {"vitamin_a_mcg": 50.0, "calcium_mg": 120.0},
        }
        ref_ranges = self._make_ref_ranges()
        result = analyze(entries, enrichment(enriched), ref_ranges)

        # Day total: Eggs 200g * 149/100 + Milk 300g * 50/100 = 298 + 150 = 448
        vit_a = result["nutrients"]["vitamin_a_mcg"]
        assert vit_a["daily_avg"] == pytest.approx(448.0)
        # Calcium: 200g * 50/100 + 300g * 120/100 = 100 + 360 = 460
        calcium = result["nutrients"]["calcium_mg"]
        assert calcium["daily_avg"] == pytest.approx(460.0)


class TestAnalyzeSupplements:
    def test_supplements_added_to_daily_avg(self):
        entries = [{"date": "2026-08-09", "food_name": "Apple", "total_weight_g": 100}]
        enriched = {"Apple": {"vitamin_c_mg": 5.0}}
        ref_ranges = {
            "demographic": {},
            "nutrients": {
                "vitamin_c_mg": {"name": "Vitamin C", "unit": "mg", "type": "rda", "rda": 90}
            }
        }
        supplements = {"vitamin_c_mg": 50.0}
        
        result = analyze(entries, enrichment(enriched), ref_ranges, supplements=supplements)
        # 5.0 from food + 50.0 from supplement
        assert result["nutrients"]["vitamin_c_mg"]["daily_avg"] == 55.0

    def test_missing_supplement_ignored(self):
        entries = [{"date": "2026-08-09", "food_name": "Apple", "total_weight_g": 100}]
        enriched = {"Apple": {"vitamin_c_mg": 5.0}}
        ref_ranges = {
            "demographic": {},
            "nutrients": {
                "vitamin_c_mg": {"name": "Vitamin C", "unit": "mg", "type": "rda", "rda": 90}
            }
        }
        
        result = analyze(entries, enrichment(enriched), ref_ranges, supplements=None)
        assert result["nutrients"]["vitamin_c_mg"]["daily_avg"] == 5.0


class TestCoverageAccounting:
    def test_combined_nutrient_with_one_missing_component_contributes_nothing(self):
        entries = [{"date": "2026-08-09", "food_name": "Olives", "total_weight_g": 400.0}]
        enriched = {"Olives": {"methionine_g": 0.35, "cysteine_g": None}}
        ref_ranges = {
            "demographic": {"body_weight_kg": 75},
            "nutrients": {
                "methionine_cysteine_g": {
                    "name": "Methionine + Cysteine", "unit": "g", "type": "who_per_kg",
                    "mg_per_kg": 15, "safe_mg_per_kg": 19, "ul": None,
                },
            },
        }
        result = analyze(entries, enrichment(enriched), ref_ranges)
        combined = result["nutrients"]["methionine_cysteine_g"]
        # The methionine half is NOT added — a partial sum is an invented number.
        assert combined["daily_avg"] == pytest.approx(0.0)
        assert combined["coverage_pct"] == 0.0

    def test_combined_nutrient_with_both_components_sums_normally(self):
        entries = [{"date": "2026-08-09", "food_name": "Chicken", "total_weight_g": 200.0}]
        enriched = {"Chicken": {"methionine_g": 0.6, "cysteine_g": 0.3}}
        ref_ranges = {
            "demographic": {"body_weight_kg": 75},
            "nutrients": {
                "methionine_cysteine_g": {
                    "name": "Methionine + Cysteine", "unit": "g", "type": "who_per_kg",
                    "mg_per_kg": 15, "safe_mg_per_kg": 19, "ul": None,
                },
            },
        }
        result = analyze(entries, enrichment(enriched), ref_ranges)
        combined = result["nutrients"]["methionine_cysteine_g"]
        # 200g * (0.6 + 0.3) / 100 = 1.8g
        assert combined["daily_avg"] == pytest.approx(1.8)
        assert combined["coverage_pct"] == 100.0

    def test_combined_epa_dha_converts_grams_to_mg(self):
        # USDA stores EPA and DHA in grams, but the reference target is in mg.
        # CLAUDE.md names this unit mismatch as an invariant to guard.
        entries = [{"date": "2026-08-09", "food_name": "Salmon", "total_weight_g": 100.0}]
        enriched = {"Salmon": {"omega3_epa_mg": 0.69, "omega3_dha_mg": 1.46}}
        ref_ranges = {
            "demographic": {},
            "nutrients": {
                "omega3_epa_dha_mg": {
                    "name": "Omega-3 (EPA + DHA)", "unit": "mg", "type": "ai",
                    "ai": 250, "ul": None,
                },
            },
        }
        result = analyze(entries, enrichment(enriched), ref_ranges)
        omega3 = result["nutrients"]["omega3_epa_dha_mg"]
        # (0.69 + 1.46) g/100g * 1000 = 2150 mg over a 100 g serving.
        assert omega3["daily_avg"] == pytest.approx(2150.0)
        assert omega3["coverage_pct"] == 100.0
        assert omega3["status"] == "ok"

    def test_combined_epa_dha_with_one_component_missing_contributes_nothing(self):
        entries = [{"date": "2026-08-09", "food_name": "Lean Beef", "total_weight_g": 400.0}]
        enriched = {"Lean Beef": {"omega3_epa_mg": 0.02, "omega3_dha_mg": None}}
        ref_ranges = {
            "demographic": {},
            "nutrients": {
                "omega3_epa_dha_mg": {
                    "name": "Omega-3 (EPA + DHA)", "unit": "mg", "type": "ai",
                    "ai": 250, "ul": None,
                },
            },
        }
        result = analyze(entries, enrichment(enriched), ref_ranges)
        omega3 = result["nutrients"]["omega3_epa_dha_mg"]
        # The BAR-41 headline case: the measured EPA half is no longer added on
        # its own, so no 80 mg "deficient" verdict gets manufactured.
        assert omega3["daily_avg"] == pytest.approx(0.0)
        assert omega3["coverage_pct"] == 0.0
        assert omega3["is_floor"] is True

    def test_measured_zero_counts_as_measured(self):
        entries = [{"date": "2026-08-09", "food_name": "Egg White", "total_weight_g": 250.0}]
        enriched = {"Egg White": {"vitamin_k_mcg": 0.0}}
        ref_ranges = {
            "demographic": {},
            "nutrients": {
                "vitamin_k_mcg": {"name": "Vitamin K", "unit": "mcg", "type": "ai", "ai": 120, "ul": None},
            },
        }
        result = analyze(entries, enrichment(enriched), ref_ranges)
        vit_k = result["nutrients"]["vitamin_k_mcg"]
        # 0.0 means "USDA measured it and found none" — that is data, not absence.
        assert vit_k["daily_avg"] == pytest.approx(0.0)
        assert vit_k["coverage_pct"] == 100.0

    def test_coverage_is_weighted_by_consumed_weight(self):
        entries = [
            {"date": "2026-08-09", "food_name": "Salmon", "total_weight_g": 300.0},
            {"date": "2026-08-09", "food_name": "Swiss Cheese", "total_weight_g": 100.0},
        ]
        enriched = {
            "Salmon": {"choline_mg": 60.0},
            "Swiss Cheese": {"choline_mg": None},
        }
        ref_ranges = {
            "demographic": {},
            "nutrients": {
                "choline_mg": {"name": "Choline", "unit": "mg", "type": "ai", "ai": 550, "ul": 3500},
            },
        }
        result = analyze(entries, enrichment(enriched), ref_ranges)
        choline = result["nutrients"]["choline_mg"]
        # 300g of 400g consumed = 75%. Counting foods would say 50%.
        assert choline["coverage_pct"] == 75.0

    def test_skipped_food_excluded_from_coverage_denominator(self):
        entries = [
            {"date": "2026-08-09", "food_name": "Eggs", "total_weight_g": 200.0},
            {"date": "2026-08-09", "food_name": "Quick Add", "total_weight_g": 100.0},
        ]
        enriched = {
            "Eggs": {"vitamin_a_mcg": 149.0},
        }
        ref_ranges = {
            "demographic": {},
            "nutrients": {
                "vitamin_a_mcg": {"name": "Vitamin A", "unit": "mcg", "type": "rda", "rda": 900, "ul": 3000},
            },
        }
        result = analyze(entries, enrichment(enriched, skipped={"Quick Add"}), ref_ranges)
        vit_a = result["nutrients"]["vitamin_a_mcg"]
        # A logged glass of water must not read as "unmeasured vitamin A".
        assert vit_a["coverage_pct"] == 100.0
        assert result["coverage"]["skipped_entries"] == 1

    def test_coverage_none_when_no_resolved_entries(self):
        entries = [{"date": "2026-08-09", "food_name": "Quick Add", "total_weight_g": 100.0}]
        ref_ranges = {
            "demographic": {},
            "nutrients": {
                "vitamin_a_mcg": {"name": "Vitamin A", "unit": "mcg", "type": "rda", "rda": 900, "ul": 3000},
            },
        }
        result = analyze(entries, enrichment(skipped={"Quick Add"}), ref_ranges)
        # No weight was attributable at all — distinct from "measured, found nothing".
        assert result["nutrients"]["vitamin_a_mcg"]["coverage_pct"] is None

    def test_reference_key_with_no_usda_mapping_reports_zero_coverage(self):
        # Guards spec §8: a reference key with no USDA_NUTRIENT_MAP entry must
        # surface as 0% coverage rather than silently reading as a measured 0.0.
        entries = [{"date": "2026-08-09", "food_name": "Mystery Food", "total_weight_g": 150.0}]
        enriched = {"Mystery Food": {"vitamin_a_mcg": 100.0}}
        ref_ranges = {
            "demographic": {},
            "nutrients": {
                "unobtainium_mg": {"name": "Unobtainium", "unit": "mg", "type": "ai", "ai": 10, "ul": None},
            },
        }
        result = analyze(entries, enrichment(enriched), ref_ranges)
        unobtainium = result["nutrients"]["unobtainium_mg"]
        assert unobtainium["daily_avg"] == pytest.approx(0.0)
        assert unobtainium["coverage_pct"] == 0.0


class TestFloorMarking:
    @pytest.mark.parametrize(
        "nutrient_config,daily_value,expected_status",
        [
            ({"name": "Vitamin A", "unit": "mcg", "type": "rda", "rda": 900, "ul": 3000}, 900.0, "ok"),
            ({"name": "Vitamin A", "unit": "mcg", "type": "rda", "rda": 900, "ul": 3000}, 500.0, "low"),
            ({"name": "Vitamin A", "unit": "mcg", "type": "rda", "rda": 900, "ul": 3000}, 100.0, "deficient"),
            ({"name": "Vitamin A", "unit": "mcg", "type": "rda", "rda": 900, "ul": 3000}, 3500.0, "high"),
            (
                {
                    "name": "Histidine", "unit": "g", "type": "who_per_kg",
                    "mg_per_kg": 10, "safe_mg_per_kg": 12, "ul": None,
                },
                500.0,
                "unknown",
            ),
        ],
    )
    def test_is_floor_set_only_for_low_and_deficient(
        self, nutrient_config, daily_value, expected_status
    ):
        # The "unknown" row uses who_per_kg with no body_weight_kg in demographic,
        # which is how config.get_nutrient_target returns a None target.
        key = "vitamin_a_mcg" if nutrient_config["type"] == "rda" else "histidine_g"
        entries = [
            {"date": "2026-08-09", "food_name": "Measured Food", "total_weight_g": 80.0},
            {"date": "2026-08-09", "food_name": "Unmeasured Food", "total_weight_g": 20.0},
        ]
        enriched = {
            "Measured Food": {key: daily_value / 0.8},
            "Unmeasured Food": {key: None},
        }
        ref_ranges = {"demographic": {}, "nutrients": {key: nutrient_config}}

        result = analyze(entries, enrichment(enriched), ref_ranges)
        nutrient = result["nutrients"][key]
        assert nutrient["status"] == expected_status
        assert nutrient["coverage_pct"] == 80.0
        assert nutrient["is_floor"] == (expected_status in ("low", "deficient"))

    def test_is_floor_false_at_full_coverage(self):
        entries = [{"date": "2026-08-09", "food_name": "Kale", "total_weight_g": 100.0}]
        enriched = {"Kale": {"vitamin_k_mcg": 80.0}}
        ref_ranges = {
            "demographic": {},
            "nutrients": {
                "vitamin_k_mcg": {"name": "Vitamin K", "unit": "mcg", "type": "ai", "ai": 120, "ul": None},
            },
        }
        result = analyze(entries, enrichment(enriched), ref_ranges)
        vit_k = result["nutrients"]["vitamin_k_mcg"]
        assert vit_k["status"] == "low"
        assert vit_k["coverage_pct"] == 100.0
        assert vit_k["is_floor"] is False

    def test_is_floor_uses_rounded_coverage(self):
        # 999.6g measured of 1000.0g total = 99.96%, which displays as 100.0%.
        # A row must never show 100.0% beside a "*".
        entries = [
            {"date": "2026-08-09", "food_name": "Big Batch", "total_weight_g": 999.6},
            {"date": "2026-08-09", "food_name": "Tiny Unmeasured", "total_weight_g": 0.4},
        ]
        enriched = {
            "Big Batch": {"vitamin_e_mg": 1.0},
            "Tiny Unmeasured": {"vitamin_e_mg": None},
        }
        ref_ranges = {
            "demographic": {},
            "nutrients": {
                "vitamin_e_mg": {"name": "Vitamin E", "unit": "mg", "type": "rda", "rda": 15, "ul": 1000},
            },
        }
        result = analyze(entries, enrichment(enriched), ref_ranges)
        vit_e = result["nutrients"]["vitamin_e_mg"]
        assert vit_e["status"] == "low"
        assert vit_e["coverage_pct"] == 100.0
        assert vit_e["is_floor"] is False

    def test_supplement_lifting_to_ok_clears_floor(self):
        entries = [
            {"date": "2026-08-09", "food_name": "Measured Food", "total_weight_g": 70.0},
            {"date": "2026-08-09", "food_name": "Unmeasured Food", "total_weight_g": 30.0},
        ]
        enriched = {
            "Measured Food": {"vitamin_d_mcg": 10.0 / 0.7},
            "Unmeasured Food": {"vitamin_d_mcg": None},
        }
        ref_ranges = {
            "demographic": {},
            "nutrients": {
                "vitamin_d_mcg": {"name": "Vitamin D", "unit": "mcg", "type": "rda", "rda": 15, "ul": 100},
            },
        }
        supplements = {"vitamin_d_mcg": 10.0}

        result = analyze(entries, enrichment(enriched), ref_ranges, supplements=supplements)
        vit_d = result["nutrients"]["vitamin_d_mcg"]
        # 10.0 from food + 10.0 from the supplement clears the 15.0 target, so the
        # verdict becomes safe and the marker drops even at 70% coverage.
        assert vit_d["coverage_pct"] == 70.0
        assert vit_d["status"] == "ok"
        assert vit_d["is_floor"] is False

    def test_is_floor_set_when_coverage_is_unknown(self):
        # No attributable weight at all (bad API key, every food skipped, empty
        # range) is less supportable than 0% coverage, so the verdict is marked.
        entries = [{"date": "2026-08-09", "food_name": "Quick Add", "total_weight_g": 100.0}]
        ref_ranges = {
            "demographic": {},
            "nutrients": {
                "calcium_mg": {"name": "Calcium", "unit": "mg", "type": "rda", "rda": 1000, "ul": 2500},
            },
        }
        result = analyze(entries, enrichment(skipped={"Quick Add"}), ref_ranges)
        calcium = result["nutrients"]["calcium_mg"]
        assert calcium["coverage_pct"] is None
        assert calcium["status"] == "deficient"
        assert calcium["is_floor"] is True

    def test_is_floor_not_set_when_coverage_unknown_and_status_safe(self):
        entries = [{"date": "2026-08-09", "food_name": "Quick Add", "total_weight_g": 100.0}]
        ref_ranges = {
            "demographic": {},
            "nutrients": {
                "calcium_mg": {"name": "Calcium", "unit": "mg", "type": "rda", "rda": 1000, "ul": 2500},
            },
        }
        supplements = {"calcium_mg": 1200.0}
        result = analyze(entries, enrichment(skipped={"Quick Add"}), ref_ranges, supplements=supplements)
        calcium = result["nutrients"]["calcium_mg"]
        assert calcium["coverage_pct"] is None
        assert calcium["status"] == "ok"
        assert calcium["is_floor"] is False


class TestLowConfidenceCoverage:
    REF_RANGES = {
        "demographic": {},
        "nutrients": {
            "vitamin_a_mcg": {"name": "Vitamin A", "unit": "mcg", "type": "rda", "rda": 900, "ul": 3000},
        },
    }

    def test_weak_foods_listed_by_grams_eaten(self):
        entries = [
            {"date": "2026-08-09", "food_name": "Caprese", "total_weight_g": 200.0},
            {"date": "2026-08-09", "food_name": "Feta Salat", "total_weight_g": 300.0},
            {"date": "2026-08-10", "food_name": "Feta Salat", "total_weight_g": 200.0},
            {"date": "2026-08-10", "food_name": "Eggs", "total_weight_g": 300.0},
        ]
        profiles = {name: {"vitamin_a_mcg": 10.0} for name in ("Caprese", "Feta Salat", "Eggs")}
        low_confidence = {
            "Caprese": {"usda_name": "Fish, tuna salad", "macro_distance": 1.5},
            "Feta Salat": {"usda_name": "Cheese, feta", "macro_distance": None},
        }

        result = analyze(entries, enrichment(profiles, low_confidence=low_confidence), self.REF_RANGES)

        coverage = result["coverage"]
        assert coverage["low_confidence_foods"] == [
            {"name": "Feta Salat", "usda_name": "Cheese, feta", "macro_distance": None, "grams": 500.0},
            {"name": "Caprese", "usda_name": "Fish, tuna salad", "macro_distance": 1.5, "grams": 200.0},
        ]
        # 700 g of 1000 g analysed
        assert coverage["low_confidence_weight_pct"] == 70.0

    def test_weak_foods_still_count_toward_nutrients(self):
        entries = [{"date": "2026-08-09", "food_name": "Caprese", "total_weight_g": 200.0}]
        low_confidence = {"Caprese": {"usda_name": "Fish, tuna salad", "macro_distance": 1.5}}

        result = analyze(
            entries,
            enrichment({"Caprese": {"vitamin_a_mcg": 10.0}}, low_confidence=low_confidence),
            self.REF_RANGES,
        )

        assert result["nutrients"]["vitamin_a_mcg"]["daily_avg"] == pytest.approx(20.0)

    def test_denominator_excludes_skipped_and_unresolved_weight(self):
        entries = [
            {"date": "2026-08-09", "food_name": "Caprese", "total_weight_g": 100.0},
            {"date": "2026-08-09", "food_name": "Eggs", "total_weight_g": 100.0},
            {"date": "2026-08-09", "food_name": "Water", "total_weight_g": 500.0},
            {"date": "2026-08-09", "food_name": "Lachs", "total_weight_g": 300.0},
        ]
        low_confidence = {"Caprese": {"usda_name": "Fish, tuna salad", "macro_distance": 1.5}}

        result = analyze(
            entries,
            enrichment(
                {"Caprese": {"vitamin_a_mcg": 10.0}, "Eggs": {"vitamin_a_mcg": 10.0}},
                skipped={"Water"}, unresolved={"Lachs"}, low_confidence=low_confidence,
            ),
            self.REF_RANGES,
        )

        assert result["coverage"]["low_confidence_weight_pct"] == 50.0

    def test_no_weak_foods_and_no_analysed_weight(self):
        entries = [{"date": "2026-08-09", "food_name": "Water", "total_weight_g": 500.0}]
        result = analyze(entries, enrichment(skipped={"Water"}), self.REF_RANGES)
        assert result["coverage"]["low_confidence_foods"] == []
        assert result["coverage"]["low_confidence_weight_pct"] == 0.0

    def test_weak_food_with_only_zero_weight_entries_is_not_listed(self):
        # A weak food logged with 0 g (e.g. a "Quick Add" style zero entry)
        # must not surface as a 0 g row in the low-confidence list.
        entries = [{"date": "2026-08-09", "food_name": "Caprese", "total_weight_g": 0.0}]
        low_confidence = {"Caprese": {"usda_name": "Fish, tuna salad", "macro_distance": 1.5}}

        result = analyze(
            entries,
            enrichment({"Caprese": {"vitamin_a_mcg": 10.0}}, low_confidence=low_confidence),
            self.REF_RANGES,
        )

        assert result["coverage"]["low_confidence_foods"] == []


def _caprese(macro_distance: float | None = 0.1) -> dict:
    """A CustomFoodMatch: 75% tomatoes, 25% mozzarella (which has no vitamin K value)."""
    return {
        "recipe": "caprese",
        "macro_distance": macro_distance,
        "parts": [
            {"share": 0.75, "fdc_id": 170457, "usda_name": "Tomatoes",
             "per_100g": {"vitamin_k_mcg": 8.0, "omega3_epa_mg": 0.0, "omega3_dha_mg": 0.0}},
            {"share": 0.25, "fdc_id": 170845, "usda_name": "Mozzarella",
             "per_100g": {"vitamin_k_mcg": None, "omega3_epa_mg": 0.01, "omega3_dha_mg": 0.02}},
        ],
    }


class TestCustomFoods:
    REF_RANGES = {
        "demographic": {},
        "nutrients": {
            "vitamin_k_mcg": {"name": "Vitamin K", "unit": "mcg", "type": "ai", "ai": 120},
            "omega3_epa_dha_mg": {"name": "EPA+DHA", "unit": "mg", "type": "ai", "ai": 250},
        },
    }

    def test_nutrients_are_the_share_weighted_sum_of_parts(self):
        entries = [{"date": "2026-08-09", "food_name": "Salat Caprese", "total_weight_g": 200.0}]

        result = analyze(entries, enrichment(custom={"Salat Caprese": _caprese()}), self.REF_RANGES)

        # 150 g of tomatoes at 8 mcg/100 g; the mozzarella has no vitamin K value
        vitamin_k = result["nutrients"]["vitamin_k_mcg"]
        assert vitamin_k["daily_avg"] == pytest.approx(12.0)
        # Only the mozzarella's 50 g of 200 g is unmeasured
        assert vitamin_k["coverage_pct"] == 75.0
        assert vitamin_k["is_floor"] is True
        # Combined nutrient per part, EPA/DHA converted from g to mg: 50 g x 30 mg/100 g
        epa_dha = result["nutrients"]["omega3_epa_dha_mg"]
        assert epa_dha["daily_avg"] == pytest.approx(15.0)
        assert epa_dha["coverage_pct"] == 100.0

    def test_custom_entries_count_as_analysed(self):
        entries = [
            {"date": "2026-08-09", "food_name": "Salat Caprese", "total_weight_g": 200.0},
            {"date": "2026-08-09", "food_name": "Feta Salat", "total_weight_g": 200.0},
        ]
        low_confidence = {"Feta Salat": {"usda_name": "Cheese, feta", "macro_distance": 3.9}}

        result = analyze(
            entries,
            enrichment(
                {"Feta Salat": {"vitamin_k_mcg": 1.0}},
                low_confidence=low_confidence,
                custom={"Salat Caprese": _caprese()},
            ),
            self.REF_RANGES,
        )

        coverage = result["coverage"]
        assert coverage["mapped_entries"] == 2
        assert coverage["unresolved_entries"] == 0
        # The custom food's weight is in the analysed-weight denominator
        assert coverage["low_confidence_weight_pct"] == 50.0

    def test_custom_recipes_group_foods_by_recipe(self):
        green_salad = {
            "recipe": "green_salad",
            "macro_distance": None,
            "parts": [{"share": 1.0, "fdc_id": 169249, "usda_name": "Lettuce", "per_100g": {"vitamin_k_mcg": 126.0}}],
        }
        entries = [
            {"date": "2026-08-09", "food_name": "Salat Caprese", "total_weight_g": 264.0},
            {"date": "2026-08-09", "food_name": "Misch Salat Rohkost", "total_weight_g": 250.0},
            {"date": "2026-08-10", "food_name": "Misch Salat Rohkost", "total_weight_g": 250.0},
            {"date": "2026-08-10", "food_name": "Salat Manhattan", "total_weight_g": 120.0},
            {"date": "2026-08-10", "food_name": "Salat Unused", "total_weight_g": 0.0},
        ]
        custom = {
            "Salat Caprese": _caprese(0.4),
            "Misch Salat Rohkost": {**green_salad, "macro_distance": 0.12},
            "Salat Manhattan": green_salad,
            "Salat Unused": green_salad,
        }

        result = analyze(entries, enrichment(custom=custom), self.REF_RANGES)

        recipes = result["coverage"]["custom_recipes"]
        assert [r["recipe"] for r in recipes] == ["green_salad", "caprese"]  # 620 g before 264 g
        assert recipes[0]["foods"] == [
            {"name": "Misch Salat Rohkost", "grams": 500.0, "macro_distance": 0.12},
            {"name": "Salat Manhattan", "grams": 120.0, "macro_distance": None},
        ]  # the 0 g food is left out
        assert recipes[1]["ingredients"] == [
            {"fdc_id": 170457, "usda_name": "Tomatoes", "share_pct": 75.0},
            {"fdc_id": 170845, "usda_name": "Mozzarella", "share_pct": 25.0},
        ]

    def test_no_custom_foods_gives_empty_list(self):
        entries = [{"date": "2026-08-09", "food_name": "Eggs", "total_weight_g": 100.0}]
        result = analyze(entries, enrichment({"Eggs": {"vitamin_k_mcg": 0.3}}), self.REF_RANGES)
        assert result["coverage"]["custom_recipes"] == []
