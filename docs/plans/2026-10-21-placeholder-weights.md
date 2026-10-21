# Estimated Weights for Placeholder Servings: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Entries logged with a placeholder 100 g weight and impossible macros get matched by macro proportions, and their grams are fitted to the match before analysis. The report lists them.

**Architecture:** The parser flags placeholder entries. `matcher.py` gains pure functions:
- the gram fit;
- proportion-based scoring in `pick_best`;
- splitting logged macros into per-100 g foods and weight-unknown foods;
- `estimate_weights`, which rewrites the entries' `total_weight_g` after enrichment.

The enricher passes the weight-unknown totals through, records `matched_by` on cache entries (and re-picks when it changes), and exposes each food's matched macros. The analyzer and reporter read the new entry key and coverage fields.

**Tech Stack:** Python 3, TinyDB, pytest (with pytest-mock), Rich, Jinja2.

**Spec:** `docs/specs/2026-10-21-placeholder-weights-design.md`. This plan covers §1–7. §8, the skill, is a separate change in `alperozaydin/my-skills` that starts after this one is merged.

## Global Constraints

- **Placeholder rule:** `serving_weight_g == 100` and `protein_g + fat_g + carbs_g > 1.05 × total_weight_g`.
- **Fit:** `grams = 100 × Σ w²·L·U / Σ w²·U²` with `w = (4, 9, 4)` for (protein, fat, carbs). The result is never below the logged protein + fat + carbs. It is `None` when any of U's protein, fat or carbs is `None`, or when `Σ w²·U² == 0`.
- **Fitted distance:** the existing `macro_distance`, with the logged macros (kcal included) scaled to per 100 g at the fitted grams.
- **`GOOD_FIT_DISTANCE = 0.15`**, used for weight-unknown foods. `GOOD_DISTANCE` (0.25) is unchanged for every other food.
- **`MATCH_VERSION` stays 1.** The cache field `matched_by` is `"per_100g"` or `"proportions"`, and a missing field counts as `"per_100g"`. It is always written on a pick, because `upsert` merges fields. Not-found records never get it.
- **Unchanged shapes:** `logged_macros_per_100g`, `LoggedMacros`, `macro_distance` and `GOOD_DISTANCE` keep their exact signatures and behaviour, because `.claude/skills/omni-pilot-custom-foods/scripts/recipe_tool.py` imports them.
- **Estimated grams are never stored:** not in TinyDB, S3 or the export.
- **No live USDA or Gemini calls in tests.**

## Review Focus

1. **A food with both real-weight and placeholder entries.** Its per-100 g figures come only from the real entries, and its placeholder entries are fitted against the resulting match. The test goes in Task 2 (`test_mixed_food_uses_only_real_weight_entries`).
2. **A cached proportions pick for a food whose entries now have real weights**, for example after you fix the weight in MacroFactor. It is re-picked by per-100 g figures, and the new pick overwrites `matched_by`. The test goes in Task 4 (`test_proportions_entry_is_repicked_when_weight_becomes_known`).
3. **A re-pick that fails after `matched_by` changed.** The old entry is kept as `unverified` and still carries `usda_macros`, so the fit still works. The test goes in Task 4 (`test_failed_mode_repick_keeps_entry_with_macros`).
4. **A placeholder entry of a skipped food (Quick Add).** It is never listed in the report and never fitted. The test goes in Task 5 (`test_skipped_placeholder_is_not_listed`).
5. **A fit that lands below the macro mass.** A wrong match must not produce fewer grams than the protein + fat + carbs. The test goes in Task 2 (`test_fit_never_below_macro_mass`).

---

### Task 1: Flag placeholder entries in the parser

**Files:**
- Modify: `src/omni_pilot/parser.py`: `FoodEntry`, the constants block, `parse_food_log`
- Modify: `tests/helpers.py`: `food_entry` gets `weight_is_placeholder: bool = False`
- Test: `tests/test_parser.py`

**Interfaces:**
- Produces:
  - `PLACEHOLDER_SERVING_G = 100.0`
  - `PLACEHOLDER_MACRO_MARGIN = 1.05`
  - `is_placeholder_weight(serving_weight_g: float, total_weight_g: float, protein_g: float, fat_g: float, carbs_g: float) -> bool`
  - `FoodEntry.weight_is_placeholder: bool`

