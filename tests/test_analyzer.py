from __future__ import annotations

import pytest

from omni_pilot.analyzer import analyze, determine_status
from tests.helpers import enrichment, food_entry


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

    def test_high_from_ul_figure_when_given(self):
        assert determine_status(45.0, 15.0, 35.0, 40.0) == "high"

    def test_value_above_ul_is_not_high_when_ul_figure_is_below(self):
        assert determine_status(50.0, 15.0, 35.0, 20.0) == "ok"

    def test_target_still_judged_on_value_when_ul_figure_is_given(self):
        assert determine_status(5.0, 15.0, 35.0, 0.0) == "deficient"


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
        assert result["period"]["left_out_day"] is None
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

    def test_missing_and_failed_foods_are_reported_apart_from_skipped(self):
        entries = [
            self._make_entry("Eggs", "2026-07-13", 200.0),
            self._make_entry("Lachs", "2026-07-13", 300.0),
            self._make_entry("Lachs", "2026-07-14", 100.0),
            self._make_entry("Braun Linsen", "2026-07-13", 80.0),
            self._make_entry("Water", "2026-07-13", 500.0),
        ]
        result = analyze(
            entries,
            enrichment(
                {"Eggs": {"vitamin_a_mcg": 149.0, "calcium_mg": 50.0}},
                skipped={"Water"},
                not_in_usda={"Braun Linsen"},
                lookup_failed={"Lachs"},
            ),
            self._make_ref_ranges(),
        )

        coverage = result["coverage"]
        # "You told it to ignore this", "USDA has no such food" and "USDA could
        # not be asked" are different report lines.
        assert coverage["mapped_entries"] == 1
        assert coverage["skipped_entries"] == 1
        assert coverage["skipped_foods"] == ["Water"]
        assert coverage["not_in_usda_entries"] == 1
        assert coverage["not_in_usda_foods"] == ["Braun Linsen"]
        assert coverage["lookup_failed_entries"] == 2
        assert coverage["lookup_failed_foods"] == ["Lachs"]
        # On the one counted day, Lachs and Braun Linsen are unmeasured beside
        # Eggs (200 of 580 g); the 14th has no resolved food and is not counted.
        assert result["nutrients"]["vitamin_a_mcg"]["coverage_pct"] == 34.5

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


class TestStatusOnShownValue:
    """The reports show one decimal, so the status is judged on that figure."""

    @staticmethod
    def _vitamin_c(per_100g: float) -> dict:
        entries = [{"date": "2026-08-09", "food_name": "Orange", "total_weight_g": 100}]
        ref_ranges = {
            "demographic": {},
            "nutrients": {
                "vitamin_c_mg": {
                    "name": "Vitamin C", "unit": "mg", "type": "rda", "rda": 110, "ul": 2000,
                }
            },
        }
        result = analyze(entries, enrichment({"Orange": {"vitamin_c_mg": per_100g}}), ref_ranges)
        return result["nutrients"]["vitamin_c_mg"]

    def test_just_below_target_that_shows_as_target_is_ok(self):
        # 109.96 is shown as 110.0, which meets the 110 target.
        assert self._vitamin_c(109.96)["status"] == "ok"

    def test_below_target_when_shown_is_still_low(self):
        assert self._vitamin_c(109.94)["status"] == "low"

    def test_just_above_ul_that_shows_as_ul_is_not_high(self):
        assert self._vitamin_c(2000.04)["status"] == "ok"

    def test_above_ul_when_shown_is_still_high(self):
        assert self._vitamin_c(2000.06)["status"] == "high"


