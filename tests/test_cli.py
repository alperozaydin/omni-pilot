"""tests/test_cli.py"""
from __future__ import annotations

import os

import pytest
import yaml
from tinydb import TinyDB

from omni_pilot.cli import main
from omni_pilot.enricher import MATCH_VERSION
from tests.helpers import enrichment, food_entry

CAPRESE_RECIPE = "caprese:\n  foods: [Salat Caprese]\n  ingredients:\n    - {fdc_id: 170457, amount: 1}\n"


@pytest.fixture(autouse=True)
def _custom_foods_in_tmp(monkeypatch, tmp_path):
    """Point the default custom_foods path into tmp_path; it's absent unless a test writes it."""
    monkeypatch.setattr("omni_pilot.cli.DEFAULT_CUSTOM_FOODS", str(tmp_path / "custom_foods.yaml"))


class TestCLIHelp:
    def test_help_exits_cleanly(self, mocker):
        mocker.patch("sys.argv", ["omni_pilot", "--help"])
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 0

    def test_connections_prefer_ipv4(self, mocker):
        install = mocker.patch("omni_pilot.cli.prefer_ipv4_connections")
        mocker.patch("sys.argv", ["omni_pilot", "--help"])
        with pytest.raises(SystemExit):
            main()
        install.assert_called_once_with()


