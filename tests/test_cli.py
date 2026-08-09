from __future__ import annotations

import os
import pytest
from unittest.mock import patch

from omni_pilot.cli import main


class TestCLIImport:
    def test_import_creates_mappings_file(self, tmp_path):
        """Integration test: import command with real xlsx."""
        mappings_path = str(tmp_path / "food_mappings.yaml")

        with patch("sys.argv", [
            "omni_pilot", "import",
            "data/MacroFactor-example.xlsx",
            "--mappings-output", mappings_path,
        ]):
            main()

        assert os.path.exists(mappings_path)
        import yaml
        with open(mappings_path) as f:
            data = yaml.safe_load(f)
        assert "mappings" in data
        assert "Boiled Eggs" in data["mappings"]


class TestCLIHelp:
    def test_help_exits_cleanly(self):
        with patch("sys.argv", ["omni_pilot", "--help"]):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0

class TestCLIDatabase:
    def test_import_reads_from_tinydb(self, tmp_path):
        from tinydb import TinyDB
        import yaml
        db_path = str(tmp_path / "test_db.json")
        mappings_path = str(tmp_path / "food_mappings.yaml")
        
        # Pre-seed database with a known translation
        db = TinyDB(db_path)
        db.table("translations").insert({"german": "Boiled Eggs", "english": "perfectly boiled eggs"})
        
        with patch("sys.argv", [
            "omni_pilot", "import",
            "data/MacroFactor-example.xlsx",
            "--mappings-output", mappings_path,
            "--db", db_path,
        ]):
            main()
            
        with open(mappings_path) as f:
            data = yaml.safe_load(f)
            
        assert "mappings" in data
        assert data["mappings"].get("Boiled Eggs") == "perfectly boiled eggs"

    @patch("omni_pilot.cli.enrich_all_foods")
    @patch("omni_pilot.cli.analyze")
    @patch("omni_pilot.cli.print_terminal_report")
    @patch("omni_pilot.cli.parse_food_log")
    def test_analyze_writes_to_tinydb(self, mock_parse, mock_print, mock_analyze, mock_enrich, tmp_path):
        from tinydb import TinyDB, Query
        import yaml
        
        db_path = str(tmp_path / "test_db.json")
        mappings_path = str(tmp_path / "test_mappings.yaml")
        settings_path = str(tmp_path / "settings.yaml")
        ref_ranges_path = str(tmp_path / "ref_ranges.yaml")
        
        # Setup mock files
        with open(mappings_path, "w") as f:
            yaml.dump({"mappings": {"Apfel": "apple", "Banane": ""}}, f)
        with open(settings_path, "w") as f:
            yaml.dump({"usda_api_key": "fake_key"}, f)
        with open(ref_ranges_path, "w") as f:
            yaml.dump({"nutrients": {}}, f)
            
        # Mock parser
        mock_parse.return_value = [{"food_name": "Apfel"}, {"food_name": "Banane"}]
        mock_enrich.return_value = {"apple": {}}
        mock_analyze.return_value = {}
        
        with patch("sys.argv", [
            "omni_pilot", "analyze",
            "data/MacroFactor-example.xlsx",
            "--mappings", mappings_path,
            "--settings", settings_path,
            "--ref-ranges", ref_ranges_path,
            "--db", db_path,
        ]):
            main()
            
        db = TinyDB(db_path)
        translations = db.table("translations").all()
        
        # It should only save non-empty mappings
        assert len(translations) == 1
        assert translations[0]["german"] == "Apfel"
        assert translations[0]["english"] == "apple"
