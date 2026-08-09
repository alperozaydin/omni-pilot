# Micronutrient Analysis Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a CLI tool that parses MacroFactor food logs, enriches them with USDA micronutrient data, and reports daily average intake vs. WHO/NIH recommended ranges.

**Architecture:** Modular Python CLI with 5 components — parser (xlsx → dicts), enricher (USDA API + TinyDB cache), analyzer (daily averages vs. reference ranges), reporter (Rich terminal + HTML), and CLI (argparse entry point). Data flows linearly: parse → enrich → analyze → report.

**Tech Stack:** Python 3, openpyxl, requests, tinydb, rich, pyyaml, jinja2

## Global Constraints

- Python 3.9+ (use `from __future__ import annotations` for type hints)
- All dependencies installed in project `.venv` — never global pip
- All config files live in `config/` directory
- TinyDB storage lives in `db/` directory
- Source code lives in `src/omni_pilot/`
- Tests live in `tests/` mirroring `src/` structure
- USDA API key is in `config/settings.yaml` (already set by user)
- Reference ranges are in `config/reference_ranges.yaml` (already created)
- Unit system: grams only — no ml handling
- Run with: `python -m omni_pilot <command>` from project root
- Use `logging` module for warnings (not print)

---

### Task 1: Project Scaffolding & Config Loader

**Files:**
- Create: `src/omni_pilot/__init__.py`
- Create: `src/omni_pilot/__main__.py`
- Create: `src/omni_pilot/config.py`
- Create: `config/settings.yaml`
- Create: `requirements.txt`
- Create: `.gitignore`
- Test: `tests/__init__.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: `config/settings.yaml`, `config/reference_ranges.yaml` (exists), `config/food_mappings.yaml` (may not exist yet)
- Produces:
  - `load_settings(path: str) -> dict` — returns parsed settings.yaml
  - `load_reference_ranges(path: str) -> dict` — returns parsed reference_ranges.yaml with computed amino acid targets
  - `load_food_mappings(path: str) -> dict[str, str]` — returns food name → English mapping (empty dict if file doesn't exist)
  - `get_nutrient_target(nutrient_key: str, ref_ranges: dict) -> tuple[float | None, float | None, str]` — returns `(target_value, ul_value, target_type)` where target_type is "rda" or "ai"

- [ ] **Step 1: Create requirements.txt**

```
openpyxl>=3.1.0
tinydb>=4.8.0
requests>=2.31.0
rich>=13.0.0
pyyaml>=6.0
jinja2>=3.1.0
pytest>=7.0.0
```

- [ ] **Step 2: Install dependencies**

Run: `source .venv/bin/activate && pip install -r requirements.txt --quiet`
Expected: All packages install successfully

- [ ] **Step 3: Create .gitignore**

```gitignore
.venv/
__pycache__/
*.pyc
db/food_db.json
.DS_Store
reports/
```

- [ ] **Step 4: Create config/settings.yaml**

```yaml
# USDA FoodData Central API key
usda_api_key: "6xJYsHf1Vlm7dbDZaG8Lb6SOq68tme3vSxY5YEBN"

# Output preferences
output:
  show_amino_acids: true
  show_ok_nutrients: true
```

- [ ] **Step 5: Create package init and __main__**

`src/omni_pilot/__init__.py`:
```python
"""Omni Pilot — Nutrition & Workout Analysis."""
```

`src/omni_pilot/__main__.py`:
```python
"""Entry point for python -m omni_pilot."""
from omni_pilot.cli import main

if __name__ == "__main__":
    main()
```

- [ ] **Step 6: Write the failing test for config loading**

`tests/__init__.py`: empty file

`tests/test_config.py`:
```python
from __future__ import annotations

import os
import tempfile
import pytest
import yaml

from omni_pilot.config import (
    load_settings,
    load_reference_ranges,
    load_food_mappings,
    get_nutrient_target,
)


def _write_yaml(path: str, data: dict) -> None:
    with open(path, "w") as f:
        yaml.dump(data, f)


class TestLoadSettings:
    def test_loads_valid_settings(self, tmp_path):
        settings_file = tmp_path / "settings.yaml"
        _write_yaml(str(settings_file), {
            "usda_api_key": "test-key",
            "output": {"show_amino_acids": True, "show_ok_nutrients": True},
        })
        result = load_settings(str(settings_file))
        assert result["usda_api_key"] == "test-key"
        assert result["output"]["show_amino_acids"] is True

    def test_raises_on_missing_file(self):
        with pytest.raises(FileNotFoundError):
            load_settings("/nonexistent/settings.yaml")


class TestLoadReferenceRanges:
    def test_loads_nutrients(self, tmp_path):
        ref_file = tmp_path / "reference_ranges.yaml"
        _write_yaml(str(ref_file), {
            "demographic": {"sex": "male", "age_group": "19-50", "body_weight_kg": 75},
            "nutrients": {
                "vitamin_a_mcg": {
                    "name": "Vitamin A", "unit": "mcg", "type": "rda",
                    "rda": 900, "ul": 3000, "source": "NIH",
                },
                "histidine_g": {
                    "name": "Histidine", "unit": "g", "type": "who_per_kg",
                    "mg_per_kg": 10, "safe_mg_per_kg": 12, "ul": None,
                    "source": "FAO/WHO/UNU 2007",
                },
            },
        })
        result = load_reference_ranges(str(ref_file))
        assert result["nutrients"]["vitamin_a_mcg"]["rda"] == 900
        assert result["demographic"]["body_weight_kg"] == 75

    def test_raises_on_missing_file(self):
        with pytest.raises(FileNotFoundError):
            load_reference_ranges("/nonexistent/ref.yaml")


class TestLoadFoodMappings:
    def test_loads_existing_mappings(self, tmp_path):
        mappings_file = tmp_path / "food_mappings.yaml"
        _write_yaml(str(mappings_file), {
            "mappings": {"Haferflocken": "rolled oats", "Quick Add": "skip"},
        })
        result = load_food_mappings(str(mappings_file))
        assert result["Haferflocken"] == "rolled oats"
        assert result["Quick Add"] == "skip"

    def test_returns_empty_dict_when_file_missing(self, tmp_path):
        result = load_food_mappings(str(tmp_path / "nonexistent.yaml"))
        assert result == {}


class TestGetNutrientTarget:
    def test_rda_nutrient(self):
        ref_ranges = {
            "demographic": {"body_weight_kg": 75},
            "nutrients": {
                "vitamin_a_mcg": {
                    "type": "rda", "rda": 900, "ul": 3000,
                },
            },
        }
        target, ul, target_type = get_nutrient_target("vitamin_a_mcg", ref_ranges)
        assert target == 900
        assert ul == 3000
        assert target_type == "rda"

    def test_ai_nutrient(self):
        ref_ranges = {
            "demographic": {"body_weight_kg": 75},
            "nutrients": {
                "potassium_mg": {
                    "type": "ai", "ai": 3400, "ul": None,
                },
            },
        }
        target, ul, target_type = get_nutrient_target("potassium_mg", ref_ranges)
        assert target == 3400
        assert ul is None
        assert target_type == "ai"

    def test_who_per_kg_nutrient(self):
        ref_ranges = {
            "demographic": {"body_weight_kg": 75},
            "nutrients": {
                "histidine_g": {
                    "type": "who_per_kg", "safe_mg_per_kg": 12, "ul": None,
                },
            },
        }
        target, ul, target_type = get_nutrient_target("histidine_g", ref_ranges)
        # 12 mg/kg * 75 kg = 900 mg = 0.9 g
        assert target == pytest.approx(0.9)
        assert target_type == "who_per_kg"

    def test_who_per_kg_missing_body_weight(self):
        ref_ranges = {
            "demographic": {"body_weight_kg": None},
            "nutrients": {
                "histidine_g": {
                    "type": "who_per_kg", "safe_mg_per_kg": 12, "ul": None,
                },
            },
        }
        target, ul, target_type = get_nutrient_target("histidine_g", ref_ranges)
        assert target is None
