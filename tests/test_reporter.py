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
                "coverage_pct": 100.0, "is_floor": False,
            },
            "vitamin_c_mg": {
                "name": "Vitamin C", "unit": "mg",
                "daily_avg": 92.1, "target": 90.0, "target_type": "rda",
                "ul": 2000.0, "status": "ok", "pct_of_target": 102.3,
                "coverage_pct": 100.0, "is_floor": False,
            },
            "vitamin_d_mcg": {
                "name": "Vitamin D", "unit": "mcg",
                "daily_avg": 4.2, "target": 15.0, "target_type": "rda",
                "ul": 100.0, "status": "deficient", "pct_of_target": 28.0,
                "coverage_pct": 68.7, "is_floor": True,
            },
            "calcium_mg": {
                "name": "Calcium", "unit": "mg",
                "daily_avg": 1102.0, "target": 1000.0, "target_type": "rda",
                "ul": 2500.0, "status": "ok", "pct_of_target": 110.2,
                "coverage_pct": 100.0, "is_floor": False,
            },
            "sodium_mg": {
                "name": "Sodium", "unit": "mg",
                "daily_avg": 3500.0, "target": 1500.0, "target_type": "ai",
                "ul": 2300.0, "status": "high", "pct_of_target": 233.3,
                "coverage_pct": 100.0, "is_floor": False,
            },
        },
        "coverage": {
            "total_food_entries": 223,
            "mapped_entries": 207,
            "skipped_entries": 12,
            "unresolved_entries": 4,
            "skipped_foods": ["Quick Add", "Dessert, Prepared"],
            "unresolved_foods": ["Unknown Thing"],
            "low_confidence_foods": [],
            "low_confidence_weight_pct": 0.0,
        },
    }


def _make_analysis_result_with_low_confidence() -> dict:
    result = _make_analysis_result()
    result["coverage"]["low_confidence_foods"] = [
        {"name": "Salzlakenkaese salat", "usda_name": "Cheese, feta", "macro_distance": 3.96, "grams": 2084.0},
        {"name": "Paprika [red]", "usda_name": "Fish, tuna salad", "macro_distance": None, "grams": 164.0},
    ]
    result["coverage"]["low_confidence_weight_pct"] = 18.4
    return result