class TestCLIAnalyze:
    def test_analyze_missing_usda_key_exits(self, mocker, tmp_path):
        settings_path = str(tmp_path / "settings.yaml")
        with open(settings_path, "w") as f:
            yaml.dump({"usda_api_key": ""}, f)

        mocker.patch("sys.argv", [
            "omni_pilot", "analyze",
            "data/MacroFactor-example.xlsx",
            "--settings", settings_path,
        ])

        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 1

    def test_analyze_runs_gemini_translation_for_new_foods(self, mocker, tmp_path):
        """Integration test: analyze command automatically invokes Gemini translation for new foods."""
        db_path = str(tmp_path / "test_db.json")
        mappings_path = str(tmp_path / "food_mappings.yaml")
        settings_path = str(tmp_path / "settings.yaml")
        ref_ranges_path = str(tmp_path / "ref_ranges.yaml")

        with open(mappings_path, "w") as f:
            yaml.dump({"mappings": {}}, f)
        with open(settings_path, "w") as f:
            yaml.dump({
                "usda_api_key": "fake_usda_key",
                "gemini_api_key": "fake_gemini_key",
                "database_path": db_path,
                "mappings_path": mappings_path,
            }, f)
        with open(ref_ranges_path, "w") as f:
            yaml.dump({"nutrients": {}}, f)

        mock_translate = mocker.patch(
            "omni_pilot.translator.translate_new_foods",
            side_effect=lambda foods, api_key, *args, **kwargs: [f"Mock {food}" for food in foods],
        )
        mocker.patch("omni_pilot.cli.enrich_all_foods", return_value=enrichment())
        mocker.patch("omni_pilot.cli.analyze", return_value={})
        mocker.patch("omni_pilot.cli.print_terminal_report")

        mocker.patch("sys.argv", [
            "omni_pilot", "analyze",
            "data/MacroFactor-example.xlsx",
            "--settings", settings_path,
            "--ref-ranges", ref_ranges_path,
        ])
        main()

        assert mock_translate.called

        # Verify DB was populated with translations
        db = TinyDB(db_path)
        translations = db.table("translations").all()
        assert any(t["german"] == "Boiled Eggs" and t["english"] == "Mock Boiled Eggs" for t in translations)

        # Verify YAML was populated
        with open(mappings_path) as f:
            data = yaml.safe_load(f)
        assert data["mappings"].get("Boiled Eggs") == "Mock Boiled Eggs"

    def test_analyze_html_generates_report(self, mocker, tmp_path):
        """Test that --html flag calls generate_html_report and copies to latest.html."""
        mappings_path = str(tmp_path / "test_mappings.yaml")
        settings_path = str(tmp_path / "settings.yaml")
        ref_ranges_path = str(tmp_path / "ref_ranges.yaml")

        with open(mappings_path, "w") as f:
            yaml.dump({"mappings": {"Apfel": "apple"}}, f)
        with open(settings_path, "w") as f:
            yaml.dump({
                "usda_api_key": "fake_key",
                "mappings_path": mappings_path,
            }, f)
        with open(ref_ranges_path, "w") as f:
            yaml.dump({"nutrients": {}}, f)

        mocker.patch(
            "omni_pilot.cli.parse_food_log",
            return_value=[food_entry("Apfel")],
        )
        mocker.patch("omni_pilot.cli.enrich_all_foods", return_value=enrichment({"Apfel": {}}))
        mocker.patch("omni_pilot.cli.analyze", return_value={})
        mocker.patch("omni_pilot.cli.print_terminal_report")
        mock_html = mocker.patch("omni_pilot.cli.generate_html_report")

        mocker.patch("sys.argv", [
            "omni_pilot", "analyze",
            "data/MacroFactor-example.xlsx",
            "--settings", settings_path,
            "--ref-ranges", ref_ranges_path,
            "--html",
        ])
        main()

        assert mock_html.called
        call_path = mock_html.call_args[0][1]
        assert "reports/micronutrient-report-" in call_path
        assert call_path.endswith(".html")

    def test_analyze_reports_resolved_count_from_profiles_only(self, mocker, tmp_path, capsys):
        """Only foods with a USDA profile count as resolved — not skips or failed lookups."""
        settings_path = str(tmp_path / "settings.yaml")
        ref_ranges_path = str(tmp_path / "ref_ranges.yaml")
        with open(settings_path, "w") as f:
            yaml.dump({
                "usda_api_key": "fake_key",
                "database_path": str(tmp_path / "db.json"),
                "mappings_path": str(tmp_path / "mappings.yaml"),
            }, f)
        with open(ref_ranges_path, "w") as f:
            yaml.dump({"nutrients": {}}, f)

        mocker.patch(
            "omni_pilot.cli.parse_food_log",
            return_value=[food_entry(name) for name in ("Apfel", "Wasser", "Lachs")],
        )
        mocker.patch(
            "omni_pilot.cli.resolve_and_sync_mappings",
            return_value=({"Apfel": "apple", "Wasser": "skip", "Lachs": "salmon"}, set()),
        )
        mocker.patch(
            "omni_pilot.cli.enrich_all_foods",
            return_value=enrichment({"Apfel": {}}, skipped={"Wasser"}, lookup_failed={"Lachs"}),
        )
        mocker.patch("omni_pilot.cli.analyze", return_value={})
        mocker.patch("omni_pilot.cli.print_terminal_report")

        mocker.patch("sys.argv", [
            "omni_pilot", "analyze",
            "data/MacroFactor-example.xlsx",
            "--settings", settings_path,
            "--ref-ranges", ref_ranges_path,
        ])
        main()

        assert "1/3 foods resolved." in capsys.readouterr().out

    def test_analyze_stops_without_report_when_every_lookup_failed(self, mocker, tmp_path, capsys):
        """A run that could not ask USDA about any food must not render an all-deficient report."""
        _, settings_path, ref_ranges_path = self._write_config(tmp_path, {"Apfel": "apple", "Wasser": "skip"})
        mocker.patch("omni_pilot.cli.parse_food_log", return_value=[food_entry("Apfel"), food_entry("Wasser")])
        mocker.patch(
            "omni_pilot.cli.enrich_all_foods",
            return_value=enrichment(skipped={"Wasser"}, lookup_failed={"Apfel"}),
        )
        mock_analyze = mocker.patch("omni_pilot.cli.analyze", return_value={})
        mock_report = mocker.patch("omni_pilot.cli.print_terminal_report")
        mock_html = mocker.patch("omni_pilot.cli.generate_html_report")
        mocker.patch("sys.argv", [
            "omni_pilot", "analyze", "data/MacroFactor-example.xlsx",
            "--settings", settings_path, "--ref-ranges", ref_ranges_path, "--html",
        ])

        with pytest.raises(SystemExit) as exc_info:
            main()

        assert exc_info.value.code == 1
        assert "Error: no food resolved and 1 USDA lookups failed" in capsys.readouterr().out
        mock_analyze.assert_not_called()
        mock_report.assert_not_called()
        mock_html.assert_not_called()

    def test_analyze_stops_without_report_when_every_food_is_untranslated(self, mocker, tmp_path, capsys):
        """Gemini down on a first run must not render an all-deficient report either."""
        _, settings_path, ref_ranges_path = self._write_config(tmp_path, {})
        mocker.patch("omni_pilot.cli.parse_food_log", return_value=[food_entry("Braune Linsen")])
        mocker.patch(
            "omni_pilot.cli.resolve_and_sync_mappings", return_value=({}, {"Braune Linsen"}),
        )
        mocker.patch(
            "omni_pilot.cli.enrich_all_foods", return_value=enrichment(untranslated={"Braune Linsen"}),
        )
        mock_analyze = mocker.patch("omni_pilot.cli.analyze", return_value={})
        mock_report = mocker.patch("omni_pilot.cli.print_terminal_report")
        mocker.patch("sys.argv", [
            "omni_pilot", "analyze", "data/MacroFactor-example.xlsx",
            "--settings", settings_path, "--ref-ranges", ref_ranges_path,
        ])

        with pytest.raises(SystemExit) as exc_info:
            main()

        assert exc_info.value.code == 1
        assert "Error: no food resolved and 1 foods not translated" in capsys.readouterr().out
        mock_analyze.assert_not_called()
        mock_report.assert_not_called()

    def test_analyze_passes_untranslated_foods_to_enrichment(self, mocker, tmp_path):
        _, settings_path, ref_ranges_path = self._write_config(tmp_path, {"Reis": "rice, cooked"})
        mocker.patch(
            "omni_pilot.cli.parse_food_log", return_value=[food_entry("Reis"), food_entry("Braune Linsen")],
        )
        mocker.patch(
            "omni_pilot.cli.resolve_and_sync_mappings",
            return_value=({"Reis": "rice, cooked"}, {"Braune Linsen"}),
        )
        mock_enrich = mocker.patch(
            "omni_pilot.cli.enrich_all_foods",
            return_value=enrichment({"Reis": {}}, untranslated={"Braune Linsen"}),
        )

        self._run(mocker, settings_path, ref_ranges_path)

        assert mock_enrich.call_args.kwargs["untranslated"] == {"Braune Linsen"}

    def test_analyze_still_reports_when_no_food_resolved_but_no_lookup_failed(self, mocker, tmp_path):
        """Nothing in USDA is an answer, not a failure: the report names those foods."""
        _, settings_path, ref_ranges_path = self._write_config(tmp_path, {"Apfel": "apple"})
        mocker.patch("omni_pilot.cli.parse_food_log", return_value=[food_entry("Apfel")])
        mocker.patch("omni_pilot.cli.enrich_all_foods", return_value=enrichment(not_in_usda={"Apfel"}))

        self._run(mocker, settings_path, ref_ranges_path)

        import omni_pilot.cli
        omni_pilot.cli.print_terminal_report.assert_called_once()

    def test_analyze_resolves_db_and_mappings_from_settings(self, mocker, tmp_path):
        """Test custom database and mappings paths from settings."""
        custom_dir = tmp_path / "custom_dir"
        custom_dir.mkdir(parents=True, exist_ok=True)
        custom_db = custom_dir / "settings_db.json"
        custom_mappings = custom_dir / "settings_mappings.yaml"
        settings_path = tmp_path / "settings.yaml"
        ref_ranges_path = tmp_path / "ref_ranges.yaml"

        with open(custom_mappings, "w") as f:
            yaml.dump({"mappings": {"Apfel": "apple"}}, f)
        with open(settings_path, "w") as f:
            yaml.dump({
                "usda_api_key": "fake_key",
                "database_path": str(custom_db),
                "mappings_path": str(custom_mappings),
            }, f)
        with open(ref_ranges_path, "w") as f:
            yaml.dump({"nutrients": {}}, f)

        mocker.patch(
            "omni_pilot.cli.parse_food_log",
            return_value=[food_entry("Apfel")],
        )
        mocker.patch("omni_pilot.cli.enrich_all_foods", return_value=enrichment({"Apfel": {}}))
        mocker.patch("omni_pilot.cli.analyze", return_value={})
        mocker.patch("omni_pilot.cli.print_terminal_report")

        mocker.patch("sys.argv", [
            "omni_pilot", "analyze",
            "data/MacroFactor-example.xlsx",
            "--settings", str(settings_path),
            "--ref-ranges", str(ref_ranges_path),
        ])
        main()

        assert custom_db.exists()
        db = TinyDB(str(custom_db))
        translations = db.table("translations").all()
        assert any(t["german"] == "Apfel" and t["english"] == "apple" for t in translations)

    def test_analyze_idempotent_does_not_modify_db_mtime(self, mocker, tmp_path):
        """Test that rerunning analyze on already translated foods is idempotent."""
        db_path = str(tmp_path / "test_db.json")
        mappings_path = str(tmp_path / "test_mappings.yaml")
        settings_path = str(tmp_path / "settings.yaml")
        ref_ranges_path = str(tmp_path / "ref_ranges.yaml")

        with open(mappings_path, "w") as f:
            yaml.dump({"mappings": {"Apfel": "apple"}}, f)
        with open(settings_path, "w") as f:
            yaml.dump({
                "usda_api_key": "fake_key",
                "database_path": db_path,
                "mappings_path": mappings_path,
            }, f)
        with open(ref_ranges_path, "w") as f:
            yaml.dump({"nutrients": {}}, f)

        mocker.patch(
            "omni_pilot.cli.parse_food_log",
            return_value=[food_entry("Apfel")],
        )
        mocker.patch("omni_pilot.cli.enrich_all_foods", return_value=enrichment({"Apfel": {}}))
        mocker.patch("omni_pilot.cli.analyze", return_value={})
        mocker.patch("omni_pilot.cli.print_terminal_report")

        # First run
        mocker.patch("sys.argv", [
            "omni_pilot", "analyze",
            "data/MacroFactor-example.xlsx",
            "--settings", settings_path,
            "--ref-ranges", ref_ranges_path,
        ])
        main()

        # Set specific past timestamp on db and mappings
        os.utime(db_path, (1000000.0, 1000000.0))
        os.utime(mappings_path, (1000000.0, 1000000.0))
        db_mtime_before = os.path.getmtime(db_path)
        mappings_mtime_before = os.path.getmtime(mappings_path)

        # Second run: identical mappings
        main()

        db_mtime_after = os.path.getmtime(db_path)
        mappings_mtime_after = os.path.getmtime(mappings_path)
        assert db_mtime_after == db_mtime_before
        assert mappings_mtime_after == mappings_mtime_before

    def _write_config(self, tmp_path, mappings: dict) -> tuple[str, str, str]:
        db_path = str(tmp_path / "db.json")
        mappings_path = str(tmp_path / "mappings.yaml")
        settings_path = str(tmp_path / "settings.yaml")
        ref_ranges_path = str(tmp_path / "ref_ranges.yaml")
        with open(mappings_path, "w") as f:
            yaml.dump({"mappings": mappings}, f)
        with open(settings_path, "w") as f:
            yaml.dump({"usda_api_key": "fake_key", "database_path": db_path, "mappings_path": mappings_path}, f)
        with open(ref_ranges_path, "w") as f:
            yaml.dump({"nutrients": {}}, f)
        return db_path, settings_path, ref_ranges_path

    def _run(self, mocker, settings_path: str, ref_ranges_path: str):
        """Run analyze with the analysis and report mocked; returns the analyze mock."""
        mock_analyze = mocker.patch("omni_pilot.cli.analyze", return_value={})
        mocker.patch("omni_pilot.cli.print_terminal_report")
        mocker.patch("sys.argv", [
            "omni_pilot", "analyze", "data/MacroFactor-example.xlsx",
            "--settings", settings_path, "--ref-ranges", ref_ranges_path,
        ])
        main()
        return mock_analyze

    def test_analyze_passes_logged_macros_to_enrichment(self, mocker, tmp_path):
        _, settings_path, ref_ranges_path = self._write_config(tmp_path, {"Reis": "rice, cooked"})
        mocker.patch("omni_pilot.cli.parse_food_log", return_value=[
            food_entry("Reis", 200.0, calories_kcal=260.0, protein_g=6.0, fat_g=0.0, carbs_g=56.0),
        ])
        mock_enrich = mocker.patch("omni_pilot.cli.enrich_all_foods", return_value=enrichment())

        self._run(mocker, settings_path, ref_ranges_path)

        logged = mock_enrich.call_args.args[2]
        assert logged["Reis"] == {"kcal": 130.0, "protein_g": 3.0, "fat_g": 0.0, "carbs_g": 28.0}

    def test_analyze_passes_weight_unknown_to_enrichment(self, mocker, tmp_path):
        _, settings_path, ref_ranges_path = self._write_config(tmp_path, {"King's": "fast foods, cheeseburger"})
        kings = food_entry("King's", 100.0, calories_kcal=930.0, protein_g=53.0, fat_g=56.0, carbs_g=54.0,
                           weight_is_placeholder=True)
        mocker.patch("omni_pilot.cli.parse_food_log", return_value=[kings])
        mock_enrich = mocker.patch("omni_pilot.cli.enrich_all_foods", return_value=enrichment())

        self._run(mocker, settings_path, ref_ranges_path)

        assert "King's" not in mock_enrich.call_args.args[2]
        assert mock_enrich.call_args.kwargs["weight_unknown"] == {
            "King's": {"kcal": 930.0, "protein_g": 53.0, "fat_g": 56.0, "carbs_g": 54.0},
        }

    def test_analyze_passes_estimated_entries_to_analyze(self, mocker, tmp_path):
        _, settings_path, ref_ranges_path = self._write_config(tmp_path, {"King's": "fast foods, cheeseburger"})
        kings = food_entry("King's", 100.0, calories_kcal=930.0, protein_g=53.0, fat_g=56.0, carbs_g=54.0,
                           weight_is_placeholder=True)
        mocker.patch("omni_pilot.cli.parse_food_log", return_value=[kings])
        double_cb = {"protein_g": 16.2, "fat_g": 16.2, "carbs_g": 18.0, "fiber_g": 0.9}
        mocker.patch(
            "omni_pilot.cli.enrich_all_foods",
            return_value=enrichment({"King's": {}}, matched_macros={"King's": double_cb}),
        )

        mock_analyze = self._run(mocker, settings_path, ref_ranges_path)

        [entry] = mock_analyze.call_args.args[0]
        assert entry["total_weight_g"] == pytest.approx(335, abs=1)
        assert entry["weight_estimate"]["logged_g"] == 100.0

    def test_analyze_announces_rematching_of_outdated_cache(self, mocker, tmp_path, capsys):
        db_path, settings_path, ref_ranges_path = self._write_config(
            tmp_path, {"Reis": "rice, cooked", "Milch": "milk"}
        )
        db = TinyDB(db_path)
        db.insert({"original_name": "Reis", "usda_query": "rice, cooked", "per_100g": {}})
        db.insert({"original_name": "Milch", "usda_query": "milk", "per_100g": {}, "match_version": MATCH_VERSION})
        db.close()
        mocker.patch("omni_pilot.cli.parse_food_log", return_value=[food_entry("Reis"), food_entry("Milch")])
        mocker.patch("omni_pilot.cli.enrich_all_foods", return_value=enrichment())

        self._run(mocker, settings_path, ref_ranges_path)

        assert "Re-matching 1 cached foods with updated USDA matching..." in capsys.readouterr().out

    def test_analyze_is_silent_when_cache_is_current(self, mocker, tmp_path, capsys):
        _, settings_path, ref_ranges_path = self._write_config(tmp_path, {"Reis": "rice, cooked"})
        mocker.patch("omni_pilot.cli.parse_food_log", return_value=[food_entry("Reis")])
        mocker.patch("omni_pilot.cli.enrich_all_foods", return_value=enrichment())

        self._run(mocker, settings_path, ref_ranges_path)

        assert "Re-matching" not in capsys.readouterr().out

    def test_analyze_keeps_custom_foods_out_of_translation(self, mocker, tmp_path, capsys):
        _, settings_path, ref_ranges_path = self._write_config(tmp_path, {"Reis": "rice, cooked"})
        (tmp_path / "custom_foods.yaml").write_text(CAPRESE_RECIPE)
        mocker.patch("omni_pilot.cli.parse_food_log", return_value=[food_entry("Reis"), food_entry("Salat Caprese")])
        mock_resolve = mocker.patch(
            "omni_pilot.cli.resolve_and_sync_mappings", return_value=({"Reis": "rice, cooked"}, set()),
        )
        mock_enrich = mocker.patch("omni_pilot.cli.enrich_all_foods", return_value=enrichment())

        self._run(mocker, settings_path, ref_ranges_path)

        assert f"Custom foods: {tmp_path / 'custom_foods.yaml'}" in capsys.readouterr().out
        assert mock_resolve.call_args.args[0] == ["Reis"]
        assert mock_enrich.call_args.args[0] == ["Reis", "Salat Caprese"]
        assert mock_enrich.call_args.kwargs["custom_foods"]["by_food"] == {"Salat Caprese": "caprese"}

    def test_analyze_reports_custom_foods_not_found(self, mocker, tmp_path, capsys):
        _, settings_path, ref_ranges_path = self._write_config(tmp_path, {"Reis": "rice, cooked"})
        mocker.patch("omni_pilot.cli.parse_food_log", return_value=[food_entry("Reis")])
        mocker.patch("omni_pilot.cli.enrich_all_foods", return_value=enrichment())

        self._run(mocker, settings_path, ref_ranges_path)

        assert f"Custom foods: {tmp_path / 'custom_foods.yaml'} (not found — none used)" in capsys.readouterr().out

    def test_analyze_reports_custom_foods_counts(self, mocker, tmp_path, capsys):
        _, settings_path, ref_ranges_path = self._write_config(tmp_path, {"Reis": "rice, cooked"})
        (tmp_path / "custom_foods.yaml").write_text(CAPRESE_RECIPE)
        mocker.patch("omni_pilot.cli.parse_food_log", return_value=[food_entry("Reis"), food_entry("Salat Caprese")])
        mocker.patch("omni_pilot.cli.enrich_all_foods", return_value=enrichment())

        self._run(mocker, settings_path, ref_ranges_path)

        assert f"Custom foods: {tmp_path / 'custom_foods.yaml'} (1 recipes, 1 foods)" in capsys.readouterr().out

    def test_analyze_exits_on_invalid_custom_foods(self, mocker, tmp_path, capsys):
        _, settings_path, ref_ranges_path = self._write_config(tmp_path, {})
        (tmp_path / "custom_foods.yaml").write_text("caprese: {foods: [], ingredients: []}\n")
        mock_enrich = mocker.patch("omni_pilot.cli.enrich_all_foods")

        with pytest.raises(SystemExit) as exc_info:
            self._run(mocker, settings_path, ref_ranges_path)

        assert exc_info.value.code == 1
        assert f"Error in {tmp_path / 'custom_foods.yaml'}: recipe 'caprese'" in capsys.readouterr().out
        mock_enrich.assert_not_called()

    def test_custom_food_with_stale_cache_entry_is_not_rematched(self, mocker, tmp_path, capsys):
        db_path, settings_path, ref_ranges_path = self._write_config(tmp_path, {"Salat Caprese": "caprese salad"})
        (tmp_path / "custom_foods.yaml").write_text(CAPRESE_RECIPE)
        db = TinyDB(db_path)
        # Left over from before the recipe existed: same query, no match_version
        db.insert({"original_name": "Salat Caprese", "usda_query": "caprese salad", "per_100g": {}})
        db.close()
        mocker.patch("omni_pilot.cli.parse_food_log", return_value=[food_entry("Salat Caprese")])
        mocker.patch("omni_pilot.cli.enrich_all_foods", return_value=enrichment())

        self._run(mocker, settings_path, ref_ranges_path)

        assert "Re-matching" not in capsys.readouterr().out

    def test_analyze_counts_custom_foods_as_resolved(self, mocker, tmp_path, capsys):
        _, settings_path, ref_ranges_path = self._write_config(tmp_path, {"Reis": "rice, cooked"})
        (tmp_path / "custom_foods.yaml").write_text(CAPRESE_RECIPE)
        mocker.patch("omni_pilot.cli.parse_food_log", return_value=[food_entry("Reis"), food_entry("Salat Caprese")])
        caprese = {"recipe": "caprese", "parts": [], "macro_distance": None}
        mocker.patch(
            "omni_pilot.cli.enrich_all_foods",
            return_value=enrichment({"Reis": {}}, custom={"Salat Caprese": caprese}),
        )

        self._run(mocker, settings_path, ref_ranges_path)

        assert "2/2 foods resolved." in capsys.readouterr().out

    def test_analyze_leaves_out_a_partly_logged_last_day(self, mocker, tmp_path, capsys):
        _, settings_path, ref_ranges_path = self._write_config(tmp_path, {"Reis": "rice, cooked", "Milch": "milk"})
        full_day = food_entry("Reis", date="2026-08-08", calories_kcal=2500.0)
        mocker.patch("omni_pilot.cli.parse_food_log", return_value=[
            full_day, food_entry("Milch", date="2026-08-09", calories_kcal=1196.0),
        ])
        mocker.patch("omni_pilot.cli.parse_daily_expenditure", return_value={"2026-08-09": 2570.0})
        mock_enrich = mocker.patch("omni_pilot.cli.enrich_all_foods", return_value=enrichment({"Reis": {}}))

        mock_analyze = self._run(mocker, settings_path, ref_ranges_path)

        assert mock_analyze.call_args.args[0] == [full_day]
        assert mock_enrich.call_args.args[0] == ["Reis"]
        assert mock_analyze.call_args.kwargs["left_out_day"] == {
            "date": "2026-08-09", "logged_kcal": 1196.0, "expenditure_kcal": 2570.0, "partial": True,
        }

    def test_analyze_counts_a_fully_logged_last_day(self, mocker, tmp_path, capsys):
        _, settings_path, ref_ranges_path = self._write_config(tmp_path, {"Reis": "rice, cooked"})
        entries = [
            food_entry("Reis", date="2026-08-08", calories_kcal=2500.0),
            food_entry("Reis", date="2026-08-09", calories_kcal=2300.0),
        ]
        mocker.patch("omni_pilot.cli.parse_food_log", return_value=entries)
        mocker.patch("omni_pilot.cli.parse_daily_expenditure", return_value={"2026-08-09": 2570.0})
        mocker.patch("omni_pilot.cli.enrich_all_foods", return_value=enrichment({"Reis": {}}))

        mock_analyze = self._run(mocker, settings_path, ref_ranges_path)

        assert mock_analyze.call_args.args[0] == entries
        assert mock_analyze.call_args.kwargs["left_out_day"] is None

    def test_analyze_counts_last_day_without_expenditure_and_says_so(self, mocker, tmp_path, capsys):
        _, settings_path, ref_ranges_path = self._write_config(tmp_path, {"Reis": "rice, cooked"})
        entries = [
            food_entry("Reis", date="2026-08-08", calories_kcal=2500.0),
            food_entry("Reis", date="2026-08-09", calories_kcal=300.0),
        ]
        mocker.patch("omni_pilot.cli.parse_food_log", return_value=entries)
        mocker.patch("omni_pilot.cli.parse_daily_expenditure", return_value={})
        mocker.patch("omni_pilot.cli.enrich_all_foods", return_value=enrichment({"Reis": {}}))

        mock_analyze = self._run(mocker, settings_path, ref_ranges_path)

        assert mock_analyze.call_args.args[0] == entries
        assert "No expenditure for 2026-08-09" in capsys.readouterr().out


