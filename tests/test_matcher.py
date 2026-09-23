"""Matcher tests. Candidate macros are real USDA values seen in the production audit."""
from __future__ import annotations

import pytest

from omni_pilot.matcher import (
    head_words,
    logged_macros_per_100g,
    matches_head,
)


def _candidate(
    description: str,
    protein: float | None,
    fat: float | None,
    carbs: float | None,
    fiber: float | None = None,
    *,
    data_type: str = "SR Legacy",
    rank: int = 0,
) -> dict:
    return {
        "fdc_id": 1000 + rank,
        "description": description,
        "data_type": data_type,
        "rank": rank,
        "protein_g": protein,
        "fat_g": fat,
        "carbs_g": carbs,
        "fiber_g": fiber,
        "raw": {"description": description},
    }


def _logged(kcal: float, protein: float, fat: float, carbs: float) -> dict:
    return {"kcal": kcal, "protein_g": protein, "fat_g": fat, "carbs_g": carbs}


def _entry(food_name: str, grams: float, kcal: float, protein: float, fat: float, carbs: float) -> dict:
    return {
        "food_name": food_name, "total_weight_g": grams, "calories_kcal": kcal,
        "protein_g": protein, "fat_g": fat, "carbs_g": carbs,
    }


class TestLoggedMacrosPer100g:
    def test_weights_entries_by_grams(self):
        entries = [
            _entry("Reis", 100.0, 130.0, 3.0, 0.0, 28.0),
            _entry("Reis", 300.0, 450.0, 9.0, 3.0, 90.0),
        ]
        macros = logged_macros_per_100g(entries)["Reis"]
        assert macros["kcal"] == pytest.approx(145.0)
        assert macros["protein_g"] == pytest.approx(3.0)
        assert macros["fat_g"] == pytest.approx(0.75)
        assert macros["carbs_g"] == pytest.approx(29.5)

    def test_ignores_entries_without_weight(self):
        entries = [
            _entry("Reis", 0.0, 500.0, 50.0, 50.0, 50.0),
            _entry("Reis", 100.0, 130.0, 3.0, 0.0, 28.0),
        ]
        assert logged_macros_per_100g(entries)["Reis"]["kcal"] == pytest.approx(130.0)

    def test_food_with_no_weight_is_absent(self):
        entries = [_entry("Quick Add", 0.0, 500.0, 0.0, 0.0, 0.0)]
        assert logged_macros_per_100g(entries) == {}


class TestHeadWords:
    def test_first_segment_names_the_food(self):
        assert head_words("chickpeas, mature seeds, cooked, boiled") == (["chickpea"], 1)

    def test_parentheses_are_dropped_and_plurals_singularised(self):
        assert head_words("tomatoes (roma), raw") == (["tomato"], 1)
        assert head_words("berries, mixed, frozen") == (["berry"], 1)

    def test_word_ending_in_double_s_is_not_singularised(self):
        assert head_words("glass noodles, cooked") == (["glass", "noodle"], 1)

    def test_generic_first_segment_takes_the_second_too(self):
        assert head_words("fish, salmon, atlantic, raw") == (["fish", "salmon"], 2)
        assert head_words("carbonated beverage, cola") == (["carbonated", "beverage", "cola"], 2)

    def test_empty_query_has_no_head(self):
        assert head_words("") == ([], 0)


class TestMatchesHead:
    def test_rejects_a_different_food_with_the_same_words_later(self):
        head, segments = head_words("chickpeas, mature seeds, cooked, boiled, without salt")
        assert not matches_head("Lentils, mature seeds, cooked, boiled, without salt", head, segments)
        assert matches_head(
            "Chickpeas (garbanzo beans, bengal gram), mature seeds, canned, drained solids", head, segments
        )

    def test_rejects_food_that_only_mentions_the_head_in_passing(self):
        head, segments = head_words("beef, ground, 80% lean meat / 20% fat, raw")
        assert not matches_head("Lebanon bologna, beef", head, segments)
        assert matches_head("Beef, ground, 80% lean meat / 20% fat, raw", head, segments)

    def test_rejects_brand_prefixed_dish(self):
        head, segments = head_words("rice, white, long-grain, regular, raw")
        assert not matches_head("ON THE BORDER, Mexican rice", head, segments)

    def test_generic_candidate_prefix_counts_as_part_of_the_name(self):
        head, segments = head_words("tea, iced, sweetened with sugar")
        assert matches_head("Beverages, tea, instant, lemon, sweetened, prepared with water", head, segments)
        assert not matches_head("Sweeteners, for baking, contains sugar and sucralose", head, segments)

    def test_generic_query_head_matches_reordered_usda_name(self):
        head, segments = head_words("carbonated beverage, cola")
        assert matches_head("Beverages, carbonated, cola, regular", head, segments)

    def test_empty_foundation_segment_is_ignored(self):
        head, segments = head_words("chickpeas, dry")
        assert matches_head("Chickpeas, (garbanzo beans, bengal gram), dry", head, segments)
