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

    @patch("omni_pilot.cli.translate_new_foods")
    def test_import_uses_gemini_translation(self, mock_translate, tmp_path):
        """Integration test: import command with mocked Gemini translation."""
        import yaml
        from tinydb import TinyDB
        
        mappings_path = str(tmp_path / "food_mappings.yaml")
        db_path = str(tmp_path / "test_db.json")
        settings_path = str(tmp_path / "settings.yaml")
        
        # Setup settings with a fake API key so translation runs
        with open(settings_path, "w") as f:
            yaml.dump({"gemini_api_key": "fake_gemini_key"}, f)
            
        def mock_translate_side_effect(new_foods, api_key, *args, **kwargs):
            return [f"Mock {food}" for food in new_foods]
        mock_translate.side_effect = mock_translate_side_effect
        
        with patch("sys.argv", [
            "omni_pilot", "import",
            "data/MacroFactor-example.xlsx",
            "--mappings-output", mappings_path,
            "--settings", settings_path,
            "--db", db_path,
        ]):
            main()
            
        assert mock_translate.called
        
        # Verify it wrote to DB and YAML
        with open(mappings_path) as f:
            data = yaml.safe_load(f)
        
        # Check if Boiled Eggs was translated
        assert data["mappings"].get("Boiled Eggs") == "Mock Boiled Eggs"
        
        db = TinyDB(db_path)
        translations = db.table("translations").all()
        # Ensure it got written to DB
        assert any(t["german"] == "Boiled Eggs" and t["english"] == "Mock Boiled Eggs" for t in translations)

    @patch("omni_pilot.cli.requests.post")
    def test_translate_new_foods_rest_success(self, mock_post):
        """Unit test for translate_new_foods REST implementation."""
        from omni_pilot.cli import translate_new_foods
        from unittest.mock import MagicMock
        import json
        
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {"text": json.dumps(["Cheese", "Ground beef"])}
                        ]
                    }
                }
            ]
        }
        mock_post.return_value = mock_response
        
        result = translate_new_foods(["Burger Cheese", "Hackfleisch"], "test_key", model="gemini-flash-latest")
        assert result == ["Cheese", "Ground beef"]
        assert mock_post.called
        call_args = mock_post.call_args
        assert "gemini-flash-latest:generateContent?key=test_key" in call_args[0][0]



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

    @patch("omni_pilot.cli.generate_html_report")
    @patch("omni_pilot.cli.enrich_all_foods")
    @patch("omni_pilot.cli.analyze")
    @patch("omni_pilot.cli.print_terminal_report")
    @patch("omni_pilot.cli.parse_food_log")
    def test_analyze_html_generates_report(
        self, mock_parse, mock_print, mock_analyze, mock_enrich, mock_html, tmp_path
    ):
        import yaml
        mappings_path = str(tmp_path / "test_mappings.yaml")
        settings_path = str(tmp_path / "settings.yaml")
        ref_ranges_path = str(tmp_path / "ref_ranges.yaml")
        
        with open(mappings_path, "w") as f:
            yaml.dump({"mappings": {"Apfel": "apple"}}, f)
        with open(settings_path, "w") as f:
            yaml.dump({"usda_api_key": "fake_key"}, f)
        with open(ref_ranges_path, "w") as f:
            yaml.dump({"nutrients": {}}, f)
            
        mock_parse.return_value = [{"food_name": "Apfel"}]
        mock_enrich.return_value = {"apple": {}}
        mock_analyze.return_value = {}
        
        with patch("sys.argv", [
            "omni_pilot", "analyze",
            "data/MacroFactor-example.xlsx",
            "--mappings", mappings_path,
            "--settings", settings_path,
            "--ref-ranges", ref_ranges_path,
            "--html",
        ]):
            main()
            
        assert mock_html.called
        call_path = mock_html.call_args[0][1]
        assert "reports/micronutrient-report-" in call_path
        assert call_path.endswith(".html")


