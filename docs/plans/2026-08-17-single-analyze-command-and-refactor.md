# Single Unified `analyze` CLI Command & Translator Decoupling Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Consolidate Omni Pilot CLI into a single autonomous `analyze` command, decouple the Gemini translation logic into a dedicated `translator.py` module with max 1 retry, eliminate duplicated parsing/DB code, and update all tests to follow the pytest skill.

**Architecture:** 
- Extract Gemini API calls, retries, and mapping synchronization into `src/omni_pilot/translator.py`.
- Refactor `src/omni_pilot/cli.py` to remove the `import` subcommand and make `cmd_analyze` autonomously execute the end-to-end pipeline (parse ➔ resolve & sync mappings ➔ USDA enrich ➔ analyze ➔ report).
- Maintain 100% functional parity for iCloud sync, TinyDB caching, fallback behaviors, and report generation.

**Tech Stack:** Python 3.14+, `pytest`, `pytest-mock`, `tinydb`, `requests`, `tenacity`, `pyyaml`, `openpyxl`, `rich`, `jinja2`.

## Global Constraints

- Max 1 retry for Gemini API calls (`stop=stop_after_attempt(2)`).
- Strict adherence to the `pytest` skill (use `test_*` functions, plain `assert`, `mocker` fixture from `pytest-mock`, no `unittest` imports or TestCase subclasses).
- Preserve existing settings schema and path resolution (`database_path`, `mappings_path`, iCloud Drive support).
- Graceful fallback: when Gemini API key is missing or fails, log a warning, mark unmapped foods, and proceed with analyzing resolved foods without crashing.

---

### Task 1: Add `pytest-mock` Dependency

**Files:**
- Modify: `pyproject.toml:20-24`

**Interfaces:**
- Produces: `pytest-mock` installed in development environment for `mocker` fixture injection.

- [ ] **Step 1: Update `pyproject.toml` with `pytest-mock`**

```toml
[dependency-groups]
dev = [
    "pytest>=7.0.0",
    "pytest-mock>=3.12.0",
]
```

- [ ] **Step 2: Sync dependencies with `uv sync`**

Run: `uv sync`
Expected: Dependencies updated and `pytest-mock` installed.

- [ ] **Step 3: Verify pytest recognizes pytest-mock**

Run: `uv run pytest --version`
Expected: Output showing `pytest-mock` registered in plugins.

---

### Task 2: Create `src/omni_pilot/translator.py` and Unit Tests

**Files:**
- Create: `src/omni_pilot/translator.py`
- Create: `tests/test_translator.py`

**Interfaces:**
- Produces:
  - `translate_new_foods(foods_list: list[str], api_key: str, model: str = "gemini-flash-latest") -> list[str]`
  - `resolve_and_sync_mappings(foods: list[str], db_path: str, mappings_path: str, settings: dict) -> dict[str, str]`
- Consumes:
  - `omni_pilot.parser.generate_food_mappings`

- [ ] **Step 1: Write unit tests in `tests/test_translator.py` using pytest & pytest-mock**