```

- [ ] **Step 7: Run tests to verify they fail**

Run: `source .venv/bin/activate && PYTHONPATH=src pytest tests/test_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'omni_pilot.config'`

- [ ] **Step 8: Implement config.py**

`src/omni_pilot/config.py`:
```python
"""Configuration loading and validation."""
from __future__ import annotations

import os
import yaml


def load_settings(path: str) -> dict:
    """Load settings.yaml and return as dict.

    Raises FileNotFoundError if file does not exist.
    """
    if not os.path.exists(path):
        raise FileNotFoundError(f"Settings file not found: {path}")
    with open(path, "r") as f:
        return yaml.safe_load(f)


def load_reference_ranges(path: str) -> dict:
    """Load reference_ranges.yaml and return as dict.

    Raises FileNotFoundError if file does not exist.
    """
    if not os.path.exists(path):
        raise FileNotFoundError(f"Reference ranges file not found: {path}")
    with open(path, "r") as f:
        return yaml.safe_load(f)


def load_food_mappings(path: str) -> dict[str, str]:
    """Load food_mappings.yaml and return the mappings dict.

    Returns empty dict if file does not exist (mappings not yet generated).
    """
    if not os.path.exists(path):
        return {}
    with open(path, "r") as f:
        data = yaml.safe_load(f)
    if data is None or "mappings" not in data:
        return {}
    return data["mappings"]


def get_nutrient_target(
    nutrient_key: str,
    ref_ranges: dict,
) -> tuple[float | None, float | None, str]:
    """Get the daily target and UL for a nutrient.

    For RDA/AI nutrients, returns the rda/ai value directly.
    For WHO per-kg amino acids, computes target from body weight.

    Returns:
        (target_value, ul_value, target_type)
        target_value is None if it cannot be computed (e.g. missing body weight).
    """
    nutrient = ref_ranges["nutrients"][nutrient_key]
    nutrient_type = nutrient["type"]
    ul = nutrient.get("ul")

    if nutrient_type == "rda":
        return nutrient["rda"], ul, "rda"
    elif nutrient_type == "ai":
        return nutrient["ai"], ul, "ai"
    elif nutrient_type == "who_per_kg":
        body_weight = ref_ranges["demographic"].get("body_weight_kg")
        if body_weight is None:
            return None, ul, "who_per_kg"
        # safe_mg_per_kg * body_weight_kg / 1000 to convert mg to g
        target_g = nutrient["safe_mg_per_kg"] * body_weight / 1000
        return target_g, ul, "who_per_kg"
    else:
        return None, ul, nutrient_type
```

- [ ] **Step 9: Run tests to verify they pass**

Run: `source .venv/bin/activate && PYTHONPATH=src pytest tests/test_config.py -v`
Expected: All tests PASS

- [ ] **Step 10: Commit**

```bash
git add -A
git commit -m "feat: project scaffolding and config loader"
```

---

### Task 2: XLSX Parser

**Files:**
- Create: `src/omni_pilot/parser.py`
- Test: `tests/test_parser.py`

**Interfaces:**
- Consumes: MacroFactor `.xlsx` file path
- Produces:
  - `FoodEntry = TypedDict("FoodEntry", date=str, time=str, food_name=str, serving_size=str, serving_qty=float, serving_weight_g=float, total_weight_g=float, calories_kcal=float, protein_g=float, fat_g=float, carbs_g=float)`
  - `parse_food_log(xlsx_path: str) -> list[FoodEntry]` — parse "Food Log" sheet, returns list of food entries
  - `extract_unique_foods(entries: list[FoodEntry]) -> list[str]` — returns sorted unique food names
  - `generate_food_mappings(foods: list[str], existing_mappings: dict[str, str], output_path: str) -> None` — writes/merges food_mappings.yaml

- [ ] **Step 1: Write the failing test**

`tests/test_parser.py`:
```python
from __future__ import annotations

import pytest
import yaml

from omni_pilot.parser import parse_food_log, extract_unique_foods, generate_food_mappings


class TestParseFoodLog:
    def test_parses_real_xlsx(self):
        """Integration test with the actual MacroFactor export."""
        entries = parse_food_log("data/MacroFactor-20260809145742.xlsx")
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
        entries = parse_food_log("data/MacroFactor-20260809145742.xlsx")
        # Find "Boiled Eggs" entry: 3 servings × 56g = 168g
        egg_entries = [e for e in entries if e["food_name"] == "Boiled Eggs"]
        assert len(egg_entries) > 0
        first_egg = egg_entries[0]
        assert first_egg["total_weight_g"] == pytest.approx(
            first_egg["serving_qty"] * first_egg["serving_weight_g"]
        )

    def test_skips_entries_with_no_food_name(self):
        entries = parse_food_log("data/MacroFactor-20260809145742.xlsx")
        for entry in entries:
            assert entry["food_name"] is not None
            assert entry["food_name"] != ""


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
    def test_creates_new_mapping_file(self, tmp_path):
        output_path = str(tmp_path / "food_mappings.yaml")
        foods = ["Banana", "Haferflocken", "Quick Add"]
        generate_food_mappings(foods, {}, output_path)

        with open(output_path) as f:
            data = yaml.safe_load(f)
        assert "mappings" in data
        assert data["mappings"]["Banana"] == ""
        assert data["mappings"]["Haferflocken"] == ""
        assert data["mappings"]["Quick Add"] == ""

    def test_preserves_existing_mappings(self, tmp_path):
        output_path = str(tmp_path / "food_mappings.yaml")
        foods = ["Banana", "Haferflocken", "New Food"]
        existing = {"Banana": "banana", "Haferflocken": "rolled oats"}
        generate_food_mappings(foods, existing, output_path)

        with open(output_path) as f:
            data = yaml.safe_load(f)
        assert data["mappings"]["Banana"] == "banana"
        assert data["mappings"]["Haferflocken"] == "rolled oats"
        assert data["mappings"]["New Food"] == ""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `source .venv/bin/activate && PYTHONPATH=src pytest tests/test_parser.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'omni_pilot.parser'`

- [ ] **Step 3: Implement parser.py**

