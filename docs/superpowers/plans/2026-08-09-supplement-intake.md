# Supplement Intake Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Include daily supplement intake in the micronutrient analysis to accurately reflect total nutrient intake.

**Architecture:** Create `config/supplements.yaml`, load it via a new `load_supplements` function in `config.py`, and pass it to `analyze()` in `analyzer.py` to add to the final daily averages.

**Tech Stack:** Python 3, PyYAML, Pytest

## Global Constraints

- Python 3.9+ (use `from __future__ import annotations` for type hints)
- All dependencies managed by `uv` — never global pip
- Unit system: grams only — no ml handling
- Run with: `uv run python -m omni_pilot <command>` from project root
- Use `logging` module for warnings (not print)

---

### Task 1: Supplements Config Loader

**Files:**
- Create: `config/supplements.yaml`
- Modify: `src/omni_pilot/config.py`
- Modify: `tests/test_config.py`

**Interfaces:**
- Consumes: `config/supplements.yaml`
- Produces:
  - `load_supplements(path: str) -> dict` — returns parsed supplements mapping (empty dict if file does not exist)

- [ ] **Step 1: Write the failing test**

```python
# Add to tests/test_config.py

from omni_pilot.config import load_supplements

class TestLoadSupplements:
    def test_loads_existing_supplements(self, tmp_path):
        supplements_file = tmp_path / "supplements.yaml"
        with open(supplements_file, "w") as f:
            f.write("vitamin_d_mcg: 25.0\nomega3_epa_dha_mg: 1000.0\n")
        
        result = load_supplements(str(supplements_file))
        assert result["vitamin_d_mcg"] == 25.0
        assert result["omega3_epa_dha_mg"] == 1000.0

    def test_returns_empty_dict_when_file_missing(self, tmp_path):
        result = load_supplements(str(tmp_path / "nonexistent.yaml"))
        assert result == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_config.py::TestLoadSupplements -v`
Expected: FAIL with "ImportError: cannot import name 'load_supplements' from 'omni_pilot.config'"

- [ ] **Step 3: Write minimal implementation**

```python
# Add to src/omni_pilot/config.py

def load_supplements(path: str) -> dict:
    """Load supplements.yaml and return the mapping dict.

    Returns empty dict if file does not exist.
    """
    if not os.path.exists(path):
        return {}
    with open(path, "r") as f:
        data = yaml.safe_load(f)
    return data if data is not None else {}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_config.py::TestLoadSupplements -v`
Expected: PASS

- [ ] **Step 5: Create empty configuration file**

Run: `echo "# Daily supplement intake matching system units (mcg, mg, g)\n# e.g., vitamin_d_mcg: 25.0\n" > config/supplements.yaml`

- [ ] **Step 6: Commit**

```bash
git add tests/test_config.py src/omni_pilot/config.py config/supplements.yaml
git commit -m "feat: add supplements config loader"
```

### Task 2: Analyzer Integration

**Files:**
- Modify: `src/omni_pilot/analyzer.py`
- Modify: `tests/test_analyzer.py`

**Interfaces:**
- Consumes: `supplements: dict` parameter in `analyze()` function (default to `{}`)
- Produces: Updated `daily_avg` for nutrients in `AnalysisResult` that include supplemental amounts.

- [ ] **Step 1: Write the failing test**

```python
# Add to tests/test_analyzer.py

from omni_pilot.analyzer import analyze

class TestAnalyzeSupplements:
    def test_supplements_added_to_daily_avg(self):
        entries = [{"date": "2026-08-09", "food_name": "Apple", "total_weight_g": 100}]
        enriched = {"Apple": {"vitamin_c_mg": 5.0}}
        ref_ranges = {
            "demographic": {},
            "nutrients": {
                "vitamin_c_mg": {"name": "Vitamin C", "unit": "mg", "type": "rda", "rda": 90}
            }
        }
        supplements = {"vitamin_c_mg": 50.0}
        
        result = analyze(entries, enriched, ref_ranges, supplements=supplements)
        # 5.0 from food + 50.0 from supplement
        assert result["nutrients"]["vitamin_c_mg"]["daily_avg"] == 55.0

    def test_missing_supplement_ignored(self):
        entries = [{"date": "2026-08-09", "food_name": "Apple", "total_weight_g": 100}]
        enriched = {"Apple": {"vitamin_c_mg": 5.0}}
        ref_ranges = {
            "demographic": {},
            "nutrients": {
                "vitamin_c_mg": {"name": "Vitamin C", "unit": "mg", "type": "rda", "rda": 90}
            }
        }
        
        result = analyze(entries, enriched, ref_ranges, supplements=None)
        assert result["nutrients"]["vitamin_c_mg"]["daily_avg"] == 5.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_analyzer.py::TestAnalyzeSupplements -v`
Expected: FAIL (either `TypeError: analyze() got an unexpected keyword argument 'supplements'` or assertion error on daily avg)

- [ ] **Step 3: Write minimal implementation**

Modify `analyze()` signature in `src/omni_pilot/analyzer.py`:
```python
def analyze(
    entries: list[dict],
    enriched: dict[str, dict[str, float | None] | None],
    ref_ranges: dict,
    supplements: dict | None = None,
) -> AnalysisResult:
```

Add inside `analyze()`, immediately before determining status (after `daily_avg = total_across_days / num_days if num_days > 0 else 0.0`):
```python
        if supplements is None:
            supplements = {}
            
        # Add supplement contribution
        daily_avg += supplements.get(nutrient_key, 0.0)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_analyzer.py::TestAnalyzeSupplements -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_analyzer.py src/omni_pilot/analyzer.py
git commit -m "feat: add supplements into daily averages"
```

### Task 3: CLI Integration

**Files:**
- Modify: `src/omni_pilot/cli.py`

**Interfaces:**
- Consumes: `load_supplements()` from config, uses `DEFAULT_SUPPLEMENTS = "config/supplements.yaml"`
- Produces: passes loaded supplements to `analyze()`

- [ ] **Step 1: Write the minimal implementation**

Modify `src/omni_pilot/cli.py` imports:
```python
from omni_pilot.config import (
    load_settings,
    load_reference_ranges,
    load_food_mappings,
    load_supplements,
)
```

Add default path (near top):
```python
DEFAULT_SUPPLEMENTS = "config/supplements.yaml"
```

Update `cmd_analyze()`:
```python
def cmd_analyze(args: argparse.Namespace) -> None:
    # ...
    mappings_path = args.mappings or DEFAULT_MAPPINGS
    supplements_path = args.supplements or DEFAULT_SUPPLEMENTS
    db_path = args.db or DEFAULT_DB

    # Load config
    settings = load_settings(settings_path)
    ref_ranges = load_reference_ranges(ref_ranges_path)
    mappings = load_food_mappings(mappings_path)
    supplements = load_supplements(supplements_path)
    
    # ... (after Enriched section)
    # Analyze
    print("Analyzing micronutrient intake...")
    result = analyze(entries, enriched, ref_ranges, supplements=supplements)
```

Update `analyze_parser` in `main()`:
```python
    analyze_parser.add_argument("--supplements", default=None)
```

- [ ] **Step 2: Run Manual test to verify it passes**

Run: `uv run python -m omni_pilot analyze data/MacroFactor-example.xlsx`
Expected: Succeeds and produces a report without errors. 

- [ ] **Step 3: Commit**

```bash
git add src/omni_pilot/cli.py
git commit -m "feat: wire supplements into CLI"
```
