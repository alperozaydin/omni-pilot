# Technical Specification: Estimated Weights for Placeholder Servings

**Document Version:** 1.0
**Date:** 2026-10-21
**Status:** Draft, awaiting approval
**Builds on:** BAR-72 (candidate matching, `match_version`), the custom-foods recipes, BAR-86 (logged fiber), BAR-87 (partly logged last day)

---

## 1. Executive Summary & Objective

### The Problem

Meals logged with MacroFactor's AI photo feature have no real weight. The export gives the serving a placeholder weight of 100 g (`Serving Weight (g) = 100`, unit "serving", "bowl" or "portion"). The kcal, protein, fat and carbs are correct for the whole serving. Only the grams are invented.

The app scales every USDA nutrient by grams (`analyzer.py`), so these meals count as 100 g of food. Fiber is the exception: it comes from MacroFactor's own figure when that is above zero (BAR-86). The placeholder also skews matching. `matcher.logged_macros_per_100g` divides the logged macros by 100 g, which gives per-100 g figures 2–12× too high, so the matcher picks the densest candidate it can find.

The 2026-10-08 → 2026-10-21 export has six such entries (four foods), about 16% of logged calories. The report's "4% of analysed weight" understates them because it weights by those same invented grams:

| Entry | Logged | kcal | Protein + fat + carbs | Matched today (macros off) |
| --- | --- | --- | --- | --- |
| King's Steakhouse Double (×2) | 1 × 100 g | 930 | 163 g | cheeseburger, double (70%) |
| Chicken and Rice in Cream Sauce | 1 × 100 g | 1192 | 232 g | chicken breast tenders, breaded (79%) |
| Ben's Original Langkorn reis with chickpeas (×2) | 1 × 100 g | 660 | 144 g | rice, white, long-grain, raw (43%) |
| Double Cheeseburger (×2) | 1 × 100 g | 440 | 81 g | cheeseburger, double (34%) |

Older exports have the same thing with a quantity other than 1: "Custom Chicken Doner" (1.1 × 100 g, 193 g of macros in 110 g) and "Grilled Cheese and Turkey Sandwich" (2 × 100 g, 217 g in 200 g). The custom-foods skill already knows about it. A comment in `custom_foods.yaml` says such a food "stays flagged until the weight is fixed in MacroFactor".

### The Solution

- **Detect** an entry whose weight is a placeholder. Its serving weight is exactly 100 g and its protein + fat + carbs weigh more than the whole entry. Such an entry is physically impossible.
- **Match by proportions.** For a food whose logged weight is unknown, the matcher compares the proportions of protein, fat and carbs instead of per-100 g figures. It first fits the grams of each candidate that best reproduce the logged macros, then scores the candidate at those grams.
- **Estimate the grams.** After matching, each placeholder entry's grams are fitted against the food's match: the USDA food, or the custom recipe's combined macros. Analysis, coverage and weight shares then use the estimate.
- **Show it.** The report lists every entry whose weight was estimated (logged → estimated grams). The low-confidence warning also gives its share of calories.

### Decisions Made During Design