- [ ] **Step 1: Write the failing tests** in a new `class TestPlaceholderWeight`:

```python
@pytest.mark.parametrize("serving_g, total_g, p, f, c, expected", [
    (100.0, 100.0, 53, 56, 54, True),     # King's Steakhouse: 163 g of macros in 100 g
    (100.0, 110.0, 55, 39, 99, True),     # Custom Chicken Doner: 1.1 x 100 g
    (100.0, 100.0, 7, 0.4, 80.6, False),  # Basmatireis: real 100 g serving, 88 g of macros
    (100.0, 100.0, 25, 22, 34, False),    # Double Cheeseburger: 81 g, possible
    (100.0, 100.0, 50, 4, 50, False),     # exactly 104 g: inside the 5% margin
    (5.0, 5.0, 0, 5, 0, False),           # 5 g of oil: not a 100 g serving
])
def test_rule(self, serving_g, total_g, p, f, c, expected):
    assert is_placeholder_weight(serving_g, total_g, p, f, c) is expected

def test_parse_food_log_sets_the_flag(self, tmp_path):
    # Two rows built in the real column layout (food_row's layout, with
    # qty at index 4, weight at 5, kcal 6, fat 7, carbs 8, protein 9):
    # one 1 x 100 g with 930 kcal, F56, C54, P53, and one ordinary row.
    # Assert the first entry's weight_is_placeholder is True, the second's False.
```

- [ ] **Step 2: Run them to check they fail.** Run `uv run pytest tests/test_parser.py -k Placeholder -v`. Expected: FAIL with an ImportError for `is_placeholder_weight`.
- [ ] **Step 3: Implement.** Add `is_placeholder_weight` and the two constants (with a short comment on why: AI photo logging). Set `weight_is_placeholder` in `parse_food_log`, and add the field to `FoodEntry` with a comment.
- [ ] **Step 4: Run all tests.** Run `uv run pytest -q`. Expected: all pass.
- [ ] **Step 5: Commit** with the message `feat: flag servings logged as 100 g with impossible macros`.

---

### Task 2: Fit grams and match weight-unknown foods by proportions

**Files:**
- Modify: `src/omni_pilot/matcher.py`
- Test: `tests/test_matcher.py`

**Interfaces:**
- Consumes: `entry["weight_is_placeholder"]` (read with `.get(..., False)`, as the analyzer reads `fiber_g`).
- Produces:
  - `GOOD_FIT_DISTANCE = 0.15`
  - `class LoggedTotals(TypedDict)`: `kcal`, `protein_g`, `fat_g`, `carbs_g`, all floats. Summed over a food's placeholder entries; not per 100 g.
  - `class LoggedFoods(TypedDict)`: `per_100g: dict[str, LoggedMacros]`, `weight_unknown: dict[str, LoggedTotals]`. A food is in at most one of the two.
  - `fit_grams(logged: LoggedTotals, macros: UsdaMacros) -> float | None`
  - `fitted_distance(logged: LoggedTotals, macros: UsdaMacros) -> float | None`
  - `logged_macros_for_matching(entries: list[dict]) -> LoggedFoods`
  - `pick_best(candidates, query, logged: LoggedMacros | None, totals: LoggedTotals | None = None) -> Pick | None`

- [ ] **Step 1: Write the failing tests.**

