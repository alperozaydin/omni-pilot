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
            "data/MacroFactor-20260809145742.xlsx",
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
