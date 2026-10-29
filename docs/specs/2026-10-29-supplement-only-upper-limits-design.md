# Technical Specification: Upper Limits That Count Only Some Intake, and Why a Nutrient Is High

**Document Version:** 1.0
**Date:** 2026-10-29
**Status:** Draft
**Fixes:** BAR-98 (no "High" from food for nutrients whose upper limit applies only to supplements)
**Related:** BAR-97 (targets from DGE, upper limits still from NIH)

---

## 1. Executive Summary & Objective

### The Problem

`analyzer.determine_status` marks a nutrient `high` when its daily average (food plus `supplements.yaml`) is above the `ul` in `config/reference_ranges.yaml`. For some nutrients the NIH/IOM upper limit does not cover all intake:

| Nutrient | Key | UL | What the UL covers (IOM DRI table footnotes) |
| -- | -- | -- | -- |
| Niacin | `b3_niacin_mg` | 35 mg | Synthetic forms: supplements and fortified foods |
| Folate | `folate_mcg` | 1000 mcg | Synthetic folic acid: supplements and fortified foods |
| Vitamin E | `vitamin_e_mg` | 1000 mg | Any form of supplemental alpha-tocopherol |
| Magnesium | `magnesium_mg` | 350 mg | Supplements (pharmacological agents) only |
| Vitamin A | `vitamin_a_mcg` | 3000 mcg | Preformed vitamin A (retinol) only, from any source |

Every other UL in the file (vitamins C, D, B6, calcium, iron, zinc, manganese, phosphorus, selenium, copper, choline, sodium) counts food and supplements together and stays as it is.

The result is false alarms. Niacin showed **High** at 36.4 mg/day on 2026-10-23, all of it from food. Vitamin A compares its UL with RAE (USDA nutrient 320), which includes beta-carotene from vegetables, which has no UL. Magnesium dodges the problem by having `ul: null`, so a large magnesium supplement is never flagged.

The report also never says why a nutrient is High, so the user cannot tell a supplement problem from a food problem.

### The Solution

1. `reference_ranges.yaml` says per nutrient what its UL counts. Niacin, folate, vitamin E and magnesium count the supplement only. Vitamin A counts retinol from food plus the supplement. Everything else counts the whole daily average, as now.
2. Retinol (USDA nutrient 319) is stored per food. Foods already in the cache get it from a one-time script that fetches each by its stored USDA ID, without re-matching; `analyze` fills any entry still missing it.
3. Every `high` row in both reports carries a `†`, and a line under the tables says why: the supplement against the limit, or the top 3 sources.

### Decisions Made During Design

- **Supplement-only limits compare the supplement amount alone with the UL.** Food never makes these nutrients High, whatever the amount; without a supplement for the nutrient the status comes from the target only. (Agreed with the user, as the issue states.)
- **The daily average shown in the report is unchanged:** food plus supplements. The target check keeps counting everything. Only the UL check uses a different figure. (My call.)
- **Vitamin A's target stays on RAE; its UL is checked against retinol from food plus the vitamin A supplement.** (Agreed with the user.)
- **The whole database gets retinol from a one-time script**, with `analyze` filling any entry still missing it. (Requested by the user.)
- **Retinol for cached foods is backfilled by USDA ID, not by bumping `MATCH_VERSION`.** A bump would re-run two searches per food, could change matches whose review marks then stop applying, and would not reach recipe ingredients (cached forever). (Agreed with the user.)
- **Every High is explained**, not only the five above, sodium included. (Agreed with the user.)
- **The marker is `†`, not `*`.** `*` already means "computed from partial USDA data". (Agreed with the user.)
- **Magnesium gets its UL of 350 mg back, marked supplement-only.** It was `null` only because the app could not express "supplements only". (Agreed with the user.)
- **A vitamin A supplement counts fully as retinol.** Most supplements are retinyl palmitate; if it is beta-carotene this errs on the side of a warning. (My call, agreed with the user.)
- **A food with no retinol value contributes zero to the vitamin A UL check, with no marker.** USDA reports retinol nearly everywhere it reports vitamin A, and a UL check has no "floor" concept to hang a marker on. (My call, agreed with the user.)
- **Food is treated as unfortified.** The app cannot tell fortified foods apart, and the recipes avoid US-fortified USDA entries on purpose. (From the issue.)

