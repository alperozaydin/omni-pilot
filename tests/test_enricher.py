from __future__ import annotations

import pytest
import requests
from tinydb import TinyDB

from omni_pilot.enricher import (
    MATCH_VERSION,
    SEARCH_PAGE_SIZE,
    USDA_NUTRIENT_MAP,
    clean_query,
    count_outdated_matches,
    enrich_all_foods,
    extract_micros_from_usda,
    fetch_usda_food,
    get_food_candidates,
    get_food_match,
    get_usda_food,
    search_usda,
)
from omni_pilot.matcher import GOOD_DISTANCE


def _usda_hit(
    fdc_id: int,
    description: str,
    protein: float = 10.0,
    fat: float = 5.0,
    carbs: float = 20.0,
    *,
    data_type: str = "SR Legacy",
    vitamin_a: float = 149.0,
) -> dict:
    return {
        "fdcId": fdc_id,
        "description": description,
        "dataType": data_type,
        "foodNutrients": [
            {"nutrientNumber": "203", "value": protein},
            {"nutrientNumber": "204", "value": fat},
            {"nutrientNumber": "205", "value": carbs},
            {"nutrientNumber": "320", "value": vitamin_a},
        ],
    }


def _candidate(fdc_id: int, description: str, protein: float, fat: float, carbs: float, rank: int = 0) -> dict:
    """A matcher Candidate, as get_food_candidates would build it."""
    return {
        "fdc_id": fdc_id, "description": description, "data_type": "SR Legacy", "rank": rank,
        "protein_g": protein, "fat_g": fat, "carbs_g": carbs, "fiber_g": None,
        "raw": _usda_hit(fdc_id, description, protein, fat, carbs),
    }


EGG_LOGGED = {"kcal": 155.0, "protein_g": 12.6, "fat_g": 10.6, "carbs_g": 1.1}


# An abridged /food/{id} response, as USDA returns it (trimmed).
ABRIDGED_TOMATO = {
    "fdcId": 170457,
    "description": "Tomatoes, red, ripe, raw, year round average",
    "dataType": "SR Legacy",
    "publicationDate": "2019-04-01",
    "foodNutrients": [
        {"number": "203", "name": "Protein", "amount": 0.88, "unitName": "G"},
        {"number": "204", "name": "Total lipid (fat)", "amount": 0.2, "unitName": "G"},
        {"number": "205", "name": "Carbohydrate, by difference", "amount": 3.89, "unitName": "G"},
        {"number": "291", "name": "Fiber, total dietary", "amount": 1.2, "unitName": "G"},
        {"number": "430", "name": "Vitamin K (phylloquinone)", "amount": 7.9, "unitName": "UG"},
        # Listed without an amount: not measured
        {"number": "418", "name": "Vitamin B-12", "unitName": "UG"},
    ],
}


class TestFetchUsdaFood:
    def test_requests_the_abridged_food_and_returns_it_in_search_shape(self, mocker):
        mock_get = mocker.patch("omni_pilot.enricher.requests.get")
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = ABRIDGED_TOMATO

        food = fetch_usda_food(170457, "fake-key")

        assert mock_get.call_args.args[0] == "https://api.nal.usda.gov/fdc/v1/food/170457"
        assert mock_get.call_args.kwargs["params"] == {"api_key": "fake-key", "format": "abridged"}
        assert food["fdcId"] == 170457
        assert food["description"] == "Tomatoes, red, ripe, raw, year round average"
        assert food["dataType"] == "SR Legacy"
        micros = extract_micros_from_usda(food)
        assert micros["vitamin_k_mcg"] == 7.9
        # A nutrient listed without an amount is not measured, never 0.0
        assert micros["b12_cobalamin_mcg"] is None

    def test_unknown_id_returns_none(self, mocker):
        mock_get = mocker.patch("omni_pilot.enricher.requests.get")
        mock_get.return_value.status_code = 404
        assert fetch_usda_food(999999999, "fake-key") is None

    def test_server_error_returns_none(self, mocker):
        mock_get = mocker.patch("omni_pilot.enricher.requests.get")
        mock_get.return_value.status_code = 500
        mock_get.return_value.raise_for_status.side_effect = requests.HTTPError("500")
        assert fetch_usda_food(170457, "fake-key") is None

    def test_request_failure_returns_none(self, mocker):
        mocker.patch("omni_pilot.enricher.requests.get", side_effect=requests.ConnectionError("offline"))
        assert fetch_usda_food(170457, "fake-key") is None

    def test_rate_limit_is_retried_once(self, mocker):
        limited = mocker.Mock(status_code=429)
        ok = mocker.Mock(status_code=200)
        ok.json.return_value = ABRIDGED_TOMATO
        mock_get = mocker.patch("omni_pilot.enricher.requests.get", side_effect=[limited, ok])
        mocker.patch("omni_pilot.enricher.time.sleep")

        assert fetch_usda_food(170457, "fake-key")["fdcId"] == 170457
        assert mock_get.call_count == 2


