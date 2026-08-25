from __future__ import annotations

import os

from omni_pilot.reporter import generate_html_report, print_terminal_report


def _make_analysis_result() -> dict:
    return {
        "period": {"start": "2026-07-13", "end": "2026-08-09", "days": 21},
        "nutrients": {
            "vitamin_a_mcg": {
                "name": "Vitamin A", "unit": "mcg",
                "daily_avg": 412.3, "target": 900.0, "target_type": "rda",
                "ul": 3000.0, "status": "low", "pct_of_target": 45.8,
            },
            "vitamin_c_mg": {
                "name": "Vitamin C", "unit": "mg",
                "daily_avg": 92.1, "target": 90.0, "target_type": "rda",
                "ul": 2000.0, "status": "ok", "pct_of_target": 102.3,
            },
            "vitamin_d_mcg": {
                "name": "Vitamin D", "unit": "mcg",
                "daily_avg": 4.2, "target": 15.0, "target_type": "rda",
                "ul": 100.0, "status": "deficient", "pct_of_target": 28.0,
            },
            "calcium_mg": {
                "name": "Calcium", "unit": "mg",
                "daily_avg": 1102.0, "target": 1000.0, "target_type": "rda",
                "ul": 2500.0, "status": "ok", "pct_of_target": 110.2,
            },
            "sodium_mg": {
                "name": "Sodium", "unit": "mg",
                "daily_avg": 3500.0, "target": 1500.0, "target_type": "ai",
                "ul": 2300.0, "status": "high", "pct_of_target": 233.3,
            },
        },
        "coverage": {
            "total_food_entries": 223,
            "mapped_entries": 207,
            "skipped_entries": 12,
            "unresolved_entries": 4,
            "skipped_foods": ["Quick Add", "Dessert, Prepared"],
            "unresolved_foods": ["Unknown Thing"],
        },
    }


class TestTerminalReport:
    def test_prints_without_error(self, capsys):
        settings = {"output": {"show_amino_acids": True, "show_ok_nutrients": True}}
        result = _make_analysis_result()
        print_terminal_report(result, settings)
        captured = capsys.readouterr()
        assert "Micronutrient Analysis Report" in captured.out
        assert "Vitamin A" in captured.out

    def test_hides_ok_nutrients_when_configured(self, capsys):
        settings = {"output": {"show_amino_acids": True, "show_ok_nutrients": False}}
        result = _make_analysis_result()
        print_terminal_report(result, settings)
        captured = capsys.readouterr()
        # Vitamin C is "ok" — should not appear
        assert "Vitamin C" not in captured.out
        # Vitamin A is "low" — should appear
        assert "Vitamin A" in captured.out


class TestHtmlReport:
    def test_generates_valid_html_file(self, tmp_path):
        result = _make_analysis_result()
        output_path = str(tmp_path / "report.html")
        generate_html_report(result, output_path)
        assert os.path.exists(output_path)
        with open(output_path) as f:
            html = f.read()
        assert "<html" in html
        assert "Vitamin A" in html
        assert "Micronutrient Analysis Report" in html