## 2. Configuration

A new optional field per nutrient in `reference_ranges.yaml`, `ul_applies_to`, says what the `ul` is compared with:

| Value | UL is compared with |
| -- | -- |
| absent | the daily average (food plus supplement), as today |
| `supplements` | the nutrient's amount in `supplements.yaml` alone (0 when not listed) |
| `retinol` | retinol from food (daily average of `retinol_mcg`) plus the nutrient's amount in `supplements.yaml` |

Changes to the file:

- `b3_niacin_mg`, `folate_mcg`, `vitamin_e_mg`: add `ul_applies_to: supplements` with a short comment naming what the IOM UL covers.
- `magnesium_mg`: `ul: 350`, `ul_applies_to: supplements`; the comment that explains the `null` goes.
- `vitamin_a_mcg`: add `ul_applies_to: retinol`, with a comment that the UL covers preformed vitamin A only and a supplement counts as retinol.
- The terminology block at the top documents `ul_applies_to`.

Only these three spellings are valid. An unknown value is a configuration error: loading stops with a message naming the nutrient and the value, like other invalid config. (`config.py` loads the file; where the check lives is the builder's call.)

`config.get_nutrient_target` keeps its return shape; the analyzer reads `ul_applies_to` from the nutrient's entry itself. (Builder's call if a cleaner seam exists.)

`config/supplements.example.yaml` gets one-line notes:

- `vitamin_a_mcg`: counts as retinol for the upper limit.
- `folate_mcg`: enter the folic acid amount as an EU label states it (µg folic acid), not a US label's DFE figure. The UL is in folic acid, so the UL check is exact. The target is in folate equivalents (1 µg folic acid counts as more than 1 µg), so the target check undercounts a folic acid supplement, as it does today. *(My call, after the builder check: no conversion factor, since DGE (2) and NIH (1.7) disagree on it and the undercount only errs toward "low".)*
- `vitamin_e_mg`: enter the mg of alpha-tocopherol on the label; the UL covers any supplemental form.

## 3. Retinol per Food

### Storing it

`USDA_NUTRIENT_MAP` in `enricher.py` gains `"retinol_mcg": "319"` (Retinol, mcg). It is a component value like `methionine_g`, not a reference nutrient: it has no entry in `reference_ranges.yaml`, `NUTRIENT_CATEGORIES` or the report tables. From then on every new USDA match and every newly fetched recipe ingredient stores it in `per_100g` through `extract_micros_from_usda`, `None` when USDA has no value.

Older code ignores the extra key (it iterates its own `USDA_NUTRIENT_MAP`), so a device running old code is unaffected by entries that have it.

### Backfill for cached entries

An entry written before this change has no `retinol_mcg` key in `per_100g` at all. That absence (not a `None` value) is what marks it for backfill:

- **Main cache table** (matched foods): when `get_food_match` reuses a cached match whose `per_100g` lacks the key, it fetches the entry's `usda_fdc_id` with `fetch_usda_food` and writes the entry back with `per_100g` extended by `retinol_mcg` (from `extract_micros_from_usda` on the fetched food). Nothing else in the entry changes: not the match, its confidence, `match_version`, `last_updated` or any field the app does not write (`reviewed_fdc_id`, `deferred_*`). The returned `FoodMatch` carries the extended `per_100g`.
- **`usda_foods` table** (recipe ingredients): `get_usda_food` does the same for a cached ingredient lacking the key. This is the one exception to "the `usda_foods` table is never invalidated": an entry can gain a missing value; the food it names never changes.
- **Failure:** a failed fetch (network error, timeout, any error status other than 404) writes nothing and leaves the key absent, so the entry is tried again next run. The food is otherwise used as cached: it does not become `lookup_failed`, and its retinol counts as zero for the run (section 1). A failure is logged as a warning naming the food.
- **404:** USDA says the ID no longer exists (possible for Foundation foods, which USDA can replace). The entry is written with `retinol_mcg: None`, so it is not fetched again every run, and the warning names the food and says its USDA ID is gone, not "check custom_foods.yaml" (that message stays for a recipe ingredient missing on a first fetch). `fetch_usda_food` needs to tell a 404 apart from other failures for this. *(My call, after the builder check.)*
- **Which entries:** only those the run uses, i.e. the foods in the export being analysed and the ingredients of their recipes, through `get_food_match` and `get_usda_food`. The whole database is filled once by the script below; this per-run path is the safety net for entries the script could not fetch and for entries a device still running old code writes without the key.
- **No USDA ID:** an entry without `usda_fdc_id` (none exist in the current database, but legacy entries may) is not backfilled and its retinol counts as zero.
- **Not-found records** have no `per_100g` and are untouched.