class TestGetUsdaFood:
    TOMATO_HIT = _usda_hit(170457, "Tomatoes, red, ripe, raw", 0.88, 0.2, 3.89, vitamin_a=42.0)

    def test_fetches_extracts_and_caches_by_id(self, mocker, tmp_path):
        db = TinyDB(str(tmp_path / "test_db.json"))
        mocker.patch("omni_pilot.enricher.fetch_usda_food", return_value=self.TOMATO_HIT)

        food = get_usda_food(170457, db, "fake-key")

        assert food["fdc_id"] == 170457
        assert food["usda_name"] == "Tomatoes, red, ripe, raw"
        assert food["usda_dataset"] == "SR Legacy"
        assert food["per_100g"]["vitamin_a_mcg"] == 42.0
        assert food["usda_macros"] == {"protein_g": 0.88, "fat_g": 0.2, "carbs_g": 3.89, "fiber_g": None}
        cached = db.table("usda_foods").all()
        assert len(cached) == 1
        assert cached[0]["fdc_id"] == 170457
        assert "last_updated" in cached[0]
        # The food-name cache (default table) is untouched
        assert db.all() == []

    def test_cached_food_is_used_without_fetching(self, mocker, tmp_path):
        db = TinyDB(str(tmp_path / "test_db.json"))
        mocker.patch("omni_pilot.enricher.fetch_usda_food", return_value=self.TOMATO_HIT)
        get_usda_food(170457, db, "fake-key")
        mock_fetch = mocker.patch("omni_pilot.enricher.fetch_usda_food")

        food = get_usda_food(170457, db, "fake-key")

        mock_fetch.assert_not_called()
        assert food["usda_name"] == "Tomatoes, red, ripe, raw"
        assert food["per_100g"]["vitamin_a_mcg"] == 42.0

    def test_failed_fetch_caches_nothing(self, mocker, tmp_path):
        db = TinyDB(str(tmp_path / "test_db.json"))
        mocker.patch("omni_pilot.enricher.fetch_usda_food", return_value=None)

        assert get_usda_food(170457, db, "fake-key") is None
        assert db.table("usda_foods").all() == []


class TestSearchUsda:
    def test_cleans_query_and_requests_a_full_page(self, mocker):
        mock_get = mocker.patch("omni_pilot.enricher.requests.get")
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = {"foods": [{"fdcId": 1}]}

        foods = search_usda("beef, ground, 80% lean meat / 20% fat, raw", "fake-key")

        params = mock_get.call_args.kwargs["params"]
        assert params["query"] == "beef, ground, 80% lean meat 20% fat, raw"
        assert params["pageSize"] == SEARCH_PAGE_SIZE
        assert foods == [{"fdcId": 1}]

    def test_request_failure_returns_none(self, mocker):
        # None is distinguishable from "no results" ([]): a failed search-2
        # must not be treated as "the food has no more candidates".
        mocker.patch(
            "omni_pilot.enricher.requests.get", side_effect=requests.ConnectionError("offline")
        )
        assert search_usda("honey", "fake-key") is None

    def test_query_empty_after_cleaning_makes_no_request(self, mocker):
        mock_get = mocker.patch("omni_pilot.enricher.requests.get")
        assert search_usda("( / )", "fake-key") == []
        mock_get.assert_not_called()