```python
KINGS = LoggedTotals(kcal=930.0, protein_g=53.0, fat_g=56.0, carbs_g=54.0)
DOUBLE_CB = UsdaMacros(protein_g=16.2, fat_g=16.2, carbs_g=18.0, fiber_g=0.9)

def test_fit_grams_kings_against_double_cheeseburger():
    assert fit_grams(KINGS, DOUBLE_CB) == pytest.approx(335, abs=1)

def test_fit_never_below_macro_mass():
    doner = LoggedTotals(kcal=935.0, protein_g=55.0, fat_g=39.0, carbs_g=99.0)
    skin = UsdaMacros(protein_g=20.4, fat_g=40.7, carbs_g=0.0, fiber_g=0.0)
    assert fit_grams(doner, skin) == pytest.approx(193.0)

def test_fit_needs_every_macro():
    assert fit_grams(KINGS, UsdaMacros(protein_g=None, fat_g=1.0, carbs_g=1.0, fiber_g=None)) is None
    assert fit_grams(KINGS, UsdaMacros(protein_g=0.0, fat_g=0.0, carbs_g=0.0, fiber_g=None)) is None

def test_fitted_distance_scores_at_the_fitted_grams():
    assert fitted_distance(KINGS, DOUBLE_CB) == pytest.approx(0.04, abs=0.01)

def test_weight_unknown_food_picks_by_proportions():
    # Two candidates that both pass the head filter for "cheeseburger": one
    # with the double cheeseburger's proportions (16.2/16.2/18.0), and one
    # denser (e.g. 30/40/40) that fits KINGS's impossible per-100 g figures
    # better. With totals=KINGS, the first is picked, confidence "good".

def test_fitted_pick_is_weak_above_good_fit_distance():
    # Ben's rice totals (P18, F13, C113, 660 kcal) against raw rice
    # (7.13/0.66/80.0, fiber 1.3): distance about 0.22, so "weak".

def test_known_weight_scoring_is_unchanged():
    # Same candidates and per-100 g LoggedMacros as an existing pick_best test;
    # with totals=None, the result equals the result without the parameter.

def test_mixed_food_uses_only_real_weight_entries():
    # Food "X": one real 200 g entry (P20 F10 C30, 290 kcal) and one
    # placeholder 100 g entry (P60 F50 C120). logged_macros_for_matching gives
    # per_100g["X"] from the 200 g entry alone (P10 F5 C15), and X is not in
    # weight_unknown.

def test_all_placeholder_food_gets_summed_totals():
    # Two placeholder entries of "King's" (930 kcal, P53 F56 C54 each)
    # -> weight_unknown["King's"] == LoggedTotals(1860, 106, 112, 108), and
    # it is not in per_100g.

def test_logged_macros_per_100g_unchanged_for_placeholders():
    # The skill relies on it: a placeholder entry still counts at its logged
    # 100 g (P53 per 100 g for King's).
```

- [ ] **Step 2: Run them to check they fail.** Run `uv run pytest tests/test_matcher.py -v`. Expected: the new tests FAIL with ImportErrors.
- [ ] **Step 3: Implement** in `matcher.py`:
  - `fit_grams` follows the Global Constraints formula and floor.
  - `fitted_distance` calls `fit_grams`, scales `logged` to per 100 g at those grams, and returns `macro_distance(...)`. It returns `None` when there is no fit.
  - `logged_macros_for_matching` calls `logged_macros_per_100g` on the non-placeholder entries for `per_100g`, then sums the placeholder entries of every food not in `per_100g` into `weight_unknown`.
  - In `pick_best`: when `totals` is given, score each candidate with `fitted_distance(totals, candidate)` instead of `macro_distance(logged, candidate)`, and judge confidence against `GOOD_FIT_DISTANCE`. Treat `totals` like a present `logged` in the `logged is None` branch.
- [ ] **Step 4: Run all tests.** Run `uv run pytest -q`. Expected: all pass.
- [ ] **Step 5: Commit** with the message `feat: match foods with a placeholder weight by macro proportions`.

---

### Task 3: Estimate each placeholder entry's grams

**Files:**
- Modify: `src/omni_pilot/matcher.py`
- Test: `tests/test_matcher.py`

**Interfaces:**
- Consumes: `fit_grams` and `fitted_distance` from Task 2.
- Produces:
  - `class WeightEstimate(TypedDict)`: `logged_g: float`, `estimated_g: float | None` (`None` means no fit), `macro_distance: float | None`.
  - `estimate_weights(entries: list[dict], matched_macros: dict[str, UsdaMacros]) -> list[dict]`. It returns new entry dicts and never mutates its input. Every placeholder entry gets the key `"weight_estimate": WeightEstimate`. When `estimated_g` is not `None`, `total_weight_g` becomes `estimated_g`. Other entries are returned unchanged.