```python
"""tests/test_translator.py"""
from __future__ import annotations

import json
import pytest
import requests
from tinydb import TinyDB
import yaml

from omni_pilot.translator import translate_new_foods, resolve_and_sync_mappings


def test_translate_new_foods_success(mocker):
    """Test successful Gemini API translation response."""
    mock_response = mocker.MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {"text": json.dumps(["milk, reduced fat, fluid, 2% milkfat", "potatoes, raw, skin"])}
                    ]
                }
            }
        ]
    }
    mocker.patch("requests.post", return_value=mock_response)

    results = translate_new_foods(["Milch 1.5%", "Kartoffeln"], api_key="fake_key")
    assert results == ["milk, reduced fat, fluid, 2% milkfat", "potatoes, raw, skin"]


def test_translate_new_foods_retry_limit(mocker):
    """Test that transient failures retry at most once (2 total attempts)."""
    mock_post = mocker.patch("requests.post", side_effect=requests.RequestException("Network Error"))

    with pytest.raises(requests.RequestException):
        translate_new_foods(["Milch"], api_key="fake_key")

    # 1 initial attempt + 1 retry = 2 total attempts
    assert mock_post.call_count == 2


def test_resolve_and_sync_mappings_gemini_success(mocker, tmp_path):
    """Test end-to-end resolution and persistence when new foods are translated."""
    db_path = str(tmp_path / "food_db.json")
    mappings_path = str(tmp_path / "food_mappings.yaml")

    # Initial mapping file with one existing item
    with open(mappings_path, "w") as f:
        yaml.dump({"mappings": {"Existing Food": "Oats"}}, f)

    mocker.patch(
        "omni_pilot.translator.translate_new_foods",
        return_value=["egg, whole, raw, fresh"],
    )

    settings = {"gemini_api_key": "valid_key", "gemini_model": "gemini-flash-latest"}
    foods = ["Existing Food", "New Food"]

    mappings = resolve_and_sync_mappings(foods, db_path, mappings_path, settings)

    assert mappings["Existing Food"] == "Oats"
    assert mappings["New Food"] == "egg, whole, raw, fresh"

    # Verify TinyDB persistence
    db = TinyDB(db_path)
    translations = db.table("translations").all()
    assert any(t["german"] == "New Food" and t["english"] == "egg, whole, raw, fresh" for t in translations)

    # Verify YAML persistence
    with open(mappings_path) as f:
        data = yaml.safe_load(f)
    assert data["mappings"]["New Food"] == "egg, whole, raw, fresh"


def test_resolve_and_sync_mappings_missing_key_graceful(tmp_path):
    """Test graceful fallback when no Gemini key is provided."""
    db_path = str(tmp_path / "food_db.json")
    mappings_path = str(tmp_path / "food_mappings.yaml")

    settings = {}
    foods = ["Unknown Food"]

    mappings = resolve_and_sync_mappings(foods, db_path, mappings_path, settings)

    # Does not crash, mappings dictionary contains known entries
    assert "Unknown Food" not in mappings
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_translator.py -v`
Expected: FAIL with ModuleNotFoundError / import error for `omni_pilot.translator`.

- [ ] **Step 3: Implement `src/omni_pilot/translator.py`**