class TestCleanQuery:
    @pytest.mark.parametrize(("query", "expected"), [
        ("olives, ripe, canned (small-extra large)", "olives, ripe, canned small-extra large"),
        ("beef, ground, 80% lean meat / 20% fat, raw", "beef, ground, 80% lean meat 20% fat, raw"),
        ("honey", "honey"),
    ])
    def test_strips_characters_usda_rejects(self, query, expected):
        assert clean_query(query) == expected


class TestGetFoodCandidates:
    def test_query_hits_rank_before_head_word_hits_and_duplicates_drop(self, mocker):
        by_query = {
            "chickpeas, cooked, boiled": [_usda_hit(1, "Lentils, cooked"), _usda_hit(2, "Chickpeas, cooked")],
            "chickpea": [_usda_hit(2, "Chickpeas, cooked"), _usda_hit(3, "Chickpeas, canned")],
        }
        mocker.patch("omni_pilot.enricher.search_usda", side_effect=lambda q, key: by_query[q])

        candidates, complete = get_food_candidates("chickpeas, cooked, boiled", "fake-key")

        assert [c["fdc_id"] for c in candidates] == [1, 2, 3]
        assert [c["rank"] for c in candidates] == [0, 1, 2]
        assert candidates[0]["protein_g"] == 10.0
        assert candidates[0]["fiber_g"] is None
        assert complete is True

    def test_single_word_query_is_searched_once(self, mocker):
        mock_search = mocker.patch("omni_pilot.enricher.search_usda", return_value=[_usda_hit(1, "Honey")])
        get_food_candidates("Honey", "fake-key")
        mock_search.assert_called_once_with("Honey", "fake-key")

    def test_head_word_search_with_no_results_keeps_query_hits_and_is_complete(self, mocker):
        # search 2 ran and simply found nothing more — that is not a failure.
        by_query = {"chickpeas, canned": [_usda_hit(3, "Chickpeas, canned")], "chickpea": []}
        mocker.patch("omni_pilot.enricher.search_usda", side_effect=lambda q, key: by_query[q])
        candidates, complete = get_food_candidates("chickpeas, canned", "fake-key")
        assert [c["fdc_id"] for c in candidates] == [3]
        assert complete is True

    def test_failed_head_word_search_keeps_query_hits_but_marks_incomplete(self, mocker):
        # search 2 failed outright (None) — the set is missing data, not empty.
        by_query = {"chickpeas, canned": [_usda_hit(3, "Chickpeas, canned")], "chickpea": None}
        mocker.patch("omni_pilot.enricher.search_usda", side_effect=lambda q, key: by_query[q])
        candidates, complete = get_food_candidates("chickpeas, canned", "fake-key")
        assert [c["fdc_id"] for c in candidates] == [3]
        assert complete is False

    def test_failed_query_search_returns_nothing(self, mocker):
        mock_search = mocker.patch("omni_pilot.enricher.search_usda", return_value=None)
        assert get_food_candidates("chickpeas, canned", "fake-key") == ([], True)
        mock_search.assert_called_once()


class TestUsdaNutrientMap:
    def test_has_all_vitamin_keys(self):
        expected_keys = [
            "vitamin_a_mcg", "vitamin_c_mg", "vitamin_d_mcg", "vitamin_e_mg",
            "vitamin_k_mcg", "b1_thiamine_mg", "b2_riboflavin_mg",
            "b3_niacin_mg", "b5_pantothenic_acid_mg", "b6_pyridoxine_mg",
            "b12_cobalamin_mcg", "folate_mcg",
        ]
        for key in expected_keys:
            assert key in USDA_NUTRIENT_MAP

    def test_has_all_mineral_keys(self):
        expected_keys = [
            "calcium_mg", "iron_mg", "zinc_mg", "magnesium_mg",
            "manganese_mg", "phosphorus_mg", "potassium_mg",
            "selenium_mcg", "copper_mg", "sodium_mg",
        ]
        for key in expected_keys:
            assert key in USDA_NUTRIENT_MAP

    def test_has_amino_acid_keys(self):
        expected_keys = [
            "histidine_g", "isoleucine_g", "leucine_g", "lysine_g",
            "methionine_g", "cysteine_g", "phenylalanine_g", "tyrosine_g",
            "threonine_g", "tryptophan_g", "valine_g",
        ]
        for key in expected_keys:
            assert key in USDA_NUTRIENT_MAP


