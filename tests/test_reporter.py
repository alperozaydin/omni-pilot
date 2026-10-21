from __future__ import annotations

import os

import pytest

from omni_pilot.reporter import _estimated_weights_line, generate_html_report, print_terminal_report


def _make_analysis_result() -> dict:
    return {
        "period": {"start": "2026-07-13", "end": "2026-08-09", "days": 21, "left_out_day": None},
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
            "mapped_entries": 205,
            "skipped_entries": 12,
            "not_in_usda_entries": 3,
            "lookup_failed_entries": 1,
            "untranslated_entries": 2,
            "skipped_foods": ["Quick Add", "Dessert, Prepared"],
            "not_in_usda_foods": ["Unknown Thing"],
            "lookup_failed_foods": ["Himbeeren"],
            "untranslated_foods": ["Braune Linsen"],
            "not_in_usda_weight_pct": 2.4,
            "lookup_failed_weight_pct": 0.6,
            "untranslated_weight_pct": 1.4,
            "low_confidence_foods": [],
            "low_confidence_weight_pct": 0.0,
            "low_confidence_kcal_pct": 0.0,
            "estimated_weights": [],
            "custom_recipes": [],
            "logged_fiber_pct": None,
        },
    }


def _make_analysis_result_with_low_confidence() -> dict:
    result = _make_analysis_result()
    result["coverage"]["low_confidence_foods"] = [
        {"name": "Salzlakenkaese salat", "usda_name": "Cheese, feta", "macro_distance": 3.96, "grams": 2084.0},
        {"name": "Paprika [red]", "usda_name": "Fish, tuna salad", "macro_distance": None, "grams": 164.0},
    ]
    result["coverage"]["low_confidence_weight_pct"] = 18.4
    result["coverage"]["low_confidence_kcal_pct"] = 16.2
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
        assert "Low-confidence matches (18% of analysed weight, 16% of calories)" in out
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
        assert "Low-confidence matches (18% of analysed weight, 16% of calories)" in html
        assert "Salzlakenkaese salat → Cheese, feta (off 396%)" in html

    def test_html_omits_warning_when_all_matches_are_good(self, tmp_path):
        html_path = str(tmp_path / "report.html")
        generate_html_report(_make_analysis_result(), html_path)
        with open(html_path) as f:
            assert "Low-confidence" not in f.read()

    def test_html_shows_warnings_block_for_low_confidence_alone(self, tmp_path):
        result = _make_analysis_result_with_low_confidence()
        result["coverage"]["skipped_foods"] = []
        result["coverage"]["not_in_usda_foods"] = []
        result["coverage"]["lookup_failed_foods"] = []
        html_path = str(tmp_path / "report.html")
        generate_html_report(result, html_path)
        with open(html_path) as f:
            assert "Low-confidence matches" in f.read()


def _make_analysis_result_with_custom_foods() -> dict:
    result = _make_analysis_result()
    result["coverage"]["custom_recipes"] = [
        {
            "recipe": "green_salad",
            "ingredients": [
                {"fdc_id": 169249, "usda_name": "Lettuce, green leaf, raw", "share_pct": 60.0},
                {"fdc_id": 170393, "usda_name": "Carrots, raw", "share_pct": 40.0},
            ],
            "foods": [
                {"name": "Misch Salat Rohkost", "grams": 750.0, "macro_distance": 0.12, "fitted": False},
                {"name": "Salat [Manhattan]", "grams": 120.0, "macro_distance": None, "fitted": False},
            ],
        },
        {
            "recipe": "caprese",
            "ingredients": [
                {"fdc_id": 170457, "usda_name": "Tomatoes, red, ripe, raw, year round average", "share_pct": 100.0},
            ],
            "foods": [{"name": "Salat Caprese", "grams": 264.0, "macro_distance": 0.4, "fitted": False}],
        },
    ]
    return result