- [ ] **Step 1: Write the failing tests:**
  - `test_estimates_placeholder_entry_against_its_match`: the King's entry with `{"King's": DOUBLE_CB}` gives `total_weight_g ≈ 335`, `weight_estimate == {"logged_g": 100.0, "estimated_g": ≈335, "macro_distance": ≈0.04}`.
  - `test_each_serving_gets_its_own_grams`: two King's entries, one at 930 kcal (P53 F56 C54) and one at 465 kcal (half the macros), come out at ≈335 and ≈168.
  - `test_no_match_keeps_logged_weight`: a placeholder entry whose food is not in `matched_macros` keeps `total_weight_g == 100` and gets `estimated_g is None`, `macro_distance is None`.
  - `test_real_weight_entries_untouched`: a non-placeholder entry comes back equal to the input, with no `weight_estimate` key.
  - `test_input_not_mutated`.
- [ ] **Step 2: Run them to check they fail.** Run `uv run pytest tests/test_matcher.py -k estimate -v`. Expected: FAIL with an ImportError.
- [ ] **Step 3: Implement `estimate_weights`.** Each entry is fitted on its own macros (a `LoggedTotals` built from that entry). Add a docstring saying the grams are recomputed every run and never stored.
- [ ] **Step 4: Run all tests.** Run `uv run pytest -q`. Expected: all pass.
- [ ] **Step 5: Commit** with the message `feat: estimate the grams of placeholder entries from their match`.

---

### Task 4: Enricher: proportion picks, the `matched_by` cache field, matched macros

**Files:**
- Modify: `src/omni_pilot/enricher.py`
- Modify: `tests/helpers.py`: `enrichment()` gets `matched_macros: dict | None = None`
- Test: `tests/test_enricher.py`, `tests/test_custom_foods_integration.py` (if it builds `EnrichmentResult` by hand)

**Interfaces:**
- Consumes: `LoggedTotals`, `fitted_distance` and `pick_best(..., totals=)` from Task 2.
- Produces:
  - `FoodMatch.usda_macros: UsdaMacros | None`, from the pick, or `entry.get("usda_macros")` from the cache.
  - `CustomFoodMatch.weight_unknown: bool`: its `macro_distance` was fitted.
  - `EnrichmentResult.matched_macros: dict[str, UsdaMacros]`: per resolved food, the USDA match's macros (when present) or the recipe's `recipe_macros`.
  - `get_food_match(food_name, usda_query, logged, db, api_key, totals: LoggedTotals | None = None)`
  - `count_outdated_matches(food_names, mappings, db, weight_unknown: Collection[str] = frozenset()) -> int`
  - `enrich_all_foods(..., untranslated=frozenset(), weight_unknown: dict[str, LoggedTotals] | None = None)`
  - Cache field `matched_by`: `"proportions"` when `totals` is given, else `"per_100g"`. It is always written on a pick.

- [ ] **Step 1: Write the failing tests.** Use the existing fake search pattern in `tests/test_enricher.py`, with no network.
  - `test_pick_with_totals_caches_matched_by_proportions`
  - `test_pick_without_totals_caches_matched_by_per_100g`
  - `test_entry_without_matched_by_is_reused_for_known_weight`: a legacy entry is reused, and the search is not called.
  - `test_per_100g_entry_is_repicked_when_weight_unknown`
  - `test_proportions_entry_is_repicked_when_weight_becomes_known`
  - `test_matching_matched_by_is_reused`
  - `test_failed_mode_repick_keeps_entry_with_macros`: the search fails; the result has confidence `"unverified"` and the old `usda_macros`.
  - `test_mode_repick_keeps_reviewed_fdc_id`
  - `test_count_outdated_counts_mode_mismatch`
  - `test_matched_macros_for_usda_and_custom_foods`: a USDA food gets its candidate's macros, and a custom food gets `recipe_macros` of its parts.
  - `test_custom_food_with_unknown_weight_gets_fitted_distance`: `weight_unknown=True` and the distance equals `fitted_distance(totals, recipe_macros(...))`.
