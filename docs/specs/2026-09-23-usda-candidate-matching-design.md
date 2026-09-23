# Technical Specification: Macro-Validated USDA Candidate Matching (BAR-72)

**Document Version:** 1.0
**Date:** 2026-09-23
**Status:** In Review
**Linear Issue:** [BAR-72](https://linear.app/knaak/issue/BAR-72/usda-matching-takes-the-first-search-result-pick-candidates-by-logged)

---

## 1. Executive Summary & Objective

### The Problem

`enricher.search_usda` sends the translated query to USDA FoodData Central and takes **the first search result**. USDA search is keyword ranking, not food identity, so the first hit is frequently the wrong food even when the query is correct. Nothing checks the pick, so a wrong match silently feeds its micronutrients into the averages.

An audit of the shared production DB (135 translations, 149 cached profiles, 36 days / 61 kg of logged food) compared each food's logged macros per 100 g (from MacroFactor) with the macros of its cached USDA match:

| kcal/100 g agreement | Foods | Share of logged grams |
| --- | --- | --- |
| within 25% | 77 | 57% |
| 25–50% off | 23 | 24% |
| more than 50% off | 24 | 19% |

The failures fall into four classes:

1. **Ranking picks the wrong food for a correct query.**
   - `chickpeas, mature seeds, cooked, boiled, without salt` → *Lentils, …* (chickpeas rank 4th).
   - `milk, whole, fluid` → *Milk, buttermilk, fluid, whole*.
   - `chicken, broilers or fryers, breast, meat only, raw` → the generic *Chicken, broilers or fryers, meat only, raw* (breast ranks 2nd).
   - `tea, iced, sweetened with sugar` → *Sweeteners, for baking* (398 vs 33 kcal).
   - `berries, mixed, frozen` → *Snack, Mixed Berry Bar*.
2. **Raw vs cooked state.** The user logs cooked weight, but some queries name the raw state.
   - Reis XXL, Express-Reis and Basmati Und Jasminreis are 2.1–2.6× too high.
   - Linsen is 3.9× too high.
3. **Sparse Foundation entries win over complete SR Legacy ones.** For example, *Beef, top sirloin steak, raw* [Foundation] is missing 29 of the 38 tracked nutrients, while the SR Legacy equivalents have them.
4. **Mixed dishes mapped to one ingredient.**
   - Salzlakenkaese salat → *Cheese, feta*: 54 vs 265 kcal.
   - Salat Caprese → *Fish, tuna salad*.
   - Misch Salat Rohkost → *Salad dressing, french*.

### The Solution

MacroFactor already records kcal, protein, fat and carbs for every entry. That is ground truth for **what the user actually ate**, and it is currently unused for matching. This change uses it to choose among USDA candidates:

1. Fetch many candidates instead of one.
2. Keep only candidates that are the named food, using a deterministic main-word filter.
3. Among those, pick the one whose macros are closest to the logged macros. On a near-tie, prefer candidates that match more query words, then SR Legacy.
4. Record a confidence level per match. Matches that remain far off are **still counted** but are **flagged** in both reports.

This fixes classes 1–3 automatically. Class 4 cannot be fixed by picking, because no single USDA food matches a mixed salad. It is surfaced by the flag and is left to a later change (see §9).

### Decisions Made During Design

- **The candidate filter is deterministic**, with no LLM in the matching step: it is testable, free, works offline, and runs identically on the iPhone.
- **Weak matches are counted and flagged**, never excluded or rescaled.
- **Rollout is automatic.** The next normal `analyze` re-picks every cached entry. There is no preview command and no DB backup: only the rebuildable USDA cache is rewritten, and translations are untouched.

---

## 2. Architecture Overview

```
entries ─► matcher.logged_macros_per_100g ─► logged_macros ─┐
food_names, mappings ───────────────────────────────────────┼─► enricher.enrich_all_foods
                                                            │     ├─ search_usda (query)      ┐ candidates,
                                                            │     ├─ search_usda (main word)  ┘ deduped by fdcId
                                                            │     └─ matcher.pick_best ─► FoodMatch
                                                            ▼
                                   EnrichmentResult (+ low_confidence) ─► analyzer ─► reporter
```

| Unit | Responsibility | I/O |
| --- | --- | --- |
| `matcher.py` (new) | logged macros, main-word filter, macro distance, candidate choice | none (pure) |
| `enricher.py` | USDA search (multi-result), cache validity, calling the matcher | HTTP, TinyDB |
| `analyzer.py` | low-confidence foods + weight share into `CoverageResult` | none |
| `reporter.py` | the low-confidence warning line (terminal + HTML) | output |
| `cli.py` | compute logged macros, pass them to the enricher, print re-match notice | orchestration |

---

## 3. `matcher.py` (new, pure functions)

### 3.1 Types

```python
class LoggedMacros(TypedDict):      # per 100 g, as logged in MacroFactor
    kcal: float
    protein_g: float
    fat_g: float
    carbs_g: float

class Candidate(TypedDict):         # one USDA search hit, normalised
    fdc_id: int
    description: str
    data_type: str                  # "SR Legacy" | "Foundation"
    rank: int                       # 0-based; search-1 hits before search-2 hits
    protein_g: float | None         # nutrient 203
    fat_g: float | None             # nutrient 204
    carbs_g: float | None           # nutrient 205 (by difference, includes fiber)
    fiber_g: float | None           # nutrient 291
    raw: dict                       # the original USDA food dict (for micro extraction)

class Pick(TypedDict):
    candidate: Candidate
    macro_distance: float | None
    confidence: str                 # "good" | "weak" | "no_macros"
```

### 3.2 `logged_macros_per_100g(entries) -> dict[str, LoggedMacros]`

- For each food name, sum `total_weight_g`, `calories_kcal`, `protein_g`, `fat_g` and `carbs_g` over all its entries. Each macro per 100 g is `100 × sum / total grams`.
- Entries with `total_weight_g <= 0` are ignored.
- A food whose total weight is 0 gets no entry, and its match will have confidence `no_macros`.

### 3.3 Main-word filter

**Normalisation** (applies to both the query and candidate descriptions):
- lowercase;
- remove parenthesised text (`Chickpeas (garbanzo beans, bengal gram)` → `chickpeas`);
- split into segments at commas, dropping empty segments (Foundation descriptions like `Chickpeas, (garbanzo beans, bengal gram), dry` leave one behind once the parentheses are removed);
- split each segment into words, where a word is a run of `[a-z0-9-]`;
- drop the stop words `and, or, with, in, of`;
- singularise each word:
  - `-ies` → `-y` (`berries` → `berry`);
  - `-oes` → `-o` (`tomatoes` → `tomato`);
  - otherwise a trailing `s` is dropped, unless the word ends in `ss`.

**Head words of the query:**
- The words of the query's first segment.
- If that segment, after normalisation, is in `GENERIC_HEADS`, the words of the first **two** segments.

```python
GENERIC_HEADS = {"snack", "beverage", "cereal", "cereal ready-to-eat", "alcoholic beverage",
                 "carbonated beverage", "fish", "crustacean", "nut", "seed", "spice",
                 "sauce", "salad dressing"}
```

**The rule:** a candidate passes if **every** head word appears in the candidate's first `h + 1` segments, where `h` is the number of head segments (1 or 2). For example:
- `carbonated beverage, cola` passes *Beverages, carbonated, cola, regular*.
- `chickpeas, …` rejects *Lentils, …*.

**Fallback:** if no candidate passes, all candidates are considered, and the resulting pick's confidence is forced to `weak`.

### 3.4 `macro_distance(logged, candidate) -> float | None`

```
ΔP = |logged.protein_g − cand.protein_g|
ΔF = |logged.fat_g     − cand.fat_g|
ΔC = min(|logged.carbs_g − cand.carbs_g|,
         |logged.carbs_g − (cand.carbs_g − cand.fiber_g)|)      # second term only if fiber known
distance = (4·ΔP + 9·ΔF + 4·ΔC) / max(logged.kcal, KCAL_FLOOR)
```

- **Carbs with or without fiber:** EU labels report carbohydrate *excluding* fiber, while USDA's nutrient 205 *includes* it. Logged foods come from both conventions, so the closer of the two readings is used.
- **`KCAL_FLOOR = 50`** stops very low-calorie foods (tomatoes, coffee, diet cola) from turning tiny absolute differences into huge relative ones.
- **Energy (208) is not used:** Foundation foods often report energy under other nutrient numbers or not at all. Alcohol calories are therefore invisible to the distance, which is acceptable for picking among beers and wines.
- Returns `None` if the candidate lacks protein, fat or carbs.

### 3.5 `pick_best(candidates, query, logged) -> Pick | None`

1. Returns `None` if `candidates` is empty.
2. `pool` is the candidates that pass the main-word filter, or all of them if none pass (fallback).
3. **If `logged` is `None`:** return `pool[0]` by `rank`, with confidence `no_macros` and distance `None`.
4. Score each candidate in `pool`. Candidates whose distance is `None` are set aside. **If none can be scored:** return `pool[0]` by `rank` as `weak`, with distance `None`.
5. `best` is the minimum distance. The **band** is the scored candidates with distance ≤ `best + NEAR_TIE_BAND` (0.05).
6. Order the band by:
   1. query-word overlap, descending. This is the fraction of the query's normalised words (all segments) that occur anywhere in the candidate description, and it keeps Gemini's intent, e.g. `breast`;
   2. `data_type == "SR Legacy"` first (complete nutrient panels);
   3. `rank` ascending.

   The first one is chosen.
7. Confidence is `good` if its distance ≤ `GOOD_DISTANCE` (0.25), otherwise `weak`. It is always `weak` if the fallback in step 2 applied.

All thresholds (`KCAL_FLOOR`, `NEAR_TIE_BAND`, `GOOD_DISTANCE`, `GENERIC_HEADS`) are module-level constants.

---

## 4. `enricher.py` Changes

### 4.1 Searching

- `search_usda(query, api_key) -> list[dict]` returns up to `SEARCH_PAGE_SIZE = 25` foods (SR Legacy + Foundation). It returns an empty list when there are no results or the request fails. The existing single 429 retry is kept.
- `get_food_candidates(query, api_key) -> list[Candidate]` runs:
  - **search 1**, with the query;
  - **search 2**, with the head words joined by spaces (e.g. `chickpea`), skipped if it equals the query. USDA search stems words, so the singular form returns the same hits as the plural (verified: `chickpea` and `chickpeas` both return the canned and dry chickpea entries that search 1 ranked out of view).

  It concatenates search 1 hits before search 2 hits, removes duplicates by `fdcId` (keeping the first occurrence), assigns `rank` in that order, and extracts macros 203/204/205/291 from each hit's `foodNutrients`.
  - If search 2 fails, search 1's candidates are used alone.
  - If search 1 fails or returns nothing, the result is `[]` (the food is unresolved).

### 4.2 Cache entries

The existing fields stay: `original_name`, `usda_query`, `usda_name`, `usda_fdc_id`, `usda_dataset`, `per_100g`, `confidence`, `last_updated`. The `confidence` field used to hold `"direct"`/`"mapped"`; it now holds `good`/`weak`/`no_macros`. Nothing reads the old values. New fields:

```python
"match_version": MATCH_VERSION,        # int, starts at 1; legacy entries lack it (treated as 0)
"macro_distance": float | None,
"usda_macros": {"protein_g", "fat_g", "carbs_g", "fiber_g"},
```

`MATCH_VERSION` must be incremented whenever the picking logic changes in a way that should re-pick cached foods.

### 4.3 `get_food_micros` → `get_food_match`

`get_food_match(food_name, usda_query, logged, db, api_key) -> FoodMatch | None`

```python
class FoodMatch(TypedDict):
    per_100g: dict[str, float | None]
    usda_name: str
    confidence: str          # "good" | "weak" | "no_macros" | "unverified"
    macro_distance: float | None
```

| Cached entry state | Behaviour |
| --- | --- |
| same query, `match_version == MATCH_VERSION` | return it from the cache |
| same query, older or missing `match_version` | re-pick. **On success**, replace the entry. **On failure** (no candidates, network error), keep the old entry and return it with confidence `unverified`. It is retried on the next run and is never deleted before its replacement exists. |
| different query | remove the entry and fetch (unchanged behaviour) |
| none | fetch |

A fetch means `get_food_candidates` followed by `matcher.pick_best`, then `extract_micros_from_usda(pick.candidate.raw)` and inserting the cache entry. The function returns `None` only when a fresh fetch finds no candidates.

### 4.4 `enrich_all_foods`

- Signature: `enrich_all_foods(food_names, mappings, logged_macros, db, api_key)`.
- `EnrichmentResult` gains:

  ```python
  low_confidence: dict[str, LowConfidenceMatch]   # food_name -> {"usda_name": str, "macro_distance": float | None}
  ```

  Only `weak` matches go in. `good`, `no_macros` and `unverified` do not.
- Low-confidence foods **stay in `profiles`**. Their nutrients are counted exactly as today.
- `count_outdated_matches(food_names, mappings, db) -> int` counts cached entries with the current query but an old `match_version`. The CLI uses it for a progress notice.

---

## 5. Analyzer and Reporter

### 5.1 `analyzer.py`

`CoverageResult` gains:

```python
low_confidence_foods: list[LowConfidenceFood]   # {"name", "usda_name", "macro_distance", "grams"}
low_confidence_weight_pct: float                # share of analysed weight, 0–100
```

- `grams` is the total `total_weight_g` of the food's entries in the period.
- The list is sorted by `grams`, descending.
- The weight share's denominator is the total weight of entries with a profile (analysed entries).

### 5.2 `reporter.py`

A new warning follows the Skipped/Unresolved lines, and appears only when the list is non-empty.

Terminal (yellow):

```
  ⚠ Low-confidence matches (18% of analysed weight): Salzlakenkaese salat → Cheese, feta (off 390%), Salat Caprese → Fish, tuna salad (off 150%), …
```

- `off N%` is `macro_distance × 100`, rounded to a whole number.
- A `None` distance is shown without the `(off …)` part.

HTML: the same content as a `<p>` in the existing warnings block, whose condition is extended to include it.

No per-nutrient marker is added. The named foods and the weight share are the disclosure.

---

## 6. CLI

In `cmd_analyze`, after parsing:

```python
logged_macros = logged_macros_per_100g(entries)
...
outdated = count_outdated_matches(food_names, mappings, db)
if outdated:
    print(f"  Re-matching {outdated} cached foods with updated USDA matching...")
enrichment = enrich_all_foods(food_names, mappings, logged_macros, db, api_key)
```

No new command or flag.

---

## 7. Rollout & Cross-Device Behaviour

- **First run on the Mac with the new code:** every cached food in the export has no `match_version`, so all are re-picked. That is about two USDA searches per food, roughly 250 calls for the current data, well under USDA's 1,000/hour limit. It takes a few minutes, and the notice from §6 explains the wait.
- **Old code on the iPhone** (the iCloud copy of `src/`): it ignores the new fields and accepts any entry whose `usda_query` matches, so it simply reads the new picks. If it ever rewrites an entry (after a query change), that entry lacks `match_version` and the Mac re-picks it next run. Mixed versions therefore never corrupt data. The iPhone just doesn't re-pick until its code is updated.
- **What is rewritten:** only the default (USDA cache) table. The `translations` table and `food_mappings.yaml` are not modified by this change.

---

## 8. Testing

All USDA HTTP calls are mocked, and no test touches Gemini. Candidate fixtures are small hand-built dicts copied from real USDA search responses observed during the audit.

**`tests/test_matcher.py`** (new):
- `logged_macros_per_100g`: weighted aggregation across several entries; zero-weight entries are ignored; a zero-total food is absent.
- Normalisation and head words:
  - parentheses are removed;
  - `tomatoes`/`berries`/`chickpeas`/`glass` singularise correctly;
  - a generic head (`fish, salmon, …`, `carbonated beverage, cola`) uses two segments.
- Filter:
  - `chickpeas, …` rejects *Lentils*;
  - `carbonated beverage, cola` accepts *Beverages, carbonated, cola, regular*.
- `macro_distance`:
  - the fiber-adjusted carbs reading is chosen when it is closer;
  - `KCAL_FLOOR` is applied for low-energy foods;
  - `None` when the candidate lacks a macro.
- `pick_best`:
  - chickpeas beat lentils for the Kichererbsen macros;
  - cooked rice beats raw rice for a logged 141 kcal / P3 F1 C29;
  - the chicken breast entry beats generic chicken, by distance and query-word overlap;
  - SR Legacy beats Foundation within the near-tie band;
  - the empty-filter fallback is `weak`;
  - no logged macros gives `no_macros` and `rank` 0;
  - no scorable candidates gives `weak` with distance `None`;
  - an empty candidate list gives `None`.

**`tests/test_enricher.py`** (extended):
- `get_food_candidates`:
  - search 1 hits come before search 2 hits;
  - duplicates are removed by `fdcId`;
  - a failed search 2 still yields search 1's candidates;
  - a failed search 1 yields `[]`.
- `get_food_match`:
  - a current-version cache hit makes no HTTP call;
  - a legacy entry is re-picked and rewritten with `match_version`;
  - a failed re-pick keeps the legacy entry and returns `unverified`;
  - a query change removes and refetches.
- `enrich_all_foods`: a `weak` match appears in both `profiles` and `low_confidence`, and `good` does not appear in `low_confidence`.
- `count_outdated_matches`: counts only same-query, old-version entries.
- Existing tests are updated for the new signatures, and `tests/helpers.enrichment` gains a `low_confidence` keyword.

**`tests/test_analyzer.py`:** `low_confidence_foods` is sorted by grams, and `low_confidence_weight_pct` uses analysed weight as the denominator.

**Reporter tests:** the warning line appears in the terminal and HTML output only when non-empty, and the formatting handles a `None` distance.

**`tests/test_cli.py`:** logged macros are passed through to `enrich_all_foods`.

---

## 9. Out of Scope

These are planned as separate design cycles that build on `matcher.pick_best`:

- **Pinning the USDA food ID in mappings.** Store the chosen `fdcId` in the translation/YAML so a match is explicit, editable, and immune to ranking drift.
- **Splitting mixed dishes into ingredients.** Gemini proposes ingredients with proportions, each one is matched separately, and the weighted sum is validated against the logged macros.

Also not addressed here:
- Gemini prompt changes;
- cleanup of the 20 stale cache entries keyed on untranslated names;
- the two mapped foods with no profile (`Apfel`, and `Bio Rinder-Hackfleisch…`, whose query contains `/`).

---

## 10. Documentation

`CLAUDE.md` is updated:
- `matcher.py` is added to the architecture list, and the enricher bullet mentions macro-validated candidate picking and `low_confidence`.
- A new key invariant: a USDA cache entry is valid only when both `usda_query` and `match_version` match, and `MATCH_VERSION` must be incremented whenever the picking logic changes in a way that should re-pick cached foods.
- The data flow diagram shows `logged_macros` feeding `enrich_all_foods`.