class TestExtractMicrosFromUsda:
    def test_extracts_nutrients_from_usda_response(self):
        usda_food = {
            "foodNutrients": [
                {"nutrientNumber": "320", "value": 149.0, "unitName": "UG"},
                {"nutrientNumber": "301", "value": 50.0, "unitName": "MG"},
                {"nutrientNumber": "303", "value": 1.19, "unitName": "MG"},
            ]
        }
        result = extract_micros_from_usda(usda_food)
        assert result["vitamin_a_mcg"] == 149.0
        assert result["calcium_mg"] == 50.0
        assert result["iron_mg"] == 1.19

    def test_missing_nutrients_are_none(self):
        usda_food = {"foodNutrients": []}
        result = extract_micros_from_usda(usda_food)
        assert result["vitamin_a_mcg"] is None


class TestGetFoodMatch:
    def _legacy_entry(self) -> dict:
        """A cache entry written before macro matching existed."""
        return {
            "original_name": "Boiled Eggs",
            "usda_query": "egg, whole, cooked, hard-boiled",
            "usda_name": "Egg, whole, raw",
            "per_100g": {"vitamin_a_mcg": 1.0},
            "confidence": "mapped",
        }

    def test_current_cache_entry_is_used_without_searching(self, mocker, tmp_path):
        db = TinyDB(str(tmp_path / "test_db.json"))
        db.insert({
            **self._legacy_entry(), "usda_name": "Egg, whole, cooked, hard-boiled",
            "per_100g": {"vitamin_a_mcg": 149.0}, "confidence": "good",
            "match_version": MATCH_VERSION, "macro_distance": 0.01,
        })
        mock_candidates = mocker.patch("omni_pilot.enricher.get_food_candidates")

        match = get_food_match("Boiled Eggs", "egg, whole, cooked, hard-boiled", EGG_LOGGED, db, "fake-key")

        assert match["per_100g"] == {"vitamin_a_mcg": 149.0}
        assert match["confidence"] == "good"
        mock_candidates.assert_not_called()

    def test_entry_stamped_newer_than_current_is_used_without_searching(self, mocker, tmp_path):
        db = TinyDB(str(tmp_path / "test_db.json"))
        db.insert({
            **self._legacy_entry(), "usda_name": "Egg, whole, cooked, hard-boiled",
            "per_100g": {"vitamin_a_mcg": 149.0}, "confidence": "good",
            "match_version": MATCH_VERSION + 1, "macro_distance": 0.01,
        })
        mock_candidates = mocker.patch("omni_pilot.enricher.get_food_candidates")

        match = get_food_match("Boiled Eggs", "egg, whole, cooked, hard-boiled", EGG_LOGGED, db, "fake-key")

        assert match["per_100g"] == {"vitamin_a_mcg": 149.0}
        mock_candidates.assert_not_called()

    def test_picks_by_macros_and_caches_the_match(self, mocker, tmp_path):
        db = TinyDB(str(tmp_path / "test_db.json"))
        mocker.patch("omni_pilot.enricher.get_food_candidates", return_value=([
            _candidate(1, "Egg, whole, raw, frozen", 12.3, 9.5, 0.8, rank=0),
            _candidate(2, "Egg, whole, cooked, hard-boiled", 12.6, 10.6, 1.1, rank=1),
        ], True))

        match = get_food_match("Boiled Eggs", "egg, whole, cooked, hard-boiled", EGG_LOGGED, db, "fake-key")

        assert match["usda_name"] == "Egg, whole, cooked, hard-boiled"
        assert match["confidence"] == "good"
        assert match["per_100g"]["vitamin_a_mcg"] == 149.0
        [entry] = db.all()
        assert entry["usda_fdc_id"] == 2
        assert entry["match_version"] == MATCH_VERSION
        assert entry["macro_distance"] == pytest.approx(0.0)
        assert entry["usda_macros"] == {"protein_g": 12.6, "fat_g": 10.6, "carbs_g": 1.1, "fiber_g": None}

    def test_legacy_entry_is_repicked_and_replaced(self, mocker, tmp_path):
        db = TinyDB(str(tmp_path / "test_db.json"))
        db.insert(self._legacy_entry())
        mocker.patch("omni_pilot.enricher.get_food_candidates", return_value=([
            _candidate(2, "Egg, whole, cooked, hard-boiled", 12.6, 10.6, 1.1),
        ], True))

        match = get_food_match("Boiled Eggs", "egg, whole, cooked, hard-boiled", EGG_LOGGED, db, "fake-key")

        assert match["usda_name"] == "Egg, whole, cooked, hard-boiled"
        # Replaced in place: a second entry would shadow or be shadowed by cached[0].
        [entry] = db.all()
        assert entry["match_version"] == MATCH_VERSION
        assert entry["usda_name"] == "Egg, whole, cooked, hard-boiled"

    def test_failed_repick_keeps_legacy_entry_as_unverified(self, mocker, tmp_path):
        db = TinyDB(str(tmp_path / "test_db.json"))
        db.insert(self._legacy_entry())
        mocker.patch("omni_pilot.enricher.get_food_candidates", return_value=([], True))

        match = get_food_match("Boiled Eggs", "egg, whole, cooked, hard-boiled", EGG_LOGGED, db, "fake-key")

        assert match["per_100g"] == {"vitamin_a_mcg": 1.0}
        assert match["confidence"] == "unverified"
        # Not stamped: the next run tries again.
        [entry] = db.all()
        assert "match_version" not in entry

    def test_changed_query_refetches(self, mocker, tmp_path):
        db = TinyDB(str(tmp_path / "test_db.json"))
        db.insert(self._legacy_entry())
        mocker.patch("omni_pilot.enricher.get_food_candidates", return_value=([], True))

        match = get_food_match("Boiled Eggs", "egg, whole, raw", EGG_LOGGED, db, "fake-key")

        # The old entry belonged to a different query, so it is not kept.
        assert match is None
        assert db.all() == []

    def test_returns_none_when_usda_has_no_results(self, mocker, tmp_path):
        db = TinyDB(str(tmp_path / "test_db.json"))
        mocker.patch("omni_pilot.enricher.get_food_candidates", return_value=([], True))
        assert get_food_match("Unknown Food", "unknown food", None, db, "fake-key") is None

    def test_incomplete_candidate_set_caches_the_pick_without_match_version(self, mocker, tmp_path):
        # search 2 failed (e.g. a USDA 503): the pick was made from a partial
        # set, so it must not be stamped current, or it would never be retried.
        db = TinyDB(str(tmp_path / "test_db.json"))
        mocker.patch("omni_pilot.enricher.get_food_candidates", return_value=([
            _candidate(1, "Lentils, sprouted, raw", 9.0, 0.5, 19.5),
        ], False))

        match = get_food_match("Linsen", "lentils, sprouted, raw", None, db, "fake-key")

        assert match["usda_name"] == "Lentils, sprouted, raw"
        [entry] = db.all()
        assert "match_version" not in entry
        assert count_outdated_matches(["Linsen"], {"Linsen": "lentils, sprouted, raw"}, db) == 1

    def test_a_later_complete_pick_stamps_a_previously_incomplete_entry(self, mocker, tmp_path):
        db = TinyDB(str(tmp_path / "test_db.json"))
        mocker.patch("omni_pilot.enricher.get_food_candidates", return_value=([
            _candidate(1, "Lentils, sprouted, raw", 9.0, 0.5, 19.5),
        ], False))
        get_food_match("Linsen", "lentils, sprouted, raw", None, db, "fake-key")

        mocker.patch("omni_pilot.enricher.get_food_candidates", return_value=([
            _candidate(2, "Lentils, cooked, boiled, without salt", 9.0, 0.4, 20.1),
        ], True))
        match = get_food_match("Linsen", "lentils, sprouted, raw", None, db, "fake-key")

        assert match["usda_name"] == "Lentils, cooked, boiled, without salt"
        [entry] = db.all()
        assert entry["match_version"] == MATCH_VERSION
        assert count_outdated_matches(["Linsen"], {"Linsen": "lentils, sprouted, raw"}, db) == 0


