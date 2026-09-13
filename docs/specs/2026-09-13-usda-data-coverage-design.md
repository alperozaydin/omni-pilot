# Technical Specification: USDA Data Coverage Reporting (BAR-41)

**Document Version:** 1.0
**Date:** 2026-09-13
**Status:** In Review
**Linear Issue:** [BAR-41](https://linear.app/knaak/issue/BAR-41/missing-usda-nutrient-components-are-zero-filled-reporting)

---

## 1. Executive Summary & Objective

### The Problem

`analyzer.py` cannot distinguish **"USDA measured this and found none"** from **"USDA never measured this"**. Both collapse to `0.0` in the daily totals, so the report presents a partial sum with the same confidence as a complete one.

Reproduced on 400 g of a beef that USDA lists methionine for but not cystine, and EPA but not DHA:

```
methionine_cysteine_g   1.4 g  / target 1.425  →  "low"       (98.2%)
omega3_epa_dha_mg      80.0 mg / target 250    →  "deficient" (32.0%)
```

Neither verdict is supportable. The true totals are unknown and could be well above target.

This is the worst failure class for this application: it emits a plausible number rather than an error, so nothing signals that the value is untrustworthy. A user could change their diet or begin supplementing on the strength of it.

### Root Cause

USDA **does** distinguish the two cases, and `enricher.py` **already preserves** the distinction correctly:

| Real-world meaning | USDA response | Stored in TinyDB |
| --- | --- | --- |
| Measured, none present | row present, `value: 0.0` | `0.0` |
| Never assayed | row absent entirely | `None` |

Verified against the live API (ground turkey, SR Legacy, `fdcId` 174493): 127 nutrient rows returned, including genuine measured zeros (`262 Caffeine = 0.0`, `269 Total Sugars = 0.0`). Verified in the cache: `vitamin_d_mcg` holds 74 explicit `0.0` values *and* 27 `None` values — two different values for two different facts.

The distinction is destroyed at exactly two lines in `analyzer.py`:

```python
:120   if value is not None:            # a None never gets a key in daily_totals
:146   daily_totals[d].get(ck, 0.0)     # ...so its absence reads as a measured zero
```

There is **no data-acquisition work in this change**. The information survives intact from USDA through `enricher.py` into TinyDB, and is discarded only during analysis. This is a representation defect in one module.

### The Solution

1. **Never zero-fill.** A food with no USDA value for a nutrient contributes nothing to that nutrient, and its weight is booked as *unmeasured* rather than silently as `0.0`.
2. **Report coverage per nutrient.** Every nutrient row carries the share of consumed weight that actually had a USDA value for it, as a percentage, aggregated across the whole reporting period.
3. **Mark unsafe verdicts.** A `low` or `deficient` verdict below 100% coverage is marked `*`, because the computed figure is a floor.

### What This Change Does Not Do

The numbers barely move. Today's code also adds nothing for a `None`, so excluding unmeasured food yields almost exactly today's figures. The only value that shifts is the partial-pair case, where we stop adding the half we used to add — making the figure very slightly **lower**, not higher.

There is no better number available: USDA never measured swiss cheese's methionine, and no amount of arithmetic will recover it. **The disclosure is the entire deliverable.** The coverage column is the feature, not a decoration on one.

---

## 2. Evidence & Scope Justification

The original issue was written around the *partial-pair* case — one component of a combined nutrient present, the other missing. Measurement against the real cached food database (125 profiles) shows that case is rare, and that the damage lies elsewhere.

### 2.1 Component availability across 125 cached foods

```
methionine_cysteine_g     both: 67    ONE MISSING: 2    neither: 56
phenylalanine_tyrosine_g  both: 69    ONE MISSING: 0    neither: 56
omega3_epa_dha_mg         both:106    ONE MISSING: 0    neither: 19
```

The partial-pair case is **2 foods of 125** (olives and papaya, both with methionine but no cystine). The EPA/DHA example in the issue does not occur at all in the real data.

The dominant error is the **"neither component present"** bucket: 56 foods (45%) contributing a flat `0.0` to every amino acid row while being counted as fully analysed. Swiss cheese and a protein bar are currently recorded as containing zero methionine.

The same shape afflicts non-combined nutrients, which have no pair at all: `choline_mg` absent for 33 foods, `vitamin_e_mg` for 29, `vitamin_k_mcg` for 29, `vitamin_d_mcg` for 27.

**Consequence for scope:** a fix confined to `COMBINED_NUTRIENTS` would correct 2 foods and leave 56 untouched. This specification therefore covers **all 35 nutrients**.

### 2.2 Why strict `None` propagation was rejected

Per-day completeness for `methionine_cysteine_g` on `data/MacroFactor-20260817231558.xlsx` (7 days, 90 entries):

```
days where EVERY food has both components :  0 / 7
days with at least one unmeasured food    :  7 / 7

share of consumed weight lacking data, per day:
  2026-08-11  33.8%    2026-08-15  33.8%
  2026-08-12  38.9%    2026-08-16  15.1%
  2026-08-13  24.6%    2026-08-17  70.1%
  2026-08-14  36.6%
```

Zero days of seven are complete. Propagating `None` up to the day would blank all nine amino acid rows plus choline, vitamin E and vitamin K — permanently, on every run. Unmeasured data is the norm here, not the exception, so a rule that discards a day on any unknown discards every day.

This rejects BAR-41's directions 1 (propagate `None`) and 2 (a fifth `insufficient_data` status) on evidence, and selects direction 3 (annotate).

### 2.3 The floor asymmetry

Absent data can only push a true value **up**, never down. The computed figure is therefore a **lower bound**, which makes the verdict trustworthy in one direction only:

| Verdict | Safe on partial data? | Reasoning |
| --- | --- | --- |
| `ok` | **Yes** | Already at or above target; unmeasured food can only reinforce it |
| `high` | **Yes** | Already above UL; unmeasured food can only reinforce it |
| `low` | **No** | The unmeasured portion could close the gap |
| `deficient` | **No** | The unmeasured portion could close the gap |
| `unknown` | n/a | No target, so no verdict to qualify |

This is why the verdict may stand on the measured portion, and why only `low` and `deficient` need marking.

### 2.4 Modelled output

Excluding unmeasured food per nutrient and reporting coverage as a share of consumed weight:

```
nutrient                         avg   target    verdict   weight%
vitamin_d_mcg                   7.09     15.0  deficient    68.7%  *
vitamin_e_mg                    8.12     15.0        low    68.1%  *
vitamin_k_mcg                  62.92    120.0        low    69.0%  *
fiber_g                        28.62     38.0        low    85.5%  *
omega3_ala_g                    1.13      1.6        low    82.6%  *
omega3_epa_dha_mg             160.89    250.0        low    74.8%  *
choline_mg                    712.72    550.0         ok    58.6%
methionine_cysteine_g           4.07      1.4         ok    65.1%
calcium_mg                   1266.52   1000.0         ok   100.0%
```

Coverage ranges from **58.6%** (choline) to **100%** (calcium, iron, potassium, sodium). Only **6 of 35** nutrients require the `*`; the rest are either fully covered or already above target.

Note that `methionine_cysteine_g` reports **4.07 g against a 1.4 g target — 290%, comfortably `ok`** — at 65% coverage. The alarming scenario in the issue is not currently occurring on this data. The mechanism is real; the illustration was hypothetical.

---

## 3. Key Decisions

| # | Decision | Rationale |
| --- | --- | --- |
| 1 | Annotate rather than withhold or propagate | §2.2 — propagation blanks every amino acid row permanently |
| 2 | Apply to all 35 nutrients, not only the 3 combined ones | §2.1 — 56 affected foods vs 2 |
| 3 | Exclude per **food**: any missing component ⇒ that food contributes nothing to that nutrient | No half-pair sums; a partial sum is an invented number |
| 4 | Measure coverage by **consumed weight**, not food count | Counts mislead — 1 g of parsley and 300 g of salmon count alike. Choline is 62/89 foods (69.7%) but only **58.6%** by weight |
| 5 | Display **percentage only** — no grams, no `x/y` counts | Gram totals are noise for the reader; the percentage carries the whole signal |
| 6 | Aggregate over the **whole period**, not per-day-then-averaged | Simpler, and the figure BAR-32 will reuse |
| 7 | Verdict stands on the measured portion, with `*` on `low`/`deficient` below 100% | §2.3 — the floor asymmetry makes `ok`/`high` provably safe |
| 8 | `skip`-mapped food excluded from **both** numerator and denominator | A logged 250 g glass of water must not read as "unmeasured methionine" |
| 9 | Failed USDA lookups count as unmeasured **and** are stated separately in the report | A bad API key must not masquerade as uniformly low coverage |
| 10 | Evaluate the `*` against the **displayed rounded** percentage | A row must never show `100.0%` beside a `*` |
| 11 | Ignore USDA `dataPoints` / `derivationCode` metadata | YAGNI — available, but not needed for this change |

---

## 4. Detailed Component Specifications

### 4.1 `analyzer.py` — coverage accounting

**New weight accumulator.** Alongside `daily_totals`, track per reference-nutrient key the consumed weight that was measured and the weight that was not:

```python
# nutrient_key -> {"measured_weight_g": float, "unmeasured_weight_g": float}
coverage_weights: dict[str, dict[str, float]]
```

**Per-entry, per-nutrient decision.** Replace the current "iterate the food's own keys" accumulation loop with a loop over the reference nutrient keys, so that a nutrient absent from the food's profile is handled rather than silently skipped. For each entry and each of the 35 reference keys:

1. Resolve `component_keys` — `COMBINED_NUTRIENTS[key]` if combined, else `[key]`.
2. Read each component from `food_micros`.
3. If **any** component is `None` or absent: add the entry's `total_weight_g` to `unmeasured_weight_g` for that key. Contribute **nothing** to `daily_totals`.
4. Otherwise: add `total_weight_g` to `measured_weight_g`, and add the scaled sum to `daily_totals[date][key]`.

Unit conversion is unchanged: `omega3_epa_mg` and `omega3_dha_mg` arrive from USDA in grams and are multiplied by `1000.0`.

Because the combined-nutrient sum now happens per entry, `daily_totals` is keyed by **reference** nutrient key rather than by enricher component key. The `.get(ck, 0.0)` at the averaging step disappears, which is the defect's second line.

**Skipped and unresolved food.** Per decisions 8 and 9, an entry is booked as follows:

| Entry condition | `measured` | `unmeasured` | Notes |
| --- | --- | --- | --- |
| Food has a value for the nutrient | + weight | — | |
| Food resolved but nutrient is `None` | — | + weight | The defect being fixed |
| Food mapped to `skip` | — | — | Excluded from both sides |
| USDA lookup failed | — | + weight | Counted as a hole in the data |

Distinguishing "mapped to `skip`" from "lookup failed" is structurally broken today — both arrive as `None` — which is BAR-42 and explicitly out of scope. Until BAR-42 lands, both reach `analyze` as `enriched[name] is None`, so the two cannot be separated inside `analyze`. **This specification therefore books all `None` profiles as `skip` (excluded from both sides), matching today's `skipped_entries` behaviour**, and relies on `coverage.unresolved_entries` remaining the channel for lookup failures once BAR-42 makes it real. Decision 9's "stated separately in the report" is satisfied by §4.3's warning line, which already exists and is driven by `coverage.unresolved_foods`.

**Coverage computation.** After the accumulation loop, for each nutrient key:

```python
total = measured_weight_g + unmeasured_weight_g
coverage_pct = (100.0 * measured_weight_g / total) if total > 0 else None
```

`None` when no weight was attributable at all (no resolved entries for the period), which renders as `—`.

**`NutrientResult` gains two fields:**

```python
class NutrientResult(TypedDict):
    ...
    coverage_pct: float | None      # share of consumed weight with a USDA value, 0-100
    is_floor: bool                  # True when the value is a lower bound and the verdict is unsafe
```

`is_floor` is computed in the analyzer, not the reporter, so the rule lives in one place:

```python
is_floor = (
    coverage_pct is not None
    and round(coverage_pct, 1) < 100.0      # decision 10: rounded, as displayed
    and status in ("low", "deficient")      # decision 7: the unsafe directions only
)
```

Supplement contributions (decision 3 in §3 of the original design, unchanged here) are added to `daily_avg` after averaging and carry no weight, so they do not participate in coverage. This has a correct side effect: a supplement that lifts a nutrient to `ok` clears `is_floor` automatically, because the verdict becomes safe.

### 4.2 `reporter.py` — terminal output

**New column**, rightmost, per decision 5:

```
table.add_column("Data", justify="right", min_width=7)
```

Rendered as `f"{n['coverage_pct']:.1f}%"`, or `"—"` when `None`.

**Floor marker** appended to the status cell when `is_floor` is true: `🟡 Low*`, `🔴 Deficient*`.

**Footnote**, emitted once after the last table, only if any row carries the marker:

```
  * computed from partial USDA data — the true value can only be higher
```

**Summary line.** `_summary_line` gains a trailing clause when any row is a floor: `… · 6 from partial data`.

The existing `show_ok_nutrients` and `show_amino_acids` filters are unaffected.

### 4.3 `reporter.py` — HTML output

The Jinja template mirrors the terminal:

- A `<th>Data</th>` column and matching `<td>`, right-aligned.
- The `status_label` already carries the marker, since it is built in `generate_html_report` from the same `STATUS_DISPLAY` lookup — append `*` there when `is_floor`.
- A `.footnote` block after the categories, rendered under the same condition.
- New CSS: `.coverage { color: #8892b0; }` for the column, `.footnote { margin-top: 1rem; color: #8892b0; font-size: 0.85rem; }`.

`STATUS_DISPLAY` and `NUTRIENT_CATEGORIES` are **not** modified — no fifth status is introduced, which is what makes direction 3 cheap.

The existing warnings block (`skipped_foods` / `unresolved_foods`) is unchanged and serves decision 9.

---

## 5. Data Flow

```text
enricher.py  →  per_100g: {nutrient_key: float | None}
                          │
                          │  None = never assayed        (preserved correctly today)
                          │  0.0  = measured, none found (preserved correctly today)
                          ▼
analyzer.py  ─────────────────────────────────────────────────────────┐
                                                                      │
  for each entry, for each of 35 reference nutrient keys:             │
                                                                      │
    any component None?  ──yes──►  unmeasured_weight_g += weight      │
                                   (contribute nothing)               │
                                                                      │
                         ──no───►  measured_weight_g  += weight       │
                                   daily_totals[date][key] += scaled  │
                                                                      │
  coverage_pct = 100 * measured / (measured + unmeasured)             │
  is_floor     = coverage < 100% AND status in (low, deficient)       │
                                                                      │
└──────────────────────────────────────────────────────────────────────┘
                          │
                          ▼
reporter.py  →  "Data" column (percentage), "*" on unsafe verdicts, footnote
```

---

## 6. Verification Plan

### 6.1 New tests — `tests/test_analyzer.py`

| Test | Asserts |
| --- | --- |
| `test_unmeasured_nutrient_is_not_zero_filled` | A food whose profile has `None` for a nutrient books its weight as unmeasured and yields `coverage_pct < 100`, not a `0.0` contribution counted as measured. **Fails today.** |
| `test_combined_nutrient_with_one_missing_component_contributes_nothing` | Methionine present, cysteine `None` ⇒ `daily_avg` excludes the methionine half entirely. **Fails today** (today adds it). |
| `test_combined_nutrient_with_both_components_sums_normally` | No regression on the 67-food happy path. |
| `test_measured_zero_counts_as_measured` | A food with `0.0` (not `None`) contributes `0.0` **and** counts toward `measured_weight_g` — coverage stays 100%. Guards the whole point of the change. |
| `test_coverage_is_weighted_by_consumed_weight` | 300 g measured + 100 g unmeasured ⇒ 75.0%, not 50% (which a food count would give). Guards decision 4. |
| `test_skipped_food_excluded_from_coverage_denominator` | A `None`-profile food does not depress coverage. Guards decision 8. |
| `test_is_floor_set_only_for_low_and_deficient` | Parametrised across `ok`/`low`/`deficient`/`high`/`unknown` at 80% coverage. Guards decision 7 / §2.3. |
| `test_is_floor_false_at_full_coverage` | 100% coverage with a `low` status ⇒ no marker. |
| `test_is_floor_uses_rounded_coverage` | 99.96% coverage displays `100.0%` and sets `is_floor = False`. Guards decision 10. |
| `test_supplement_lifting_to_ok_clears_floor` | A supplement that pushes a partial-data nutrient to `ok` clears the marker. |
| `test_coverage_none_when_no_resolved_entries` | Empty/all-skipped input ⇒ `coverage_pct is None`, no division by zero. |

### 6.2 Modified test

`tests/test_analyzer.py::test_handles_missing_nutrient_in_enriched` currently asserts:

```python
calcium = result["nutrients"]["calcium_mg"]
assert calcium["daily_avg"] == pytest.approx(0.0)
```

This **pins the defect** for a non-combined nutrient. The `daily_avg` of `0.0` remains correct (there is nothing to add), but the test must additionally assert `coverage_pct == 0.0` and that the value is not presented as measured. Renaming it to `test_unmeasured_nutrient_reports_zero_coverage` makes its intent legible.

### 6.3 Reporter tests — `tests/test_reporter.py`

`_make_analysis_result()` must gain `coverage_pct` and `is_floor` on all five fixture nutrients. New assertions:

- The `Data` column header and a percentage appear in terminal output.
- A row with `is_floor: True` renders `*` adjacent to its status label.
- The footnote appears when any row is a floor, and is **absent** when none is.
- The HTML report contains the column, the marker, and the footnote block.

### 6.4 End-to-end check

`make analyze FILE=data/MacroFactor-20260817231558.xlsx --html` must reproduce the §2.4 table: coverage spanning 58.6%–100.0%, exactly 6 starred rows, and `methionine_cysteine_g` at 4.07 g / `ok` / 65.1%.

Full suite green: `uv run pytest`. Lint clean: `ruff`.

---

## 7. Out of Scope

| Item | Issue | Why deferred |
| --- | --- | --- |
| `num_days` denominator — partially-unresolved days counting at full weight | BAR-32 | Changes the number itself; needs its own decision on thresholds vs. weighting. The per-nutrient coverage mechanism here is built so BAR-32 can consume it rather than inventing a parallel notion of completeness. |
| `skipped` vs `unresolved` structurally indistinguishable | BAR-42 | `enrich_all_foods` encodes both as `None`. Constrains §4.1's booking table as noted; fixing it is a change to the enricher's return contract. |
| USDA `dataPoints` / `derivationCode` provenance | — | YAGNI (decision 11). |
| Re-fetching foods to fill nutrient gaps | — | Impossible; USDA has no data to give. §1. |

---

## 8. Risks & Notes for Future Maintainers

**The invariant in `CLAUDE.md` still holds and now has a fifth member.** Nutrient keys tie together `reference_ranges.yaml`, `USDA_NUTRIENT_MAP`, `COMBINED_NUTRIENTS`, and `NUTRIENT_CATEGORIES`. This change adds no new key, but it does make `analyzer.py` iterate reference keys rather than enricher keys — so a reference key with no `USDA_NUTRIENT_MAP` entry will now report 0% coverage instead of silently reading as `0.0`. That is the correct and more honest behaviour, and it surfaces such a mismatch rather than hiding it.

**Coverage will look alarming at first, and should.** Amino acids sit near 65% and choline near 59%. These are not regressions; they are the pre-existing state of the data becoming visible for the first time. Resist the temptation to "fix" low coverage by widening the USDA dataset filter in `search_usda` — SR Legacy and Foundation are the datasets with real lab analyses, and Branded foods would supply label data with far worse micronutrient completeness.

**Do not extrapolate.** Scaling the measured figure up by the unmeasured weight fraction was considered and rejected: it assumes the unmeasured foods resemble the measured ones, which is exactly backwards. Foods lacking amino acid panels are disproportionately processed and composite items whose composition differs from the whole foods that do have panels.

---

## 9. Spec Self-Review

- **Placeholders:** none. No TBD or TODO sections remain.
- **Internal consistency:** §4.1's booking table constrains decision 9; the constraint and its BAR-42 dependency are stated explicitly in both §4.1 and §7 rather than left as a contradiction.
- **Scope:** single implementation plan. Covers one mechanism (per-nutrient weight coverage) and its two consumers (terminal, HTML).
- **Ambiguity:** the two candidate readings of "coverage" — food count vs consumed weight — are resolved to weight in decision 4, with a test (§6.1, `test_coverage_is_weighted_by_consumed_weight`) pinning it. Whole-period vs per-day aggregation is resolved in decision 6.
- **Known deviation:** decision 9 ("failed USDA lookups count as unmeasured") is only partially implementable before BAR-42, because `enrich_all_foods` encodes a deliberate `skip` and a failed lookup identically as `None`. §4.1 documents the interim behaviour and §7 records the dependency. This is the one place where the specification knowingly falls short of an approved decision.