class TestCustomFoodsReport:
    SETTINGS = {"output": {"show_amino_acids": True, "show_ok_nutrients": True}}

    def test_terminal_names_each_food_with_recipe_and_macro_check(self, capsys, monkeypatch):
        monkeypatch.setenv("COLUMNS", "300")
        print_terminal_report(_make_analysis_result_with_custom_foods(), self.SETTINGS)
        out = capsys.readouterr().out
        assert (
            "Custom foods: Misch Salat Rohkost → green_salad (off 12%), "
            "Salat [Manhattan] → green_salad (not checked), "
            "Salat Caprese → caprese (⚠ off 40%)"
        ) in out

    def test_terminal_omits_line_without_custom_foods(self, capsys):
        print_terminal_report(_make_analysis_result(), self.SETTINGS)
        assert "Custom foods" not in capsys.readouterr().out

    def test_html_lists_recipes_ingredients_and_foods(self, tmp_path):
        html_path = str(tmp_path / "report.html")
        generate_html_report(_make_analysis_result_with_custom_foods(), html_path)
        with open(html_path) as f:
            html = f.read()
        assert "<h2>Custom foods</h2>" in html
        assert "<h3>green_salad</h3>" in html
        assert "Lettuce, green leaf, raw (FDC 169249) — 60%" in html
        assert "Misch Salat Rohkost — 750 g — macros off 12%" in html
        assert "Salat [Manhattan] — 120 g — macros not checked" in html
        assert "Salat Caprese — 264 g — ⚠ macros off 40%" in html
        # The section comes after the warnings, at the bottom of the report
        assert html.index("<h2>Custom foods</h2>") > html.index('<div class="warnings">')

    def test_html_omits_section_without_custom_foods(self, tmp_path):
        html_path = str(tmp_path / "report.html")
        generate_html_report(_make_analysis_result(), html_path)
        with open(html_path) as f:
            assert "Custom foods" not in f.read()


class TestMissingFoodWarnings:
    SETTINGS = {"output": {"show_amino_acids": True, "show_ok_nutrients": True}}

    def test_terminal_shows_counts_and_both_warning_lines(self, capsys, monkeypatch):
        monkeypatch.setenv("COLUMNS", "300")
        print_terminal_report(_make_analysis_result(), self.SETTINGS)
        out = capsys.readouterr().out
        assert "205/223 entries analyzed (12 skipped, 3 not in USDA, 1 lookup failed, 2 not translated)" in out
        assert (
            "⚠ Not in USDA (2% of food weight): Unknown Thing — give them a custom recipe in custom_foods.yaml"
            in out
        )
        assert "⚠ Lookup failed (1% of food weight): Himbeeren — retried next run" in out
        assert "⚠ Not translated (1% of food weight): Braune Linsen — Gemini failed, retried next run" in out
        assert "Unresolved" not in out

    def test_terminal_omits_lines_with_no_foods(self, capsys, monkeypatch):
        monkeypatch.setenv("COLUMNS", "300")
        result = _make_analysis_result()
        result["coverage"]["not_in_usda_foods"] = []
        result["coverage"]["lookup_failed_foods"] = []
        result["coverage"]["untranslated_foods"] = []
        print_terminal_report(result, self.SETTINGS)
        out = capsys.readouterr().out
        assert "Not in USDA" not in out
        assert "Lookup failed" not in out
        assert "Not translated" not in out

    def test_terminal_prints_brackets_in_food_names_literally(self, capsys, monkeypatch):
        monkeypatch.setenv("COLUMNS", "300")
        result = _make_analysis_result()
        result["coverage"]["not_in_usda_foods"] = ["Reis [bio]"]
        result["coverage"]["lookup_failed_foods"] = ["Tee [grün]"]
        result["coverage"]["untranslated_foods"] = ["Reis [fit]"]
        print_terminal_report(result, self.SETTINGS)
        out = capsys.readouterr().out
        assert "Not translated (1% of food weight): Reis [fit]" in out
        assert "Not in USDA (2% of food weight): Reis [bio]" in out
        assert "Lookup failed (1% of food weight): Tee [grün]" in out

    def test_html_shows_counts_and_both_warning_lines(self, tmp_path):
        html_path = str(tmp_path / "report.html")
        generate_html_report(_make_analysis_result(), html_path)
        with open(html_path) as f:
            html = f.read()
        assert "205/223 entries analyzed (12 skipped, 3 not in USDA, 1 lookup failed, 2 not translated)" in html
        assert (
            "⚠ Not in USDA (2% of food weight): Unknown Thing — give them a custom recipe in custom_foods.yaml"
            in html
        )
        assert "⚠ Lookup failed (1% of food weight): Himbeeren — retried next run" in html
        assert "⚠ Not translated (1% of food weight): Braune Linsen — Gemini failed, retried next run" in html
        assert "Unresolved" not in html

    def test_html_omits_lines_with_no_foods(self, tmp_path):
        result = _make_analysis_result()
        result["coverage"]["not_in_usda_foods"] = []
        result["coverage"]["lookup_failed_foods"] = []
        result["coverage"]["untranslated_foods"] = []
        html_path = str(tmp_path / "report.html")
        generate_html_report(result, html_path)
        with open(html_path) as f:
            html = f.read()
        assert "Not in USDA" not in html
        assert "Lookup failed" not in html
        assert "Not translated" not in html