`src/omni_pilot/parser.py`:
```python
"""Parse MacroFactor xlsx exports."""
from __future__ import annotations

import logging
from typing import TypedDict

import openpyxl
import yaml

logger = logging.getLogger(__name__)


class FoodEntry(TypedDict):
    date: str
    time: str
    food_name: str
    serving_size: str
    serving_qty: float
    serving_weight_g: float
    total_weight_g: float
    calories_kcal: float
    protein_g: float
    fat_g: float
    carbs_g: float


# Column indices in the "Food Log" sheet (0-indexed)
_COL_DATE = 0
_COL_TIME = 1
_COL_FOOD_NAME = 2
_COL_SERVING_SIZE = 3
_COL_SERVING_QTY = 4
_COL_SERVING_WEIGHT_G = 5
_COL_CALORIES = 6
_COL_FAT = 7
_COL_CARBS = 8
_COL_PROTEIN = 9


def parse_food_log(xlsx_path: str) -> list[FoodEntry]:
    """Parse the 'Food Log' sheet from a MacroFactor export.

    Returns a list of FoodEntry dicts. Skips rows with no food name.
    """
    wb = openpyxl.load_workbook(xlsx_path, read_only=True)
    ws = wb["Food Log"]

    entries: list[FoodEntry] = []
    for row_idx, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
        food_name = row[_COL_FOOD_NAME]
        if food_name is None or str(food_name).strip() == "":
            continue

        food_name = str(food_name).strip()
        serving_qty = float(row[_COL_SERVING_QTY] or 0)
        serving_weight_g = float(row[_COL_SERVING_WEIGHT_G] or 0)
        total_weight_g = serving_qty * serving_weight_g

        if food_name == "Quick Add":
            logger.warning(
                "Row %d: 'Quick Add' entry skipped for micro analysis "
                "(no food name to look up).",
                row_idx,
            )

        entries.append(FoodEntry(
            date=str(row[_COL_DATE] or ""),
            time=str(row[_COL_TIME] or ""),
            food_name=food_name,
            serving_size=str(row[_COL_SERVING_SIZE] or ""),
            serving_qty=serving_qty,
            serving_weight_g=serving_weight_g,
            total_weight_g=total_weight_g,
            calories_kcal=float(row[_COL_CALORIES] or 0),
            protein_g=float(row[_COL_PROTEIN] or 0),
            fat_g=float(row[_COL_FAT] or 0),
            carbs_g=float(row[_COL_CARBS] or 0),
        ))

    wb.close()
    return entries


def extract_unique_foods(entries: list[FoodEntry]) -> list[str]:
    """Extract sorted list of unique food names from parsed entries."""
    return sorted({e["food_name"] for e in entries})


def generate_food_mappings(
    foods: list[str],
    existing_mappings: dict[str, str],
    output_path: str,
) -> None:
    """Generate or merge food_mappings.yaml.

    New foods get empty string values. Existing mappings are preserved.
    """
    merged: dict[str, str] = {}
    for food in sorted(foods):
        if food in existing_mappings:
            merged[food] = existing_mappings[food]
        else:
            merged[food] = ""

    data = {
        "mappings": merged,
    }

    header = (
        "# Generated by: python -m omni_pilot import\n"
        "# Fill in the USDA-searchable English name for each food.\n"
        "# Set to \"skip\" for foods to exclude from micro analysis.\n"
        "# Leave empty and re-run import to see which foods still need mapping.\n\n"
    )

    with open(output_path, "w") as f:
        f.write(header)
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `source .venv/bin/activate && PYTHONPATH=src pytest tests/test_parser.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat: xlsx parser with food mapping generation"
```

---

### Task 3: USDA Enricher & TinyDB Cache

**Files:**
- Create: `src/omni_pilot/enricher.py`
- Create: `db/` directory (empty, TinyDB creates file on first use)
- Test: `tests/test_enricher.py`

**Interfaces:**
- Consumes:
  - `load_food_mappings()` from `config.py` — `dict[str, str]`
  - `load_settings()` from `config.py` — for USDA API key
- Produces:
  - `USDA_NUTRIENT_MAP: dict[str, str]` — maps our nutrient keys to USDA nutrient numbers (e.g., `"vitamin_a_mcg": "320"`)
  - `get_food_micros(food_name: str, usda_query: str, db: TinyDB, api_key: str) -> dict | None` — returns per-100g micro dict from cache or USDA API. Returns None if unresolved.
  - `enrich_all_foods(food_names: list[str], mappings: dict[str, str], db: TinyDB, api_key: str) -> dict[str, dict | None]` — enriches all foods, returns `{food_name: per_100g_dict_or_None}`
  - `search_usda(query: str, api_key: str) -> dict | None` — raw USDA API search, returns best match food data or None
  - `extract_micros_from_usda(usda_food: dict) -> dict[str, float | None]` — extracts per-100g micros from USDA response

- [ ] **Step 1: Write the failing test**

`tests/test_enricher.py`:
```python
from __future__ import annotations

import json
import pytest
from unittest.mock import patch, MagicMock
from tinydb import TinyDB

from omni_pilot.enricher import (
    USDA_NUTRIENT_MAP,
    search_usda,
    extract_micros_from_usda,
    get_food_micros,
    enrich_all_foods,
)


class TestUsdaNutrientMap:
    def test_has_all_vitamin_keys(self):
        expected_keys = [
            "vitamin_a_mcg", "vitamin_c_mg", "vitamin_d_mcg", "vitamin_e_mg",
            "vitamin_k_mcg", "b1_thiamine_mg", "b2_riboflavin_mg",
            "b3_niacin_mg", "b5_pantothenic_acid_mg", "b6_pyridoxine_mg",
            "b12_cobalamin_mcg", "folate_mcg",
        ]
        for key in expected_keys:
            assert key in USDA_NUTRIENT_MAP

    def test_has_all_mineral_keys(self):
        expected_keys = [
            "calcium_mg", "iron_mg", "zinc_mg", "magnesium_mg",
            "manganese_mg", "phosphorus_mg", "potassium_mg",
            "selenium_mcg", "copper_mg", "sodium_mg",
        ]
        for key in expected_keys:
            assert key in USDA_NUTRIENT_MAP

    def test_has_amino_acid_keys(self):
        expected_keys = [
            "histidine_g", "isoleucine_g", "leucine_g", "lysine_g",
            "methionine_g", "cysteine_g", "phenylalanine_g", "tyrosine_g",
            "threonine_g", "tryptophan_g", "valine_g",
        ]
        for key in expected_keys:
            assert key in USDA_NUTRIENT_MAP


class TestExtractMicrosFromUsda:
    def test_extracts_nutrients_from_usda_response(self):
        usda_food = {
            "foodNutrients": [
                {"nutrientNumber": "320", "value": 149.0, "unitName": "UG"},
                {"nutrientNumber": "301", "value": 50.0, "unitName": "MG"},
                {"nutrientNumber": "303", "value": 1.19, "unitName": "MG"},
            ]
        }
        result = extract_micros_from_usda(usda_food)
        assert result["vitamin_a_mcg"] == 149.0
        assert result["calcium_mg"] == 50.0
        assert result["iron_mg"] == 1.19

    def test_missing_nutrients_are_none(self):
        usda_food = {"foodNutrients": []}
        result = extract_micros_from_usda(usda_food)
        assert result["vitamin_a_mcg"] is None


class TestGetFoodMicros:
    def test_returns_cached_data(self, tmp_path):
        db = TinyDB(str(tmp_path / "test_db.json"))
        db.insert({
            "original_name": "Boiled Eggs",
            "usda_query": "boiled egg",
            "per_100g": {"vitamin_a_mcg": 149.0, "calcium_mg": 50.0},
            "confidence": "direct",
        })
        result = get_food_micros("Boiled Eggs", "boiled egg", db, "fake-key")
        assert result["vitamin_a_mcg"] == 149.0

    @patch("omni_pilot.enricher.search_usda")
    def test_queries_usda_when_not_cached(self, mock_search, tmp_path):
        db = TinyDB(str(tmp_path / "test_db.json"))
        mock_search.return_value = {
            "description": "Egg, whole, cooked, hard-boiled",
            "fdcId": 173424,
            "dataType": "SR Legacy",
            "foodNutrients": [
                {"nutrientNumber": "320", "value": 149.0, "unitName": "UG"},
            ],
        }
        result = get_food_micros("Boiled Eggs", "boiled egg", db, "fake-key")
        assert result is not None
        assert result["vitamin_a_mcg"] == 149.0
        mock_search.assert_called_once_with("boiled egg", "fake-key")

    @patch("omni_pilot.enricher.search_usda")
    def test_returns_none_when_usda_has_no_results(self, mock_search, tmp_path):
        db = TinyDB(str(tmp_path / "test_db.json"))
        mock_search.return_value = None
        result = get_food_micros("Unknown Food", "unknown food", db, "fake-key")
        assert result is None


class TestEnrichAllFoods:
    @patch("omni_pilot.enricher.get_food_micros")
    def test_skips_foods_mapped_to_skip(self, mock_get, tmp_path):
        db = TinyDB(str(tmp_path / "test_db.json"))
        mappings = {"Quick Add": "skip", "Boiled Eggs": "boiled egg"}
        mock_get.return_value = {"vitamin_a_mcg": 149.0}

        result = enrich_all_foods(
            ["Quick Add", "Boiled Eggs"], mappings, db, "fake-key"
        )
        assert result["Quick Add"] is None
        assert result["Boiled Eggs"] == {"vitamin_a_mcg": 149.0}
        # get_food_micros should only be called for Boiled Eggs
        mock_get.assert_called_once()

    @patch("omni_pilot.enricher.get_food_micros")
    def test_uses_original_name_when_mapping_empty(self, mock_get, tmp_path):
        db = TinyDB(str(tmp_path / "test_db.json"))
        mappings = {"Boiled Eggs": ""}
        mock_get.return_value = {"vitamin_a_mcg": 149.0}

        enrich_all_foods(["Boiled Eggs"], mappings, db, "fake-key")
        mock_get.assert_called_once_with("Boiled Eggs", "Boiled Eggs", db, "fake-key")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `source .venv/bin/activate && PYTHONPATH=src pytest tests/test_enricher.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'omni_pilot.enricher'`