| Decision | Choice | Why |
| --- | --- | --- |
| When a weight is a placeholder | Serving weight exactly 100 g **and** protein + fat + carbs > 105% of the entry's grams | The user's rule: 100 g and a physically impossible entry. The macro test is used instead of kcal/g > 9 because it is the same physical limit (no food has more than 100 g of macros per 100 g) but catches more: kcal/g > 9 catches only two of the six entries. The 5% margin absorbs whole-gram rounding on small macros. |
| The Double Cheeseburger (440 kcal, 81 g of macros in 100 g) | Not detected: it stays at 100 g | Unlikely but physically possible, so the rule doesn't fire. Its match is already flagged as low-confidence (34%), so it stays visible. The user accepted this. |
| Real 100 g servings (Basmatireis, Lachsfilet, Tomaten…) | Untouched | They are physically possible, so they never meet the rule. |
| Mixed dishes (Chicken and Rice, Ben's rice with chickpeas) | No automatic split into ingredients | A split needs an ingredient list nobody has. Mixed dishes keep getting a custom recipe through the custom-foods skill, and the grams are then fitted against the recipe. |
| Quality bar for a fitted match | `GOOD_FIT_DISTANCE = 0.15`, stricter than `GOOD_DISTANCE` (0.25) | Fitting the grams gives the matcher one more free parameter, so the same distance means a worse match. With 0.25, raw rice alone would pass as a "good" match for rice with chickpeas (0.22). See §3.3. *Claude's call.* |
| Re-picking cached foods | A cache entry records how its pick was made: by per-100 g figures or by proportions. It is re-picked when that no longer matches the food. No `MATCH_VERSION` bump. | Otherwise the first pick would win forever. For example, after the weight is fixed in MacroFactor the food would keep its proportions pick, or the other way round. With the record, only the affected foods are re-picked, not all 187. *Claude's call.* |
| The custom-foods skill's imports | `logged_macros_per_100g`, `LoggedMacros`, `macro_distance` and `GOOD_DISTANCE` keep their current shape and meaning. The app uses a new function for its own matching. | `recipe_tool.py` imports all four. Kept as they are, the skill keeps working unchanged and keeps catching impossible macros itself, so no skill change has to ship with this one. *Claude's call.* |
| Placeholder entries of unresolved foods | Listed as "weight not estimated"; skipped foods (Quick Add included) are not listed | A skipped food is outside coverage on both sides, so its grams change nothing. A not-in-USDA, lookup-failed or untranslated food has no match to fit, and its invented 100 g goes into its "% of food weight". The report should say so. *Claude's call.* |
| Calories in the warnings | Only the low-confidence line gets a calorie share | That's the warning that hid these meals. The other warning lines (not in USDA, lookup failed, untranslated) keep their weight share. With estimated grams, weight shares become truthful again. *Claude's call.* |

---

## 2. Detection

The parser adds one field to `FoodEntry`:

| Field | Meaning |
| --- | --- |
| `weight_is_placeholder: bool` | `serving_weight_g == 100` and `protein_g + fat_g + carbs_g > 1.05 × total_weight_g` |

`total_weight_g` stays as logged (`serving_qty × serving_weight_g`). The parser does not guess grams, because it has no food to fit against yet.

The rule depends on nothing else: not the unit text, not the quantity, not the food name. Quick Add entries are kept by the parser, which only logs a warning. They are usually 1 × 100 g with impossible macros, so the flag fires on them too. That is harmless: "Quick Add" is mapped to `skip`, and §4 leaves skipped foods alone.

---

## 3. Matching

### 3.1 The fit

For logged macros `L` (protein, fat, carbs, in grams for the entry or food) and a food's per-100 g macros `U`, the fitted grams are found by least squares, weighting each macro by its kcal per gram (`w = 4, 9, 4`):

```
grams = 100 × Σ w²·L·U / Σ w²·U²
```

- **Floor:** the result is never below the logged protein + fat + carbs. Food can't weigh less than its macros. A wrong match can fit below that: the doner against chicken skin fits 104 g for 193 g of macros.
- **Nothing to fit:** when a macro of `U` is missing (`None`), or `Σ w²·U²` is 0, there is no fit. See §4 for what happens then.
- **Distance:** the candidate's distance is the existing `macro_distance`, computed with the logged macros divided by the fitted grams. The carbs-with-or-without-fiber rule and `KCAL_FLOOR` stay as they are.

The fit is a pure function in `matcher.py`, with no I/O.

### 3.2 Logged macros of a food

`logged_macros_per_100g` and `LoggedMacros` stay exactly as they are, because the custom-foods skill's `recipe_tool.py` imports them. The app's own matching uses a new function, which differs from it in two ways:

- **Mixed food:** when a food has some entries with a real weight and some with a placeholder, only the real-weight entries make its per-100 g figures. The placeholder entries get their grams in §4.
- **All placeholders:** when every entry of a food is a placeholder, the food's logged macros carry a "weight unknown" marker with the summed macros of those entries. They are not per-100 g figures. How to represent this is the builder's choice, for example a flag on `LoggedMacros` or a separate type. It must be impossible to read the sums as per-100 g figures by accident.

### 3.3 Picking

`pick_best` keeps the head-word filter, the identity penalty, the near-tie band and the SR Legacy preference unchanged. Only for a weight-unknown food:

- each candidate's macro distance is computed after fitting the grams (§3.1);
- the pick is `good` when the distance is at or below `GOOD_FIT_DISTANCE` (0.15), otherwise `weak`.

For a custom food whose logged weight is unknown, `_custom_food_match` computes its `macro_distance` the same way, against `recipe_macros`.

Fits against today's matches (the matches themselves may change under the new scoring):

| Food | Against | Fitted grams | Distance | Verdict at 0.15 |
| --- | --- | --- | --- | --- |
| King's Steakhouse Double | cheeseburger, double | 335 g | 0.04 | good |
| Ben's rice with chickpeas | rice, white, raw | 143 g | 0.22 | weak: needs a recipe |
| Chicken and Rice in Cream Sauce | chicken tenders, breaded | 456 g | 0.19 | weak: needs a recipe |
| Chicken and Rice in Cream Sauce | chicken breast, cooked | 291 g | 0.78 | weak |
| Custom Chicken Doner | chicken skin, roasted | 104 → 193 g (floor) | 0.60 | weak |

### 3.4 Cache

A matched food's cache entry gets a new field that records how the pick was made: by proportions (weight unknown) or by per-100 g figures. An entry without the field counts as made by per-100 g figures, which is true of every entry today. `get_food_match` reuses an entry only when the field agrees with the food in this run, as well as the existing `usda_query` and `match_version` checks. Otherwise it re-picks, as it does for an outdated version: the entry is kept as `unverified` if the re-pick fails, and the new pick is written with `upsert`, so fields the app doesn't write (`reviewed_fdc_id`) survive.

- Not-found records don't get the field. Whether USDA has a food doesn't depend on its weight.
- `MATCH_VERSION` stays at 1. Older code on another device ignores the field and reuses the entry. Current code never re-picks an entry whose field already agrees. So two devices can't keep rewriting each other's picks.

---

## 4. Estimating Each Entry's Grams

After enrichment and before analysis, each placeholder entry gets its grams fitted against its own food's match:

| The food is… | Fit against |
| --- | --- |
| In `profiles` (USDA search) | That match's per-100 g macros (`usda_macros` in the cache entry) |
| In `custom` | `recipe_macros` of its recipe |
| Skipped | Nothing: keeps the logged weight, not reported (outside coverage) |
| Not in USDA, lookup failed, untranslated | Nothing: keeps the logged weight, reported as "weight not estimated" |

Each entry is fitted on its own macros, not the food's sums, so two different servings of one food get their own grams.

- **Successful fit:** the entry's `total_weight_g` becomes the estimate, and the logged figure is kept beside it (for example `logged_weight_g`), with a marker that the weight was estimated.
- **No fit possible:** a missing macro, or a legacy cache entry without `usda_macros` (kept as `unverified`). The entry keeps its logged weight and is reported as "weight not estimated".

The analyzer then needs no special case. Nutrients, coverage, the weight shares, the low-confidence weight, the custom-recipe grams and the logged-fiber bookkeeping all read `total_weight_g`.

To fit against a USDA match, the enrichment result must expose each food's matched macros. Today `FoodMatch` and `profiles` carry only the micronutrients, and `usda_macros` sits in the cache entry unread. The builder chooses where they go, for example a new `EnrichmentResult` field keyed by food name. `profiles` must keep meaning "micronutrients per 100 g" and nothing else.

The step is called from `cmd_analyze` between `enrich_all_foods` and `analyze`, after the BAR-43 stop. The builder chooses its module; a pure function in `matcher.py` fits the existing split.

---

## 5. Report

- **New coverage data:** one row per food with estimated weights. Each row has the food name, its entries' logged and estimated grams, the fit's macro distance, and whether any entry could not be estimated.
- **Terminal:** a dim line after the low-confidence line, for example `Estimated weights (logged as 100 g, impossible for their macros): King's Steakhouse Double 100 g → 335 g (×2), Ben's … 100 g → 143 g (×2) ⚠ …`. Use ⚠ when the fit distance is above `GOOD_FIT_DISTANCE`, like the custom-foods line does for `GOOD_DISTANCE`. In the Custom foods line and section, a custom food whose weight is unknown also gets ⚠ above `GOOD_FIT_DISTANCE`, not `GOOD_DISTANCE`, so both places agree.
- **HTML:** the same content, in the style of the existing Custom foods section.
- **Low-confidence line:** gains the calorie share next to the weight share, for example `(4% of analysed weight, 16% of calories)`. This means a new coverage field, `low_confidence_kcal_pct`, computed in `analyzer.py` from `calories_kcal` of the analysed entries.

The exact wording and layout are the builder's call, within the existing style of the report.

---

## 6. Out of Scope

- Splitting a mixed dish into ingredients automatically.
- Detecting a placeholder weight that is physically possible (the Double Cheeseburger).
- EU/DGE reference targets.
- **The custom-foods skill.** Its `SKILL.md` and `recipe_tool.py` (a separate repo, installed into `.claude/skills/`) tell the reader that impossible macros stay flagged until the weight is fixed in MacroFactor. That guidance becomes outdated and should be updated afterwards. The comment in the user's `custom_foods.yaml` (stored in S3, not in git) also becomes outdated.
- Re-reading MacroFactor's other micronutrient columns. Only fiber comes from the export, as today.

---

## 7. Verification

**Tests** (no live USDA or Gemini calls):

- **Parser:**
  - the rule fires for 1 × 100 g with 163 g of macros, and for 1.1 × 100 g with 193 g;
  - it doesn't fire for a real 100 g serving (Basmatireis: 3.56 kcal/g, 88 g of macros);
  - it doesn't fire for the Double Cheeseburger (81 g);
  - it doesn't fire for 100 g with exactly 104 g of macros (the margin);
  - it doesn't fire for a 5 g entry logged with 5 g of fat (not 100 g).
- **Fit:**
  - King's against cheeseburger, double gives 335 g;
  - the floor applies;
  - a missing macro gives no fit;
  - with a fitted weight, the distance uses the fitted grams.
- **Matching:**
  - a weight-unknown food picks by proportions: a candidate with the right proportions beats a denser candidate that fits the wrong per-100 g figures;
  - `GOOD_FIT_DISTANCE` decides good or weak;
  - a known-weight food scores exactly as before;
  - a mixed food uses only its real-weight entries.
- **Estimation:**
  - a USDA match, a custom recipe and an unresolved food each behave as in the §4 table;
  - two servings of one food get separate grams;
  - an `unverified` entry without macros keeps its weight and is reported.
- **Analyzer:**
  - nutrients scale with the estimated grams;
  - `low_confidence_kcal_pct` is computed.
- **Reporter:** both renderers show the new line or section, and show nothing when no weight was estimated.
- **Cache:**
  - an entry picked by per-100 g figures (or without the new field) is re-picked once its food has an unknown weight, and the other way round;
  - an entry whose field agrees is reused;
  - a failed re-pick keeps the old entry as `unverified`;
  - a re-pick keeps `reviewed_fdc_id`.
- **Skill compatibility:** `logged_macros_per_100g` returns the same figures as before for placeholder entries.

**On the real export** (`MacroFactor-20261021145424.xlsx`):

- Oct 21 is left out (already done on `alozay/partial-day-threshold`).
- King's comes out at about 335 g and good.
- Chicken and Rice and Ben's rice get estimates but stay weak and listed.
- The Double Cheeseburger stays at 100 g and low-confidence.
- Vitamin A and potassium rise.

A real `analyze` with S3 configured saves the re-picked database to S3. Ask the user before running it.

**Docs:** in `CLAUDE.md`, describe the new parser field, the matcher's fitted matching and `GOOD_FIT_DISTANCE`, the estimation step in the data flow, the new report line and the cache field with its re-pick rule (in Key invariants).
