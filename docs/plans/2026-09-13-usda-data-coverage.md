# USDA Data Coverage Reporting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop `analyzer.py` from zero-filling nutrients USDA never measured, and report a per-nutrient data-coverage percentage (by consumed weight) with a `*` marker on `low`/`deficient` verdicts that rest on partial data.

**Architecture:** `analyze()` gains a per-entry, per-reference-nutrient-key accumulation loop that replaces today's "iterate the food's own keys" loop, tracking `measured_weight_g` / `unmeasured_weight_g` per nutrient alongside `daily_totals` — which becomes keyed by reference nutrient key, because combined nutrients are now summed per entry rather than at the averaging step. `NutrientResult` gains `coverage_pct` and `is_floor`, both computed in the analyzer so the rule lives in one place. `reporter.py` renders a rightmost "Data" column, appends `*` to floor verdicts, and prints a footnote, mirrored between the Rich terminal table and the Jinja2 HTML template.

**Tech Stack:** Python 3.14+, `pytest` (plain `assert`, plain classes, no `unittest`), `rich`, `jinja2`, `uv`.

**Spec:** `docs/specs/2026-09-13-usda-data-coverage-design.md` — read it alongside this plan. Each task below carries a **Verifies** line naming the spec section and the §6 test names it satisfies.

## Global Constraints

- Never zero-fill absent USDA data: a food with no value for a nutrient contributes nothing to it (decision 1).
- Applies to **all 35** reference nutrient keys, not only the 3 in `COMBINED_NUTRIENTS` (decision 2).
- For a combined nutrient, a food missing **any** component contributes nothing to that nutrient — no half-pair sums (decision 3).
- Coverage is measured by **consumed weight in grams**, never by food or entry count (decision 4).
- Coverage displays as a **percentage only** — no gram totals, no `x/y` counts (decision 5).
- Coverage aggregates over the **whole reporting period**, not per-day-then-averaged (decision 6).
- The verdict stands on the measured portion; only `low` and `deficient` below 100% coverage get the `*`. `ok`, `high` and `unknown` never do, because absent data can only raise a value (decision 7).
- A `skip`-mapped food is excluded from **both** the numerator and the denominator (decision 8).
- Evaluate the `*` against the **displayed rounded** percentage — a row must never show `100.0%` beside a `*` (decision 10).
- `STATUS_DISPLAY` and `NUTRIENT_CATEGORIES` in `reporter.py` are **not** modified. No fifth status, no new nutrient key.
- **Known interim deviation (BAR-42, spec §4.1):** `analyze()` cannot distinguish a deliberate `skip` mapping from a failed USDA lookup — `enrich_all_foods` returns `None` for both. This plan books both identically, excluded from coverage on both sides, matching today's `skipped_entries` behaviour. Do not try to fix that here; failed lookups reach the user through the existing `⚠ Unresolved foods:` warning line.
- Unit conversion is unchanged: `omega3_epa_mg` and `omega3_dha_mg` arrive from USDA in grams and are multiplied by `1000.0`.

## File Structure

Four files change; no files are created. The mechanism lives entirely in `analyzer.py`, and `reporter.py` is its only consumer — so the split below is by responsibility, not by layer.

| File | Responsibility after this change |
|---|---|
| `src/omni_pilot/analyzer.py` | Owns the coverage decision end to end: which weight is measured, which is not, the resulting percentage, and whether a verdict is a floor. Nothing downstream recomputes any of it. |
| `src/omni_pilot/reporter.py` | Pure presentation of the two new fields — a column, a `*`, a footnote, a summary clause — in both the terminal and HTML renderers. Contains no coverage logic. |
| `tests/test_analyzer.py` | Pins the accounting rules: unmeasured vs measured-zero, combined-component exclusion, weight weighting, skip exclusion, and every `is_floor` boundary. |
| `tests/test_reporter.py` | Pins the rendering only, driven by hand-built `AnalysisResult` fixtures, so a rendering failure can never be confused with an accounting failure. |

---

### Task 1: Analyzer — per-nutrient coverage accounting

**Files:**
- Modify: `src/omni_pilot/analyzer.py:13-21` (`NutrientResult`), `src/omni_pilot/analyzer.py:69-193` (`analyze`)
- Test: `tests/test_analyzer.py`

**Verifies:** spec §4.1 and §5; §6.1 tests `test_combined_nutrient_with_one_missing_component_contributes_nothing`, `test_combined_nutrient_with_both_components_sums_normally`, `test_measured_zero_counts_as_measured`, `test_coverage_is_weighted_by_consumed_weight`, `test_skipped_food_excluded_from_coverage_denominator`, `test_is_floor_set_only_for_low_and_deficient`, `test_is_floor_false_at_full_coverage`, `test_is_floor_uses_rounded_coverage`, `test_supplement_lifting_to_ok_clears_floor`, `test_coverage_none_when_no_resolved_entries`; §6.2's modified test; and spec §8's warning about a reference key with no `USDA_NUTRIENT_MAP` entry.