`per_100g` is a nested dict, and `upsert` merges only top-level fields, so the write passes the whole extended `per_100g`.

### One-time fill of the whole database

A script, `scripts/backfill_retinol.py`, fills `retinol_mcg` for every entry in the database at once, so the S3 copy is complete right after the change rather than as foods come back in exports. *(Requested by the user.)* It is run once on the Mac:

```bash
PYTHONPATH=src uv run python scripts/backfill_retinol.py
```

1. **Pull** from S3 when configured, like `make pull` (data files only, no exports); an incomplete S3 setup or a failed pull exits 1 and writes nothing. Without S3 it works on the local files.
2. **Fill** every main-table match and every `usda_foods` entry whose `per_100g` lacks `retinol_mcg`, using the same backfill function as the per-run path (same 404 and failure rules, every other field untouched). An FDC ID shared by several entries is fetched once. Not-found records and entries without `usda_fdc_id` are skipped.
3. **Print** a summary: entries filled, entries skipped (no USDA ID), fetches failed (named, retried by the next `analyze` or another run of the script), IDs gone (404).
4. **Push** the database when anything changed, like `cmd_push`, including its conflict hint on a refused push.

It needs the USDA key from `settings.yaml` and nothing else (no export, no Gemini). It is idempotent: a second run fetches only what the first could not. About 330 fetches today, within USDA's 1,000 an hour; if USDA starts refusing (HTTP 429 or repeated failures), the script stops fetching, still saves and pushes what it filled, and says to run it again later. The script stays in the repo after the run *(my call: it is harmless and covers a device that wrote old entries later)*; it is not part of the CLI, `make` or the a-Shell copy's workflow.

After the script, an `analyze` only fetches retinol for entries written since by old code, or that the script failed on. The database it changes is pushed to S3 by `analyze` as usual. The daily review never runs `analyze`, so it neither backfills nor needs retinol.

`recipe_tool.py` (custom-foods skill) writes `usda_foods` entries through `extract_micros_from_usda`, so its new entries get the key too. Its "missing nutrients" listing (`food["per_100g"]` values that are `None`) will name `retinol_mcg` for an ingredient USDA has no retinol for; that is acceptable, and the builder may leave it out of that listing if it reads as noise.

## 4. Status

For each nutrient with a UL, the analyzer computes the figure the UL is compared with (the **UL figure**), per section 2:

- absent: the daily average, as now;
- `supplements`: `supplements.get(key, 0.0)`;
- `retinol`: the period's daily average of `retinol_mcg` from food plus `supplements.get(key, 0.0)`.