def _make_analysis_result_without_floor() -> dict:
    result = _make_analysis_result()
    result["nutrients"]["vitamin_d_mcg"]["is_floor"] = False
    return result


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

    def test_shows_data_column_and_floor_marker(self, capsys, monkeypatch):
        monkeypatch.setenv("COLUMNS", "200")
        settings = {"output": {"show_amino_acids": True, "show_ok_nutrients": True}}
        result = _make_analysis_result()
        print_terminal_report(result, settings)
        captured = capsys.readouterr()
        assert "Data" in captured.out
        assert "68.7%" in captured.out
        assert "Deficient*" in captured.out
        # The footnote is the legend for that "*" — a marker with no legend is the bug.
        assert captured.out.count("computed from partial USDA data") == 1

    def test_footnote_absent_when_no_floor(self, capsys, monkeypatch):
        monkeypatch.setenv("COLUMNS", "200")
        settings = {"output": {"show_amino_acids": True, "show_ok_nutrients": True}}
        result = _make_analysis_result_without_floor()
        print_terminal_report(result, settings)
        captured = capsys.readouterr()
        assert "computed from partial USDA data" not in captured.out
        assert "Deficient*" not in captured.out

    def test_summary_line_reports_partial_data_count(self, capsys, monkeypatch):
        monkeypatch.setenv("COLUMNS", "200")
        settings = {"output": {"show_amino_acids": True, "show_ok_nutrients": True}}
        result = _make_analysis_result()
        print_terminal_report(result, settings)
        captured = capsys.readouterr()
        assert "1 from partial data" in captured.out

    def test_unmeasured_nutrient_shows_dash_in_data_column(self, capsys, monkeypatch):
        monkeypatch.setenv("COLUMNS", "200")
        settings = {"output": {"show_amino_acids": True, "show_ok_nutrients": True}}
        result = _make_analysis_result()
        result["nutrients"]["vitamin_a_mcg"]["coverage_pct"] = None
        print_terminal_report(result, settings)
        captured = capsys.readouterr()
        matching_lines = [
            line for line in captured.out.splitlines() if "Vitamin A" in line
        ]
        assert len(matching_lines) == 1
        assert "900.0" in matching_lines[0]
        assert "—" in matching_lines[0]


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

    def test_html_report_shows_coverage_and_floor_marker(self, tmp_path):
        result = _make_analysis_result()
        output_path = str(tmp_path / "report.html")
        generate_html_report(result, output_path)
        with open(output_path) as f:
            html = f.read()
        assert '<th class="data-col">Data</th>' in html
        assert "68.7%" in html
        assert "Deficient*" in html
        assert html.count("computed from partial USDA data") == 1

    def test_html_report_omits_footnote_when_no_floor(self, tmp_path):
        result = _make_analysis_result_without_floor()
        output_path = str(tmp_path / "report.html")
        generate_html_report(result, output_path)
        with open(output_path) as f:
            html = f.read()
        assert "computed from partial USDA data" not in html
        assert "Deficient*" not in html

    def test_html_report_renders_dash_for_unmeasured_nutrient(self, tmp_path):
        result = _make_analysis_result()
        result["nutrients"]["vitamin_a_mcg"]["coverage_pct"] = None
        output_path = str(tmp_path / "report.html")
        generate_html_report(result, output_path)
        with open(output_path) as f:
            html = f.read()
        assert '<th class="data-col">Data</th>' in html
        assert '<td class="data-col">\n                    —\n                </td>' in html


class TestLowConfidenceWarning:
    def test_terminal_names_weak_matches_with_weight_share(self, capsys, monkeypatch):
        monkeypatch.setenv("COLUMNS", "300")
        settings = {"output": {"show_amino_acids": True, "show_ok_nutrients": True}}
        print_terminal_report(_make_analysis_result_with_low_confidence(), settings)
        out = capsys.readouterr().out
        assert "Low-confidence matches (18% of analysed weight)" in out
        assert "Salzlakenkaese salat → Cheese, feta (off 396%)" in out
        # No distance: no "(off ...)", and brackets in names are printed, not parsed as markup
        assert "Paprika [red] → Fish, tuna salad" in out
        assert "Fish, tuna salad (off" not in out

    def test_terminal_omits_warning_when_all_matches_are_good(self, capsys):
        settings = {"output": {"show_amino_acids": True, "show_ok_nutrients": True}}
        print_terminal_report(_make_analysis_result(), settings)
        assert "Low-confidence" not in capsys.readouterr().out

    def test_html_names_weak_matches(self, tmp_path):
        html_path = str(tmp_path / "report.html")
        generate_html_report(_make_analysis_result_with_low_confidence(), html_path)
        with open(html_path) as f:
            html = f.read()
        assert "Low-confidence matches (18% of analysed weight)" in html
        assert "Salzlakenkaese salat → Cheese, feta (off 396%)" in html

    def test_html_omits_warning_when_all_matches_are_good(self, tmp_path):
        html_path = str(tmp_path / "report.html")
        generate_html_report(_make_analysis_result(), html_path)
        with open(html_path) as f:
            assert "Low-confidence" not in f.read()

    def test_html_shows_warnings_block_for_low_confidence_alone(self, tmp_path):
        result = _make_analysis_result_with_low_confidence()
        result["coverage"]["skipped_foods"] = []
        result["coverage"]["unresolved_foods"] = []
        html_path = str(tmp_path / "report.html")
        generate_html_report(result, html_path)
        with open(html_path) as f:
            assert "Low-confidence matches" in f.read()