class TestCountOutdatedMatches:
    def test_counts_only_same_query_entries_from_older_versions(self, tmp_path):
        db = TinyDB(str(tmp_path / "test_db.json"))
        db.insert({"original_name": "Legacy", "usda_query": "egg", "per_100g": {}})
        db.insert({"original_name": "Current", "usda_query": "milk", "per_100g": {}, "match_version": MATCH_VERSION})
        db.insert({"original_name": "Remapped", "usda_query": "old query", "per_100g": {}})
        mappings = {"Legacy": "egg", "Current": "milk", "Remapped": "new query", "Water": "skip"}

        count = count_outdated_matches(["Legacy", "Current", "Remapped", "Water", "New"], mappings, db)

        assert count == 1

    def test_an_entry_stamped_newer_than_current_is_not_outdated(self, tmp_path):
        # A device running newer matching code may stamp match_version ahead
        # of this build's MATCH_VERSION; a lagging device must not re-pick it.
        db = TinyDB(str(tmp_path / "test_db.json"))
        db.insert({
            "original_name": "Newer", "usda_query": "egg", "per_100g": {},
            "match_version": MATCH_VERSION + 1,
        })
        count = count_outdated_matches(["Newer"], {"Newer": "egg"}, db)
        assert count == 0


class TestEnrichAllFoods:
    def test_skips_foods_mapped_to_skip(self, mocker, tmp_path):
        db = TinyDB(str(tmp_path / "test_db.json"))
        mappings = {"Quick Add": "skip", "Boiled Eggs": "boiled egg"}
        mock_match = mocker.patch("omni_pilot.enricher.get_food_match")
        mock_match.return_value = {
            "per_100g": {"vitamin_a_mcg": 149.0}, "usda_name": "Egg", "confidence": "good", "macro_distance": 0.01,
        }

        result = enrich_all_foods(["Quick Add", "Boiled Eggs"], mappings, {}, db, "fake-key")

        assert result["skipped"] == {"Quick Add"}
        assert result["unresolved"] == set()
        assert "Quick Add" not in result["profiles"]
        assert result["profiles"]["Boiled Eggs"] == {"vitamin_a_mcg": 149.0}
        # get_food_match should only be called for Boiled Eggs
        mock_match.assert_called_once()

    def test_failed_usda_lookup_is_unresolved_not_skipped(self, mocker, tmp_path):
        # Only the network seam is faked, so the real get_food_match and
        # enrich_all_foods produce the shape the pipeline actually sees.
        db = TinyDB(str(tmp_path / "test_db.json"))
        mocker.patch("omni_pilot.enricher.search_usda", return_value=[])

        result = enrich_all_foods(["Lachs"], {"Lachs": "salmon"}, {}, db, "fake-key")

        assert result["unresolved"] == {"Lachs"}
        assert result["skipped"] == set()
        assert "Lachs" not in result["profiles"]

    def test_profiles_never_contain_none(self, mocker, tmp_path):
        db = TinyDB(str(tmp_path / "test_db.json"))
        mocker.patch("omni_pilot.enricher.search_usda", return_value=[])
        mappings = {"Quick Add": "skip", "Lachs": "salmon"}

        result = enrich_all_foods(["Quick Add", "Lachs"], mappings, {}, db, "fake-key")

        # Neither outcome may leak into profiles as a None value — that
        # overloading is what made the two indistinguishable.
        assert None not in result["profiles"].values()
        assert result["profiles"] == {}

    def test_uses_original_name_and_logged_macros(self, mocker, tmp_path):
        db = TinyDB(str(tmp_path / "test_db.json"))
        mock_match = mocker.patch("omni_pilot.enricher.get_food_match")
        mock_match.return_value = {
            "per_100g": {"vitamin_a_mcg": 149.0}, "usda_name": "Egg", "confidence": "good", "macro_distance": 0.01,
        }

        enrich_all_foods(["Boiled Eggs"], {"Boiled Eggs": ""}, {"Boiled Eggs": EGG_LOGGED}, db, "fake-key")

        mock_match.assert_called_once_with("Boiled Eggs", "Boiled Eggs", EGG_LOGGED, db, "fake-key")

    def test_weak_match_is_counted_and_listed_as_low_confidence(self, mocker, tmp_path):
        db = TinyDB(str(tmp_path / "test_db.json"))
        mocker.patch("omni_pilot.enricher.get_food_candidates", return_value=([
            _candidate(1, "Cheese, feta", 14.2, 21.5, 3.9),
        ], True))
        # A feta salad: far fewer calories than feta itself.
        logged = {"Salzlakenkaese salat": {"kcal": 54.0, "protein_g": 4.0, "fat_g": 3.0, "carbs_g": 2.0}}

        result = enrich_all_foods(
            ["Salzlakenkaese salat"], {"Salzlakenkaese salat": "cheese, feta"}, logged, db, "fake-key"
        )

        assert "Salzlakenkaese salat" in result["profiles"]
        weak = result["low_confidence"]["Salzlakenkaese salat"]
        assert weak["usda_name"] == "Cheese, feta"
        assert weak["macro_distance"] > 1.0

    def test_good_match_is_not_low_confidence(self, mocker, tmp_path):
        db = TinyDB(str(tmp_path / "test_db.json"))
        mocker.patch("omni_pilot.enricher.get_food_candidates", return_value=([
            _candidate(2, "Egg, whole, cooked, hard-boiled", 12.6, 10.6, 1.1),
        ], True))

        result = enrich_all_foods(
            ["Boiled Eggs"], {"Boiled Eggs": "egg, whole, cooked, hard-boiled"}, {"Boiled Eggs": EGG_LOGGED},
            db, "fake-key",
        )

        assert result["low_confidence"] == {}