- [ ] **Step 3: Implement enricher.py**

`src/omni_pilot/enricher.py`:
```python
"""USDA FoodData Central enrichment with TinyDB caching."""
from __future__ import annotations

import logging
import time
from datetime import date

import requests
from tinydb import TinyDB, Query

logger = logging.getLogger(__name__)

# Maps our internal nutrient keys to USDA nutrient numbers.
# Numbers verified against USDA FoodData Central SR Legacy dataset.
USDA_NUTRIENT_MAP: dict[str, str] = {
    # Vitamins
    "vitamin_a_mcg": "320",       # Vitamin A, RAE
    "vitamin_c_mg": "401",        # Vitamin C, total ascorbic acid
    "vitamin_d_mcg": "328",       # Vitamin D (D2 + D3)
    "vitamin_e_mg": "323",        # Vitamin E (alpha-tocopherol)
    "vitamin_k_mcg": "430",       # Vitamin K (phylloquinone)
    "b1_thiamine_mg": "404",      # Thiamin
    "b2_riboflavin_mg": "405",    # Riboflavin
    "b3_niacin_mg": "406",        # Niacin
    "b5_pantothenic_acid_mg": "410",  # Pantothenic acid
    "b6_pyridoxine_mg": "415",    # Vitamin B-6
    "b12_cobalamin_mcg": "418",   # Vitamin B-12
    "folate_mcg": "417",          # Folate, total
    # Minerals
    "calcium_mg": "301",          # Calcium, Ca
    "iron_mg": "303",             # Iron, Fe
    "zinc_mg": "309",             # Zinc, Zn
    "magnesium_mg": "304",        # Magnesium, Mg
    "manganese_mg": "315",        # Manganese, Mn
    "phosphorus_mg": "305",       # Phosphorus, P
    "potassium_mg": "306",        # Potassium, K
    "selenium_mcg": "317",        # Selenium, Se
    "copper_mg": "312",           # Copper, Cu
    "sodium_mg": "307",           # Sodium, Na
    # Other
    "fiber_g": "291",             # Fiber, total dietary
    "choline_mg": "421",          # Choline, total
    "omega3_ala_g": "619",        # PUFA 18:3 (ALA)
    "omega3_epa_mg": "629",       # PUFA 20:5 n-3 (EPA) — in g from USDA
    "omega3_dha_mg": "621",       # PUFA 22:6 n-3 (DHA) — in g from USDA
    # Amino acids (per 100g, in grams)
    "histidine_g": "512",         # Histidine
    "isoleucine_g": "503",        # Isoleucine
    "leucine_g": "504",           # Leucine
    "lysine_g": "505",            # Lysine
    "methionine_g": "506",        # Methionine
    "cysteine_g": "507",          # Cystine
    "phenylalanine_g": "508",     # Phenylalanine
    "tyrosine_g": "509",          # Tyrosine
    "threonine_g": "502",         # Threonine
    "tryptophan_g": "501",        # Tryptophan
    "valine_g": "510",            # Valine
}

# Reverse map: USDA number -> our key
_USDA_NUMBER_TO_KEY = {v: k for k, v in USDA_NUTRIENT_MAP.items()}

USDA_SEARCH_URL = "https://api.nal.usda.gov/fdc/v1/foods/search"


def search_usda(query: str, api_key: str) -> dict | None:
    """Search USDA FoodData Central for a food.

    Returns the best match food dict, or None if no results.
    Prefers SR Legacy and Foundation datasets.
    """
    params = {
        "api_key": api_key,
        "query": query,
        "dataType": "SR Legacy,Foundation",
        "pageSize": 5,
    }

    try:
        resp = requests.get(USDA_SEARCH_URL, params=params, timeout=30)
        if resp.status_code == 429:
            logger.warning("USDA API rate limit hit, waiting 5 seconds...")
            time.sleep(5)
            resp = requests.get(USDA_SEARCH_URL, params=params, timeout=30)
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.error("USDA API request failed for '%s': %s", query, e)
        return None

    data = resp.json()
    foods = data.get("foods", [])
    if not foods:
        logger.warning("No USDA results for query: '%s'", query)
        return None

    # Return the first (best) match
    return foods[0]


def extract_micros_from_usda(usda_food: dict) -> dict[str, float | None]:
    """Extract per-100g micro values from a USDA food response.

    Returns a dict mapping our nutrient keys to values.
    Missing nutrients are set to None.
    """
    # Build lookup: USDA number -> amount
    nutrient_lookup: dict[str, float] = {}
    for fn in usda_food.get("foodNutrients", []):
        number = str(fn.get("nutrientNumber", ""))
        value = fn.get("value")
        if number and value is not None:
            nutrient_lookup[number] = float(value)

    # Map to our keys
    result: dict[str, float | None] = {}
    for our_key, usda_number in USDA_NUTRIENT_MAP.items():
        result[our_key] = nutrient_lookup.get(usda_number)

    return result


def get_food_micros(
    food_name: str,
    usda_query: str,
    db: TinyDB,
    api_key: str,
) -> dict[str, float | None] | None:
    """Get per-100g micro profile for a food.

    Checks TinyDB cache first, then queries USDA API.
    Returns None if the food cannot be resolved.
    """
    Food = Query()
    cached = db.search(Food.original_name == food_name)
    if cached:
        return cached[0]["per_100g"]

    # Query USDA
    usda_food = search_usda(usda_query, api_key)
    if usda_food is None:
        return None

    micros = extract_micros_from_usda(usda_food)

    # Determine confidence
    confidence = "direct" if usda_query == food_name else "mapped"

    # Cache in TinyDB
    db.insert({
        "original_name": food_name,
        "usda_query": usda_query,
        "usda_name": usda_food.get("description", ""),
        "usda_fdc_id": usda_food.get("fdcId"),
        "usda_dataset": usda_food.get("dataType", ""),
        "per_100g": micros,
        "confidence": confidence,
        "last_updated": str(date.today()),
    })

    return micros


def enrich_all_foods(
    food_names: list[str],
    mappings: dict[str, str],
    db: TinyDB,
    api_key: str,
) -> dict[str, dict[str, float | None] | None]:
    """Enrich all foods with USDA micro data.

    Returns a dict mapping food names to their per-100g micro profiles.
    Foods mapped to "skip" get None.
    """
    result: dict[str, dict[str, float | None] | None] = {}

    for food_name in food_names:
        mapping = mappings.get(food_name, "")

        if mapping == "skip":
            result[food_name] = None
            logger.info("Skipping '%s' (mapped to 'skip')", food_name)
            continue

        # Use the mapping if provided, otherwise use the original name
        query = mapping if mapping else food_name
        micros = get_food_micros(food_name, query, db, api_key)

        if micros is None:
            logger.warning(
                "Could not resolve '%s' (query: '%s') — marking as unresolved",
                food_name, query,
            )

        result[food_name] = micros

    return result
```

- [ ] **Step 4: Create db/ directory**

```bash
mkdir -p db
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `source .venv/bin/activate && PYTHONPATH=src pytest tests/test_enricher.py -v`
Expected: All tests PASS

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat: USDA enricher with TinyDB caching"
```

---

### Task 4: Analyzer

**Files:**
- Create: `src/omni_pilot/analyzer.py`
- Test: `tests/test_analyzer.py`

**Interfaces:**
- Consumes:
  - `FoodEntry` from `parser.py`
  - `enrich_all_foods()` result — `dict[str, dict | None]`
  - `load_reference_ranges()` from `config.py`
  - `get_nutrient_target()` from `config.py`
