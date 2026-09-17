"""tests/test_cli.py"""
from __future__ import annotations

import os

import pytest
import yaml
from tinydb import TinyDB

from omni_pilot.cli import main
from tests.helpers import enrichment


class TestCLIHelp:
    def test_help_exits_cleanly(self, mocker):
        mocker.patch("sys.argv", ["omni_pilot", "--help"])
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 0


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
            return_value=[{"food_name": "Apfel", "total_weight_g": 100.0, "date": "2026-08-01"}],
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
            return_value=[
                {"food_name": name, "total_weight_g": 100.0, "date": "2026-08-01"}
                for name in ("Apfel", "Wasser", "Lachs")
            ],
        )
        mocker.patch(
            "omni_pilot.cli.resolve_and_sync_mappings",
            return_value={"Apfel": "apple", "Wasser": "skip", "Lachs": "salmon"},
        )
        mocker.patch(
            "omni_pilot.cli.enrich_all_foods",
            return_value=enrichment({"Apfel": {}}, skipped={"Wasser"}, unresolved={"Lachs"}),
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
            return_value=[{"food_name": "Apfel", "total_weight_g": 100.0, "date": "2026-08-01"}],
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
            return_value=[{"food_name": "Apfel", "total_weight_g": 100.0, "date": "2026-08-01"}],
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