class TestUpperLimitScope:
    """The UL is compared with the intake it covers; a High names its sources."""

    REF_RANGES = {
        "demographic": {},
        "nutrients": {
            "b3_niacin_mg": {
                "name": "Niacin", "unit": "mg", "type": "rda", "rda": 15, "ul": 35,
                "ul_applies_to": "supplements",
            },
            "magnesium_mg": {
                "name": "Magnesium", "unit": "mg", "type": "ai", "ai": 350, "ul": 350,
                "ul_applies_to": "supplements",
            },
            "vitamin_a_mcg": {
                "name": "Vitamin A", "unit": "mcg", "type": "rda", "rda": 850, "ul": 3000,
                "ul_applies_to": "retinol",
            },
            "sodium_mg": {"name": "Sodium", "unit": "mg", "type": "ai", "ai": 1500, "ul": 2300},
            "zinc_mg": {"name": "Zinc", "unit": "mg", "type": "rda", "rda": 14, "ul": 40},
            "vitamin_k_mcg": {"name": "Vitamin K", "unit": "mcg", "type": "ai", "ai": 70},
        },
    }

    def _analyze(self, entries: list[dict], profiles=None, supplements=None, custom=None) -> dict:
        result = analyze(entries, enrichment(profiles, custom=custom), self.REF_RANGES, supplements)
        return result["nutrients"]

    def test_niacin_from_food_above_the_limit_is_ok(self):
        # The 2026-10-23 case: 36.4 mg of niacin, all of it from food
        nutrients = self._analyze([food_entry("Hähnchenbrust")], {"Hähnchenbrust": {"b3_niacin_mg": 36.4}})

        niacin = nutrients["b3_niacin_mg"]
        assert niacin["status"] == "ok"
        assert niacin["daily_avg"] == pytest.approx(36.4)
        assert niacin["ul_applies_to"] == "supplements"
        assert niacin["ul_figure"] == 0.0
        assert niacin["high_sources"] == []

    def test_niacin_supplement_above_the_limit_is_high_with_supplement_as_only_source(self):
        nutrients = self._analyze(
            [food_entry("Brot")], {"Brot": {"b3_niacin_mg": 5.0}}, supplements={"b3_niacin_mg": 40.0},
        )

        niacin = nutrients["b3_niacin_mg"]
        assert niacin["status"] == "high"
        assert niacin["daily_avg"] == pytest.approx(45.0)
        assert niacin["ul_figure"] == pytest.approx(40.0)
        assert niacin["high_sources"] == [{"name": "supplement", "amount": 40.0}]

    def test_niacin_supplement_below_the_limit_is_ok_even_with_food_above(self):
        nutrients = self._analyze(
            [food_entry("Hähnchenbrust")], {"Hähnchenbrust": {"b3_niacin_mg": 30.0}},
            supplements={"b3_niacin_mg": 20.0},
        )

        niacin = nutrients["b3_niacin_mg"]
        assert niacin["daily_avg"] == pytest.approx(50.0)
        assert niacin["ul_figure"] == pytest.approx(20.0)
        assert niacin["status"] == "ok"

    def test_magnesium_supplement_above_350_is_high(self):
        nutrients = self._analyze(
            [food_entry("Haferflocken")], {"Haferflocken": {"magnesium_mg": 130.0}},
            supplements={"magnesium_mg": 400.0},
        )

        magnesium = nutrients["magnesium_mg"]
        assert magnesium["status"] == "high"
        assert magnesium["ul_figure"] == pytest.approx(400.0)
        assert magnesium["high_sources"] == [{"name": "supplement", "amount": 400.0}]

    def test_magnesium_from_food_above_350_is_ok(self):
        nutrients = self._analyze(
            [food_entry("Kürbiskerne")], {"Kürbiskerne": {"magnesium_mg": 550.0}},
        )

        assert nutrients["magnesium_mg"]["status"] == "ok"

    def test_supplement_only_limit_still_reports_low(self):
        nutrients = self._analyze([food_entry("Brot")], {"Brot": {"b3_niacin_mg": 5.0}})

        assert nutrients["b3_niacin_mg"]["status"] == "deficient"

    def test_vitamin_a_from_carrots_is_not_high(self):
        # 500 g of carrots: 4175 mcg RAE, all of it beta-carotene, no retinol
        nutrients = self._analyze(
            [food_entry("Karotten", 500.0)], {"Karotten": {"vitamin_a_mcg": 835.0, "retinol_mcg": 0.0}},
        )

        vitamin_a = nutrients["vitamin_a_mcg"]
        assert vitamin_a["daily_avg"] == pytest.approx(4175.0)
        assert vitamin_a["ul_figure"] == 0.0
        assert vitamin_a["status"] == "ok"
        assert vitamin_a["high_sources"] == []

    def test_vitamin_a_from_liver_is_high_with_retinol_sources(self):
        entries = [food_entry("Rinderleber", 70.0), food_entry("Eier", 100.0)]
        profiles = {
            "Rinderleber": {"vitamin_a_mcg": 5000.0, "retinol_mcg": 4968.0},
            "Eier": {"vitamin_a_mcg": 170.0, "retinol_mcg": 160.0},
        }

        nutrients = self._analyze(entries, profiles, supplements={"vitamin_a_mcg": 800.0})

        vitamin_a = nutrients["vitamin_a_mcg"]
        assert vitamin_a["status"] == "high"
        # The target check stays on RAE plus the supplement
        assert vitamin_a["daily_avg"] == pytest.approx(3500.0 + 170.0 + 800.0)
        # The UL check counts retinol plus the supplement
        assert vitamin_a["ul_figure"] == pytest.approx(3477.6 + 160.0 + 800.0)
        sources = vitamin_a["high_sources"]
        assert [s["name"] for s in sources] == ["Rinderleber", "supplement", "Eier"]
        assert [s["amount"] for s in sources] == pytest.approx([3477.6, 800.0, 160.0])

    def test_vitamin_a_food_without_retinol_key_counts_zero(self):
        # An older cache entry whose retinol backfill failed has no key at all
        nutrients = self._analyze([food_entry("Leberwurst")], {"Leberwurst": {"vitamin_a_mcg": 5000.0}})

        vitamin_a = nutrients["vitamin_a_mcg"]
        assert vitamin_a["ul_figure"] == 0.0
        assert vitamin_a["status"] == "ok"

    def test_vitamin_a_food_with_retinol_none_counts_zero(self):
        nutrients = self._analyze(
            [food_entry("Leberwurst")], {"Leberwurst": {"vitamin_a_mcg": 5000.0, "retinol_mcg": None}},
        )

        assert nutrients["vitamin_a_mcg"]["ul_figure"] == 0.0

    def test_vitamin_a_supplement_counts_as_retinol(self):
        nutrients = self._analyze(
            [food_entry("Karotten")], {"Karotten": {"vitamin_a_mcg": 835.0, "retinol_mcg": 0.0}},
            supplements={"vitamin_a_mcg": 3100.0},
        )

        vitamin_a = nutrients["vitamin_a_mcg"]
        assert vitamin_a["status"] == "high"
        assert vitamin_a["high_sources"] == [{"name": "supplement", "amount": 3100.0}]

    def test_retinol_of_custom_food_parts_counts_by_share(self):
        custom = {"Leberknödel": {
            "recipe": "leberknoedel", "macro_distance": 0.1,
            "parts": [
                {"share": 0.5, "fdc_id": 1, "usda_name": "Beef liver",
                 "per_100g": {"vitamin_a_mcg": 5000.0, "retinol_mcg": 4968.0}},
                {"share": 0.5, "fdc_id": 2, "usda_name": "Bread crumbs",
                 "per_100g": {"vitamin_a_mcg": 0.0}},
            ],
        }}

        nutrients = self._analyze([food_entry("Leberknödel", 200.0)], custom=custom)

        vitamin_a = nutrients["vitamin_a_mcg"]
        assert vitamin_a["ul_figure"] == pytest.approx(4968.0)
        assert vitamin_a["high_sources"] == [{"name": "Leberknödel", "amount": pytest.approx(4968.0)}]

    def test_sodium_from_food_stays_high_with_food_sources(self):
        # Amounts are daily averages: each food's total over the two days, halved
        entries = [
            food_entry("Brot", 200.0, "2026-10-01"),
            food_entry("Feta", 100.0, "2026-10-01"),
            food_entry("Brot", 300.0, "2026-10-02"),
            food_entry("Feta", 200.0, "2026-10-02"),
        ]
        profiles = {"Brot": {"sodium_mg": 500.0}, "Feta": {"sodium_mg": 1100.0}}

        nutrients = self._analyze(entries, profiles)

        sodium = nutrients["sodium_mg"]
        assert sodium["status"] == "high"
        assert sodium["ul_applies_to"] is None
        assert sodium["ul_figure"] == pytest.approx(2900.0)
        assert sodium["high_sources"] == [
            {"name": "Feta", "amount": pytest.approx(1650.0)},
            {"name": "Brot", "amount": pytest.approx(1250.0)},
        ]

    def test_sources_with_equal_amounts_are_ordered_by_name(self):
        entries = [food_entry("Salami"), food_entry("Feta")]
        profiles = {"Salami": {"sodium_mg": 1500.0}, "Feta": {"sodium_mg": 1500.0}}

        sources = self._analyze(entries, profiles)["sodium_mg"]["high_sources"]

        assert [s["name"] for s in sources] == ["Feta", "Salami"]

    def test_supplement_as_only_source_of_an_all_sources_high(self):
        nutrients = self._analyze(
            [food_entry("Apfel")], {"Apfel": {"zinc_mg": 0.0}}, supplements={"zinc_mg": 45.0},
        )

        zinc = nutrients["zinc_mg"]
        assert zinc["status"] == "high"
        assert zinc["high_sources"] == [{"name": "supplement", "amount": 45.0}]

    def test_custom_food_is_one_source_under_its_logged_name(self):
        custom = {"Salat Caprese": {
            "recipe": "caprese", "macro_distance": 0.1,
            "parts": [
                {"share": 0.75, "fdc_id": 1, "usda_name": "Tomatoes", "per_100g": {"sodium_mg": 200.0}},
                {"share": 0.25, "fdc_id": 2, "usda_name": "Mozzarella", "per_100g": {"sodium_mg": 2000.0}},
            ],
        }}
        entries = [food_entry("Salat Caprese", 400.0), food_entry("Brot")]

        nutrients = self._analyze(entries, {"Brot": {"sodium_mg": 500.0}}, custom=custom)

        # 300 g of tomatoes (600 mg) and 100 g of mozzarella (2000 mg): one source
        assert nutrients["sodium_mg"]["high_sources"] == [
            {"name": "Salat Caprese", "amount": pytest.approx(2600.0)},
            {"name": "Brot", "amount": pytest.approx(500.0)},
        ]

    def test_not_high_has_no_sources(self):
        nutrients = self._analyze([food_entry("Brot")], {"Brot": {"sodium_mg": 1000.0}})

        sodium = nutrients["sodium_mg"]
        assert sodium["status"] == "low"
        assert sodium["ul_figure"] == pytest.approx(1000.0)
        assert sodium["high_sources"] == []

    def test_nutrient_without_ul_has_no_ul_figure(self):
        nutrients = self._analyze([food_entry("Spinat")], {"Spinat": {"vitamin_k_mcg": 480.0}})

        vitamin_k = nutrients["vitamin_k_mcg"]
        assert vitamin_k["ul_figure"] is None
        assert vitamin_k["ul_applies_to"] is None
        assert vitamin_k["high_sources"] == []

    def test_ul_figure_is_judged_on_its_shown_value(self):
        # 35.04 is shown as 35.0, which does not exceed the limit
        nutrients = self._analyze([food_entry("Brot")], {"Brot": {}}, supplements={"b3_niacin_mg": 35.04})

        assert nutrients["b3_niacin_mg"]["status"] == "ok"


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


