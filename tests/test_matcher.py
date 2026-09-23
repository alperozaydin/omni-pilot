"""Matcher tests. Candidate macros are real USDA values seen in the production audit."""
from __future__ import annotations

import pytest

from omni_pilot.matcher import (
    GOOD_DISTANCE,
    head_words,
    logged_macros_per_100g,
    macro_distance,
    matches_head,
    pick_best,
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


class TestMacroDistance:
    def test_distance_is_calorie_weighted_and_relative_to_logged_kcal(self):
        logged = _logged(200.0, 10.0, 10.0, 10.0)
        candidate = _candidate("X", 11.0, 9.0, 12.0)
        # (4*1 + 9*1 + 4*2) / 200
        assert macro_distance(logged, candidate) == pytest.approx(21.0 / 200.0)

    def test_uses_fiber_free_carbs_when_closer(self):
        # EU labels exclude fiber: logged 16.1 g matches USDA's 22.5 g minus 6.4 g fiber.
        logged = _logged(120.0, 7.05, 2.77, 16.1)
        candidate = _candidate("Chickpeas, canned", 7.05, 2.77, 22.5, fiber=6.4)
        assert macro_distance(logged, candidate) == pytest.approx(0.0)

    def test_low_calorie_food_uses_kcal_floor(self):
        logged = _logged(13.0, 1.0, 0.0, 2.0)
        candidate = _candidate("Tomatoes, raw", 1.0, 0.0, 4.0)
        assert macro_distance(logged, candidate) == pytest.approx(8.0 / 50.0)

    def test_zero_calorie_food_does_not_divide_by_zero(self):
        logged = _logged(0.0, 0.0, 0.0, 0.0)
        candidate = _candidate("Beverages, carbonated, cola, without caffeine", 0.0, 0.0, 1.0)
        assert macro_distance(logged, candidate) == pytest.approx(4.0 / 50.0)

    def test_none_when_candidate_lacks_a_macro(self):
        logged = _logged(100.0, 10.0, 5.0, 5.0)
        assert macro_distance(logged, _candidate("X", 10.0, None, 5.0)) is None


class TestPickBest:
    def test_named_food_beats_different_food_with_closer_macros(self):
        # "Feta (Schaf- & Ziegenmilch)": camembert's macros are closer, but it is not feta.
        candidates = [
            _candidate("Cheese, camembert", 19.8, 24.3, 0.46, 0.0, rank=0),
            _candidate("Cheese, feta", 14.2, 21.5, 3.88, 0.0, rank=1),
        ]
        pick = pick_best(candidates, "cheese, feta", _logged(288.2, 18.6, 24.5, 0.0))
        assert pick["candidate"]["description"] == "Cheese, feta"
        assert pick["confidence"] == "good"

    def test_macros_switch_raw_to_cooked(self):
        # "Reis XXL" is logged by cooked weight although the query says raw.
        candidates = [
            _candidate("Rice, white, long-grain, regular, raw, enriched", 7.13, 0.66, 80.0, 1.3, rank=0),
            _candidate("Rice, white, long-grain, regular, enriched, cooked", 2.69, 0.28, 28.2, 0.4, rank=1),
        ]
        pick = pick_best(candidates, "rice, white, long-grain, regular, raw", _logged(141.1, 3.3, 1.3, 28.9))
        assert pick["candidate"]["description"] == "Rice, white, long-grain, regular, enriched, cooked"
        assert pick["confidence"] == "good"
        assert pick["macro_distance"] == pytest.approx(0.1022, abs=1e-3)

    def test_lentils_never_chosen_for_chickpeas(self):
        candidates = [
            _candidate("Lentils, mature seeds, cooked, boiled, without salt", 9.02, 0.38, 20.1, 7.9, rank=0),
            _candidate(
                "Chickpeas (garbanzo beans, bengal gram), mature seeds, cooked, boiled, without salt",
                8.86, 2.59, 27.4, 7.6, rank=1,
            ),
        ]
        pick = pick_best(
            candidates, "chickpeas, mature seeds, cooked, boiled, without salt", _logged(120.0, 6.5, 2.3, 14.0)
        )
        assert pick["candidate"]["description"].startswith("Chickpeas")

    def test_named_colour_beats_closer_other_colour(self):
        # "Paprika salat": green pepper is closer on macros, red is what was eaten.
        candidates = [
            _candidate("Peppers, sweet, green, raw", 0.86, 0.17, 4.64, 1.7, rank=0),
            _candidate("Peppers, sweet, red, raw", 0.99, 0.3, 6.03, 2.1, rank=1),
        ]
        pick = pick_best(candidates, "peppers, sweet, red, raw", _logged(31.1, 1.2, 1.4, 2.9))
        assert pick["candidate"]["description"] == "Peppers, sweet, red, raw"
        # 0.297 > GOOD_DISTANCE: the salad's oil makes it an imperfect match, so it is flagged.
        assert pick["confidence"] == "weak"

    def test_query_words_break_near_ties(self):
        # "Hähnchen Brustfilet": the breast entry, not generic or light meat.
        candidates = [
            _candidate("Chicken, broilers or fryers, meat only, raw", 21.4, 3.08, 0.0, 0.0, rank=0),
            _candidate(
                "Chicken, broiler or fryers, breast, skinless, boneless, meat only, raw", 22.5, 2.62, 0.0, 0.0, rank=1
            ),
            _candidate("Chicken, broilers or fryers, light meat, meat only, raw", 23.2, 1.65, 0.0, 0.0, rank=2),
        ]
        pick = pick_best(
            candidates, "chicken, broilers or fryers, breast, meat only, raw", _logged(104.1, 22.0, 1.7, 0.0)
        )
        assert "breast" in pick["candidate"]["description"]

    def test_sr_legacy_wins_a_near_tie_over_foundation(self):
        candidates = [
            _candidate("Beef, top sirloin steak, raw", 22.0, 3.0, 0.0, data_type="Foundation", rank=0),
            _candidate("Beef, top sirloin steak, lean, raw", 22.0, 3.2, 0.0, data_type="SR Legacy", rank=1),
        ]
        pick = pick_best(candidates, "beef, top sirloin steak, raw", _logged(110.0, 22.0, 3.0, 0.0))
        assert pick["candidate"]["data_type"] == "SR Legacy"

    def test_no_candidate_matching_head_falls_back_as_weak(self):
        candidates = [_candidate("Fish, tuna salad", 16.0, 9.3, 9.4, rank=0)]
        pick = pick_best(candidates, "caprese salad", _logged(74.0, 5.0, 4.0, 3.0))
        assert pick["candidate"]["description"] == "Fish, tuna salad"
        assert pick["confidence"] == "weak"

    def test_fallback_is_weak_even_when_macros_are_close(self):
        candidates = [_candidate("Fish, tuna salad", 5.0, 4.0, 3.0, rank=0)]
        pick = pick_best(candidates, "caprese salad", _logged(74.0, 5.0, 4.0, 3.0))
        assert pick["macro_distance"] <= GOOD_DISTANCE
        assert pick["confidence"] == "weak"

    def test_without_logged_macros_takes_first_matching_candidate(self):
        candidates = [
            _candidate("Lentils, raw", 24.6, 1.1, 63.4, rank=0),
            _candidate("Chickpeas, raw", 20.5, 6.0, 63.0, rank=1),
        ]
        pick = pick_best(candidates, "chickpeas, raw", None)
        assert pick["candidate"]["description"] == "Chickpeas, raw"
        assert pick["confidence"] == "no_macros"
        assert pick["macro_distance"] is None

    def test_no_scorable_candidate_is_weak_without_distance(self):
        candidates = [_candidate("Ketchup, restaurant", None, None, None, rank=0)]
        pick = pick_best(candidates, "ketchup", _logged(100.0, 0.0, 0.0, 21.0))
        assert pick["candidate"]["description"] == "Ketchup, restaurant"
        assert pick["confidence"] == "weak"
        assert pick["macro_distance"] is None

    def test_no_candidates_returns_none(self):
        assert pick_best([], "anything", _logged(100.0, 1.0, 1.0, 1.0)) is None
