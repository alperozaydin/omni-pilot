from __future__ import annotations

import os

import pytest
import yaml

from omni_pilot.config import (
    get_nutrient_target,
    load_food_mappings,
    load_reference_ranges,
    load_settings,
    load_supplements,
)


def _write_yaml(path: str, data: dict) -> None:
    with open(path, "w") as f:
        yaml.dump(data, f)


class TestLoadSettings:
    def test_loads_valid_settings(self, tmp_path):
        settings_file = tmp_path / "settings.yaml"
        _write_yaml(str(settings_file), {
            "usda_api_key": "test-key",
            "output": {"show_amino_acids": True, "show_ok_nutrients": True},
        })
        result = load_settings(str(settings_file))
        assert result["usda_api_key"] == "test-key"
        assert result["output"]["show_amino_acids"] is True

    def test_raises_on_missing_file(self):
        with pytest.raises(FileNotFoundError):
            load_settings("/nonexistent/settings.yaml")


class TestLoadReferenceRanges:
    def test_loads_nutrients(self, tmp_path):
        ref_file = tmp_path / "reference_ranges.yaml"
        _write_yaml(str(ref_file), {
            "demographic": {"sex": "male", "age_group": "19-50", "body_weight_kg": 75},
            "nutrients": {
                "vitamin_a_mcg": {
                    "name": "Vitamin A", "unit": "mcg", "type": "rda",
                    "rda": 900, "ul": 3000, "source": "NIH",
                },
                "histidine_g": {
                    "name": "Histidine", "unit": "g", "type": "who_per_kg",
                    "mg_per_kg": 10, "safe_mg_per_kg": 12, "ul": None,
                    "source": "FAO/WHO/UNU 2007",
                },
            },
        })
        result = load_reference_ranges(str(ref_file))
        assert result["nutrients"]["vitamin_a_mcg"]["rda"] == 900
        assert result["demographic"]["body_weight_kg"] == 75

    def test_raises_on_missing_file(self):
        with pytest.raises(FileNotFoundError):
            load_reference_ranges("/nonexistent/ref.yaml")


class TestLoadFoodMappings:
    def test_loads_existing_mappings(self, tmp_path):
        mappings_file = tmp_path / "food_mappings.yaml"
        _write_yaml(str(mappings_file), {
            "mappings": {"Haferflocken": "rolled oats", "Quick Add": "skip"},
        })
        result = load_food_mappings(str(mappings_file))
        assert result["Haferflocken"] == "rolled oats"
        assert result["Quick Add"] == "skip"

    def test_returns_empty_dict_when_file_missing(self, tmp_path):
        result = load_food_mappings(str(tmp_path / "nonexistent.yaml"))
        assert result == {}


class TestGetNutrientTarget:
    def test_rda_nutrient(self):
        ref_ranges = {
            "demographic": {"body_weight_kg": 75},
            "nutrients": {
                "vitamin_a_mcg": {
                    "type": "rda", "rda": 900, "ul": 3000,
                },
            },
        }
        target, ul, target_type = get_nutrient_target("vitamin_a_mcg", ref_ranges)
        assert target == 900
        assert ul == 3000
        assert target_type == "rda"

    def test_ai_nutrient(self):
        ref_ranges = {
            "demographic": {"body_weight_kg": 75},
            "nutrients": {
                "potassium_mg": {
                    "type": "ai", "ai": 3400, "ul": None,
                },
            },
        }
        target, ul, target_type = get_nutrient_target("potassium_mg", ref_ranges)
        assert target == 3400
        assert ul is None
        assert target_type == "ai"

    def test_who_per_kg_nutrient(self):
        ref_ranges = {
            "demographic": {"body_weight_kg": 75},
            "nutrients": {
                "histidine_g": {
                    "type": "who_per_kg", "safe_mg_per_kg": 12, "ul": None,
                },
            },
        }
        target, ul, target_type = get_nutrient_target("histidine_g", ref_ranges)
        # 12 mg/kg * 75 kg = 900 mg = 0.9 g
        assert target == pytest.approx(0.9)
        assert target_type == "who_per_kg"

    def test_who_per_kg_missing_body_weight(self):
        ref_ranges = {
            "demographic": {"body_weight_kg": None},
            "nutrients": {
                "histidine_g": {
                    "type": "who_per_kg", "safe_mg_per_kg": 12, "ul": None,
                },
            },
        }
        target, ul, target_type = get_nutrient_target("histidine_g", ref_ranges)
        assert target is None


class TestLoadSupplements:
    def test_loads_existing_supplements(self, tmp_path):
        supplements_file = tmp_path / "supplements.yaml"
        with open(supplements_file, "w") as f:
            f.write("vitamin_d_mcg: 25.0\nomega3_epa_dha_mg: 1000.0\n")
        
        result = load_supplements(str(supplements_file))
        assert result["vitamin_d_mcg"] == 25.0
        assert result["omega3_epa_dha_mg"] == 1000.0

    def test_returns_empty_dict_when_file_missing(self, tmp_path):
        result = load_supplements(str(tmp_path / "nonexistent.yaml"))
        assert result == {}


class TestResolvePath:
    def test_settings_yaml_takes_priority_over_default(self, tmp_path):
        from omni_pilot.config import resolve_path
        settings_db = str(tmp_path / "settings_db.json")
        settings = {"database_path": settings_db}
        result = resolve_path("database_path", "db/food_db.json", settings)
        assert result == settings_db

    def test_tilde_expansion_in_settings(self):
        from omni_pilot.config import resolve_path
        settings = {"database_path": "~/my_test_dir/food_db.json"}
        result = resolve_path("database_path", "db/food_db.json", settings)
        expected = os.path.expanduser("~/my_test_dir/food_db.json")
        assert result == expected

    def test_fallback_to_default_when_no_setting(self):
        from omni_pilot.config import resolve_path
        result = resolve_path("database_path", "db/food_db.json", settings={})
        assert result == "db/food_db.json"

    def test_fallback_to_default_when_settings_none(self):
        from omni_pilot.config import resolve_path
        result = resolve_path("database_path", "db/food_db.json", settings=None)
        assert result == "db/food_db.json"