- Produces:
  - `NutrientResult = TypedDict(...)` — per-nutrient analysis result
  - `AnalysisResult = TypedDict(...)` — complete analysis output
  - `determine_status(value: float, target: float, ul: float | None) -> str` — returns "ok", "low", "deficient", or "high"
  - `analyze(entries: list[dict], enriched: dict[str, dict | None], ref_ranges: dict) -> AnalysisResult`

- [ ] **Step 1: Write the failing test**

`tests/test_analyzer.py`:
```python
from __future__ import annotations

import pytest

from omni_pilot.analyzer import analyze, determine_status


class TestDetermineStatus:
    def test_ok_when_above_rda_below_ul(self):
        assert determine_status(100.0, 90.0, 2000.0) == "ok"

    def test_ok_when_above_rda_no_ul(self):
        assert determine_status(100.0, 90.0, None) == "ok"

    def test_low_when_between_50_and_100_pct(self):
        assert determine_status(60.0, 90.0, None) == "low"

    def test_deficient_when_below_50_pct(self):
        assert determine_status(30.0, 90.0, None) == "deficient"

    def test_high_when_above_ul(self):
        assert determine_status(3500.0, 90.0, 2000.0) == "high"

    def test_ok_at_exact_rda(self):
        assert determine_status(90.0, 90.0, None) == "ok"

    def test_low_at_exact_50_pct(self):
        assert determine_status(45.0, 90.0, None) == "low"

    def test_ok_at_exact_ul(self):
        assert determine_status(2000.0, 90.0, 2000.0) == "ok"


class TestAnalyze:
    def _make_entry(self, food_name: str, date: str, total_weight_g: float) -> dict:
        return {
            "date": date,
            "time": "12:00",
            "food_name": food_name,
            "serving_size": "g",
            "serving_qty": total_weight_g,
            "serving_weight_g": 1.0,
            "total_weight_g": total_weight_g,
            "calories_kcal": 100.0,
            "protein_g": 10.0,
            "fat_g": 5.0,
            "carbs_g": 15.0,
        }

    def _make_ref_ranges(self) -> dict:
        return {
            "demographic": {"sex": "male", "age_group": "19-50", "body_weight_kg": 75},
            "nutrients": {
                "vitamin_a_mcg": {
                    "name": "Vitamin A", "unit": "mcg", "type": "rda",
                    "rda": 900, "ul": 3000, "source": "NIH",
                },
                "calcium_mg": {
                    "name": "Calcium", "unit": "mg", "type": "rda",
                    "rda": 1000, "ul": 2500, "source": "NIH",
                },
            },
        }

    def test_daily_average_calculation(self):
        entries = [
            self._make_entry("Eggs", "2026-07-13", 200.0),
            self._make_entry("Eggs", "2026-07-14", 200.0),
        ]
        enriched = {
            "Eggs": {"vitamin_a_mcg": 149.0, "calcium_mg": 50.0},
        }
        ref_ranges = self._make_ref_ranges()
        result = analyze(entries, enriched, ref_ranges)

        assert result["period"]["days"] == 2
        # 200g of eggs per day -> 149 * (200/100) = 298 mcg vitamin A per day
        # Average over 2 days = 298
        vit_a = result["nutrients"]["vitamin_a_mcg"]
        assert vit_a["daily_avg"] == pytest.approx(298.0)
        # 298/900 = 33.1% which is < 50%, so deficient
        assert vit_a["status"] == "deficient"

    def test_skipped_foods_excluded(self):
        entries = [
            self._make_entry("Eggs", "2026-07-13", 200.0),
            self._make_entry("Quick Add", "2026-07-13", 100.0),
        ]
        enriched = {
            "Eggs": {"vitamin_a_mcg": 149.0, "calcium_mg": 50.0},
            "Quick Add": None,
        }
        ref_ranges = self._make_ref_ranges()
        result = analyze(entries, enriched, ref_ranges)

        assert result["coverage"]["mapped_entries"] == 1
        assert result["coverage"]["skipped_entries"] == 1

    def test_handles_missing_nutrient_in_enriched(self):
        entries = [self._make_entry("Eggs", "2026-07-13", 200.0)]
        enriched = {
            "Eggs": {"vitamin_a_mcg": 149.0, "calcium_mg": None},
        }
        ref_ranges = self._make_ref_ranges()
        result = analyze(entries, enriched, ref_ranges)

        vit_a = result["nutrients"]["vitamin_a_mcg"]
        assert vit_a["daily_avg"] == pytest.approx(298.0)
        # calcium_mg has None value from USDA — should not contribute
        calcium = result["nutrients"]["calcium_mg"]
        assert calcium["daily_avg"] == pytest.approx(0.0)

    def test_multiple_foods_same_day(self):
        entries = [
            self._make_entry("Eggs", "2026-07-13", 200.0),
            self._make_entry("Milk", "2026-07-13", 300.0),
        ]
        enriched = {
            "Eggs": {"vitamin_a_mcg": 149.0, "calcium_mg": 50.0},
            "Milk": {"vitamin_a_mcg": 50.0, "calcium_mg": 120.0},
        }
        ref_ranges = self._make_ref_ranges()
        result = analyze(entries, enriched, ref_ranges)

        # Day total: Eggs 200g * 149/100 + Milk 300g * 50/100 = 298 + 150 = 448
        vit_a = result["nutrients"]["vitamin_a_mcg"]
        assert vit_a["daily_avg"] == pytest.approx(448.0)
        # Calcium: 200g * 50/100 + 300g * 120/100 = 100 + 360 = 460
        calcium = result["nutrients"]["calcium_mg"]
        assert calcium["daily_avg"] == pytest.approx(460.0)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `source .venv/bin/activate && PYTHONPATH=src pytest tests/test_analyzer.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'omni_pilot.analyzer'`

- [ ] **Step 3: Implement analyzer.py**

`src/omni_pilot/analyzer.py`:
```python
"""Analyze daily micronutrient intake against reference ranges."""
from __future__ import annotations

import logging
from collections import defaultdict
from typing import TypedDict

from omni_pilot.config import get_nutrient_target

logger = logging.getLogger(__name__)


class NutrientResult(TypedDict):
    name: str
    unit: str
    daily_avg: float
    target: float | None
    target_type: str
    ul: float | None
    status: str
    pct_of_target: float | None


class CoverageResult(TypedDict):
    total_food_entries: int
    mapped_entries: int
    skipped_entries: int
    unresolved_entries: int
    skipped_foods: list[str]
    unresolved_foods: list[str]


class PeriodResult(TypedDict):
    start: str
    end: str
    days: int


class AnalysisResult(TypedDict):
    period: PeriodResult
    nutrients: dict[str, NutrientResult]
    coverage: CoverageResult


# Combined amino acid keys — WHO tracks these as pairs, but USDA/enricher
# stores them individually. The analyzer sums them for comparison.
COMBINED_AMINOS = {
    "methionine_cysteine_g": ["methionine_g", "cysteine_g"],
    "phenylalanine_tyrosine_g": ["phenylalanine_g", "tyrosine_g"],
}


def determine_status(value: float, target: float, ul: float | None) -> str:
    """Determine nutrient status based on value vs target and UL.

    Returns one of: "ok", "low", "deficient", "high".
    """
    if ul is not None and value > ul:
        return "high"
    if value >= target:
        return "ok"
    if value >= target * 0.5:
        return "low"
    return "deficient"