- [ ] **Step 2: Run them to check they fail.** Run `uv run pytest tests/test_enricher.py -v`. Expected: the new tests FAIL.
- [ ] **Step 3: Implement.**
  - In `get_food_match`, reuse an entry only when `entry.get("matched_by", "per_100g")` equals this run's mode, as well as the existing query and version checks. A mismatch makes it a `stale_entry` (the existing re-pick path).
  - Pass `totals` to `pick_best`.
  - Write `matched_by` and return `usda_macros` in `FoodMatch`; `_match_from_entry` reads `entry.get("usda_macros")`.
  - In `_custom_food_match`, take `totals`. When it is given, compute the distance with `fitted_distance` and set `weight_unknown=True`.
  - In `enrich_all_foods`, fill `matched_macros`, skipping USDA foods whose `usda_macros` is `None`.
  - Update the `get_food_match` docstring and the `EnrichmentResult` field comments.
- [ ] **Step 4: Run all tests.** Run `uv run pytest -q`. Expected: all pass, including `test_repick_keeps_fields_the_app_does_not_write`.
- [ ] **Step 5: Commit** with the message `feat: pick weight-unknown foods by proportions and record how each pick was made`.

---

### Task 5: Analyzer: estimated-weight rows, low-confidence calorie share, fitted custom foods

**Files:**
- Modify: `src/omni_pilot/analyzer.py`
- Test: `tests/test_analyzer.py`

**Interfaces:**
- Consumes: `entry["weight_estimate"]` (Task 3) and `CustomFoodMatch.weight_unknown` (Task 4).
- Produces:
  - `class EstimatedWeightFood(TypedDict)`: `name: str`, `estimates: list[WeightEstimate]` (in log order).
  - `CoverageResult.estimated_weights: list[EstimatedWeightFood]`: foods that are not skipped. They are sorted by total grams, descending (`estimated_g`, or `logged_g` when there is no fit), then by name.
  - `CoverageResult.low_confidence_kcal_pct: float`: the `calories_kcal` of low-confidence foods ÷ the `calories_kcal` of analysed entries, × 100, rounded to 1 place. It is 0.0 when no calories were analysed.
  - `CustomFoodUse.fitted: bool`

- [ ] **Step 1: Write the failing tests:**
  - `test_nutrients_scale_with_estimated_grams`: an entry with `total_weight_g=335` and a `weight_estimate` counts 3.35× its per-100 g profile.
  - `test_estimated_weights_listed_for_resolved_and_unresolved_foods`: a profile food and a `not_in_usda` food with placeholder entries both appear, the latter with `estimated_g None`.
  - `test_skipped_placeholder_is_not_listed`: Quick Add with a `weight_estimate` is mapped to skip and is absent from the list.
  - `test_no_estimated_weights_gives_empty_list`
  - `test_low_confidence_kcal_pct`: low-confidence food at 930 of 2000 analysed kcal gives 46.5.
  - `test_custom_food_use_carries_fitted`
- [ ] **Step 2: Run them to check they fail.** Run `uv run pytest tests/test_analyzer.py -v`. Expected: the new tests FAIL with KeyErrors.
- [ ] **Step 3: Implement** in the existing per-entry loop. Collect estimates for entries that have the key and are not skipped. Sum analysed kcal and low-confidence kcal next to the existing gram sums. Also update `tests/helpers.py`'s callers and any fixture that builds `CoverageResult` by hand in `tests/test_reporter.py`.
- [ ] **Step 4: Run all tests.** Run `uv run pytest -q`. Expected: all pass.
- [ ] **Step 5: Commit** with the message `feat: report estimated weights and the low-confidence share of calories`.

---

### Task 6: Reporter: estimated-weights line, calorie share, fitted threshold for custom foods

**Files:**
- Modify: `src/omni_pilot/reporter.py`
- Test: `tests/test_reporter.py`

**Interfaces:**
- Consumes: `coverage["estimated_weights"]`, `coverage["low_confidence_kcal_pct"]`, `CustomFoodUse.fitted`, `GOOD_FIT_DISTANCE`.
- Produces:
  - `_estimated_weights_line(coverage: dict) -> str | None`
  - `_macro_check(distance, prefix="", limit=GOOD_DISTANCE)`, now taking a `limit`.

