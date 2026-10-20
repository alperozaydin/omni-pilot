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
            return_value={"Apfel": "apple", "Wasser": "skip", "Lachs": "salmon"},
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
            "omni_pilot.cli.resolve_and_sync_mappings", return_value={"Reis": "rice, cooked"},
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
            food_entry("Reis", date="2026-08-09", calories_kcal=1300.0),
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
        for var in ("BUCKET", "REGION", "ACCESS_KEY_ID", "SECRET_ACCESS_KEY"):
            monkeypatch.delenv(f"OMNI_PILOT_S3_{var}", raising=False)
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
        assert "changed in S3" in capsys.readouterr().out