class TestUnresolvedFoodCoverage:
    """Foods with no USDA profile leave a hole in their day's totals (BAR-32)."""

    REF_RANGES = {
        "demographic": {},
        "nutrients": {
            "choline_mg": {"name": "Choline", "unit": "mg", "type": "ai", "ai": 550, "ul": 3500},
            "vitamin_a_mcg": {"name": "Vitamin A", "unit": "mcg", "type": "rda", "rda": 900, "ul": 3000},
        },
    }
    PROFILES = {"Eggs": {"choline_mg": 294.0, "vitamin_a_mcg": 149.0}}

    def _entries(self) -> list[dict]:
        return [
            {"date": "2026-08-01", "food_name": "Eggs", "total_weight_g": 200.0},
            {"date": "2026-08-02", "food_name": "Eggs", "total_weight_g": 200.0},
            {"date": "2026-08-02", "food_name": "Salmon", "total_weight_g": 300.0},
        ]

    @pytest.mark.parametrize("unresolved", ["not_in_usda", "lookup_failed"])
    def test_unresolved_food_on_a_counted_day_is_unmeasured(self, unresolved):
        result = analyze(
            self._entries(), enrichment(self.PROFILES, **{unresolved: {"Salmon"}}), self.REF_RANGES,
        )
        choline = result["nutrients"]["choline_mg"]
        # 400 g of eggs measured, 300 g of salmon not: 400 / 700
        assert choline["coverage_pct"] == 57.1
        assert result["nutrients"]["vitamin_a_mcg"]["coverage_pct"] == 57.1

    def test_unresolved_food_marks_a_deficient_verdict_as_floor(self):
        result = analyze(
            self._entries(), enrichment(self.PROFILES, not_in_usda={"Salmon"}), self.REF_RANGES,
        )
        vit_a = result["nutrients"]["vitamin_a_mcg"]
        assert vit_a["status"] == "deficient"
        assert vit_a["is_floor"] is True

    def test_unresolved_food_on_a_dropped_day_is_not_unmeasured(self):
        # A day with no resolved food leaves the average entirely, so its
        # unresolved food cannot pull the average down.
        entries = [
            {"date": "2026-08-01", "food_name": "Eggs", "total_weight_g": 200.0},
            {"date": "2026-08-02", "food_name": "Salmon", "total_weight_g": 300.0},
        ]
        result = analyze(entries, enrichment(self.PROFILES, not_in_usda={"Salmon"}), self.REF_RANGES)
        assert result["period"]["days"] == 1
        assert result["nutrients"]["choline_mg"]["coverage_pct"] == 100.0

    def test_unresolved_weight_shares_exclude_skipped_food(self):
        entries = [
            {"date": "2026-08-01", "food_name": "Eggs", "total_weight_g": 500.0},
            {"date": "2026-08-01", "food_name": "Salmon", "total_weight_g": 300.0},
            {"date": "2026-08-01", "food_name": "Lachs", "total_weight_g": 200.0},
            {"date": "2026-08-01", "food_name": "Water", "total_weight_g": 500.0},
            # A dropped day still counts: the warning names what is missing from the log
            {"date": "2026-08-02", "food_name": "Salmon", "total_weight_g": 100.0},
        ]
        result = analyze(
            entries,
            enrichment(self.PROFILES, not_in_usda={"Salmon"}, lookup_failed={"Lachs"}, skipped={"Water"}),
            self.REF_RANGES,
        )
        coverage = result["coverage"]
        # Of 1100 g food weight: 400 g not in USDA, 200 g failed
        assert coverage["not_in_usda_weight_pct"] == 36.4
        assert coverage["lookup_failed_weight_pct"] == 18.2

    def test_untranslated_food_is_counted_apart_and_unmeasured(self):
        entries = [
            {"date": "2026-08-01", "food_name": "Eggs", "total_weight_g": 300.0},
            {"date": "2026-08-01", "food_name": "Braune Linsen", "total_weight_g": 100.0},
            {"date": "2026-08-01", "food_name": "Salmon", "total_weight_g": 100.0},
        ]
        result = analyze(
            entries,
            enrichment(self.PROFILES, not_in_usda={"Salmon"}, untranslated={"Braune Linsen"}),
            self.REF_RANGES,
        )
        coverage = result["coverage"]
        assert coverage["untranslated_entries"] == 1
        assert coverage["untranslated_foods"] == ["Braune Linsen"]
        assert coverage["untranslated_weight_pct"] == 20.0
        assert coverage["not_in_usda_foods"] == ["Salmon"]
        assert coverage["not_in_usda_weight_pct"] == 20.0
        assert coverage["lookup_failed_entries"] == 0
        # Eggs is 300 of the day's 500 g
        assert result["nutrients"]["choline_mg"]["coverage_pct"] == 60.0

    def test_unresolved_weight_shares_zero_without_food_weight(self):
        entries = [{"date": "2026-08-01", "food_name": "Water", "total_weight_g": 500.0}]
        result = analyze(entries, enrichment(skipped={"Water"}), self.REF_RANGES)
        assert result["coverage"]["not_in_usda_weight_pct"] == 0.0
        assert result["coverage"]["lookup_failed_weight_pct"] == 0.0


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

    def test_denominator_excludes_skipped_and_lookup_failed_weight(self):
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
                skipped={"Water"}, lookup_failed={"Lachs"}, low_confidence=low_confidence,
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
        assert coverage["not_in_usda_entries"] == 0
        assert coverage["lookup_failed_entries"] == 0
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
            {"name": "Misch Salat Rohkost", "grams": 500.0, "macro_distance": 0.12, "fitted": False},
            {"name": "Salat Manhattan", "grams": 120.0, "macro_distance": None, "fitted": False},
        ]  # the 0 g food is left out
        assert recipes[1]["ingredients"] == [
            {"fdc_id": 170457, "usda_name": "Tomatoes", "share_pct": 75.0},
            {"fdc_id": 170845, "usda_name": "Mozzarella", "share_pct": 25.0},
        ]

    def test_no_custom_foods_gives_empty_list(self):
        entries = [{"date": "2026-08-09", "food_name": "Eggs", "total_weight_g": 100.0}]
        result = analyze(entries, enrichment({"Eggs": {"vitamin_k_mcg": 0.3}}), self.REF_RANGES)
        assert result["coverage"]["custom_recipes"] == []


