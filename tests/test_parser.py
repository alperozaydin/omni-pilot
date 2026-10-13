from __future__ import annotations

import openpyxl
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


def _write_food_log(path, rows: list[list]) -> str:
    """Write a "Food Log" sheet in MacroFactor's column layout, one list per food row."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Food Log"
    ws.append(["Date", "Time", "Food Name"])
    for row in rows:
        ws.append(row)
    wb.save(path)
    return str(path)


def _food_row(food_name: str, fiber: float | None = None, columns: int = 61) -> list:
    """A food row as wide as a real export, with the fiber cell set (None leaves it empty)."""
    row = ["2026-10-04", "12:00", food_name, "g", 100, 1, 50.0, 1.0, 10.0, 2.0] + [None] * (columns - 10)
    if fiber is not None:
        row[27] = fiber
    return row


class TestParseLoggedFiber:
    def test_reads_logged_fiber(self, tmp_path):
        path = _write_food_log(tmp_path / "export.xlsx", [_food_row("Himbeeren", fiber=8.1)])

        assert parse_food_log(path)[0]["fiber_g"] == pytest.approx(8.1)

    def test_empty_fiber_cell_is_none_not_zero(self, tmp_path):
        """MacroFactor leaves the cell empty when it has no value; that is not a measured zero."""
        path = _write_food_log(tmp_path / "export.xlsx", [_food_row("Pretzel")])

        assert parse_food_log(path)[0]["fiber_g"] is None

    def test_logged_zero_fiber_is_kept_as_zero(self, tmp_path):
        path = _write_food_log(tmp_path / "export.xlsx", [_food_row("Honey", fiber=0.0)])

        assert parse_food_log(path)[0]["fiber_g"] == 0.0

    def test_export_without_fiber_column_has_no_logged_fiber(self, tmp_path):
        path = _write_food_log(tmp_path / "export.xlsx", [_food_row("Reis", columns=10)])

        assert parse_food_log(path)[0]["fiber_g"] is None


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