`determine_status` decides `high` from the UL figure and `ok` / `low` / `deficient` from the daily average against the target, in that order, both rounded to one decimal as now. Its signature changes accordingly (builder's call how).

**Retinol from food** is summed like any nutrient: per entry, per part of a custom food (each part's share of the entry's weight), per day, averaged over the counted days. A part with `retinol_mcg` `None` or absent contributes nothing. Retinol takes no part in coverage: it is not a reference nutrient and gets no `coverage_pct`.

`NutrientResult` gains the data the reports need to explain a High (section 5). A nutrient that is not `high` needs none of it. Suggested shape (builder's call): `ul_figure: float | None` and `high_sources: list[Source]` where a `Source` is a name and its daily amount, plus whether the UL counts supplements only.

## 5. Explaining a High

### Sources

For a nutrient that is `high` and whose UL counts food (absent `ul_applies_to`, or `retinol`), the analyzer lists the sources of the UL figure:

- each **logged food name** with its daily-average contribution (its total over the counted days divided by the number of days), a custom food counted as one source under its logged name, not split into ingredients;
- the **supplement** as one more source named `supplement`, when `supplements.yaml` lists the nutrient;
- for `retinol`, the amounts are retinol, not RAE.

Sorted by amount, largest first; the reports show the top 3 with each one's share of the UL figure. Logged fiber has no UL, so it never needs this.

For a `supplements` nutrient that is `high`, the only source is the supplement; no food list is shown.

### Report lines

Every `high` row's status shows `High†` in the terminal table and the HTML table. Under the tables, after the `*` footnote and before the logged-fiber line, one line per High nutrient, in table order (dim in the terminal, `footnote` class in HTML):

```
† Sodium: 3,100 of 2,300 mg/day — bread 900 (29%), feta 610 (20%), salami 480 (15%)
† Vitamin A: retinol 3,410 of 3,000 mcg/day — beef liver 2,150 (63%), supplement 800 (23%), eggs 310 (9%)
† Niacin: supplement 40 of 35 mg/day — food isn't counted toward this limit
```

- Amounts are the UL figure and the UL, with the nutrient's unit and `/day`; source amounts in the same unit, shares as whole percents. Number formatting (thousands separator, decimals for small mcg/mg values) is the builder's call, kept consistent between both reports.
- Fewer than 3 sources: show what there is.
- A line may wrap in an 80-column terminal (a-Shell); it is not truncated.
- `show_ok_nutrients: false` hides OK rows only; High rows and their lines always show.

## 6. Tests

New and changed tests, all without network (USDA responses mocked as today):

- **Config:** `ul_applies_to` accepted for the three spellings; an unknown value stops loading with a message naming the nutrient.
- **Status:** niacin from food above 35 with no supplement is `ok` (the 2026-10-23 case); niacin with a 40 mg supplement is `high` even with little food; niacin with a 20 mg supplement and food bringing the total above 35 is `ok`; magnesium with a 400 mg supplement is `high`; vitamin A with a high RAE from carrots but little retinol is not `high`; vitamin A with liver over 3000 mcg retinol is `high`; a vitamin A supplement counts toward retinol; sodium over 2300 from food stays `high`; a below-target nutrient with a supplement-only UL is still `low` / `deficient`.
- **Sources:** top-3 order and daily amounts; supplement listed as a source; a custom food counted as one source under its logged name; retinol sources are retinol amounts.
- **Enricher backfill:** a cached match without `retinol_mcg` is fetched by `usda_fdc_id` and written back with the key added and every other field unchanged, including `reviewed_fdc_id` and `deferred_*`; a failed fetch writes nothing and the food is still used (not `lookup_failed`); a 404 stores `retinol_mcg: None` and is not fetched again; an entry with the key (even `None`) is not fetched; the same for `usda_foods`; no entry is backfilled twice in one run.
- **Backfill script:** fills both tables, fetches a shared FDC ID once, skips not-found records and entries without an ID, leaves the database unchanged and pushes nothing when every entry already has the key, stops fetching on HTTP 429 but saves what it filled, and pulls before / pushes after with S3 configured (S3 mocked).
- **Reports:** `High†` and the footnote lines in terminal and HTML, for each of the three kinds; no `†` line when nothing is High.
- Existing tests that assert vitamin A, niacin, folate, vitamin E or magnesium status from the shipped config are updated to the new rules.

## 7. Documentation

`CLAUDE.md`: the analyzer paragraph (status uses the UL figure; `ul_applies_to`), the reporter paragraph (`†` lines), the enricher paragraph and the `usda_foods` invariant (retinol backfill is the one way an ingredient entry changes), and the key-invariants note that `retinol_mcg` is a stored component, not a reference nutrient. The Commands section names the one-time `scripts/backfill_retinol.py`.

## 8. Out of Scope

- Changing any UL value other than magnesium's restored 350 mg.
- EFSA upper limits (some differ from NIH, for example vitamin B6).
- Telling fortified foods apart.
- Per-day UL checks (a single day over the limit); the check stays on the period's daily average, like everything else in the report.