§6.1's eleventh test, `test_unmeasured_nutrient_is_not_zero_filled`, is not written separately: it asserts exactly what §6.2's renamed `test_unmeasured_nutrient_reports_zero_coverage` asserts on the same scenario (a resolved food with `None` for one nutrient), so Step 1 satisfies both rather than adding a duplicate under a second name.

**Interfaces:**
- Consumes: `analyze(entries, enriched, ref_ranges, supplements=None) -> AnalysisResult` — signature unchanged. `get_nutrient_target(nutrient_key, ref_ranges) -> tuple[float | None, float | None, str]` from `omni_pilot.config`, unchanged.
- Produces: every `result["nutrients"][key]` dict now carries two additional keys that Tasks 2 and 3 read directly — `coverage_pct` (a `float` rounded to one decimal, or `None` when no weight was attributable at all) and `is_floor` (a `bool`). `daily_totals` becomes an internal detail keyed by reference nutrient key; nothing outside `analyze()` reads it.

- [ ] **Step 1: Rewrite the test that currently pins the defect**

In `tests/test_analyzer.py`, inside `class TestAnalyze:`, replace `test_handles_missing_nutrient_in_enriched` with this renamed version (spec §6.2 — the `daily_avg` of `0.0` stays correct; what was missing is the disclosure):

```python
    def test_unmeasured_nutrient_reports_zero_coverage(self):
        entries = [self._make_entry("Eggs", "2026-07-13", 200.0)]
        enriched = {
            "Eggs": {"vitamin_a_mcg": 149.0, "calcium_mg": None},
        }
        ref_ranges = self._make_ref_ranges()
        result = analyze(entries, enriched, ref_ranges)

        vit_a = result["nutrients"]["vitamin_a_mcg"]
        assert vit_a["daily_avg"] == pytest.approx(298.0)
        assert vit_a["coverage_pct"] == 100.0
        # USDA never measured calcium for this food: it contributes nothing,
        # and the 200g is disclosed as unmeasured rather than as a measured zero.
        calcium = result["nutrients"]["calcium_mg"]
        assert calcium["daily_avg"] == pytest.approx(0.0)
        assert calcium["coverage_pct"] == 0.0
```

- [ ] **Step 2: Add the coverage-accounting tests**

Append these two classes to `tests/test_analyzer.py`. They build `entries`/`ref_ranges` inline rather than reusing `TestAnalyze`'s helpers, matching the style already used by `TestAnalyzeSupplements`, so each test states its own scenario:

```python
class TestCoverageAccounting:
    def test_combined_nutrient_with_one_missing_component_contributes_nothing(self):
        entries = [{"date": "2026-08-09", "food_name": "Olives", "total_weight_g": 400.0}]
        enriched = {"Olives": {"methionine_g": 0.35, "cysteine_g": None}}
        ref_ranges = {
            "demographic": {"body_weight_kg": 75},
            "nutrients": {
                "methionine_cysteine_g": {
                    "name": "Methionine + Cysteine", "unit": "g", "type": "who_per_kg",
                    "mg_per_kg": 15, "safe_mg_per_kg": 19, "ul": None,
                },
            },
        }
        result = analyze(entries, enriched, ref_ranges)
        combined = result["nutrients"]["methionine_cysteine_g"]
        # The methionine half is NOT added — a partial sum is an invented number.
        assert combined["daily_avg"] == pytest.approx(0.0)
        assert combined["coverage_pct"] == 0.0

    def test_combined_nutrient_with_both_components_sums_normally(self):
        entries = [{"date": "2026-08-09", "food_name": "Chicken", "total_weight_g": 200.0}]
        enriched = {"Chicken": {"methionine_g": 0.6, "cysteine_g": 0.3}}
        ref_ranges = {
            "demographic": {"body_weight_kg": 75},
            "nutrients": {
                "methionine_cysteine_g": {
                    "name": "Methionine + Cysteine", "unit": "g", "type": "who_per_kg",
                    "mg_per_kg": 15, "safe_mg_per_kg": 19, "ul": None,
                },
            },
        }
        result = analyze(entries, enriched, ref_ranges)
        combined = result["nutrients"]["methionine_cysteine_g"]
        # 200g * (0.6 + 0.3) / 100 = 1.8g
        assert combined["daily_avg"] == pytest.approx(1.8)
        assert combined["coverage_pct"] == 100.0

    def test_measured_zero_counts_as_measured(self):
        entries = [{"date": "2026-08-09", "food_name": "Egg White", "total_weight_g": 250.0}]
        enriched = {"Egg White": {"vitamin_k_mcg": 0.0}}
        ref_ranges = {
            "demographic": {},
            "nutrients": {
                "vitamin_k_mcg": {"name": "Vitamin K", "unit": "mcg", "type": "ai", "ai": 120, "ul": None},
            },
        }
        result = analyze(entries, enriched, ref_ranges)
        vit_k = result["nutrients"]["vitamin_k_mcg"]
        # 0.0 means "USDA measured it and found none" — that is data, not absence.
        assert vit_k["daily_avg"] == pytest.approx(0.0)
        assert vit_k["coverage_pct"] == 100.0

    def test_coverage_is_weighted_by_consumed_weight(self):
        entries = [
            {"date": "2026-08-09", "food_name": "Salmon", "total_weight_g": 300.0},
            {"date": "2026-08-09", "food_name": "Swiss Cheese", "total_weight_g": 100.0},
        ]
        enriched = {
            "Salmon": {"choline_mg": 60.0},
            "Swiss Cheese": {"choline_mg": None},
        }
        ref_ranges = {
            "demographic": {},
            "nutrients": {
                "choline_mg": {"name": "Choline", "unit": "mg", "type": "ai", "ai": 550, "ul": 3500},
            },
        }
        result = analyze(entries, enriched, ref_ranges)
        choline = result["nutrients"]["choline_mg"]
        # 300g of 400g consumed = 75%. Counting foods would say 50%.
        assert choline["coverage_pct"] == 75.0

    def test_skipped_food_excluded_from_coverage_denominator(self):
        entries = [
            {"date": "2026-08-09", "food_name": "Eggs", "total_weight_g": 200.0},
            {"date": "2026-08-09", "food_name": "Quick Add", "total_weight_g": 100.0},
        ]
        enriched = {
            "Eggs": {"vitamin_a_mcg": 149.0},
            "Quick Add": None,
        }
        ref_ranges = {
            "demographic": {},
            "nutrients": {
                "vitamin_a_mcg": {"name": "Vitamin A", "unit": "mcg", "type": "rda", "rda": 900, "ul": 3000},
            },
        }
        result = analyze(entries, enriched, ref_ranges)
        vit_a = result["nutrients"]["vitamin_a_mcg"]
        # A logged glass of water must not read as "unmeasured vitamin A".
        assert vit_a["coverage_pct"] == 100.0
        assert result["coverage"]["skipped_entries"] == 1

    def test_coverage_none_when_no_resolved_entries(self):
        entries = [{"date": "2026-08-09", "food_name": "Quick Add", "total_weight_g": 100.0}]
        enriched = {"Quick Add": None}
        ref_ranges = {
            "demographic": {},
            "nutrients": {
                "vitamin_a_mcg": {"name": "Vitamin A", "unit": "mcg", "type": "rda", "rda": 900, "ul": 3000},
            },
        }
        result = analyze(entries, enriched, ref_ranges)
        # No weight was attributable at all — distinct from "measured, found nothing".
        assert result["nutrients"]["vitamin_a_mcg"]["coverage_pct"] is None

    def test_reference_key_with_no_usda_mapping_reports_zero_coverage(self):
        # Guards spec §8: a reference key with no USDA_NUTRIENT_MAP entry must
        # surface as 0% coverage rather than silently reading as a measured 0.0.
        entries = [{"date": "2026-08-09", "food_name": "Mystery Food", "total_weight_g": 150.0}]
        enriched = {"Mystery Food": {"vitamin_a_mcg": 100.0}}
        ref_ranges = {
            "demographic": {},
            "nutrients": {
                "unobtainium_mg": {"name": "Unobtainium", "unit": "mg", "type": "ai", "ai": 10, "ul": None},
            },
        }
        result = analyze(entries, enriched, ref_ranges)
        unobtainium = result["nutrients"]["unobtainium_mg"]
        assert unobtainium["daily_avg"] == pytest.approx(0.0)
        assert unobtainium["coverage_pct"] == 0.0


class TestFloorMarking:
    @pytest.mark.parametrize(
        "nutrient_config,daily_value,expected_status",
        [
            ({"name": "Vitamin A", "unit": "mcg", "type": "rda", "rda": 900, "ul": 3000}, 900.0, "ok"),
            ({"name": "Vitamin A", "unit": "mcg", "type": "rda", "rda": 900, "ul": 3000}, 500.0, "low"),
            ({"name": "Vitamin A", "unit": "mcg", "type": "rda", "rda": 900, "ul": 3000}, 100.0, "deficient"),
            ({"name": "Vitamin A", "unit": "mcg", "type": "rda", "rda": 900, "ul": 3000}, 3500.0, "high"),
            (
                {
                    "name": "Histidine", "unit": "g", "type": "who_per_kg",
                    "mg_per_kg": 10, "safe_mg_per_kg": 12, "ul": None,
                },
                500.0,
                "unknown",
            ),
        ],
    )
    def test_is_floor_set_only_for_low_and_deficient(
        self, nutrient_config, daily_value, expected_status
    ):
        # The "unknown" row uses who_per_kg with no body_weight_kg in demographic,
        # which is how config.get_nutrient_target returns a None target.
        key = "vitamin_a_mcg" if nutrient_config["type"] == "rda" else "histidine_g"
        entries = [
            {"date": "2026-08-09", "food_name": "Measured Food", "total_weight_g": 80.0},
            {"date": "2026-08-09", "food_name": "Unmeasured Food", "total_weight_g": 20.0},
        ]
        enriched = {
            "Measured Food": {key: daily_value / 0.8},
            "Unmeasured Food": {key: None},
        }
        ref_ranges = {"demographic": {}, "nutrients": {key: nutrient_config}}

        result = analyze(entries, enriched, ref_ranges)
        nutrient = result["nutrients"][key]
        assert nutrient["status"] == expected_status
        assert nutrient["coverage_pct"] == 80.0
        assert nutrient["is_floor"] == (expected_status in ("low", "deficient"))

    def test_is_floor_false_at_full_coverage(self):
        entries = [{"date": "2026-08-09", "food_name": "Kale", "total_weight_g": 100.0}]
        enriched = {"Kale": {"vitamin_k_mcg": 80.0}}
        ref_ranges = {
            "demographic": {},
            "nutrients": {
                "vitamin_k_mcg": {"name": "Vitamin K", "unit": "mcg", "type": "ai", "ai": 120, "ul": None},
            },
        }
        result = analyze(entries, enriched, ref_ranges)
        vit_k = result["nutrients"]["vitamin_k_mcg"]
        assert vit_k["status"] == "low"
        assert vit_k["coverage_pct"] == 100.0
        assert vit_k["is_floor"] is False

    def test_is_floor_uses_rounded_coverage(self):
        # 999.6g measured of 1000.0g total = 99.96%, which displays as 100.0%.
        # A row must never show 100.0% beside a "*".
        entries = [
            {"date": "2026-08-09", "food_name": "Big Batch", "total_weight_g": 999.6},
            {"date": "2026-08-09", "food_name": "Tiny Unmeasured", "total_weight_g": 0.4},
        ]
        enriched = {
            "Big Batch": {"vitamin_e_mg": 1.0},
            "Tiny Unmeasured": {"vitamin_e_mg": None},
        }
        ref_ranges = {
            "demographic": {},
            "nutrients": {
                "vitamin_e_mg": {"name": "Vitamin E", "unit": "mg", "type": "rda", "rda": 15, "ul": 1000},
            },
        }
        result = analyze(entries, enriched, ref_ranges)
        vit_e = result["nutrients"]["vitamin_e_mg"]
        assert vit_e["status"] == "low"
        assert vit_e["coverage_pct"] == 100.0
        assert vit_e["is_floor"] is False

    def test_supplement_lifting_to_ok_clears_floor(self):
        entries = [
            {"date": "2026-08-09", "food_name": "Measured Food", "total_weight_g": 70.0},
            {"date": "2026-08-09", "food_name": "Unmeasured Food", "total_weight_g": 30.0},
        ]
        enriched = {
            "Measured Food": {"vitamin_d_mcg": 10.0 / 0.7},
            "Unmeasured Food": {"vitamin_d_mcg": None},
        }
        ref_ranges = {
            "demographic": {},
            "nutrients": {
                "vitamin_d_mcg": {"name": "Vitamin D", "unit": "mcg", "type": "rda", "rda": 15, "ul": 100},
            },
        }
        supplements = {"vitamin_d_mcg": 10.0}

        result = analyze(entries, enriched, ref_ranges, supplements=supplements)
        vit_d = result["nutrients"]["vitamin_d_mcg"]
        # 10.0 from food + 10.0 from the supplement clears the 15.0 target, so the
        # verdict becomes safe and the marker drops even at 70% coverage.
        assert vit_d["coverage_pct"] == 70.0
        assert vit_d["status"] == "ok"
        assert vit_d["is_floor"] is False
```