class TestLoggedFiber:
    """MacroFactor's own fiber figure wins over USDA's when it is above zero."""

    REF_RANGES = {
        "demographic": {},
        "nutrients": {
            "fiber_g": {"name": "Fiber", "unit": "g", "type": "ai", "ai": 38},
            "calcium_mg": {"name": "Calcium", "unit": "mg", "type": "rda", "rda": 1000},
        },
    }
    # 200 g of bread: USDA says 2 g fiber per 100 g, so 4 g for the entry
    BREAD = {"Brot": {"fiber_g": 2.0, "calcium_mg": 50.0}}

    def _entry(self, food_name: str = "Brot", fiber_g: float | None = None, grams: float = 200.0) -> dict:
        return {"date": "2026-10-04", "food_name": food_name, "total_weight_g": grams, "fiber_g": fiber_g}

    def test_positive_logged_fiber_is_used_as_logged(self):
        result = analyze([self._entry(fiber_g=7.5)], enrichment(self.BREAD), self.REF_RANGES)

        # The logged figure is for the whole entry: not scaled by weight, not added to USDA's
        assert result["nutrients"]["fiber_g"]["daily_avg"] == pytest.approx(7.5)

    @pytest.mark.parametrize("logged", [None, 0.0])
    def test_empty_or_zero_logged_fiber_falls_back_to_usda(self, logged):
        result = analyze([self._entry(fiber_g=logged)], enrichment(self.BREAD), self.REF_RANGES)

        assert result["nutrients"]["fiber_g"]["daily_avg"] == pytest.approx(4.0)

    def test_logged_fiber_leaves_other_nutrients_on_usda(self):
        result = analyze([self._entry(fiber_g=7.5)], enrichment(self.BREAD), self.REF_RANGES)

        assert result["nutrients"]["calcium_mg"]["daily_avg"] == pytest.approx(100.0)

    def test_logged_fiber_is_measured_when_usda_has_no_value(self):
        profiles = {"Brot": {"fiber_g": None, "calcium_mg": 50.0}}

        result = analyze([self._entry(fiber_g=3.0)], enrichment(profiles), self.REF_RANGES)

        fiber = result["nutrients"]["fiber_g"]
        assert fiber["daily_avg"] == pytest.approx(3.0)
        assert fiber["coverage_pct"] == 100.0

    def test_logged_fiber_replaces_every_part_of_a_custom_food(self):
        custom = {"Salat": {
            "recipe": "salat", "macro_distance": 0.1,
            "parts": [
                {"share": 0.75, "fdc_id": 1, "usda_name": "Tomatoes", "per_100g": {"fiber_g": 1.2}},
                {"share": 0.25, "fdc_id": 2, "usda_name": "Mozzarella", "per_100g": {"fiber_g": None}},
            ],
        }}

        result = analyze([self._entry("Salat", fiber_g=5.0)], enrichment(custom=custom), self.REF_RANGES)

        fiber = result["nutrients"]["fiber_g"]
        assert fiber["daily_avg"] == pytest.approx(5.0)
        # The logged figure covers the whole entry, the part without a USDA value included
        assert fiber["coverage_pct"] == 100.0

    def test_skipped_food_keeps_its_logged_fiber_out(self):
        entries = [self._entry(), self._entry("Flohsamen", fiber_g=9.0)]

        result = analyze(entries, enrichment(self.BREAD, skipped={"Flohsamen"}), self.REF_RANGES)

        assert result["nutrients"]["fiber_g"]["daily_avg"] == pytest.approx(4.0)

    @pytest.mark.parametrize("unresolved", ["not_in_usda", "lookup_failed"])
    def test_unresolved_food_stays_unmeasured_despite_logged_fiber(self, unresolved):
        entries = [self._entry(), self._entry("Linsen", fiber_g=9.0)]

        result = analyze(entries, enrichment(self.BREAD, **{unresolved: {"Linsen"}}), self.REF_RANGES)

        fiber = result["nutrients"]["fiber_g"]
        assert fiber["daily_avg"] == pytest.approx(4.0)
        assert fiber["coverage_pct"] == 50.0

    def test_reports_share_of_fiber_taken_from_logged_values(self):
        # 6 g logged for one entry, 2 g from USDA (100 g at 2 g/100 g) for the other
        entries = [self._entry(fiber_g=6.0), self._entry(grams=100.0)]

        result = analyze(entries, enrichment(self.BREAD), self.REF_RANGES)

        assert result["coverage"]["logged_fiber_pct"] == 75.0

    def test_logged_fiber_share_is_none_without_any_fiber(self):
        result = analyze([self._entry()], enrichment({"Brot": {"fiber_g": 0.0}}), self.REF_RANGES)

        assert result["coverage"]["logged_fiber_pct"] is None


