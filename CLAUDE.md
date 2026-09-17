# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

Omni Pilot is a Python CLI that analyzes daily micronutrient intake from MacroFactor nutrition-tracking `.xlsx` exports. It compares intake against configurable NIH/WHO reference ranges and produces terminal + HTML reports. It's designed to run identically on a Mac (via `uv`) and on an iPhone inside the **a-Shell** app (via plain `pip`/`python`), so it deliberately uses only pure-Python dependencies (no C/Rust extensions).

## Commands

```bash
# Install deps (uv-based project)
uv sync

# Run the full pipeline: parse xlsx -> translate/enrich foods -> analyze -> report
make analyze FILE=data/MacroFactor-Export.xlsx
# equivalent to:
PYTHONPATH=src uv run python -m omni_pilot.cli analyze "data/MacroFactor-Export.xlsx" --html

# Run tests
make test
# or
uv run pytest

# Run a single test file / test
uv run pytest tests/test_analyzer.py
uv run pytest tests/test_analyzer.py::test_name -v

# Regenerate requirements.txt (used for the iOS a-Shell / pip install path)
uv pip compile pyproject.toml -o requirements.txt
```

Config files must be created from templates before first run (`config/settings.yaml`, `config/supplements.yaml`, `config/food_mappings.yaml` from their `*.example.yaml` counterparts). `config/settings.yaml` requires a USDA FoodData Central API key; a Gemini API key is optional but enables automatic food translation.

## Architecture

The CLI has a single command (`analyze`, in [src/omni_pilot/cli.py](src/omni_pilot/cli.py)) that runs a fixed autonomous pipeline through five modules in `src/omni_pilot/`:

1. **`parser.py`** — Reads the "Food Log" sheet of a MacroFactor `.xlsx` export (fixed 0-indexed column layout) into `FoodEntry` records, and extracts the set of unique food names.
2. **`translator.py`** — Resolves each unique food name to a USDA-searchable English query string. Mapping precedence: TinyDB cache > `config/food_mappings.yaml` > Gemini API translation for anything still unresolved (via `resolve_and_sync_mappings`). Foods explicitly mapped to `"skip"` (e.g. water, "Quick Add", supplement pills) are excluded from analysis. Gemini calls go through the REST API directly (not an SDK) with a structured JSON schema response and `tenacity` retries.
3. **`enricher.py`** — Looks up each mapped food name in USDA FoodData Central (SR Legacy / Foundation datasets), extracting a fixed set of nutrients (`USDA_NUTRIENT_MAP`: vitamins, minerals, omega-3s, amino acids) per 100g. Results are cached in TinyDB (`db/food_db.json`) keyed by original food name + the USDA query used, so a changed mapping invalidates and refetches the cache entry. `enrich_all_foods` returns an `EnrichmentResult` with three parts: `profiles` (resolved foods only, never `None`), `skipped` (mapped to `"skip"`) and `unresolved` (USDA lookup failed). Keep those two sets distinct — the report labels them differently, and encoding both as `None` once made failed lookups read as deliberate skips.
4. **`analyzer.py`** — Scales each food entry's per-100g nutrient values by actual serving weight, sums per day, averages across the date range, adds any configured supplement contributions, and compares against `config/reference_ranges.yaml` targets (RDA / AI / WHO per-kg-bodyweight amino acid targets, computed in `config.get_nutrient_target`). Some reference nutrients are sums of multiple USDA-tracked values (`COMBINED_NUTRIENTS`, e.g. methionine+cysteine, EPA+DHA). Status per nutrient is one of `ok` / `low` / `deficient` / `high` based on target and upper limit (UL). It also tracks **USDA data coverage** per nutrient: a food whose USDA profile has no value for a nutrient (`None`, meaning never assayed — distinct from a measured `0.0`) contributes nothing to that nutrient, and its serving weight is booked as unmeasured rather than zero-filled. Each nutrient carries `coverage_pct` (share of consumed weight that had a USDA value, `None` when no weight was attributable at all) and `is_floor` (a `low`/`deficient` verdict below full coverage, marked `*` in both reports because absent data can only push the true value up).
5. **`reporter.py`** — Renders the `AnalysisResult` as a Rich terminal table (grouped by category: Vitamins, Minerals, Other, Amino Acids) and/or a self-contained HTML report via a Jinja2 template. HTML reports are timestamped in `reports/` and also copied to `reports/latest.html`.

`config.py` centralizes YAML loading (`settings.yaml`, `reference_ranges.yaml`, `food_mappings.yaml`, `supplements.yaml`) and path resolution (`resolve_path`, which expands `~`/env vars — used to point `database_path`/`mappings_path` at an iCloud Drive location for cross-device sync between Mac and iPhone).

### Data flow

```
xlsx → parser.parse_food_log → entries
entries → parser.extract_unique_foods → food_names
food_names → translator.resolve_and_sync_mappings (TinyDB + YAML + Gemini) → mappings
food_names, mappings → enricher.enrich_all_foods (TinyDB cache + USDA API) → EnrichmentResult
entries, EnrichmentResult, ref_ranges, supplements → analyzer.analyze → AnalysisResult
AnalysisResult → reporter.print_terminal_report / generate_html_report
```

### Key invariants

- Nutrient keys are the single vocabulary tying `reference_ranges.yaml`, `USDA_NUTRIENT_MAP` in `enricher.py`, `COMBINED_NUTRIENTS` in `analyzer.py`, and `NUTRIENT_CATEGORIES` in `reporter.py` together — adding a new nutrient means touching all four.
- A reference nutrient key with no `USDA_NUTRIENT_MAP` entry surfaces as 0% coverage, not as a measured `0.0` — the coverage figure is what makes such a gap visible, so never reintroduce a `.get(key, 0.0)` default in the analyzer's summing or averaging steps.
- `omega3_epa_mg`/`omega3_dha_mg` are stored in grams by USDA but converted to mg during analysis (`analyzer.py`); watch for this unit mismatch when touching nutrient math.
- TinyDB (`db/food_db.json`) is the source of truth for both food translations (`translations` table, written by `translator.py`) and USDA nutrient profiles (default table, written by `enricher.py`); `config/food_mappings.yaml` is a human-editable mirror of the translations that gets synced both ways.