class TestCLIPullPush:
    @pytest.fixture(autouse=True)
    def _isolated(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)  # pull --exports writes to ./data
        self.db = tmp_path / "db" / "food_db.json"
        self.mappings = tmp_path / "food_mappings.yaml"
        self.recipes = tmp_path / "recipes.yaml"

    def settings(self, tmp_path, s3=None):
        path = tmp_path / "settings.yaml"
        body = {
            "database_path": str(self.db),
            "mappings_path": str(self.mappings),
            "custom_foods_path": str(self.recipes),
        }
        if s3 is not None:
            body["s3"] = s3
        path.write_text(yaml.dump(body))
        return str(path)

    FULL_S3 = {"bucket": "omni-pilot-data", "region": "eu-central-1", "access_key_id": "AKIA", "secret_access_key": "x"}

    def run(self, mocker, *argv):
        mocker.patch("sys.argv", ["omni_pilot", *argv])
        main()

    def test_pull_not_configured_exits_1(self, mocker, tmp_path, capsys):
        with pytest.raises(SystemExit) as exc:
            self.run(mocker, "pull", "--settings", self.settings(tmp_path))
        assert exc.value.code == 1
        assert "S3 is not configured" in capsys.readouterr().out

    def test_pull_incomplete_settings_exits_1(self, mocker, tmp_path, capsys):
        s3 = {"bucket": "omni-pilot-data"}
        with pytest.raises(SystemExit) as exc:
            self.run(mocker, "pull", "--settings", self.settings(tmp_path, s3))
        assert exc.value.code == 1
        assert "region" in capsys.readouterr().out

    def test_pull_prints_labels_and_exits_0(self, mocker, tmp_path, capsys):
        from tests.helpers import FakeS3Client

        fake = FakeS3Client({
            "food_db.json": (b"{}", '"1"'),
            "food_mappings.yaml": (b"mappings: {}\n", '"2"'),
            "custom_foods.yaml": (b"{}\n", '"3"'),
        }, bucket="omni-pilot-data")
        mocker.patch("omni_pilot.cli.S3Client", return_value=fake)
        self.run(mocker, "pull", "--settings", self.settings(tmp_path, self.FULL_S3))
        assert "S3: pulled from omni-pilot-data (database, mappings, recipes)" in capsys.readouterr().out
        assert self.db.read_bytes() == b"{}"

    def test_pull_exports_flag_fetches_exports(self, mocker, tmp_path, capsys):
        from tests.helpers import FakeS3Client

        fake = FakeS3Client({"food_db.json": (b"{}", '"0"'), "exports/a.xlsx": (b"xlsx", '"1"')})
        mocker.patch("omni_pilot.cli.S3Client", return_value=fake)
        self.run(mocker, "pull", "--exports", "--settings", self.settings(tmp_path, self.FULL_S3))
        assert (tmp_path / "data" / "a.xlsx").read_bytes() == b"xlsx"
        assert "a.xlsx" in capsys.readouterr().out

    def test_pull_without_exports_flag_lists_nothing(self, mocker, tmp_path):
        from tests.helpers import FakeS3Client

        fake = FakeS3Client({"food_db.json": (b"{}", '"0"'), "exports/a.xlsx": (b"xlsx", '"1"')})
        mocker.patch("omni_pilot.cli.S3Client", return_value=fake)
        self.run(mocker, "pull", "--settings", self.settings(tmp_path, self.FULL_S3))
        assert not (tmp_path / "data").exists()
        assert ("list", "exports/") not in fake.calls

    def test_pull_s3_error_prints_message_exits_1(self, mocker, tmp_path, capsys):
        from tests.helpers import FakeS3Client

        fake = FakeS3Client()
        fake.fail_get_on = "food_db.json"
        mocker.patch("omni_pilot.cli.S3Client", return_value=fake)
        with pytest.raises(SystemExit) as exc:
            self.run(mocker, "pull", "--settings", self.settings(tmp_path, self.FULL_S3))
        assert exc.value.code == 1
        assert "S3 not reachable" in capsys.readouterr().out

    def export(self, tmp_path, name="e.xlsx"):
        from tests.helpers import food_row, write_food_log

        return write_food_log(tmp_path / name, [food_row("Apple")])

    def test_push_uploads_exports_before_data_files(self, mocker, tmp_path, capsys):
        from tests.helpers import FakeS3Client

        self.db.parent.mkdir()
        self.db.write_text("{}")
        fake = FakeS3Client()
        mocker.patch("omni_pilot.cli.S3Client", return_value=fake)
        path = self.export(tmp_path)
        self.run(mocker, "push", "--export", path, "--settings", self.settings(tmp_path, self.FULL_S3))
        keys = [k for op, k in fake.calls if op == "put"]
        assert keys[0].startswith("exports/")
        assert keys[1] == "food_db.json"
        out = capsys.readouterr().out
        assert "uploaded" in out and "database" in out

    @pytest.mark.parametrize("repeat_flag", [False, True], ids=["one-flag", "flag-per-file"])
    def test_push_accepts_several_exports(self, mocker, tmp_path, repeat_flag):
        from tests.helpers import FakeS3Client, food_row, write_food_log

        fake = FakeS3Client()
        mocker.patch("omni_pilot.cli.S3Client", return_value=fake)
        first = write_food_log(tmp_path / "a.xlsx", [food_row("Apple", date="2026-10-01")])
        second = write_food_log(tmp_path / "b.xlsx", [food_row("Pear", date="2026-10-02")])
        flags = ["--export", first, "--export", second] if repeat_flag else ["--export", first, second]
        self.run(mocker, "push", *flags, "--settings", self.settings(tmp_path, self.FULL_S3))
        exports = [k for op, k in fake.calls if op == "put" and k.startswith("exports/")]
        assert [k[len("exports/"):][:10] for k in exports] == ["2026-10-01", "2026-10-02"]

    def test_push_reports_export_already_in_s3(self, mocker, tmp_path, capsys):
        from tests.helpers import FakeS3Client

        fake = FakeS3Client()
        mocker.patch("omni_pilot.cli.S3Client", return_value=fake)
        path = self.export(tmp_path)
        argv = ["push", "--export", path, "--settings", self.settings(tmp_path, self.FULL_S3)]
        self.run(mocker, *argv)
        capsys.readouterr()
        self.run(mocker, *argv)
        assert "already in S3" in capsys.readouterr().out

    def test_push_export_failure_pushes_no_data_file(self, mocker, tmp_path, capsys):
        from omni_pilot.s3 import S3Error
        from tests.helpers import FakeS3Client

        self.db.parent.mkdir()
        self.db.write_text("{}")
        fake = FakeS3Client()
        orig_put = fake.put

        def failing_put(key, body, **kw):
            if key.startswith("exports/"):
                raise S3Error("network", "S3 not reachable")
            return orig_put(key, body, **kw)

        fake.put = failing_put
        mocker.patch("omni_pilot.cli.S3Client", return_value=fake)
        with pytest.raises(SystemExit) as exc:
            self.run(mocker, "push", "--export", self.export(tmp_path),
                     "--settings", self.settings(tmp_path, self.FULL_S3))
        assert exc.value.code == 1
        assert "food_db.json" not in [k for _, k in fake.calls]

    def test_push_conflict_exits_1_with_message(self, mocker, tmp_path, capsys):
        from tests.helpers import FakeS3Client

        self.db.parent.mkdir()
        self.db.write_text("{}")
        fake = FakeS3Client({"food_db.json": (b"other", '"x"')})  # no state: if_none_match conflicts
        mocker.patch("omni_pilot.cli.S3Client", return_value=fake)
        with pytest.raises(SystemExit) as exc:
            self.run(mocker, "push", "--settings", self.settings(tmp_path, self.FULL_S3))
        assert exc.value.code == 1
        out = capsys.readouterr().out
        assert (
            "food_db.json changed in S3 since it was downloaded (another device or the daily routine "
            "wrote it). Nothing more was uploaded. Run `make pull` (it replaces your local changes), "
            "redo them, then push again." in out
        )


