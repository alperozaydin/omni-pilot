from __future__ import annotations

import pytest
import yaml

from omni_pilot.parser import parse_food_log, extract_unique_foods, generate_food_mappings


class TestParseFoodLog:
    def test_parses_real_xlsx(self):
        """Integration test with the actual MacroFactor export."""
        entries = parse_food_log("data/MacroFactor-20260809145742.xlsx")
        assert len(entries) > 0
        # Check first entry structure
        entry = entries[0]
        assert "date" in entry
        assert "food_name" in entry
        assert "serving_qty" in entry
        assert "serving_weight_g" in entry
        assert "total_weight_g" in entry
        assert "calories_kcal" in entry

    def test_computes_total_weight(self):
        entries = parse_food_log("data/MacroFactor-20260809145742.xlsx")
        # Find "Boiled Eggs" entry: 3 servings × 56g = 168g
        egg_entries = [e for e in entries if e["food_name"] == "Boiled Eggs"]
        assert len(egg_entries) > 0
        first_egg = egg_entries[0]
        assert first_egg["total_weight_g"] == pytest.approx(
            first_egg["serving_qty"] * first_egg["serving_weight_g"]
        )

    def test_skips_entries_with_no_food_name(self):
        entries = parse_food_log("data/MacroFactor-20260809145742.xlsx")
        for entry in entries:
            assert entry["food_name"] is not None
            assert entry["food_name"] != ""


class TestExtractUniqueFoods:
    def test_returns_sorted_unique_names(self):
        entries = [
            {"food_name": "Banana", "date": "2026-01-01"},
            {"food_name": "Apple", "date": "2026-01-01"},
            {"food_name": "Banana", "date": "2026-01-02"},
        ]
        result = extract_unique_foods(entries)
        assert result == ["Apple", "Banana"]

    def test_empty_entries(self):
        assert extract_unique_foods([]) == []


class TestGenerateFoodMappings:
    def test_creates_new_mapping_file(self, tmp_path):
        output_path = str(tmp_path / "food_mappings.yaml")
        foods = ["Banana", "Haferflocken", "Quick Add"]
        generate_food_mappings(foods, {}, output_path)

        with open(output_path) as f:
            data = yaml.safe_load(f)
        assert "mappings" in data
        assert data["mappings"]["Banana"] == ""
        assert data["mappings"]["Haferflocken"] == ""
        assert data["mappings"]["Quick Add"] == ""

    def test_preserves_existing_mappings(self, tmp_path):
        output_path = str(tmp_path / "food_mappings.yaml")
        foods = ["Banana", "Haferflocken", "New Food"]
        existing = {"Banana": "banana", "Haferflocken": "rolled oats"}
        generate_food_mappings(foods, existing, output_path)

        with open(output_path) as f:
            data = yaml.safe_load(f)
        assert data["mappings"]["Banana"] == "banana"
        assert data["mappings"]["Haferflocken"] == "rolled oats"
        assert data["mappings"]["New Food"] == ""