class TestLoggedFiberNote:
    SETTINGS = {"output": {"show_amino_acids": True, "show_ok_nutrients": True}}
    NOTE = "Fiber: 81% from MacroFactor's logged values, the rest from USDA"

    def _result(self, logged_fiber_pct: float | None) -> dict:
        result = _make_analysis_result()
        result["coverage"]["logged_fiber_pct"] = logged_fiber_pct
        return result

    def test_terminal_says_how_much_fiber_came_from_macrofactor(self, capsys, monkeypatch):
        monkeypatch.setenv("COLUMNS", "300")
        print_terminal_report(self._result(80.6), self.SETTINGS)
        assert self.NOTE in capsys.readouterr().out

    def test_html_says_how_much_fiber_came_from_macrofactor(self, tmp_path):
        html_path = str(tmp_path / "report.html")
        generate_html_report(self._result(80.6), html_path)
        with open(html_path) as f:
            assert self.NOTE in f.read()

    @pytest.mark.parametrize("logged_fiber_pct", [None, 0.0])
    def test_note_is_omitted_when_no_fiber_came_from_macrofactor(self, logged_fiber_pct, capsys, tmp_path):
        result = self._result(logged_fiber_pct)
        print_terminal_report(result, self.SETTINGS)
        html_path = str(tmp_path / "report.html")
        generate_html_report(result, html_path)
        with open(html_path) as f:
            assert "logged values" not in f.read()
        assert "logged values" not in capsys.readouterr().out


LEFT_OUT_DAY = {"date": "2026-08-10", "logged_kcal": 1196.0, "expenditure_kcal": 2570.0, "partial": True}


class TestLeftOutDay:
    def test_html_names_the_left_out_day_in_the_header(self, tmp_path):
        result = _make_analysis_result()
        result["period"]["left_out_day"] = LEFT_OUT_DAY
        html_path = str(tmp_path / "report.html")
        generate_html_report(result, html_path)
        html = open(html_path).read()
        header = html[html.index('<div class="header">'):html.index('<div class="summary">')]
        assert "2026-08-10 left out" in header
        assert "1196 of 2570 kcal" in header

    def test_html_says_nothing_when_no_day_was_left_out(self, tmp_path):
        html_path = str(tmp_path / "report.html")
        generate_html_report(_make_analysis_result(), html_path)
        assert "left out" not in open(html_path).read()

    def test_terminal_names_the_left_out_day(self, capsys):
        result = _make_analysis_result()
        result["period"]["left_out_day"] = LEFT_OUT_DAY
        print_terminal_report(result, {})
        assert "2026-08-10 left out" in capsys.readouterr().out

    def test_terminal_says_nothing_when_no_day_was_left_out(self, capsys):
        print_terminal_report(_make_analysis_result(), {})
        assert "left out" not in capsys.readouterr().out


def _estimates(logged_g: float, estimated_g: float | None, distance: float | None, n: int = 1) -> list[dict]:
    return [{"logged_g": logged_g, "estimated_g": estimated_g, "macro_distance": distance}] * n


ESTIMATED_WEIGHTS = [
    {"name": "King's Steakhouse Double", "estimates": _estimates(100.0, 335.2, 0.04, n=2)},
    {"name": "Ben's", "estimates": _estimates(100.0, 143.0, 0.22, n=2)},
]