```python
"""Gemini AI food translation and mapping synchronization."""
from __future__ import annotations

import json
import logging
import os
import requests
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from tinydb import TinyDB, Query

from omni_pilot.config import load_food_mappings
from omni_pilot.parser import generate_food_mappings

logger = logging.getLogger(__name__)

GEMINI_PROMPT_TEMPLATE = """
You are an expert nutritionist translating food log entries (mostly German or branded) into optimal USDA FoodData Central (SR Legacy and Foundation datasets) search queries.

CRITICAL RULES FOR USDA SEARCH OPTIMIZATION:
1. USDA TAXONOMY: Format queries to match USDA staple naming conventions so whole staple foods rank #1 over processed byproducts (powders, baby food, crackers, flours, breads).
2. STATE & FORM: Always specify state ('raw', 'cooked', 'fluid', 'frozen', 'canned') when applicable:
   - VEGETABLES/FRUITS: 'tomatoes, red, ripe, raw' (never plain 'tomatoes'), 'potatoes, raw', 'vegetables, mixed, frozen, unprepared', 'olives, ripe, canned'.
   - LIQUID MILK: Must include 'fluid' and fat % (e.g. 'milk, reduced fat, fluid, 2% milkfat' or 'milk, whole, fluid'). Never plain 'milk' or 'low fat milk'.
   - GRAINS/CEREALS: Specify grain/cereal (e.g. 'rice, white, long-grain, regular, raw', 'rice, brown, long-grain, raw', 'cereals, oats, regular and quick, not fortified, dry'). Never plain 'rice' or 'oats'.
   - MEAT/POULTRY: Specify whole meat cut (e.g. 'chicken, broilers or fryers, breast, meat only, raw', 'chicken, liver, raw', 'beef, ground, 85% lean, raw'). Never plain 'chicken breast' (matches sliced lunchmeat).
   - OILS & BUTTER: 'oil, olive, salad or cooking', 'butter, without salt'.
   - CHEESES: 'cheese, swiss', 'cheese, feta', 'cheese, mozzarella, whole milk', 'cheese, gruyere'.
3. NOISE CLEANING: Remove prices, package weights, store names (e.g., Rewe, Edeka, Bio, XXL, 250g, 1.29€).
4. EXCLUSIONS & BASE FOOD MAPPING:
   - ALWAYS return 'skip' for 'Quick Add', water, and non-food entries (e.g., pill/capsule supplements).
   - For branded cereals/muesli/granola (e.g., 'Protein Müsli', 'Krunchy Chocolate Chunks'), map to base cereal: 'muesli' or 'cereals ready-to-eat, granola'.
   - For puddings/desserts (e.g., 'High-Protein-Pudding - Schoko'), map to base dessert: 'puddings, chocolate, ready-to-eat'.
   - For protein bars/bites, map to: 'protein bar' or 'snacks, granola bars, hard, almond'.
   - Return 'skip' for ultra-processed plant-based meat substitutes without whole-food equivalents (e.g., 'Like Tender Crunch').

EXAMPLES:
- "Frische Fettarme Bio Alpenmilch Laktosefrei" -> "milk, reduced fat, fluid, 2% milkfat"
- "Die Feinen Speisekartoffeln, Qualität I, Festkochend" -> "potatoes, raw, skin"
- "Haferflocken" -> "cereals, oats, regular and quick, not fortified, dry"
- "Tomaten" -> "tomatoes, red, ripe, raw, year round average"
- "Königsgemüse" / "Suppengemüse" -> "vegetables, mixed, frozen, unprepared"
- "Jasmin-Reis" -> "rice, white, long-grain, regular, raw, unenriched"
- "Hähnchen Brustfilet" -> "chicken, broilers or fryers, breast, meat only, raw"
- "Einfach Bio Hackfleisch Rind Zum Braten" -> "beef, ground, raw"
- "Rinder Rumpsteak" -> "beef, top sirloin, steak, raw"
- "Oliven Schwarze Oliven" -> "olives, ripe, canned (small-extra large)"
- "Protein Müsli 2" -> "muesli"
- "Krunchy Chocolate Chunks Mit Cornflakes" -> "cereals ready-to-eat, granola"
- "High-Protein-Pudding - Schoko" -> "puddings, chocolate, ready-to-eat"
- "Like Tender Crunch Original" -> "skip"
- "Quick Add" -> "skip"

Input foods:
{foods_json}
"""


@retry(
    stop=stop_after_attempt(2),
    wait=wait_exponential(multiplier=1, min=2, max=5),
    retry=retry_if_exception_type((requests.RequestException, json.JSONDecodeError, KeyError, IndexError)),
    reraise=True,
)
def translate_new_foods(foods_list: list[str], api_key: str, model: str = "gemini-flash-latest") -> list[str]:
    """Translate and clean food log queries for USDA FoodData Central search using Gemini REST API."""
    prompt = GEMINI_PROMPT_TEMPLATE.format(foods_json=json.dumps(foods_list))
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.0,
            "responseMimeType": "application/json",
            "responseSchema": {
                "type": "ARRAY",
                "items": {"type": "STRING"}
            }
        }
    }
    resp = requests.post(url, json=payload, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    raw_text = data["candidates"][0]["content"]["parts"][0]["text"]
    return json.loads(raw_text)


def resolve_and_sync_mappings(
    foods: list[str],
    db_path: str,
    mappings_path: str,
    settings: dict,
) -> dict[str, str]:
    """Resolve food mappings from DB & YAML, translate unknown foods via Gemini, and sync persistence."""
    # 1. Load existing mappings from YAML
    existing_mappings = load_food_mappings(mappings_path)

    # 2. Load translations from TinyDB
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    db = TinyDB(db_path)
    translations_table = db.table("translations")
    db_mappings = {
        doc["german"]: doc["english"]
        for doc in translations_table.all()
        if doc.get("english", "").strip()
    }

    # 3. Merge: DB takes precedence, then existing non-empty YAML
    known_mappings = {**existing_mappings, **db_mappings}
    known_mappings = {k: str(v).strip() for k, v in known_mappings.items() if v and str(v).strip()}
    new_foods = [f for f in foods if f not in known_mappings]

    # 4. If new foods exist, attempt Gemini translation
    if new_foods:
        print(f"  {len(new_foods)} new foods need English equivalents.")
        gemini_key = settings.get("gemini_api_key")
        gemini_model = settings.get("gemini_model", "gemini-flash-latest")

        if gemini_key and gemini_key != "YOUR_GEMINI_API_KEY_HERE":
            print(f"  Using Gemini API ({gemini_model}) to automatically translate and clean foods...")
            try:
                translations = translate_new_foods(new_foods, gemini_key, model=gemini_model)
                if len(translations) == len(new_foods):
                    print("  Gemini translation successful! Saving to database...")
                    for german_food, english_food in zip(new_foods, translations):
                        if english_food and english_food.strip() and english_food != "ERROR":
                            clean_english = english_food.strip()
                            known_mappings[german_food] = clean_english
                            translations_table.upsert(
                                {"german": german_food, "english": clean_english},
                                Query().german == german_food,
                            )
                else:
                    logger.warning("Gemini returned a different number of translations (%d) than expected (%d).", len(translations), len(new_foods))
            except Exception as e:
                logger.warning("Gemini translation failed: %s. Proceeding with unresolved items.", e)
        else:
            print("  No valid gemini_api_key found in settings. Skipping automatic translation.")
            print(f"  You can edit {mappings_path} and fill in USDA-searchable names.")

    # 5. Sync any manual YAML updates into TinyDB
    existing_db_translations = {
        doc["german"]: doc.get("english", "")
        for doc in translations_table.all()
    }
    TranslationQuery = Query()
    for german, english in known_mappings.items():
        if english and existing_db_translations.get(german) != english:
            translations_table.upsert(
                {"german": german, "english": english},
                TranslationQuery.german == german,
            )

    # 6. Update YAML file (only writes if changed)
    generate_food_mappings(foods, known_mappings, mappings_path)

    return known_mappings
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_translator.py -v`
Expected: 4 passed.