- [ ] **Step 3: Run the new tests to verify they fail**

Run: `uv run pytest tests/test_analyzer.py -v`
Expected: the 11 new tests and `test_unmeasured_nutrient_reports_zero_coverage` FAIL with `KeyError: 'coverage_pct'`, because the field does not exist yet. `test_daily_average_calculation`, `test_skipped_foods_excluded`, `test_multiple_foods_same_day`, `TestAnalyzeSupplements` and `TestDetermineStatus` still PASS untouched.

- [ ] **Step 4: Add the two new fields to `NutrientResult`**

In `src/omni_pilot/analyzer.py`, replace the `NutrientResult` class:

```python
class NutrientResult(TypedDict):
    name: str
    unit: str
    daily_avg: float
    target: float | None
    target_type: str
    ul: float | None
    status: str
    pct_of_target: float | None
    coverage_pct: float | None
    is_floor: bool
```

- [ ] **Step 5: Replace the `analyze()` function**

In `src/omni_pilot/analyzer.py`, replace the whole `analyze()` function with the version below. Two structural changes to note while reading it: the accumulation loop now iterates the **reference** nutrient keys (so a nutrient missing from a food's profile is handled instead of silently skipped), and `daily_totals` is therefore keyed by reference key, which removes the `.get(ck, 0.0)` at the averaging step.

```python
def analyze(
    entries: list[dict],
    enriched: dict[str, dict[str, float | None] | None],
    ref_ranges: dict,
    supplements: dict | None = None,
) -> AnalysisResult:
    """Run the full micronutrient analysis.

    1. For each food entry, scale USDA per-100g data by actual weight
    2. Sum per day
    3. Average across days
    4. Compare against reference ranges

    Coverage is tracked per reference nutrient key: a food with no USDA value
    for a nutrient — or, for a combined nutrient, missing any one component —
    contributes nothing to it, and its weight is booked as unmeasured rather
    than silently as 0.0.
    """
    total_entries = len(entries)
    mapped_entries = 0
    skipped_entries = 0
    unresolved_entries = 0
    skipped_food_names: set[str] = set()
    unresolved_food_names: set[str] = set()

    # Collect all nutrient keys from reference_ranges
    nutrient_keys = list(ref_ranges["nutrients"].keys())

    # Daily totals: date -> reference nutrient key -> total (components combined)
    daily_totals: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    # Reference nutrient key -> consumed grams that did / did not have a USDA value
    measured_weight_g: dict[str, float] = defaultdict(float)
    unmeasured_weight_g: dict[str, float] = defaultdict(float)
    dates: set[str] = set()

    for entry in entries:
        food_name = entry["food_name"]
        food_micros = enriched.get(food_name)

        if food_micros is None:
            # A deliberate "skip" mapping and a failed USDA lookup are the same
            # None here (BAR-42). Both are excluded from coverage on both sides.
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

        for nutrient_key in nutrient_keys:
            component_keys = COMBINED_NUTRIENTS.get(nutrient_key, [nutrient_key])
            component_values = [food_micros.get(ck) for ck in component_keys]

            if any(value is None for value in component_values):
                unmeasured_weight_g[nutrient_key] += total_weight_g
                continue

            measured_weight_g[nutrient_key] += total_weight_g
            # USDA provides EPA and DHA in grams, but our reference target is in mg
            contribution = sum(
                value * 1000.0 if ck in ("omega3_epa_mg", "omega3_dha_mg") else value
                for ck, value in zip(component_keys, component_values)
            )
            daily_totals[entry_date][nutrient_key] += contribution * scale_factor

    # Compute daily averages
    num_days = len(dates) if dates else 1
    sorted_dates = sorted(dates)

    if supplements is None:
        supplements = {}

    # Build nutrient results
    nutrients_result: dict[str, NutrientResult] = {}

    for nutrient_key in nutrient_keys:
        nutrient_info = ref_ranges["nutrients"][nutrient_key]
        target, ul, target_type = get_nutrient_target(nutrient_key, ref_ranges)

        total_across_days = sum(
            daily_totals[d].get(nutrient_key, 0.0) for d in sorted_dates
        )
        daily_avg = total_across_days / num_days if num_days > 0 else 0.0

        # Add supplement contribution. Supplements carry no weight, so they do
        # not participate in coverage.
        daily_avg += supplements.get(nutrient_key, 0.0)

        measured = measured_weight_g.get(nutrient_key, 0.0)
        unmeasured = unmeasured_weight_g.get(nutrient_key, 0.0)
        attributable = measured + unmeasured
        # Rounded once, here: the "*" rule and the displayed figure must agree.
        coverage_pct = (
            round(100.0 * measured / attributable, 1) if attributable > 0 else None
        )

        # Determine status
        if target is not None:
            status = determine_status(daily_avg, target, ul)
            pct = (daily_avg / target * 100) if target > 0 else None
        else:
            status = "unknown"
            pct = None

        # Absent data can only raise a value, so only low/deficient are unsafe.
        is_floor = (
            coverage_pct is not None
            and coverage_pct < 100.0
            and status in ("low", "deficient")
        )

        nutrients_result[nutrient_key] = NutrientResult(
            name=nutrient_info["name"],
            unit=nutrient_info["unit"],
            daily_avg=round(daily_avg, 2),
            target=target,
            target_type=target_type,
            ul=ul,
            status=status,
            pct_of_target=round(pct, 1) if pct is not None else None,
            coverage_pct=coverage_pct,
            is_floor=is_floor,
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

- [ ] **Step 6: Run the analyzer tests to verify they pass**

Run: `uv run pytest tests/test_analyzer.py -v`
Expected: every test PASSES, including the pre-existing ones with their assertions unchanged.

- [ ] **Step 7: Commit**

```bash
git add src/omni_pilot/analyzer.py tests/test_analyzer.py
git commit -m "feat: track per-nutrient USDA data coverage instead of zero-filling (BAR-41)"
```

---

### Task 2: Reporter — terminal Data column, floor marker, footnote

**Files:**
- Modify: `src/omni_pilot/reporter.py:44-58` (`_summary_line`), `src/omni_pilot/reporter.py:94-146` (table loop and footnote in `print_terminal_report`)
- Test: `tests/test_reporter.py`

**Verifies:** spec §4.2; §6.3 bullets 1–3 (the `Data` header and a percentage appear, `*` renders adjacent to the status label, the footnote appears when a row is a floor and is absent when none is).

**Interfaces:**
- Consumes: `nutrients[key]["coverage_pct"]` (`float` or `None`) and `nutrients[key]["is_floor"]` (`bool`) from Task 1.
- Produces: `_make_analysis_result()` and `_make_analysis_result_without_floor()` in `tests/test_reporter.py`, both returning an `AnalysisResult`-shaped dict whose five nutrients all carry `coverage_pct` and `is_floor`. Task 3 reuses both.

- [ ] **Step 1: Add the two new fields to the reporter test fixture**

In `tests/test_reporter.py`, replace `_make_analysis_result()` and add the floor-free variant below it. The `coverage_pct` values mirror spec §2.4, and `vitamin_d_mcg` is the floor row because it is already `deficient` in this fixture:

```python
def _make_analysis_result() -> dict:
    return {
        "period": {"start": "2026-07-13", "end": "2026-08-09", "days": 21},
        "nutrients": {
            "vitamin_a_mcg": {
                "name": "Vitamin A", "unit": "mcg",
                "daily_avg": 412.3, "target": 900.0, "target_type": "rda",
                "ul": 3000.0, "status": "low", "pct_of_target": 45.8,
                "coverage_pct": 100.0, "is_floor": False,
            },
            "vitamin_c_mg": {
                "name": "Vitamin C", "unit": "mg",
                "daily_avg": 92.1, "target": 90.0, "target_type": "rda",
                "ul": 2000.0, "status": "ok", "pct_of_target": 102.3,
                "coverage_pct": 100.0, "is_floor": False,
            },
            "vitamin_d_mcg": {
                "name": "Vitamin D", "unit": "mcg",
                "daily_avg": 4.2, "target": 15.0, "target_type": "rda",
                "ul": 100.0, "status": "deficient", "pct_of_target": 28.0,
                "coverage_pct": 68.7, "is_floor": True,
            },
            "calcium_mg": {
                "name": "Calcium", "unit": "mg",
                "daily_avg": 1102.0, "target": 1000.0, "target_type": "rda",
                "ul": 2500.0, "status": "ok", "pct_of_target": 110.2,
                "coverage_pct": 100.0, "is_floor": False,
            },
            "sodium_mg": {
                "name": "Sodium", "unit": "mg",
                "daily_avg": 3500.0, "target": 1500.0, "target_type": "ai",
                "ul": 2300.0, "status": "high", "pct_of_target": 233.3,
                "coverage_pct": 100.0, "is_floor": False,
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


def _make_analysis_result_without_floor() -> dict:
    result = _make_analysis_result()
    result["nutrients"]["vitamin_d_mcg"]["is_floor"] = False
    return result
```

- [ ] **Step 2: Add the terminal-rendering tests**

Append to `class TestTerminalReport:` in `tests/test_reporter.py`. The `COLUMNS` env var is set because `print_terminal_report` builds its own `Console()`, which falls back to an 80-column width when stdout is not a terminal — at that width Rich shrinks the six columns and can truncate `Deficient*` or the percentage, which would fail the assertions for layout reasons rather than logic ones:

```python
    def test_shows_data_column_and_floor_marker(self, capsys, monkeypatch):
        monkeypatch.setenv("COLUMNS", "200")
        settings = {"output": {"show_amino_acids": True, "show_ok_nutrients": True}}
        result = _make_analysis_result()
        print_terminal_report(result, settings)
        captured = capsys.readouterr()
        assert "Data" in captured.out
        assert "68.7%" in captured.out
        assert "Deficient*" in captured.out
        # The footnote is the legend for that "*" — a marker with no legend is the bug.
        assert captured.out.count("computed from partial USDA data") == 1

    def test_footnote_absent_when_no_floor(self, capsys, monkeypatch):
        monkeypatch.setenv("COLUMNS", "200")
        settings = {"output": {"show_amino_acids": True, "show_ok_nutrients": True}}
        result = _make_analysis_result_without_floor()
        print_terminal_report(result, settings)
        captured = capsys.readouterr()
        assert "computed from partial USDA data" not in captured.out
        assert "Deficient*" not in captured.out

    def test_summary_line_reports_partial_data_count(self, capsys, monkeypatch):
        monkeypatch.setenv("COLUMNS", "200")
        settings = {"output": {"show_amino_acids": True, "show_ok_nutrients": True}}
        result = _make_analysis_result()
        print_terminal_report(result, settings)
        captured = capsys.readouterr()
        assert "1 from partial data" in captured.out
```

- [ ] **Step 3: Run the new tests to verify they fail**

Run: `uv run pytest tests/test_reporter.py -v`
Expected: the three new tests FAIL — no `Data` column, no `68.7%`, no `*`, no footnote in today's output. `test_prints_without_error` and `test_hides_ok_nutrients_when_configured` still PASS.

- [ ] **Step 4: Add the partial-data clause to `_summary_line`**

In `src/omni_pilot/reporter.py`, replace `_summary_line`:

```python
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
    floor_count = sum(1 for n in nutrients.values() if n["is_floor"])
    if floor_count:
        parts.append(f"{floor_count} from partial data")
    return " · ".join(parts)
```

- [ ] **Step 5: Add the column, the marker and the footnote to the terminal report**

In `src/omni_pilot/reporter.py`, replace everything in `print_terminal_report` from the `# Nutrient tables by category` comment down to (but not including) the `# Warnings` comment:

```python
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
        table.add_column("Data", justify="right", min_width=7)

        for key, n in category_nutrients:
            emoji, label, color = STATUS_DISPLAY.get(
                n["status"], ("⚪", "Unknown", "dim")
            )
            if n["is_floor"]:
                label = f"{label}*"
            target_str = (
                f"{n['target']:.1f}" if n["target"] is not None else "—"
            )
            coverage_str = (
                f"{n['coverage_pct']:.1f}%" if n["coverage_pct"] is not None else "—"
            )
            status_text = Text(f"{emoji} {label}", style=color)
            table.add_row(
                n["name"],
                n["unit"],
                f"{n['daily_avg']:.1f}",
                target_str,
                status_text,
                coverage_str,
            )

        console.print(table)
        console.print()

    if any(n["is_floor"] for n in nutrients.values()):
        console.print(
            "  * computed from partial USDA data — the true value can only be higher",
            style="dim",
        )
        console.print()
```

- [ ] **Step 6: Run the reporter tests to verify they pass**

Run: `uv run pytest tests/test_reporter.py -v`
Expected: every `TestTerminalReport` test PASSES. `TestHtmlReport::test_generates_valid_html_file` still PASSES.

- [ ] **Step 7: Commit**

```bash
git add src/omni_pilot/reporter.py tests/test_reporter.py
git commit -m "feat: show USDA data coverage and floor markers in the terminal report (BAR-41)"
```

---

### Task 3: Reporter — HTML Data column, floor marker, footnote

**Files:**
- Modify: `src/omni_pilot/reporter.py:149-223` (`HTML_TEMPLATE`), `src/omni_pilot/reporter.py:226-257` (`generate_html_report`)
- Test: `tests/test_reporter.py`

**Verifies:** spec §4.3; §6.3 bullet 4 (the HTML report contains the column, the marker and the footnote block).

**Interfaces:**
- Consumes: `nutrients[key]["coverage_pct"]`, `nutrients[key]["is_floor"]` from Task 1; `_summary_line` and `_coverage_line` from `reporter.py`, unchanged in signature; `_make_analysis_result()` and `_make_analysis_result_without_floor()` from Task 2.
- Produces: nothing new. `generate_html_report(result, output_path) -> None` keeps its signature.

- [ ] **Step 1: Add the HTML-rendering tests**

Append to `class TestHtmlReport:` in `tests/test_reporter.py`:

```python
    def test_html_report_shows_coverage_and_floor_marker(self, tmp_path):
        result = _make_analysis_result()
        output_path = str(tmp_path / "report.html")
        generate_html_report(result, output_path)
        with open(output_path) as f:
            html = f.read()
        assert "<th>Data</th>" in html
        assert "68.7%" in html
        assert "Deficient*" in html
        assert html.count("computed from partial USDA data") == 1

    def test_html_report_omits_footnote_when_no_floor(self, tmp_path):
        result = _make_analysis_result_without_floor()
        output_path = str(tmp_path / "report.html")
        generate_html_report(result, output_path)
        with open(output_path) as f:
            html = f.read()
        assert "computed from partial USDA data" not in html
        assert "Deficient*" not in html
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `uv run pytest tests/test_reporter.py -v`
Expected: both new tests FAIL — the rendered HTML has no `<th>Data</th>`, no percentage cell, no `*`, no footnote.

- [ ] **Step 3: Replace `HTML_TEMPLATE`**

In `src/omni_pilot/reporter.py`, replace `HTML_TEMPLATE` with the version below. Note a deliberate deviation from spec §4.3: the spec proposes a `.coverage` class for the new cells, but `.coverage` is already taken by the centred coverage-line `<div>` above the tables (`text-align: center; margin-bottom: 2rem`), and reusing it would push every table cell around. The new cells use `.data-col` instead:

```python
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
        .data-col { text-align: right; color: #8892b0; }
        .footnote { margin-top: 1rem; color: #8892b0; font-size: 0.85rem; }
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
            <thead><tr><th>Nutrient</th><th>Unit</th><th>Daily Avg</th><th>Target</th><th>Status</th><th>Data</th></tr></thead>
            <tbody>
            {% for n in category_nutrients %}
            <tr>
                <td>{{ n.name }}</td>
                <td>{{ n.unit }}</td>
                <td>{{ "%.1f"|format(n.daily_avg) }}</td>
                <td>{{ "%.1f"|format(n.target) if n.target is not none else "—" }}</td>
                <td class="status-{{ n.status }}">{{ n.status_label }}</td>
                <td class="data-col">{% if n.coverage_pct is not none %}{{ "%.1f"|format(n.coverage_pct) }}%{% else %}—{% endif %}</td>
            </tr>
            {% endfor %}
            </tbody>
        </table>
    </div>
    {% endif %}
    {% endfor %}
    {% if has_floor %}
    <p class="footnote">* computed from partial USDA data — the true value can only be higher</p>
    {% endif %}
    {% if skipped_foods or unresolved_foods %}
    <div class="warnings">
        {% if skipped_foods %}<p>⚠ Skipped: {{ skipped_foods|join(", ") }}</p>{% endif %}
        {% if unresolved_foods %}<p>⚠ Unresolved: {{ unresolved_foods|join(", ") }}</p>{% endif %}
    </div>
    {% endif %}
</body>
</html>
"""
```

- [ ] **Step 4: Replace `generate_html_report`**

In `src/omni_pilot/reporter.py`:

```python
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
                if n["is_floor"]:
                    label = f"{label}*"
                n["status_label"] = f"{emoji} {label}"
                cat_nutrients.append(n)
        categories.append((category, cat_nutrients))

    template = Template(HTML_TEMPLATE)
    html = template.render(
        period=result["period"],
        summary=_summary_line(nutrients),
        coverage_line=_coverage_line(coverage),
        categories=categories,
        has_floor=any(n["is_floor"] for n in nutrients.values()),
        skipped_foods=coverage["skipped_foods"],
        unresolved_foods=coverage["unresolved_foods"],
    )

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "w") as f:
        f.write(html)
```

- [ ] **Step 5: Run the reporter tests to verify they pass**

Run: `uv run pytest tests/test_reporter.py -v`
Expected: every test in the file PASSES.

- [ ] **Step 6: Commit**

```bash
git add src/omni_pilot/reporter.py tests/test_reporter.py
git commit -m "feat: mirror USDA data coverage and floor markers in the HTML report (BAR-41)"
```

---

### Task 4: Whole-suite, lint, and real-data verification

**Files:** none — verification only.

**Verifies:** spec §6.4.

**Interfaces:**
- Consumes: the finished `analyze()` and `reporter.py` from Tasks 1–3, plus a real MacroFactor export and a working USDA API key in `config/settings.yaml`.
- Produces: nothing. The output of this task is the developer's confirmation.

- [ ] **Step 1: Run the whole suite**

Run: `make test`
Expected: every test PASSES, including `tests/test_parser.py`, `tests/test_translator.py`, `tests/test_enricher.py` and `tests/test_config.py`, which this change does not touch.

- [ ] **Step 2: Lint**

Run: `make lint`
Expected: `All checks passed!` — no `ruff` findings.

- [ ] **Step 3: Run the real pipeline**

Run: `make analyze FILE=data/MacroFactor-20260817231558.xlsx`

Substitute whichever export exists locally. Note the flag: the `analyze` target already appends `--html` itself, so passing `--html` on the make command line (as spec §6.4 writes it) would be read as a make goal and fail.

- [ ] **Step 4: Check the output against spec §2.4**

MANUAL: read the terminal report and open `reports/latest.html`, and confirm four things:

1. The `Data` column varies per nutrient — roughly 58%–100% on the spec's data — rather than reading a uniform `100.0%` or `—` for every row. A uniform column means the accumulation loop is booking every food the same way.
2. `*` appears only on `low`/`deficient` rows whose coverage is below 100%, and never on an `ok`, `high` or `unknown` row.
3. The footnote `* computed from partial USDA data — the true value can only be higher` appears exactly once, and the summary line ends with a `… · N from partial data` clause whose count equals the number of starred rows.
4. `methionine_cysteine_g` reads `ok` at a coverage well under 100%. This is the case BAR-41 was filed about: the number is a floor, and the honest disclosure is the coverage figure, not a false `deficient` verdict.

The exact figures in spec §2.4 (58.6%–100.0%, six starred rows, `methionine_cysteine_g` at 4.07 g / `ok` / 65.1%) were measured against one export and one TinyDB cache state. If the local numbers differ because the cache or the export has changed, that is expected — what must hold is the shape above.

- [ ] **Step 5: Commit nothing, report findings**

This task makes no code changes. If step 4 surfaces a discrepancy, that is a defect in Tasks 1–3 rather than something to patch here — go back to the failing task, add a test that reproduces it, and fix it there.