class TestEstimatedWeightsReport:
    SETTINGS = {"output": {"show_amino_acids": True, "show_ok_nutrients": True}}

    def _result(self, estimated_weights: list[dict]) -> dict:
        result = _make_analysis_result()
        result["coverage"]["estimated_weights"] = estimated_weights
        return result

    def test_line_collapses_repeats_and_flags_poor_fits(self):
        assert _estimated_weights_line({"estimated_weights": ESTIMATED_WEIGHTS}) == (
            "Estimated weights (100 g placeholder servings, impossible for their macros): "
            "King's Steakhouse Double 100 g → 335 g (×2), Ben's 100 g → 143 g (×2, ⚠ off 22%)"
        )

    def test_distinct_servings_are_listed_separately(self):
        food = {"name": "King's", "estimates": _estimates(100.0, 335.0, 0.04) + _estimates(100.0, 168.0, 0.04)}
        assert _estimated_weights_line({"estimated_weights": [food]}) == (
            "Estimated weights (100 g placeholder servings, impossible for their macros): "
            "King's 100 g → 335 g, King's 100 g → 168 g"
        )

    def test_header_fits_servings_other_than_one(self):
        food = {"name": "Custom Chicken Doner", "estimates": _estimates(110.0, 193.0, 0.6)}
        assert _estimated_weights_line({"estimated_weights": [food]}) == (
            "Estimated weights (100 g placeholder servings, impossible for their macros): "
            "Custom Chicken Doner 110 g → 193 g (⚠ off 60%)"
        )

    def test_no_fit_says_weight_not_estimated(self):
        food = {"name": "Mystery Bowl", "estimates": _estimates(100.0, None, None)}
        assert _estimated_weights_line({"estimated_weights": [food]}) == (
            "Estimated weights (100 g placeholder servings, impossible for their macros): "
            "Mystery Bowl 100 g (weight not estimated)"
        )

    def test_no_estimated_weights_gives_none(self):
        assert _estimated_weights_line({"estimated_weights": []}) is None

    def test_terminal_prints_the_line(self, capsys, monkeypatch):
        monkeypatch.setenv("COLUMNS", "300")
        print_terminal_report(self._result(ESTIMATED_WEIGHTS), self.SETTINGS)
        assert "King's Steakhouse Double 100 g → 335 g (×2)" in capsys.readouterr().out

    def test_terminal_omits_the_line_without_estimates(self, capsys):
        print_terminal_report(_make_analysis_result(), self.SETTINGS)
        assert "Estimated weights" not in capsys.readouterr().out

    def test_html_shows_the_line_in_the_warnings(self, tmp_path):
        html_path = str(tmp_path / "report.html")
        generate_html_report(self._result(ESTIMATED_WEIGHTS), html_path)
        with open(html_path) as f:
            html = f.read()
        assert "Estimated weights (100 g placeholder servings" in html
        assert html.index("Estimated weights") > html.index('<div class="warnings">')

    def test_html_omits_the_line_without_estimates(self, tmp_path):
        html_path = str(tmp_path / "report.html")
        generate_html_report(_make_analysis_result(), html_path)
        with open(html_path) as f:
            assert "Estimated weights" not in f.read()


class TestFittedCustomFoods:
    SETTINGS = {"output": {"show_amino_acids": True, "show_ok_nutrients": True}}

    def _result(self, fitted: bool) -> dict:
        result = _make_analysis_result()
        result["coverage"]["custom_recipes"] = [{
            "recipe": "chicken_rice",
            "ingredients": [{"fdc_id": 1, "usda_name": "Chicken", "share_pct": 100.0}],
            "foods": [{"name": "Chicken and Rice", "grams": 700.0, "macro_distance": 0.2, "fitted": fitted}],
        }]
        return result

    def test_fitted_food_is_flagged_above_good_fit_distance(self, capsys, monkeypatch):
        monkeypatch.setenv("COLUMNS", "300")
        print_terminal_report(self._result(fitted=True), self.SETTINGS)
        assert "Chicken and Rice → chicken_rice (⚠ off 20%)" in capsys.readouterr().out

    def test_food_with_real_weight_keeps_good_distance(self, capsys, monkeypatch):
        monkeypatch.setenv("COLUMNS", "300")
        print_terminal_report(self._result(fitted=False), self.SETTINGS)
        assert "Chicken and Rice → chicken_rice (off 20%)" in capsys.readouterr().out

    def test_html_flags_fitted_food(self, tmp_path):
        html_path = str(tmp_path / "report.html")
        generate_html_report(self._result(fitted=True), html_path)
        with open(html_path) as f:
            assert "Chicken and Rice — 700 g — ⚠ macros off 20%" in f.read()