- [ ] **Step 1: Write the failing tests** (exact copy):
  - `_estimated_weights_line` for King's ×2 at 335 g (distance 0.04) and Ben's ×2 at 143 g (0.22) returns:
    `"Estimated weights (logged as 100 g, impossible for their macros): King's Steakhouse Double 100 g → 335 g (×2), Ben's 100 g → 143 g (×2, ⚠ off 22%)"`
    Identical rounded (logged, estimated) pairs collapse with `×n`. Distinct pairs are listed separately. ⚠ appears when the food's largest distance is above `GOOD_FIT_DISTANCE`.
  - No fit: `"X 100 g (weight not estimated)"`.
  - An empty list gives `None`, and neither renderer prints anything.
  - The low-confidence line reads `"Low-confidence matches (4% of analysed weight, 16% of calories): …"`.
  - A custom food with `fitted=True` at distance 0.20 gets `⚠ off 20%` (above 0.15). One with `fitted=False` at 0.20 gets no ⚠.
  - The HTML report contains the estimated-weights line inside the warnings block when present.
- [ ] **Step 2: Run them to check they fail.** Run `uv run pytest tests/test_reporter.py -v`. Expected: the new tests FAIL.
- [ ] **Step 3: Implement.**
  - Terminal: print the line dim, right after the low-confidence line.
  - HTML: add it to the warnings `<div>`, next to `low_confidence_line`, and extend the `{% if %}` guard.
  - The custom-foods line and the HTML custom section pass `limit=GOOD_FIT_DISTANCE` for `fitted` foods, and so does their `flagged` check.
- [ ] **Step 4: Run all tests.** Run `uv run pytest -q`. Expected: all pass.
- [ ] **Step 5: Commit** with the message `feat: show estimated weights in the report`.

---

### Task 7: Wire it into `analyze`, document it, check it on the real export

**Files:**
- Modify: `src/omni_pilot/cli.py` (`cmd_analyze`)
- Modify: `CLAUDE.md`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `logged_macros_for_matching` and `estimate_weights` (matcher); the `weight_unknown` parameters of `enrich_all_foods` and `count_outdated_matches`; `enrichment["matched_macros"]`.

- [ ] **Step 1: Write the failing tests:**
  - `test_analyze_passes_estimated_entries_to_analyze`: a placeholder King's entry, with `enrich_all_foods` mocked to return `matched_macros={"King's…": DOUBLE_CB}`, reaches `analyze` with `total_weight_g ≈ 335`.
  - `test_analyze_passes_weight_unknown_to_enrichment`: `enrich_all_foods` is called with `weight_unknown` containing that food, and with `logged_macros` not containing it.
- [ ] **Step 2: Run them to check they fail.** Run `uv run pytest tests/test_cli.py -v`. Expected: the new tests FAIL.
- [ ] **Step 3: Implement.** Replace `logged_macros_per_100g(entries)` with `logged_macros_for_matching(entries)`, and pass its two halves on. Call `entries = estimate_weights(entries, enrichment["matched_macros"])` after the BAR-43 stop and before `analyze`.
- [ ] **Step 4: Update `CLAUDE.md`:**
  - parser: the flag;
  - matcher: the fit, `GOOD_FIT_DISTANCE` and `estimate_weights`;
  - enricher: `matched_macros` and `matched_by`;
  - analyzer and reporter: the new coverage fields and line;
  - the data-flow diagram: the new step between enrich and analyze;
  - Key invariants: the `matched_by` re-pick rule and "estimated grams are never stored".
- [ ] **Step 5: Run all tests.** Run `uv run pytest -q`. Expected: all pass.
- [ ] **Step 6: Commit** with the message `feat: estimate placeholder weights in analyze`.
- [ ] **Step 7: Check against the real export without touching S3.** Copy `db/food_db.json`, `config/food_mappings.yaml` and `config/custom_foods.yaml` to the scratchpad. Write a scratch `settings.yaml`: the real one without its `s3` section, with `database_path`, `mappings_path` and `custom_foods_path` pointing at the copies. Then run, with the four `OMNI_PILOT_S3_*` variables unset:
  `PYTHONPATH=src uv run python -m omni_pilot.cli analyze <export> --settings <scratch settings>`
  Expected:
  - Oct 21 is left out (only once the Bug 1 branch is merged; this branch is cut from `main`, so here it still counts);
  - King's at about 335 g and not low-confidence;
  - Chicken and Rice and Ben's rice estimated, with ⚠, and low-confidence;
  - the Double Cheeseburger absent from the estimated list;
  - vitamin A and potassium higher than in `latest.pdf`.

  Report the figures you actually got.
