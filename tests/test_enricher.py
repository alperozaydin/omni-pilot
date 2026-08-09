from __future__ import annotations

import json
import pytest
from unittest.mock import patch, MagicMock
from tinydb import TinyDB

from omni_pilot.enricher import (
    USDA_NUTRIENT_MAP,
    search_usda,
    extract_micros_from_usda,
    get_food_micros,
    enrich_all_foods,
)


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

    @patch("omni_pilot.enricher.search_usda")
    def test_queries_usda_when_not_cached(self, mock_search, tmp_path):
        db = TinyDB(str(tmp_path / "test_db.json"))
        mock_search.return_value = {
            "description": "Egg, whole, cooked, hard-boiled",
            "fdcId": 173424,
            "dataType": "SR Legacy",
            "foodNutrients": [
                {"nutrientNumber": "320", "value": 149.0, "unitName": "UG"},
            ],
        }
        result = get_food_micros("Boiled Eggs", "boiled egg", db, "fake-key")
        assert result is not None
        assert result["vitamin_a_mcg"] == 149.0
        mock_search.assert_called_once_with("boiled egg", "fake-key")

    @patch("omni_pilot.enricher.search_usda")
    def test_returns_none_when_usda_has_no_results(self, mock_search, tmp_path):
        db = TinyDB(str(tmp_path / "test_db.json"))
        mock_search.return_value = None
        result = get_food_micros("Unknown Food", "unknown food", db, "fake-key")
        assert result is None


class TestEnrichAllFoods:
    @patch("omni_pilot.enricher.get_food_micros")
    def test_skips_foods_mapped_to_skip(self, mock_get, tmp_path):
        db = TinyDB(str(tmp_path / "test_db.json"))
        mappings = {"Quick Add": "skip", "Boiled Eggs": "boiled egg"}
        mock_get.return_value = {"vitamin_a_mcg": 149.0}

        result = enrich_all_foods(
            ["Quick Add", "Boiled Eggs"], mappings, db, "fake-key"
        )
        assert result["Quick Add"] is None
        assert result["Boiled Eggs"] == {"vitamin_a_mcg": 149.0}
        # get_food_micros should only be called for Boiled Eggs
        mock_get.assert_called_once()

    @patch("omni_pilot.enricher.get_food_micros")
    def test_uses_original_name_when_mapping_empty(self, mock_get, tmp_path):
        db = TinyDB(str(tmp_path / "test_db.json"))
        mappings = {"Boiled Eggs": ""}
        mock_get.return_value = {"vitamin_a_mcg": 149.0}

        enrich_all_foods(["Boiled Eggs"], mappings, db, "fake-key")
        mock_get.assert_called_once_with("Boiled Eggs", "Boiled Eggs", db, "fake-key")