class TestLeftOutDay:
    def test_left_out_day_is_kept_in_the_period(self):
        left_out = {"date": "2026-08-09", "logged_kcal": 1196.0, "expenditure_kcal": 2570.0, "partial": True}
        entries = [{"date": "2026-08-08", "time": "12:00", "food_name": "Eggs", "total_weight_g": 100.0}]
        result = analyze(entries, enrichment({"Eggs": {}}), {"nutrients": {}}, left_out_day=left_out)
        assert result["period"]["left_out_day"] == left_out


def _estimated(food_name: str, logged_g: float, estimated_g: float | None, distance: float | None = None,
               kcal: float = 930.0) -> dict:
    """A placeholder entry as matcher.estimate_weights leaves it."""
    return {
        "date": "2026-10-11", "food_name": food_name,
        "total_weight_g": estimated_g if estimated_g is not None else logged_g,
        "calories_kcal": kcal, "weight_is_placeholder": True,
        "weight_estimate": {"logged_g": logged_g, "estimated_g": estimated_g, "macro_distance": distance},
    }


class TestEstimatedWeights:
    REF_RANGES = {
        "demographic": {},
        "nutrients": {"vitamin_a_mcg": {"name": "Vitamin A", "unit": "mcg", "type": "rda", "rda": 900}},
    }

    def test_nutrients_scale_with_estimated_grams(self):
        entries = [_estimated("King's", 100.0, 335.0, 0.04)]
        result = analyze(entries, enrichment({"King's": {"vitamin_a_mcg": 10.0}}), self.REF_RANGES)
        assert result["nutrients"]["vitamin_a_mcg"]["daily_avg"] == pytest.approx(33.5)

    def test_estimated_weights_listed_for_resolved_and_unresolved_foods(self):
        entries = [
            _estimated("King's", 100.0, 335.0, 0.04),
            _estimated("King's", 100.0, 335.0, 0.04),
            _estimated("Mystery Bowl", 100.0, None),
            {"date": "2026-10-11", "food_name": "Eggs", "total_weight_g": 120.0, "calories_kcal": 170.0},
        ]
        result = analyze(
            entries, enrichment({"King's": {}, "Eggs": {}}, not_in_usda={"Mystery Bowl"}), self.REF_RANGES,
        )
        assert result["coverage"]["estimated_weights"] == [
            {"name": "King's", "estimates": [
                {"logged_g": 100.0, "estimated_g": 335.0, "macro_distance": 0.04},
                {"logged_g": 100.0, "estimated_g": 335.0, "macro_distance": 0.04},
            ]},
            {"name": "Mystery Bowl", "estimates": [
                {"logged_g": 100.0, "estimated_g": None, "macro_distance": None},
            ]},
        ]

    def test_skipped_placeholder_is_not_listed(self):
        entries = [_estimated("Quick Add", 100.0, None, kcal=1746.0)]
        result = analyze(entries, enrichment(skipped={"Quick Add"}), self.REF_RANGES)
        assert result["coverage"]["estimated_weights"] == []

    def test_no_estimated_weights_gives_empty_list(self):
        entries = [{"date": "2026-10-11", "food_name": "Eggs", "total_weight_g": 120.0}]
        result = analyze(entries, enrichment({"Eggs": {}}), self.REF_RANGES)
        assert result["coverage"]["estimated_weights"] == []

    def test_low_confidence_kcal_pct(self):
        entries = [
            _estimated("Chicken and Rice", 100.0, 456.0, 0.19, kcal=930.0),
            {"date": "2026-10-11", "food_name": "Eggs", "total_weight_g": 500.0, "calories_kcal": 1070.0},
            {"date": "2026-10-11", "food_name": "Water", "total_weight_g": 500.0, "calories_kcal": 0.0},
        ]
        low_confidence = {"Chicken and Rice": {"usda_name": "Chicken tenders", "macro_distance": 0.19}}
        result = analyze(
            entries,
            enrichment({"Chicken and Rice": {}, "Eggs": {}}, skipped={"Water"}, low_confidence=low_confidence),
            self.REF_RANGES,
        )
        assert result["coverage"]["low_confidence_kcal_pct"] == 46.5

    def test_low_confidence_kcal_pct_without_calories(self):
        entries = [{"date": "2026-10-11", "food_name": "Eggs", "total_weight_g": 100.0}]
        result = analyze(entries, enrichment({"Eggs": {}}), self.REF_RANGES)
        assert result["coverage"]["low_confidence_kcal_pct"] == 0.0

    def test_custom_food_use_carries_fitted(self):
        entries = [_estimated("Salat Caprese", 100.0, 400.0, 0.05)]
        result = analyze(
            entries, enrichment(custom={"Salat Caprese": {**_caprese(0.05), "weight_unknown": True}}),
            self.REF_RANGES,
        )
        [recipe] = result["coverage"]["custom_recipes"]
        assert recipe["foods"] == [{"name": "Salat Caprese", "grams": 400.0, "macro_distance": 0.05, "fitted": True}]
