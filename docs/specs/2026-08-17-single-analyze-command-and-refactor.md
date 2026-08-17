# Technical Specification: Single Unified `analyze` CLI Command & Translator Decoupling (BAR-34)

**Document Version:** 1.1  
**Date:** 2026-08-17  
**Status:** In Review  
**Linear Issue:** [BAR-34](https://linear.app/knaak/issue/BAR-34/refactor-the-code)

---

## 1. Executive Summary & Objective

### The Problem
Previously, Omni Pilot used a 2-step manual CLI workflow:
1. `python -m omni_pilot.cli import <file.xlsx>` to parse foods, translate new items via Gemini, and generate `config/food_mappings.yaml`.
2. `python -m omni_pilot.cli analyze <file.xlsx> --html` to enrich foods with USDA data, calculate daily averages vs NIH/WHO reference ranges, and generate reports.

This separation was originally created when food mappings had to be manually edited by a human. With the addition of automatic Gemini AI translation, requiring two separate commands became redundant. Furthermore, `cmd_import` and `cmd_analyze` duplicated code for:
- Path resolution and configuration loading (`settings.yaml`)
- Excel workbook parsing (`parse_food_log`)
- Unique food extraction (`extract_unique_foods`)
- TinyDB opening and `translations` table syncing

### The Solution
1. **Single Autonomous Command (`analyze`)**: The user runs `python -m omni_pilot.cli analyze <file.xlsx> [--html]` (or `make analyze FILE=<path>`). It autonomously parses the log, checks and translates any newly introduced foods via Gemini, syncs mappings to TinyDB and `food_mappings.yaml`, enriches them via USDA FoodData Central, and generates the reports in one seamless run.
2. **Dedicated `translator.py` Module**: Move and encapsulate the Gemini REST API calls, retry policies, and mapping synchronization logic from `cli.py` into a clean, testable `src/omni_pilot/translator.py` module.
3. **Preserve 100% Functional Parity**:
   - Keep identical behavior for iCloud Drive path expansion (`database_path`, `mappings_path`) across macOS Desktop and iPhone (a-Shell / Shortcuts).
   - Keep identical TinyDB `translations` table format, precedence rules (TinyDB takes precedence, merges non-empty YAML), and mapping file formatting.
   - Graceful fallback: if no Gemini API key is configured or the translation request fails, warn the user, record unmapped foods in `food_mappings.yaml`, mark them as unresolved, and continue analyzing resolved foods.

---

## 2. System Architecture & Pipeline

```text
MacroFactor .xlsx Log
       │
       ▼
┌────────────────────────────────────────────────────────┐
│ 1. Parser (parser.py: parse_food_log)                  │
│    Extracts food log entries and unique food names     │
└──────────────────────────┬─────────────────────────────┘
                           │
                           ▼
┌────────────────────────────────────────────────────────┐
│ 2. Translation Layer (translator.py)                   │
│    - Loads existing mappings from TinyDB & YAML        │
│    - If new foods exist:                               │
│        • If Gemini API key present ➔ translate & clean │
│          (with max 1 retry on transient failure)       │
│        • If no key / API error ➔ warn & mark unmapped  │
│    - Persists non-empty translations to TinyDB & YAML  │
└──────────────────────────┬─────────────────────────────┘
                           │
                           ▼
┌────────────────────────────────────────────────────────┐
│ 3. USDA Enrichment (enricher.py: enrich_all_foods)     │
│    - Checks TinyDB cache for USDA nutrient profiles    │
│    - Fetches uncached foods from USDA FoodData API     │
└──────────────────────────┬─────────────────────────────┘
                           │
                           ▼
┌────────────────────────────────────────────────────────┐
│ 4. Micronutrient Analyzer (analyzer.py: analyze)       │
│    - Computes daily averages & merges supplements      │
│    - Compares intake vs NIH/WHO reference targets      │
└──────────────────────────┬─────────────────────────────┘
                           │
                           ▼
┌────────────────────────────────────────────────────────┐
│ 5. Reporting Layer (reporter.py)                       │
│    - Renders terminal color-coded summary table        │
│    - Generates HTML report & copies to latest.html     │
└────────────────────────────────────────────────────────┘
```

---

## 3. Detailed Component Specifications

### 3.1 New Module: `src/omni_pilot/translator.py`
Move and encapsulate translation and mapping synchronization responsibilities into `src/omni_pilot/translator.py`:
- `translate_new_foods(foods_list: list[str], api_key: str, model: str = "gemini-flash-latest") -> list[str]`:
  - Retains the exact nutritional prompt and structured JSON array schema.
  - **Retry Policy**: Configured with `@retry(stop=stop_after_attempt(2), wait=wait_exponential(multiplier=1, min=2, max=5), retry=retry_if_exception_type((requests.RequestException, json.JSONDecodeError, KeyError, IndexError)), reraise=True)` to allow **only 1 retry** (2 total attempts max) on transient failure.
- `resolve_and_sync_mappings(foods: list[str], db_path: str, mappings_path: str, settings: dict) -> dict[str, str]`:
  - Encapsulates the existing logic previously split across `cmd_import` and `cmd_analyze`:
    1. Loads existing mappings from `mappings_path` (YAML) and `db_path` (TinyDB `translations` table).
    2. Merges known mappings (TinyDB takes precedence + non-empty YAML entries).
    3. Detects `new_foods = [f for f in foods if f not in known_mappings]`.
    4. If `new_foods` exist:
       - If `gemini_api_key` is present and valid, calls `translate_new_foods` (with max 1 retry).
       - Upserts non-empty translations to TinyDB `translations` table.
       - If Gemini key is missing or call fails, warns the user and marks unmapped foods so they can be processed as unresolved by the enricher.
    5. Saves/updates non-empty mappings to `food_mappings.yaml` via `generate_food_mappings`.
    6. Returns `known_mappings`.

### 3.2 Refactored: `src/omni_pilot/cli.py`
- Remove `cmd_import` and the `import` subparser.
- Keep `cmd_analyze(args)` as the sole pipeline orchestrator:
  1. Loads configuration (`settings.yaml`, `reference_ranges.yaml`, `supplements.yaml`).
  2. Resolves paths (`database_path`, `mappings_path`).
  3. Parses Excel file (`parse_food_log`) and extracts unique foods (`extract_unique_foods`).
  4. Calls `translator.resolve_and_sync_mappings(...)`.
  5. Calls `enricher.enrich_all_foods(...)`.
  6. Calls `analyzer.analyze(...)`.
  7. Calls `reporter.print_terminal_report(...)` and optionally `reporter.generate_html_report(...)`.
- CLI syntax:
  ```bash
  python -m omni_pilot.cli analyze <xlsx_file> [--html] [--settings <path>] [--ref-ranges <path>] [--supplements <path>]
  ```

### 3.3 Supporting Files & Automation
- **`Makefile`**:
  - Update `make analyze FILE=<path>` to run the unified command.
  - Remove `make import` target.
- **`README.md`**:
  - Update Desktop and a-Shell/Shortcuts usage sections to reflect the single-step `analyze` command.
- **`tests/`**:
  - Follow the **pytest skill** conventions strictly (plain functions `test_*`, plain `assert`, `mocker` fixture, zero `unittest` imports/subclasses).
  - Add `pytest-mock` to dev dependencies in `pyproject.toml`.
  - Add unit tests in `tests/test_translator.py`.
  - Refactor `tests/test_cli.py` to test the integrated `analyze` command.

---

## 4. Verification Plan

### Automated Tests (Pytest Skill Compliant)
1. **Unit Tests (`tests/test_translator.py` & `tests/test_cli.py`)**:
   - `test_translate_new_foods_success(mocker)`: Mock Gemini API response and verify returned translations.
   - `test_translate_new_foods_retry_limit(mocker)`: Verify that failing calls are retried at most once (2 total attempts).
   - `test_resolve_and_sync_mappings_gemini_success(mocker, tmp_path)`: Verify TinyDB upsert and YAML update when new foods are translated.
   - `test_resolve_and_sync_mappings_missing_key_graceful(mocker, tmp_path)`: Verify graceful warning and non-crashing behavior when Gemini API key is missing.
   - `test_analyze_pipeline_end_to_end(mocker, tmp_path)`: Verify `analyze` runs end-to-end with mock Gemini and mock USDA responses.
2. **Regression Suite**:
   - Run full pytest test suite: `uv run pytest`.

### Manual Verification
- Run `make analyze FILE=data/MacroFactor-example.xlsx` against sample dataset and verify terminal output and HTML report generation.
