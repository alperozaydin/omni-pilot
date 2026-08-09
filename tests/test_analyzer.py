from __future__ import annotations

import pytest

from omni_pilot.analyzer import analyze, determine_status


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
        result = analyze(entries, enriched, ref_ranges)

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
            "Quick Add": None,
        }
        ref_ranges = self._make_ref_ranges()
        result = analyze(entries, enriched, ref_ranges)

        assert result["coverage"]["mapped_entries"] == 1
        assert result["coverage"]["skipped_entries"] == 1

    def test_handles_missing_nutrient_in_enriched(self):
        entries = [self._make_entry("Eggs", "2026-07-13", 200.0)]
        enriched = {
            "Eggs": {"vitamin_a_mcg": 149.0, "calcium_mg": None},
        }
        ref_ranges = self._make_ref_ranges()
        result = analyze(entries, enriched, ref_ranges)

        vit_a = result["nutrients"]["vitamin_a_mcg"]
        assert vit_a["daily_avg"] == pytest.approx(298.0)
        # calcium_mg has None value from USDA — should not contribute
        calcium = result["nutrients"]["calcium_mg"]
        assert calcium["daily_avg"] == pytest.approx(0.0)

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
        result = analyze(entries, enriched, ref_ranges)

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
        
        result = analyze(entries, enriched, ref_ranges, supplements=supplements)
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
        
        result = analyze(entries, enriched, ref_ranges, supplements=None)
        assert result["nutrients"]["vitamin_c_mg"]["daily_avg"] == 5.0
