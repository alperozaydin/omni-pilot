from __future__ import annotations

import pytest
import requests
from tinydb import TinyDB

from omni_pilot.enricher import (
    SEARCH_PAGE_SIZE,
    USDA_NUTRIENT_MAP,
    clean_query,
    enrich_all_foods,
    extract_micros_from_usda,
    get_food_candidates,
    get_food_micros,
    search_usda,
)


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

    def test_request_failure_returns_empty_list(self, mocker):
        mocker.patch(
            "omni_pilot.enricher.requests.get", side_effect=requests.ConnectionError("offline")
        )
        assert search_usda("honey", "fake-key") == []

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

        candidates = get_food_candidates("chickpeas, cooked, boiled", "fake-key")

        assert [c["fdc_id"] for c in candidates] == [1, 2, 3]
        assert [c["rank"] for c in candidates] == [0, 1, 2]
        assert candidates[0]["protein_g"] == 10.0
        assert candidates[0]["fiber_g"] is None

    def test_single_word_query_is_searched_once(self, mocker):
        mock_search = mocker.patch("omni_pilot.enricher.search_usda", return_value=[_usda_hit(1, "Honey")])
        get_food_candidates("Honey", "fake-key")
        mock_search.assert_called_once_with("Honey", "fake-key")

    def test_failed_head_word_search_keeps_query_hits(self, mocker):
        by_query = {"chickpeas, canned": [_usda_hit(3, "Chickpeas, canned")], "chickpea": []}
        mocker.patch("omni_pilot.enricher.search_usda", side_effect=lambda q, key: by_query[q])
        assert [c["fdc_id"] for c in get_food_candidates("chickpeas, canned", "fake-key")] == [3]

    def test_failed_query_search_returns_nothing(self, mocker):
        mock_search = mocker.patch("omni_pilot.enricher.search_usda", return_value=[])
        assert get_food_candidates("chickpeas, canned", "fake-key") == []
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


class TestGetFoodMicros:
    def test_returns_cached_data(self, tmp_path):
        db = TinyDB(str(tmp_path / "test_db.json"))
        db.insert({
            "original_name": "Boiled Eggs",
            "usda_query": "boiled egg",
            "per_100g": {"vitamin_a_mcg": 149.0, "calcium_mg": 50.0},
            "confidence": "direct",
        })
        result = get_food_micros("Boiled Eggs", "boiled egg", db, "fake-key")
        assert result["vitamin_a_mcg"] == 149.0

    def test_queries_usda_when_not_cached(self, mocker, tmp_path):
        db = TinyDB(str(tmp_path / "test_db.json"))
        mock_search = mocker.patch("omni_pilot.enricher.search_usda")
        mock_search.return_value = [{
            "description": "Egg, whole, cooked, hard-boiled",
            "fdcId": 173424,
            "dataType": "SR Legacy",
            "foodNutrients": [
                {"nutrientNumber": "320", "value": 149.0, "unitName": "UG"},
            ],
        }]
        result = get_food_micros("Boiled Eggs", "boiled egg", db, "fake-key")
        assert result is not None
        assert result["vitamin_a_mcg"] == 149.0
        mock_search.assert_called_once_with("boiled egg", "fake-key")

    def test_returns_none_when_usda_has_no_results(self, mocker, tmp_path):
        db = TinyDB(str(tmp_path / "test_db.json"))
        mock_search = mocker.patch("omni_pilot.enricher.search_usda")
        mock_search.return_value = []
        result = get_food_micros("Unknown Food", "unknown food", db, "fake-key")
        assert result is None


class TestEnrichAllFoods:
    def test_skips_foods_mapped_to_skip(self, mocker, tmp_path):
        db = TinyDB(str(tmp_path / "test_db.json"))
        mappings = {"Quick Add": "skip", "Boiled Eggs": "boiled egg"}
        mock_get = mocker.patch("omni_pilot.enricher.get_food_micros")
        mock_get.return_value = {"vitamin_a_mcg": 149.0}

        result = enrich_all_foods(
            ["Quick Add", "Boiled Eggs"], mappings, db, "fake-key"
        )
        assert result["skipped"] == {"Quick Add"}
        assert result["unresolved"] == set()
        assert "Quick Add" not in result["profiles"]
        assert result["profiles"]["Boiled Eggs"] == {"vitamin_a_mcg": 149.0}
        # get_food_micros should only be called for Boiled Eggs
        mock_get.assert_called_once()

    def test_failed_usda_lookup_is_unresolved_not_skipped(self, mocker, tmp_path):
        # Only the network seam is faked, so the real get_food_micros and
        # enrich_all_foods produce the shape the pipeline actually sees.
        db = TinyDB(str(tmp_path / "test_db.json"))
        mocker.patch("omni_pilot.enricher.search_usda", return_value=[])

        result = enrich_all_foods(["Lachs"], {"Lachs": "salmon"}, db, "fake-key")

        assert result["unresolved"] == {"Lachs"}
        assert result["skipped"] == set()
        assert "Lachs" not in result["profiles"]

    def test_profiles_never_contain_none(self, mocker, tmp_path):
        db = TinyDB(str(tmp_path / "test_db.json"))
        mocker.patch("omni_pilot.enricher.search_usda", return_value=[])
        mappings = {"Quick Add": "skip", "Lachs": "salmon"}

        result = enrich_all_foods(["Quick Add", "Lachs"], mappings, db, "fake-key")

        # Neither outcome may leak into profiles as a None value — that
        # overloading is what made the two indistinguishable.
        assert None not in result["profiles"].values()
        assert result["profiles"] == {}

    def test_uses_original_name_when_mapping_empty(self, mocker, tmp_path):
        db = TinyDB(str(tmp_path / "test_db.json"))
        mappings = {"Boiled Eggs": ""}
        mock_get = mocker.patch("omni_pilot.enricher.get_food_micros")
        mock_get.return_value = {"vitamin_a_mcg": 149.0}

        enrich_all_foods(["Boiled Eggs"], mappings, db, "fake-key")
        mock_get.assert_called_once_with("Boiled Eggs", "Boiled Eggs", db, "fake-key")