---

### Task 3: Refactor `src/omni_pilot/cli.py` and Update CLI Tests

**Files:**
- Modify: `src/omni_pilot/cli.py`
- Modify: `tests/test_cli.py`

**Interfaces:**
- Produces: CLI with single `analyze` subparser; imports `resolve_and_sync_mappings` from `omni_pilot.translator`.
- Removes: `cmd_import`, `translate_new_foods` from `cli.py`.

- [ ] **Step 1: Refactor `src/omni_pilot/cli.py`**

Cleanly replace the duplicated `import` command and internal `translate_new_foods` in `cli.py` with calls to `omni_pilot.translator.resolve_and_sync_mappings`:

```python
"""CLI entry point for Omni Pilot."""
from __future__ import annotations

import argparse
import logging
import os
import shutil
import sys
from datetime import date

from tinydb import TinyDB

from omni_pilot.config import (
    load_settings,
    load_reference_ranges,
    load_supplements,
    resolve_path,
)
from omni_pilot.parser import parse_food_log, extract_unique_foods
from omni_pilot.translator import resolve_and_sync_mappings
from omni_pilot.enricher import enrich_all_foods
from omni_pilot.analyzer import analyze
from omni_pilot.reporter import print_terminal_report, generate_html_report

logger = logging.getLogger(__name__)

# Default paths relative to project root
DEFAULT_SETTINGS = "config/settings.yaml"
DEFAULT_REF_RANGES = "config/reference_ranges.yaml"
DEFAULT_MAPPINGS = "config/food_mappings.yaml"
DEFAULT_SUPPLEMENTS = "config/supplements.yaml"
DEFAULT_DB = "db/food_db.json"


def cmd_analyze(args: argparse.Namespace) -> None:
    """Run full autonomous pipeline: parse → resolve/translate mappings → enrich → analyze → report."""
    xlsx_path = args.xlsx_file
    settings_path = args.settings or DEFAULT_SETTINGS
    ref_ranges_path = args.ref_ranges or DEFAULT_REF_RANGES
    supplements_path = args.supplements or DEFAULT_SUPPLEMENTS

    # Load config
    settings = load_settings(settings_path)
    ref_ranges = load_reference_ranges(ref_ranges_path)
    supplements = load_supplements(supplements_path)

    db_path = resolve_path("database_path", DEFAULT_DB, settings)
    mappings_path = resolve_path("mappings_path", DEFAULT_MAPPINGS, settings)

    print(f"Database: {db_path}")
    print(f"Mappings: {mappings_path}")

    api_key = settings.get("usda_api_key", "")
    if not api_key:
        print("Error: No USDA API key in settings.yaml.")
        sys.exit(1)

    # 1. Parse Excel Food Log
    print(f"Parsing {xlsx_path}...")
    entries = parse_food_log(xlsx_path)
    print(f"  Found {len(entries)} food entries.")
    food_names = extract_unique_foods(entries)
    print(f"  Found {len(food_names)} unique foods.")

    # 2. Resolve & Sync Mappings (Auto-Translate with Gemini if new foods found)
    mappings = resolve_and_sync_mappings(food_names, db_path, mappings_path, settings)

    # 3. Enrich foods with USDA data
    print("Enriching foods with USDA data...")
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    db = TinyDB(db_path)
    enriched = enrich_all_foods(food_names, mappings, db, api_key)
    resolved = sum(1 for v in enriched.values() if v is not None)
    print(f"  {resolved}/{len(food_names)} foods resolved.")

    # 4. Analyze Micronutrient Intake
    print("Analyzing micronutrient intake...")
    result = analyze(entries, enriched, ref_ranges, supplements=supplements)

    # 5. Report
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
        latest_path = os.path.join(reports_dir, "latest.html")
        if os.path.exists(html_path):
            shutil.copyfile(html_path, latest_path)
        print(f"\nHTML report saved to: {html_path}")
        print(f"  Latest report copy: {latest_path}")


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

    # Analyze command (single autonomous command)
    analyze_parser = subparsers.add_parser(
        "analyze", help="Analyze micronutrient intake"
    )
    analyze_parser.add_argument("xlsx_file", help="Path to MacroFactor xlsx export")
    analyze_parser.add_argument(
        "--html", action="store_true", help="Also generate HTML report"
    )
    analyze_parser.add_argument("--settings", default=None, help=f"Path to settings (default: {DEFAULT_SETTINGS})")
    analyze_parser.add_argument("--ref-ranges", default=None, help=f"Path to reference ranges (default: {DEFAULT_REF_RANGES})")
    analyze_parser.add_argument("--supplements", default=None, help=f"Path to supplements (default: {DEFAULT_SUPPLEMENTS})")

    args = parser.parse_args()

    if args.command == "analyze":
        cmd_analyze(args)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Refactor `tests/test_cli.py` to use native pytest & test the autonomous `analyze` command**

Refactor `tests/test_cli.py` removing legacy `import` tests, replacing `mock.patch` with `mocker.patch`, and testing the integrated `cmd_analyze` pipeline.

- [ ] **Step 3: Run CLI tests**

Run: `uv run pytest tests/test_cli.py -v`
Expected: All tests pass.

---

### Task 4: Update `Makefile`, `README.md`, and Documentation

**Files:**
- Modify: `Makefile`
- Modify: `README.md`

- [ ] **Step 1: Update `Makefile`**

Remove `import:` target; ensure `analyze:` target works standalone.

```makefile
.PHONY: help analyze test

help:
	@echo "Available commands:"
	@echo "  make analyze FILE=<path> - Analyze micronutrient intake (with HTML report)"
	@echo "  make test                - Run tests with pytest"

analyze:
ifndef FILE
	$(error FILE is not set. Usage: make analyze FILE=data/your-export.xlsx)
endif
	@test -f "$(FILE)" || (echo "Error: File '$(FILE)' does not exist." && exit 1)
	PYTHONPATH=src uv run python -m omni_pilot.cli analyze "$(FILE)" --html

test:
	uv run pytest
```

- [ ] **Step 2: Update `README.md`**

Update the architecture diagram, Desktop usage, a-Shell daily usage, and setup guides to reference `make analyze` / single `analyze` command.

---

### Task 5: End-to-End Verification & Test Suite Execution

- [ ] **Step 1: Run complete pytest suite**

Run: `uv run pytest -v`
Expected: 100% tests pass.

- [ ] **Step 2: Run live manual CLI execution with sample data**

Run: `uv run python -m omni_pilot.cli analyze data/MacroFactor-example.xlsx --html`
Expected: Terminal report output, HTML report generated at `reports/latest.html`, exit code 0.