def analyze(
    entries: list[dict],
    enriched: dict[str, dict[str, float | None] | None],
    ref_ranges: dict,
) -> AnalysisResult:
    """Run the full micronutrient analysis.

    1. For each food entry, scale USDA per-100g data by actual weight
    2. Sum per day
    3. Average across days
    4. Compare against reference ranges
    """
    # Track coverage
    total_entries = len(entries)
    mapped_entries = 0
    skipped_entries = 0
    unresolved_entries = 0
    skipped_food_names: set[str] = set()
    unresolved_food_names: set[str] = set()

    # Collect all nutrient keys from reference_ranges
    nutrient_keys = list(ref_ranges["nutrients"].keys())

    # Daily totals: date -> nutrient_key -> total
    daily_totals: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    dates: set[str] = set()

    for entry in entries:
        food_name = entry["food_name"]
        food_micros = enriched.get(food_name)

        if food_micros is None:
            # If the key exists in enriched but is None, it was explicitly skipped
            if food_name in enriched:
                skipped_entries += 1
                skipped_food_names.add(food_name)
            else:
                unresolved_entries += 1
                unresolved_food_names.add(food_name)
            continue

        mapped_entries += 1
        entry_date = entry["date"]
        dates.add(entry_date)
        total_weight_g = entry["total_weight_g"]
        scale_factor = total_weight_g / 100.0

        # Add scaled nutrients to daily totals
        for nutrient_key in food_micros:
            value = food_micros.get(nutrient_key)
            if value is not None:
                daily_totals[entry_date][nutrient_key] += value * scale_factor

    # Compute daily averages
    num_days = len(dates) if dates else 1
    sorted_dates = sorted(dates)

    # Build nutrient results
    nutrients_result: dict[str, NutrientResult] = {}

    for nutrient_key in nutrient_keys:
        nutrient_info = ref_ranges["nutrients"][nutrient_key]
        target, ul, target_type = get_nutrient_target(nutrient_key, ref_ranges)

        # Determine which enricher keys to sum for this reference key
        if nutrient_key in COMBINED_AMINOS:
            component_keys = COMBINED_AMINOS[nutrient_key]
        else:
            component_keys = [nutrient_key]

        # Sum across days, then average
        total_across_days = 0.0
        for d in sorted_dates:
            day_total = sum(
                daily_totals[d].get(ck, 0.0) for ck in component_keys
            )
            total_across_days += day_total

        daily_avg = total_across_days / num_days if num_days > 0 else 0.0

        # Determine status
        if target is not None:
            status = determine_status(daily_avg, target, ul)
            pct = (daily_avg / target * 100) if target > 0 else None
        else:
            status = "unknown"
            pct = None

        nutrients_result[nutrient_key] = NutrientResult(
            name=nutrient_info["name"],
            unit=nutrient_info["unit"],
            daily_avg=round(daily_avg, 2),
            target=target,
            target_type=target_type,
            ul=ul,
            status=status,
            pct_of_target=round(pct, 1) if pct is not None else None,
        )

    return AnalysisResult(
        period=PeriodResult(
            start=sorted_dates[0] if sorted_dates else "",
            end=sorted_dates[-1] if sorted_dates else "",
            days=num_days,
        ),
        nutrients=nutrients_result,
        coverage=CoverageResult(
            total_food_entries=total_entries,
            mapped_entries=mapped_entries,
            skipped_entries=skipped_entries,
            unresolved_entries=unresolved_entries,
            skipped_foods=sorted(skipped_food_names),
            unresolved_foods=sorted(unresolved_food_names),
        ),
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `source .venv/bin/activate && PYTHONPATH=src pytest tests/test_analyzer.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat: micronutrient analyzer with status determination"
```

---

### Task 5: Reporter (Rich CLI + HTML)

**Files:**
- Create: `src/omni_pilot/reporter.py`
- Test: `tests/test_reporter.py`

**Interfaces:**
- Consumes:
  - `AnalysisResult` from `analyzer.py`
  - `load_settings()` from `config.py` — for display preferences
- Produces:
  - `print_terminal_report(result: AnalysisResult, settings: dict) -> None` — prints Rich-formatted report to stdout
  - `generate_html_report(result: AnalysisResult, output_path: str) -> None` — writes styled HTML report

- [ ] **Step 1: Write the failing test**

`tests/test_reporter.py`:
```python
from __future__ import annotations

import os
import pytest

from omni_pilot.reporter import print_terminal_report, generate_html_report


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `source .venv/bin/activate && PYTHONPATH=src pytest tests/test_reporter.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'omni_pilot.reporter'`

- [ ] **Step 3: Implement reporter.py**

`src/omni_pilot/reporter.py`:
```python
"""Report generation for micronutrient analysis."""
from __future__ import annotations

import os
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from jinja2 import Template


# Status emoji and color mapping
STATUS_DISPLAY = {
    "ok": ("🟢", "OK", "green"),
    "low": ("🟡", "Low", "yellow"),
    "deficient": ("🔴", "Deficient", "red"),
    "high": ("🟠", "High", "dark_orange"),
    "unknown": ("⚪", "Unknown", "dim"),
}

# Nutrient category grouping by key
NUTRIENT_CATEGORIES = {
    "Vitamins": [
        "vitamin_a_mcg", "vitamin_c_mg", "vitamin_d_mcg", "vitamin_e_mg",
        "vitamin_k_mcg", "b1_thiamine_mg", "b2_riboflavin_mg", "b3_niacin_mg",
        "b5_pantothenic_acid_mg", "b6_pyridoxine_mg", "b12_cobalamin_mcg",
        "folate_mcg",
    ],
    "Minerals": [
        "calcium_mg", "iron_mg", "zinc_mg", "magnesium_mg", "manganese_mg",
        "phosphorus_mg", "potassium_mg", "selenium_mcg", "copper_mg", "sodium_mg",
    ],
    "Other": [
        "fiber_g", "choline_mg", "omega3_ala_g", "omega3_epa_dha_mg",
    ],
    "Amino Acids": [
        "histidine_g", "isoleucine_g", "leucine_g", "lysine_g",
        "methionine_cysteine_g", "phenylalanine_tyrosine_g",
        "threonine_g", "tryptophan_g", "valine_g",
    ],
}


def _summary_line(nutrients: dict) -> str:
    """Build the summary counts line."""
    counts = {"ok": 0, "low": 0, "deficient": 0, "high": 0, "unknown": 0}
    for n in nutrients.values():
        status = n["status"]
        counts[status] = counts.get(status, 0) + 1
    total = sum(counts.values())
    ok = counts["ok"]
    parts = [
        f"{ok}/{total} in range",
        f"{counts['low']} low",
        f"{counts['deficient']} deficient",
        f"{counts['high']} high",
    ]
    return " · ".join(parts)


def _coverage_line(coverage: dict) -> str:
    """Build the coverage stats line."""
    mapped = coverage["mapped_entries"]
    total = coverage["total_food_entries"]
    skipped = coverage["skipped_entries"]
    unresolved = coverage["unresolved_entries"]
    return f"{mapped}/{total} entries analyzed ({skipped} skipped, {unresolved} unresolved)"


def print_terminal_report(result: dict, settings: dict) -> None:
    """Print a Rich-formatted micronutrient report to the terminal."""
    console = Console()
    show_ok = settings.get("output", {}).get("show_ok_nutrients", True)
    show_aminos = settings.get("output", {}).get("show_amino_acids", True)

    period = result["period"]
    nutrients = result["nutrients"]
    coverage = result["coverage"]

    # Header panel
    header_text = Text()
    header_text.append("Micronutrient Analysis Report\n", style="bold")
    header_text.append(
        f"Period: {period['start']} → {period['end']} ({period['days']} days)"
    )
    console.print(Panel(header_text, expand=True))
    console.print()

    # Summary and coverage
    console.print(f"  Summary: {_summary_line(nutrients)}")
    console.print(f"  Coverage: {_coverage_line(coverage)}")
    console.print()

    # Nutrient tables by category
    for category, keys in NUTRIENT_CATEGORIES.items():
        if category == "Amino Acids" and not show_aminos:
            continue

        # Filter to nutrients that exist in the result
        category_nutrients = [
            (k, nutrients[k]) for k in keys if k in nutrients
        ]
        if not category_nutrients:
            continue

        # Filter out OK nutrients if configured
        if not show_ok:
            category_nutrients = [
                (k, n) for k, n in category_nutrients if n["status"] != "ok"
            ]
            if not category_nutrients:
                continue

        table = Table(title=f"── {category} ──", show_header=True, expand=True)
        table.add_column("Nutrient", style="bold", min_width=30)
        table.add_column("Unit", justify="center", min_width=6)
        table.add_column("Daily Avg", justify="right", min_width=10)
        table.add_column("Target", justify="right", min_width=10)
        table.add_column("Status", justify="center", min_width=12)

        for key, n in category_nutrients:
            emoji, label, color = STATUS_DISPLAY.get(
                n["status"], ("⚪", "Unknown", "dim")
            )
            target_str = (
                f"{n['target']:.1f}" if n["target"] is not None else "—"
            )
            status_text = Text(f"{emoji} {label}", style=color)
            table.add_row(
                n["name"],
                n["unit"],
                f"{n['daily_avg']:.1f}",
                target_str,
                status_text,
            )

        console.print(table)
        console.print()

    # Warnings
    if coverage["skipped_foods"]:
        foods_str = ", ".join(coverage["skipped_foods"])
        console.print(f"  ⚠ Skipped foods: {foods_str}", style="yellow")
    if coverage["unresolved_foods"]:
        foods_str = ", ".join(coverage["unresolved_foods"])
        console.print(f"  ⚠ Unresolved foods: {foods_str}", style="red")


HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Micronutrient Analysis Report</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            background: #1a1a2e; color: #eee; padding: 2rem;
        }
        .header {
            text-align: center; margin-bottom: 2rem;
            padding: 1.5rem; background: #16213e; border-radius: 12px;
        }
        .header h1 { font-size: 1.5rem; margin-bottom: 0.5rem; }
        .header .period { color: #8892b0; }
        .summary { text-align: center; margin-bottom: 1rem; color: #ccd6f6; }
        .coverage { text-align: center; margin-bottom: 2rem; color: #8892b0; font-size: 0.9rem; }
        .category { margin-bottom: 2rem; }
        .category h2 {
            font-size: 1.1rem; margin-bottom: 0.5rem; padding-bottom: 0.3rem;
            border-bottom: 1px solid #233554;
        }
        table { width: 100%; border-collapse: collapse; }
        th { text-align: left; padding: 0.5rem; color: #8892b0; font-size: 0.85rem; border-bottom: 1px solid #233554; }
        td { padding: 0.5rem; border-bottom: 1px solid #1a1a2e; }
        tr:hover { background: #16213e; }
        .status-ok { color: #64ffda; }
        .status-low { color: #ffd93d; }
        .status-deficient { color: #ff6b6b; }
        .status-high { color: #ff9f43; }
        .warnings { margin-top: 2rem; padding: 1rem; background: #16213e; border-radius: 8px; }
        .warnings p { margin: 0.3rem 0; font-size: 0.9rem; }
    </style>
</head>
<body>
    <div class="header">
        <h1>Micronutrient Analysis Report</h1>
        <div class="period">{{ period.start }} → {{ period.end }} ({{ period.days }} days)</div>
    </div>
    <div class="summary">{{ summary }}</div>
    <div class="coverage">{{ coverage_line }}</div>
    {% for category, category_nutrients in categories %}
    {% if category_nutrients %}
    <div class="category">
        <h2>{{ category }}</h2>
        <table>
            <thead><tr><th>Nutrient</th><th>Unit</th><th>Daily Avg</th><th>Target</th><th>Status</th></tr></thead>
            <tbody>
            {% for n in category_nutrients %}
            <tr>
                <td>{{ n.name }}</td>
                <td>{{ n.unit }}</td>
                <td>{{ "%.1f"|format(n.daily_avg) }}</td>
                <td>{{ "%.1f"|format(n.target) if n.target is not none else "—" }}</td>
                <td class="status-{{ n.status }}">{{ n.status_label }}</td>
            </tr>
            {% endfor %}
            </tbody>
        </table>
    </div>
    {% endif %}
    {% endfor %}
    {% if skipped_foods or unresolved_foods %}
    <div class="warnings">
        {% if skipped_foods %}<p>⚠ Skipped: {{ skipped_foods|join(", ") }}</p>{% endif %}
        {% if unresolved_foods %}<p>⚠ Unresolved: {{ unresolved_foods|join(", ") }}</p>{% endif %}
    </div>
    {% endif %}
</body>
</html>
"""


def generate_html_report(result: dict, output_path: str) -> None:
    """Generate an HTML report file."""
    nutrients = result["nutrients"]
    coverage = result["coverage"]

    # Build categories data
    categories = []
    for category, keys in NUTRIENT_CATEGORIES.items():
        cat_nutrients = []
        for key in keys:
            if key in nutrients:
                n = nutrients[key].copy()
                emoji, label, _ = STATUS_DISPLAY.get(
                    n["status"], ("⚪", "Unknown", "dim")
                )
                n["status_label"] = f"{emoji} {label}"
                cat_nutrients.append(n)
        categories.append((category, cat_nutrients))

    template = Template(HTML_TEMPLATE)
    html = template.render(
        period=result["period"],
        summary=_summary_line(nutrients),
        coverage_line=_coverage_line(coverage),
        categories=categories,
        skipped_foods=coverage["skipped_foods"],
        unresolved_foods=coverage["unresolved_foods"],
    )

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "w") as f:
        f.write(html)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `source .venv/bin/activate && PYTHONPATH=src pytest tests/test_reporter.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat: Rich terminal and HTML report generation"
```

---

### Task 6: CLI Entry Point

**Files:**
- Create: `src/omni_pilot/cli.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: All modules — `config`, `parser`, `enricher`, `analyzer`, `reporter`
- Produces:
  - `main() -> None` — argparse entry point with `import` and `analyze` subcommands
  - `cmd_import(args: argparse.Namespace) -> None` — runs import pipeline
  - `cmd_analyze(args: argparse.Namespace) -> None` — runs full analysis pipeline

- [ ] **Step 1: Write the failing test**

`tests/test_cli.py`:
```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `source .venv/bin/activate && PYTHONPATH=src pytest tests/test_cli.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'omni_pilot.cli'`

- [ ] **Step 3: Implement cli.py**

`src/omni_pilot/cli.py`:
```python
"""CLI entry point for Omni Pilot."""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import date

from tinydb import TinyDB

from omni_pilot.config import (
    load_settings,
    load_reference_ranges,
    load_food_mappings,
)
from omni_pilot.parser import parse_food_log, extract_unique_foods, generate_food_mappings
from omni_pilot.enricher import enrich_all_foods
from omni_pilot.analyzer import analyze
from omni_pilot.reporter import print_terminal_report, generate_html_report

logger = logging.getLogger(__name__)

# Default paths relative to project root
DEFAULT_SETTINGS = "config/settings.yaml"
DEFAULT_REF_RANGES = "config/reference_ranges.yaml"
DEFAULT_MAPPINGS = "config/food_mappings.yaml"
DEFAULT_DB = "db/food_db.json"


def cmd_import(args: argparse.Namespace) -> None:
    """Import MacroFactor xlsx and generate food_mappings.yaml."""
    xlsx_path = args.xlsx_file
    mappings_output = args.mappings_output or DEFAULT_MAPPINGS

    print(f"Parsing {xlsx_path}...")
    entries = parse_food_log(xlsx_path)
    print(f"  Found {len(entries)} food entries.")

    foods = extract_unique_foods(entries)
    print(f"  Found {len(foods)} unique foods.")

    # Load existing mappings if present
    existing_mappings = load_food_mappings(mappings_output)

    # Generate/merge mappings
    generate_food_mappings(foods, existing_mappings, mappings_output)

    new_foods = [f for f in foods if f not in existing_mappings]
    print(f"\nMappings written to: {mappings_output}")
    if new_foods:
        print(f"  {len(new_foods)} new foods need English equivalents.")
        print("  Edit the file and fill in USDA-searchable names.")
    else:
        print("  All foods already mapped.")


def cmd_analyze(args: argparse.Namespace) -> None:
    """Run full analysis pipeline: parse → enrich → analyze → report."""
    xlsx_path = args.xlsx_file
    settings_path = args.settings or DEFAULT_SETTINGS
    ref_ranges_path = args.ref_ranges or DEFAULT_REF_RANGES
    mappings_path = args.mappings or DEFAULT_MAPPINGS
    db_path = args.db or DEFAULT_DB

    # Load config
    settings = load_settings(settings_path)
    ref_ranges = load_reference_ranges(ref_ranges_path)
    mappings = load_food_mappings(mappings_path)

    if not mappings:
        print("Error: No food mappings found. Run 'import' first.")
        sys.exit(1)

    api_key = settings.get("usda_api_key", "")
    if not api_key:
        print("Error: No USDA API key in settings.yaml.")
        sys.exit(1)

    # Parse
    print(f"Parsing {xlsx_path}...")
    entries = parse_food_log(xlsx_path)
    print(f"  Found {len(entries)} food entries.")

    # Enrich
    print("Enriching foods with USDA data...")
    food_names = extract_unique_foods(entries)
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    db = TinyDB(db_path)
    enriched = enrich_all_foods(food_names, mappings, db, api_key)

    resolved = sum(1 for v in enriched.values() if v is not None)
    print(f"  {resolved}/{len(food_names)} foods resolved.")

    # Analyze
    print("Analyzing micronutrient intake...")
    result = analyze(entries, enriched, ref_ranges)

    # Report
    print()
    print_terminal_report(result, settings)

    # Optional HTML report
    if args.html:
        reports_dir = "reports"
        os.makedirs(reports_dir, exist_ok=True)
        html_path = os.path.join(
            reports_dir, f"micronutrient-report-{date.today()}.html"
        )
        generate_html_report(result, html_path)
        print(f"\nHTML report saved to: {html_path}")


def main() -> None:
    """Main CLI entry point."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    parser = argparse.ArgumentParser(
        prog="omni_pilot",
        description="Omni Pilot — Nutrition & Workout Analysis",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Import command
    import_parser = subparsers.add_parser(
        "import", help="Import MacroFactor xlsx and generate food mappings"
    )
    import_parser.add_argument("xlsx_file", help="Path to MacroFactor xlsx export")
    import_parser.add_argument(
        "--mappings-output", default=None,
        help=f"Output path for food_mappings.yaml (default: {DEFAULT_MAPPINGS})",
    )

    # Analyze command
    analyze_parser = subparsers.add_parser(
        "analyze", help="Analyze micronutrient intake"
    )
    analyze_parser.add_argument("xlsx_file", help="Path to MacroFactor xlsx export")
    analyze_parser.add_argument(
        "--html", action="store_true", help="Also generate HTML report"
    )
    analyze_parser.add_argument("--settings", default=None)
    analyze_parser.add_argument("--ref-ranges", default=None)
    analyze_parser.add_argument("--mappings", default=None)
    analyze_parser.add_argument("--db", default=None)

    args = parser.parse_args()

    if args.command == "import":
        cmd_import(args)
    elif args.command == "analyze":
        cmd_analyze(args)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `source .venv/bin/activate && PYTHONPATH=src pytest tests/test_cli.py -v`
Expected: All tests PASS

- [ ] **Step 5: Run full test suite**

Run: `source .venv/bin/activate && PYTHONPATH=src pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat: CLI entry point with import and analyze commands"
```

---

### Task 7: End-to-End Integration Test

**Files:**
- Test: `tests/test_integration.py`

**Interfaces:**
- Consumes: All modules
- Produces: Confidence that the full pipeline works with real data

- [ ] **Step 1: Write the integration test**

`tests/test_integration.py`:
```python
from __future__ import annotations

import os
import pytest
from tinydb import TinyDB

from omni_pilot.parser import parse_food_log, extract_unique_foods
from omni_pilot.enricher import enrich_all_foods
from omni_pilot.analyzer import analyze
from omni_pilot.config import load_reference_ranges
from omni_pilot.reporter import print_terminal_report, generate_html_report


XLSX_PATH = "data/MacroFactor-20260809145742.xlsx"
REF_RANGES_PATH = "config/reference_ranges.yaml"


@pytest.mark.skipif(
    not os.path.exists(XLSX_PATH),
    reason="MacroFactor xlsx not found",
)
class TestIntegration:
    def test_parse_and_extract(self):
        entries = parse_food_log(XLSX_PATH)
        assert len(entries) == 223
        foods = extract_unique_foods(entries)
        assert len(foods) == 81

    def test_full_pipeline_with_mock_usda(self, tmp_path):
        """Full pipeline test with dummy micro data (no real API calls)."""
        entries = parse_food_log(XLSX_PATH)
        foods = extract_unique_foods(entries)

        # Create a simple enriched dict — map everything to a dummy micro profile
        dummy_micros = {
            "vitamin_a_mcg": 100.0,
            "vitamin_c_mg": 10.0,
            "vitamin_d_mcg": 1.0,
            "calcium_mg": 50.0,
            "iron_mg": 2.0,
            "zinc_mg": 1.5,
            "magnesium_mg": 20.0,
            "manganese_mg": 0.3,
            "phosphorus_mg": 80.0,
            "potassium_mg": 150.0,
            "selenium_mcg": 5.0,
            "copper_mg": 0.1,
            "sodium_mg": 200.0,
            "fiber_g": 2.0,
            "choline_mg": 30.0,
            "omega3_ala_g": 0.05,
            "b1_thiamine_mg": 0.1,
            "b2_riboflavin_mg": 0.1,
            "b3_niacin_mg": 1.0,
            "b5_pantothenic_acid_mg": 0.5,
            "b6_pyridoxine_mg": 0.1,
            "b12_cobalamin_mcg": 0.5,
            "folate_mcg": 20.0,
            "vitamin_e_mg": 0.5,
            "vitamin_k_mcg": 5.0,
            "histidine_g": 0.3,
            "isoleucine_g": 0.5,
            "leucine_g": 0.8,
            "lysine_g": 0.7,
            "methionine_g": 0.2,
            "cysteine_g": 0.1,
            "phenylalanine_g": 0.4,
            "tyrosine_g": 0.3,
            "threonine_g": 0.4,
            "tryptophan_g": 0.1,
            "valine_g": 0.5,
        }
        enriched = {food: dummy_micros for food in foods}

        ref_ranges = load_reference_ranges(REF_RANGES_PATH)
        result = analyze(entries, enriched, ref_ranges)

        assert result["period"]["days"] > 0
        assert result["coverage"]["mapped_entries"] > 0
        assert "vitamin_a_mcg" in result["nutrients"]

        # Terminal report should not error
        settings = {"output": {"show_amino_acids": True, "show_ok_nutrients": True}}
        print_terminal_report(result, settings)

        # HTML report should generate
        html_path = str(tmp_path / "test_report.html")
        generate_html_report(result, html_path)
        assert os.path.exists(html_path)
```

- [ ] **Step 2: Run integration tests**

Run: `source .venv/bin/activate && PYTHONPATH=src pytest tests/test_integration.py -v`
Expected: All tests PASS

- [ ] **Step 3: Run full test suite one final time**

Run: `source .venv/bin/activate && PYTHONPATH=src pytest tests/ -v --tb=short`
Expected: All tests PASS

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "test: end-to-end integration tests"
```

---

## Verification Plan

### Automated Tests
```bash
source .venv/bin/activate && PYTHONPATH=src pytest tests/ -v
```

### Manual Verification
1. Run the import command and verify food_mappings.yaml is generated with all 81 foods:
   ```bash
   source .venv/bin/activate && PYTHONPATH=src python -m omni_pilot import data/MacroFactor-20260809145742.xlsx
   ```
2. Fill in a few food mappings manually in `config/food_mappings.yaml`
3. Fill in demographic section in `config/reference_ranges.yaml`
4. Run the analyze command and verify terminal output shows the nutrient table:
   ```bash
   source .venv/bin/activate && PYTHONPATH=src python -m omni_pilot analyze data/MacroFactor-20260809145742.xlsx
   ```
5. Run with `--html` flag and verify the HTML report is generated and viewable in a browser:
   ```bash
   source .venv/bin/activate && PYTHONPATH=src python -m omni_pilot analyze data/MacroFactor-20260809145742.xlsx --html
   ```
