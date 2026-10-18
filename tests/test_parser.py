from __future__ import annotations

from datetime import datetime

import openpyxl
import pytest
import yaml

from omni_pilot.parser import (
    check_last_day,
    extract_unique_foods,
    generate_food_mappings,
    parse_daily_expenditure,
    parse_food_log,
)
from tests.helpers import food_entry


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




def _write_export(path, quick_export: list[list] | None) -> str:
    """Write a Food Log sheet and, unless None, a "Quick Export" sheet with the given rows."""
    wb = openpyxl.Workbook()
    wb.active.title = "Food Log"
    if quick_export is not None:
        ws = wb.create_sheet("Quick Export")
        ws.append(["Date", "Expenditure", "Trend Weight (kg)", "Calories (kcal)", "Target Calories (kcal)"])
        for row in quick_export:
            ws.append(row)
    wb.save(path)
    return str(path)


class TestParseDailyExpenditure:
    def test_reads_expenditure_by_date(self, tmp_path):
        path = _write_export(tmp_path / "export.xlsx", [
            [datetime(2026, 8, 8), 2570.0, 74.9, 2400.0, 2600.0],
            [datetime(2026, 8, 9), 2565.0, 74.9, None, 2600.0],
        ])
        assert parse_daily_expenditure(path) == {"2026-08-08": 2570.0, "2026-08-09": 2565.0}

    def test_falls_back_to_target_calories(self, tmp_path):
        path = _write_export(tmp_path / "export.xlsx", [[datetime(2026, 8, 9), None, 74.9, None, 2600.0]])
        assert parse_daily_expenditure(path) == {"2026-08-09": 2600.0}

    def test_leaves_out_days_with_neither_figure(self, tmp_path):
        path = _write_export(tmp_path / "export.xlsx", [[datetime(2026, 8, 9), None, 74.9, None, None]])
        assert parse_daily_expenditure(path) == {}

    def test_export_without_quick_export_sheet_has_no_expenditure(self, tmp_path):
        path = _write_export(tmp_path / "export.xlsx", None)
        assert parse_daily_expenditure(path) == {}

    def test_reads_real_export(self):
        expenditure = parse_daily_expenditure("data/MacroFactor-example.xlsx")
        assert expenditure
        assert all(kcal > 0 for kcal in expenditure.values())


class TestCheckLastDay:
    def _entries(self, kcal_by_date: dict[str, float]) -> list:
        return [food_entry("Reis", date=d, calories_kcal=kcal) for d, kcal in kcal_by_date.items()]

    def test_last_day_under_half_of_expenditure_is_partial(self):
        entries = self._entries({"2026-08-08": 2500.0, "2026-08-09": 1196.0})
        check = check_last_day(entries, {"2026-08-08": 2570.0, "2026-08-09": 2570.0})
        assert check == {"date": "2026-08-09", "logged_kcal": 1196.0, "expenditure_kcal": 2570.0, "partial": True}

    def test_last_day_at_or_over_half_of_expenditure_counts(self):
        entries = self._entries({"2026-08-08": 2500.0, "2026-08-09": 1285.0})
        check = check_last_day(entries, {"2026-08-09": 2570.0})
        assert check is not None and check["partial"] is False

    def test_sums_every_entry_of_the_last_day(self):
        entries = self._entries({"2026-08-08": 2500.0}) + [
            food_entry("Reis", date="2026-08-09", calories_kcal=700.0),
            food_entry("Quick Add", date="2026-08-09", calories_kcal=700.0),
        ]
        check = check_last_day(entries, {"2026-08-09": 2570.0})
        assert check is not None
        assert check["logged_kcal"] == 1400.0
        assert check["partial"] is False

    def test_last_day_without_expenditure_counts(self):
        entries = self._entries({"2026-08-08": 2500.0, "2026-08-09": 300.0})
        check = check_last_day(entries, {"2026-08-08": 2570.0})
        assert check == {"date": "2026-08-09", "logged_kcal": 300.0, "expenditure_kcal": None, "partial": False}

    def test_only_day_is_never_partial(self):
        """Leaving out the only day would leave nothing to analyse."""
        assert check_last_day(self._entries({"2026-08-09": 300.0}), {"2026-08-09": 2570.0}) is None

    def test_no_entries(self):
        assert check_last_day([], {}) is None