class TestCLIAnalyzeS3:
    """analyze pulls from S3 before it starts and saves the export and data files after the report."""

    FULL_S3 = TestCLIPullPush.FULL_S3

    @pytest.fixture(autouse=True)
    def _isolated(self, mocker, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)  # reports/ is written in the working directory
        self.db = tmp_path / "db" / "food_db.json"
        self.mappings = tmp_path / "food_mappings.yaml"
        self.recipes = tmp_path / "recipes.yaml"
        self.ref_ranges = tmp_path / "ref_ranges.yaml"
        self.ref_ranges.write_text(yaml.dump({"nutrients": {}}))
        from tests.helpers import food_row, write_food_log

        self.export = write_food_log(
            tmp_path / "data.xlsx", [food_row("Apple", date="2026-10-01"), food_row("Apple", date="2026-10-02")]
        )
        mocker.patch("omni_pilot.cli.resolve_and_sync_mappings", return_value=({"Apple": "apple"}, set()))
        self.mock_enrich = mocker.patch("omni_pilot.cli.enrich_all_foods", side_effect=self._enrich)
        mocker.patch("omni_pilot.cli.analyze", return_value={})
        self.mock_report = mocker.patch("omni_pilot.cli.print_terminal_report")
        self.mock_html = mocker.patch("omni_pilot.cli.generate_html_report", side_effect=self._write_html)
        self.enrichment = enrichment({"Apple": {}})

    def _enrich(self, food_names, mappings, logged_macros, db, api_key, custom_foods, untranslated, weight_unknown):
        db.insert({"original_name": "Apple", "usda_query": "apple", "per_100g": {}})  # the run changes the database
        return self.enrichment

    @staticmethod
    def _write_html(result, path):
        with open(path, "w") as f:
            f.write("<html></html>")

    def settings(self, tmp_path, s3=None):
        path = tmp_path / "settings.yaml"
        body = {
            "usda_api_key": "fake_key",
            "database_path": str(self.db),
            "mappings_path": str(self.mappings),
            "custom_foods_path": str(self.recipes),
        }
        if s3 is not None:
            body["s3"] = s3
        path.write_text(yaml.dump(body))
        return str(path)

    def fake(self, **extra):
        from tests.helpers import FakeS3Client

        objects = {
            "food_db.json": (b'{"_default": {}}', '"db-1"'),
            "food_mappings.yaml": (b"mappings:\n  Apple: apple\n", '"map-1"'),
        }
        objects.update(extra)
        return FakeS3Client(objects, bucket="omni-pilot-data")

    def run(self, mocker, tmp_path, s3=None, html=False):
        argv = ["omni_pilot", "analyze", self.export, "--settings", self.settings(tmp_path, s3),
                "--ref-ranges", str(self.ref_ranges)]
        if html:
            argv.append("--html")
        mocker.patch("sys.argv", argv)
        main()

    @staticmethod
    def output(capsys) -> str:
        """Captured stdout with line breaks folded, since Rich wraps long lines."""
        return " ".join(capsys.readouterr().out.split())

    def puts(self, fake) -> list[str]:
        return [key for op, key in fake.calls if op == "put"]

    def test_analyze_not_configured_makes_no_s3_call_and_says_so(self, mocker, tmp_path, capsys):
        mock_client = mocker.patch("omni_pilot.cli.S3Client")
        self.run(mocker, tmp_path)
        mock_client.assert_not_called()
        self.mock_report.assert_called_once()
        assert "S3: not configured — local files only" in capsys.readouterr().out

    def test_analyze_incomplete_s3_settings_exit_1_before_anything(self, mocker, tmp_path, capsys):
        mock_client = mocker.patch("omni_pilot.cli.S3Client")
        with pytest.raises(SystemExit) as exc:
            self.run(mocker, tmp_path, s3={"bucket": "omni-pilot-data"})
        assert exc.value.code == 1
        assert "region" in capsys.readouterr().out
        mock_client.assert_not_called()
        self.mock_enrich.assert_not_called()
        self.mock_report.assert_not_called()

    def test_analyze_pull_failure_exits_1_without_report(self, mocker, tmp_path, capsys):
        fake = self.fake()
        fake.fail_get_on = "food_db.json"
        mocker.patch("omni_pilot.cli.S3Client", return_value=fake)
        with pytest.raises(SystemExit) as exc:
            self.run(mocker, tmp_path, s3=self.FULL_S3, html=True)
        assert exc.value.code == 1
        assert "S3 not reachable" in capsys.readouterr().out
        self.mock_enrich.assert_not_called()
        self.mock_report.assert_not_called()
        assert not (tmp_path / "reports" / "latest.html").exists()
        assert self.puts(fake) == []

    def test_analyze_pulls_before_reading_custom_foods(self, mocker, tmp_path, capsys):
        fake = self.fake(**{"custom_foods.yaml": (CAPRESE_RECIPE.encode(), '"rec-1"')})
        mocker.patch("omni_pilot.cli.S3Client", return_value=fake)
        assert not self.recipes.exists()
        self.run(mocker, tmp_path, s3=self.FULL_S3)
        out = capsys.readouterr().out
        assert "S3: pulled from omni-pilot-data (database, mappings, recipes)" in out
        assert f"Custom foods: {self.recipes} (1 recipes, 1 foods)" in out
        assert self.mock_enrich.call_args.kwargs["custom_foods"]["by_food"] == {"Salat Caprese": "caprese"}

    def test_analyze_uploads_export_then_pushes(self, mocker, tmp_path, capsys):
        fake = self.fake()
        mocker.patch("omni_pilot.cli.S3Client", return_value=fake)
        self.run(mocker, tmp_path, s3=self.FULL_S3)
        puts = self.puts(fake)
        assert puts[0].startswith("exports/2026-10-01_2026-10-02_")
        assert puts[1:] == ["food_db.json"]
        name = puts[0].removeprefix("exports/")
        assert f"S3: uploaded export {name}; database" in capsys.readouterr().out

    def test_analyze_same_export_twice_still_pushes_and_exits_0(self, mocker, tmp_path, capsys):
        fake = self.fake()
        mocker.patch("omni_pilot.cli.S3Client", return_value=fake)
        self.run(mocker, tmp_path, s3=self.FULL_S3)
        capsys.readouterr()
        fake.calls.clear()

        self.run(mocker, tmp_path, s3=self.FULL_S3)  # no SystemExit

        assert self.puts(fake)[-1] == "food_db.json"
        out = capsys.readouterr().out
        assert "already in S3; database" in out

    def test_analyze_says_nothing_changed_when_nothing_was_uploaded(self, mocker, tmp_path, capsys):
        fake = self.fake()
        mocker.patch("omni_pilot.cli.S3Client", return_value=fake)
        self.run(mocker, tmp_path, s3=self.FULL_S3)
        capsys.readouterr()
        self.mock_enrich.side_effect = lambda *args, **kwargs: self.enrichment  # database left as pulled

        self.run(mocker, tmp_path, s3=self.FULL_S3)

        assert "S3: nothing changed" in capsys.readouterr().out

    def test_analyze_export_without_entries_is_not_uploaded_but_data_files_are(self, mocker, tmp_path, capsys):
        from tests.helpers import write_food_log

        self.export = write_food_log(tmp_path / "empty.xlsx", [])
        fake = self.fake()
        mocker.patch("omni_pilot.cli.S3Client", return_value=fake)
        self.run(mocker, tmp_path, s3=self.FULL_S3)
        assert self.puts(fake) == ["food_db.json"]
        out = capsys.readouterr().out
        assert "S3: export has no Food Log entries — not uploaded" in out
        assert "S3: database" in out

    def test_analyze_export_upload_failure_pushes_no_data_file(self, mocker, tmp_path, capsys):
        from omni_pilot.s3 import S3Error

        fake = self.fake()
        orig_put = fake.put

        def failing_put(key, body, **kw):
            if key.startswith("exports/"):
                fake.calls.append(("put", key))
                raise S3Error("network", "S3 not reachable — is the network down?")
            return orig_put(key, body, **kw)

        fake.put = failing_put
        mocker.patch("omni_pilot.cli.S3Client", return_value=fake)
        with pytest.raises(SystemExit) as exc:
            self.run(mocker, tmp_path, s3=self.FULL_S3, html=True)
        assert exc.value.code == 1
        assert "food_db.json" not in self.puts(fake)
        self.mock_report.assert_called_once()
        assert (tmp_path / "reports" / "latest.html").exists()
        out = self.output(capsys)
        assert "Results were not saved to S3: S3 not reachable — is the network down? Run analyze again." in out

    def test_analyze_push_conflict_writes_report_then_red_line_exit_1(self, mocker, tmp_path, capsys):
        fake = self.fake()
        mocker.patch("omni_pilot.cli.S3Client", return_value=fake)

        def enrich_while_another_device_writes(*args, **kwargs):
            fake.objects["food_db.json"] = (b"newer", '"db-2"')
            return self._enrich(*args, **kwargs)

        self.mock_enrich.side_effect = enrich_while_another_device_writes
        with pytest.raises(SystemExit) as exc:
            self.run(mocker, tmp_path, s3=self.FULL_S3, html=True)
        assert exc.value.code == 1
        self.mock_report.assert_called_once()
        self.mock_html.assert_called_once()
        assert (tmp_path / "reports" / "latest.html").exists()
        out = self.output(capsys)
        assert "Results were not saved to S3:" in out
        assert "food_db.json changed in S3 since it was downloaded" in out
        assert out.endswith("Nothing more was uploaded. Run analyze again.")

    def test_analyze_bar43_stop_does_not_upload(self, mocker, tmp_path, capsys):
        fake = self.fake()
        mocker.patch("omni_pilot.cli.S3Client", return_value=fake)
        self.enrichment = enrichment(lookup_failed={"Apple"})
        with pytest.raises(SystemExit) as exc:
            self.run(mocker, tmp_path, s3=self.FULL_S3)
        assert exc.value.code == 1
        self.mock_report.assert_not_called()
        assert self.puts(fake) == []
