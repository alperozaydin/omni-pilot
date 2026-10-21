"""Matcher tests. Candidate macros are real USDA values seen in the production audit."""
from __future__ import annotations

import pytest

from omni_pilot.matcher import (
    GOOD_DISTANCE,
    GOOD_FIT_DISTANCE,
    LoggedTotals,
    UsdaMacros,
    fit_grams,
    fitted_distance,
    head_words,
    logged_macros_for_matching,
    logged_macros_per_100g,
    macro_distance,
    matches_head,
    pick_best,
)
from tests.helpers import food_entry


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

    def test_without_logged_macros_and_no_head_match_is_weak_not_no_macros(self):
        # No candidate is the named food at all, and there are no macros to
        # fall back on either — this is weaker than an ordinary no_macros pick.
        candidates = [_candidate("Fish, tuna salad", 16.0, 9.3, 9.4, rank=0)]
        pick = pick_best(candidates, "caprese salad", None)
        assert pick["candidate"]["description"] == "Fish, tuna salad"
        assert pick["confidence"] == "weak"
        assert pick["macro_distance"] is None

    def test_no_scorable_candidate_is_weak_without_distance(self):
        candidates = [_candidate("Ketchup, restaurant", None, None, None, rank=0)]
        pick = pick_best(candidates, "ketchup", _logged(100.0, 0.0, 0.0, 21.0))
        assert pick["candidate"]["description"] == "Ketchup, restaurant"
        assert pick["confidence"] == "weak"
        assert pick["macro_distance"] is None

    def test_no_candidates_returns_none(self):
        assert pick_best([], "anything", _logged(100.0, 1.0, 1.0, 1.0)) is None


def test_macro_distance_accepts_plain_macros():
    macros = {"protein_g": 6.0, "fat_g": 5.0, "carbs_g": 3.0, "fiber_g": None}
    logged = {"kcal": 74.0, "protein_g": 4.9, "fat_g": 4.2, "carbs_g": 3.0}
    assert macro_distance(logged, macros) == pytest.approx((4 * 1.1 + 9 * 0.8) / 74.0)


KINGS = LoggedTotals(kcal=930.0, protein_g=53.0, fat_g=56.0, carbs_g=54.0)
DOUBLE_CB = UsdaMacros(protein_g=16.2, fat_g=16.2, carbs_g=18.0, fiber_g=0.9)


class TestFitGrams:
    def test_kings_against_double_cheeseburger(self):
        assert fit_grams(KINGS, DOUBLE_CB) == pytest.approx(335, abs=1)

    def test_fit_never_below_macro_mass(self):
        doner = LoggedTotals(kcal=935.0, protein_g=55.0, fat_g=39.0, carbs_g=99.0)
        skin = UsdaMacros(protein_g=20.4, fat_g=40.7, carbs_g=0.0, fiber_g=0.0)
        assert fit_grams(doner, skin) == pytest.approx(193.0)

    def test_fit_needs_every_macro(self):
        assert fit_grams(KINGS, UsdaMacros(protein_g=None, fat_g=1.0, carbs_g=1.0, fiber_g=None)) is None

    def test_fit_needs_a_macro_above_zero(self):
        assert fit_grams(KINGS, UsdaMacros(protein_g=0.0, fat_g=0.0, carbs_g=0.0, fiber_g=None)) is None

    def test_fitted_distance_scores_at_the_fitted_grams(self):
        assert fitted_distance(KINGS, DOUBLE_CB) == pytest.approx(0.04, abs=0.01)

    def test_fitted_distance_without_fit_is_none(self):
        assert fitted_distance(KINGS, UsdaMacros(protein_g=None, fat_g=1.0, carbs_g=1.0, fiber_g=None)) is None


class TestPickBestByProportions:
    QUERY = "fast foods, cheeseburger"
    CANDIDATES = [
        # Denser and meat-heavy: closer to King's impossible per-100 g figures.
        _candidate("Fast foods, cheeseburger; large, double patty, no bun", 40.0, 45.0, 5.0, rank=0),
        # The double cheeseburger's proportions.
        _candidate("Fast foods, cheeseburger; double, regular patty; with condiments", 16.2, 16.2, 18.0, 0.9, rank=1),
    ]

    def test_weight_unknown_food_picks_by_proportions(self):
        pick = pick_best(self.CANDIDATES, self.QUERY, None, totals=KINGS)
        assert pick["candidate"]["rank"] == 1
        assert pick["confidence"] == "good"
        assert pick["macro_distance"] == pytest.approx(0.04, abs=0.01)

    def test_per_100g_figures_would_pick_the_denser_one(self):
        pick = pick_best(self.CANDIDATES, self.QUERY, _logged(930.0, 53.0, 56.0, 54.0))
        assert pick["candidate"]["rank"] == 0

    def test_fitted_pick_is_weak_above_good_fit_distance(self):
        bens = LoggedTotals(kcal=660.0, protein_g=18.0, fat_g=13.0, carbs_g=113.0)
        rice = [_candidate("Rice, white, long-grain, regular, raw, unenriched", 7.13, 0.66, 80.0, 1.3)]
        pick = pick_best(rice, "rice, white, long-grain, regular, raw", None, totals=bens)
        assert GOOD_FIT_DISTANCE < pick["macro_distance"] <= GOOD_DISTANCE
        assert pick["confidence"] == "weak"

    def test_known_weight_scoring_is_unchanged(self):
        candidates = [
            _candidate("Rice, white, long-grain, regular, raw, enriched", 7.13, 0.66, 80.0, 1.3, rank=0),
            _candidate("Rice, white, long-grain, regular, enriched, cooked", 2.69, 0.28, 28.2, 0.4, rank=1),
        ]
        logged = _logged(141.1, 3.3, 1.3, 28.9)
        query = "rice, white, long-grain, regular, raw"
        assert pick_best(candidates, query, logged, totals=None) == pick_best(candidates, query, logged)


class TestLoggedMacrosForMatching:
    def test_mixed_food_uses_only_real_weight_entries(self):
        entries = [
            food_entry("X", 200.0, calories_kcal=290.0, protein_g=20.0, fat_g=10.0, carbs_g=30.0),
            food_entry("X", 100.0, calories_kcal=1180.0, protein_g=60.0, fat_g=50.0, carbs_g=120.0,
                       weight_is_placeholder=True),
        ]
        logged = logged_macros_for_matching(entries)
        assert logged["per_100g"]["X"] == pytest.approx(
            {"kcal": 145.0, "protein_g": 10.0, "fat_g": 5.0, "carbs_g": 15.0}
        )
        assert "X" not in logged["weight_unknown"]

    def test_all_placeholder_food_gets_summed_totals(self):
        kings = dict(calories_kcal=930.0, protein_g=53.0, fat_g=56.0, carbs_g=54.0, weight_is_placeholder=True)
        logged = logged_macros_for_matching([food_entry("King's", **kings), food_entry("King's", **kings)])
        assert logged["weight_unknown"]["King's"] == {
            "kcal": 1860.0, "protein_g": 106.0, "fat_g": 112.0, "carbs_g": 108.0,
        }
        assert "King's" not in logged["per_100g"]

    def test_logged_macros_per_100g_unchanged_for_placeholders(self):
        """The custom-foods skill relies on it: a placeholder still counts at its logged 100 g."""
        entry = food_entry("King's", 100.0, calories_kcal=930.0, protein_g=53.0, fat_g=56.0, carbs_g=54.0,
                           weight_is_placeholder=True)
        assert logged_macros_per_100g([entry])["King's"]["protein_g"] == pytest.approx(53.0)
