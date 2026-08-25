from __future__ import annotations

import pytest
import yaml

from omni_pilot.parser import extract_unique_foods, generate_food_mappings, parse_food_log


class TestParseFoodLog:
    def test_parses_real_xlsx(self):
        """Integration test with the actual MacroFactor export."""
        entries = parse_food_log("data/MacroFactor-example.xlsx")
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
        entries = parse_food_log("data/MacroFactor-example.xlsx")
        # Find "Boiled Eggs" entry: 3 servings × 56g = 168g
        egg_entries = [e for e in entries if e["food_name"] == "Boiled Eggs"]
        assert len(egg_entries) > 0
        first_egg = egg_entries[0]
        assert first_egg["total_weight_g"] == pytest.approx(
            first_egg["serving_qty"] * first_egg["serving_weight_g"]
        )

    def test_skips_entries_with_no_food_name(self):
        entries = parse_food_log("data/MacroFactor-example.xlsx")
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
    def test_creates_empty_mappings_when_no_valid_mappings(self, tmp_path):
        output_path = str(tmp_path / "food_mappings.yaml")
        foods = ["Banana", "Haferflocken", "Quick Add"]
        generate_food_mappings(foods, {}, output_path)

        with open(output_path) as f:
            data = yaml.safe_load(f)
        assert "mappings" in data
        assert data["mappings"] == {}

    def test_preserves_existing_valid_mappings_only(self, tmp_path):
        output_path = str(tmp_path / "food_mappings.yaml")
        foods = ["Banana", "Haferflocken", "New Food"]
        existing = {"Banana": "banana", "Haferflocken": "rolled oats", "New Food": ""}
        result = generate_food_mappings(foods, existing, output_path)
        assert result is True

        with open(output_path) as f:
            data = yaml.safe_load(f)
        assert data["mappings"] == {
            "Banana": "banana",
            "Haferflocken": "rolled oats",
        }

    def test_does_not_modify_file_if_mappings_are_identical(self, tmp_path):
        import os
        output_path = str(tmp_path / "food_mappings.yaml")
        foods = ["Banana", "Haferflocken"]
        existing = {"Banana": "banana", "Haferflocken": "rolled oats"}
        # Initial write
        first_result = generate_food_mappings(foods, existing, output_path)
        assert first_result is True

        # Set specific old mtime
        os.utime(output_path, (1000000.0, 1000000.0))
        mtime_before = os.path.getmtime(output_path)

        # Call again with identical data
        second_result = generate_food_mappings(foods, existing, output_path)
        assert second_result is False

        mtime_after = os.path.getmtime(output_path)
        assert mtime_after == mtime_before