TOMATO_HIT = _usda_hit(1, "Tomatoes, red, ripe, raw", 0.88, 0.2, 3.89, vitamin_a=42.0)
MOZZARELLA_HIT = _usda_hit(2, "Cheese, mozzarella, whole milk", 22.2, 22.1, 2.4, vitamin_a=179.0)
CAPRESE_FOODS = {
    "recipes": {
        "caprese": {
            "name": "caprese",
            "foods": ["Salat Caprese", "Caprese to go"],
            "ingredients": [{"fdc_id": 1, "share": 0.75}, {"fdc_id": 2, "share": 0.25}],
        },
    },
    "by_food": {"Salat Caprese": "caprese", "Caprese to go": "caprese"},
}
CAPRESE_LOGGED = {"kcal": 74.0, "protein_g": 4.9, "fat_g": 4.2, "carbs_g": 3.0}


class TestEnrichCustomFoods:
    def _fetch(self, mocker, foods: dict[int, dict]):
        return mocker.patch(
            "omni_pilot.enricher.fetch_usda_food", side_effect=lambda fdc_id, api_key: foods.get(fdc_id),
        )

    def test_custom_food_is_built_from_its_recipe_without_searching(self, mocker, tmp_path):
        db = TinyDB(str(tmp_path / "test_db.json"))
        self._fetch(mocker, {1: TOMATO_HIT, 2: MOZZARELLA_HIT})
        mock_search = mocker.patch("omni_pilot.enricher.search_usda")

        result = enrich_all_foods(["Salat Caprese"], {}, {}, db, "fake-key", custom_foods=CAPRESE_FOODS)

        assert result["profiles"] == {}
        match = result["custom"]["Salat Caprese"]
        assert match["recipe"] == "caprese"
        assert [(p["share"], p["fdc_id"], p["usda_name"]) for p in match["parts"]] == [
            (0.75, 1, "Tomatoes, red, ripe, raw"),
            (0.25, 2, "Cheese, mozzarella, whole milk"),
        ]
        assert match["parts"][1]["per_100g"]["vitamin_a_mcg"] == 179.0
        mock_search.assert_not_called()

    def test_recipe_overrides_skip_and_usda_mappings(self, mocker, tmp_path):
        db = TinyDB(str(tmp_path / "test_db.json"))
        self._fetch(mocker, {1: TOMATO_HIT, 2: MOZZARELLA_HIT})
        mock_match = mocker.patch("omni_pilot.enricher.get_food_match")
        mappings = {"Salat Caprese": "skip", "Caprese to go": "caprese salad"}

        result = enrich_all_foods(
            ["Caprese to go", "Salat Caprese"], mappings, {}, db, "fake-key", custom_foods=CAPRESE_FOODS,
        )

        assert set(result["custom"]) == {"Salat Caprese", "Caprese to go"}
        assert result["skipped"] == set()
        mock_match.assert_not_called()

    def test_unavailable_ingredient_makes_every_food_of_the_recipe_unresolved(self, mocker, tmp_path):
        db = TinyDB(str(tmp_path / "test_db.json"))
        mock_fetch = self._fetch(mocker, {1: TOMATO_HIT})  # FDC ID 2 cannot be fetched

        result = enrich_all_foods(
            ["Caprese to go", "Salat Caprese"], {}, {}, db, "fake-key", custom_foods=CAPRESE_FOODS,
        )

        assert result["unresolved"] == {"Salat Caprese", "Caprese to go"}
        assert result["custom"] == {}
        # The failing ID is asked for once per run, not once per food
        assert sorted(call.args[0] for call in mock_fetch.call_args_list) == [1, 2]

    def test_shared_ingredients_are_fetched_once(self, mocker, tmp_path):
        db = TinyDB(str(tmp_path / "test_db.json"))
        mock_fetch = self._fetch(mocker, {1: TOMATO_HIT, 2: MOZZARELLA_HIT})
        custom = {
            "recipes": {
                **CAPRESE_FOODS["recipes"],
                "tomato_salad": {
                    "name": "tomato_salad", "foods": ["Tomatensalat"], "ingredients": [{"fdc_id": 1, "share": 1.0}],
                },
            },
            "by_food": {**CAPRESE_FOODS["by_food"], "Tomatensalat": "tomato_salad"},
        }

        enrich_all_foods(
            ["Caprese to go", "Salat Caprese", "Tomatensalat"], {}, {}, db, "fake-key", custom_foods=custom,
        )

        assert mock_fetch.call_count == 2

    def test_macro_check_is_per_food(self, mocker, tmp_path):
        db = TinyDB(str(tmp_path / "test_db.json"))
        self._fetch(mocker, {1: TOMATO_HIT, 2: MOZZARELLA_HIT})

        result = enrich_all_foods(
            ["Caprese to go", "Salat Caprese"], {}, {"Salat Caprese": CAPRESE_LOGGED}, db, "fake-key",
            custom_foods=CAPRESE_FOODS,
        )

        # Recipe per 100 g: P 6.21, F 5.675, C 3.5175 (the fixtures have no fiber)
        expected = (4 * 1.31 + 9 * 1.475 + 4 * 0.5175) / 74.0
        assert result["custom"]["Salat Caprese"]["macro_distance"] == pytest.approx(expected)
        # No logged macros: not checked
        assert result["custom"]["Caprese to go"]["macro_distance"] is None

    def test_badly_matching_recipe_is_counted_but_not_low_confidence(self, mocker, tmp_path):
        db = TinyDB(str(tmp_path / "test_db.json"))
        self._fetch(mocker, {1: TOMATO_HIT, 2: MOZZARELLA_HIT})
        logged = {"Salat Caprese": {"kcal": 300.0, "protein_g": 30.0, "fat_g": 20.0, "carbs_g": 1.0}}

        result = enrich_all_foods(["Salat Caprese"], {}, logged, db, "fake-key", custom_foods=CAPRESE_FOODS)

        assert result["custom"]["Salat Caprese"]["macro_distance"] > GOOD_DISTANCE
        assert result["low_confidence"] == {}

    def test_without_custom_foods_nothing_is_custom(self, mocker, tmp_path):
        db = TinyDB(str(tmp_path / "test_db.json"))
        mocker.patch("omni_pilot.enricher.search_usda", return_value=[])

        result = enrich_all_foods(["Salat Caprese"], {}, {}, db, "fake-key")

        assert result["custom"] == {}
        assert result["unresolved"] == {"Salat Caprese"}
