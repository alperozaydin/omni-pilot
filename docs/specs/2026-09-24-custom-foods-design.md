# Technical Specification: Custom Foods from Fixed USDA Recipes (BAR-75)

**Document Version:** 1.0
**Date:** 2026-09-24
**Status:** In Review
**Linear Issue:** [BAR-75](https://linear.app/knaak/issue/BAR-75/more-deterministic-approach-for-food-query)
**Builds on:** BAR-72 (macro-validated USDA candidate matching), whose branch this work is based on

---

## 1. Executive Summary & Objective

### The Problem

BAR-72 made the enricher choose among USDA search candidates by comparing their macros with the logged ones. That works when USDA has an entry for the food. It cannot work for **mixed dishes**, because USDA has no entry for them: after the main-word filter, the only "salad" foods left are dressings and fast-food items, and the closest of those wins. BAR-72 §1 names this failure class and §9 defers it.

Current state of the shared production DB:

| Food | Logged per 100 g | USDA match today |
| --- | --- | --- |
| Misch Salat Rohkost | 19 kcal, P 1.2, F 0.4, C 1.6 | *Salad dressing, sweet and sour* (off 33%) |
| Salat Caprese | 74 kcal, P 4.9, F 4.2, C 3.0 | *McDONALD'S, Side Salad* (off 71%) |
| Salzlakenkaese salat | — | *Cheese, feta* (off 396%) |
| Salat Manhattan | — | *Babyfood, vegetables, garden vegetable, strained* |
| Rewe to Go Hirtensalat | — | *Snacks, granola bars, QUAKER OATMEAL TO GO* |
| Mixed Green Salad with Walnuts and Chicken | — | *Salad dressing, green goddess, regular* |

A dressing has almost no vitamin K, vitamin A or folate, so a leafy salad matched to one quietly under-reports exactly what the salad contributes. The weak flag from BAR-72 shows the problem, but the food is still counted with the wrong nutrients.

### The Solution

The user defines **custom foods** in a YAML file. Each one is a **recipe**: a list of USDA ingredients, each fixed by its FoodData Central ID (`fdc_id`), with a relative amount by weight. Each recipe also lists the logged food names it stands for.

- A food listed in a recipe **bypasses Gemini translation and USDA search entirely**.
- Each ingredient is fetched from USDA **by ID**, once, and cached permanently. No search or ranking is involved, so the result is deterministic.
- The food's nutrients are the weight-share-weighted combination of its ingredients' nutrients.
- The recipe's combined macros are compared with the logged macros using BAR-72's `macro_distance`, so a badly proportioned recipe is flagged.
- The HTML report ends with a **Custom foods** section listing each recipe, its ingredients and the foods that used it. The terminal report gets a one-line summary.

### Decisions Made During Design

- **Recipes of USDA ingredients, not hand-entered nutrient values.** The user writes no nutrient numbers. Every value comes from assayed USDA data, and "not measured" stays distinct from 0 without any effort from the user.
- **Shares are set manually.** The app never splits a dish or fits shares automatically. The macro check is the feedback: it reports how far a recipe is from the logged macros but does not suggest a correction.
- **Ingredient IDs are looked up by hand** on the FoodData Central website (or with an AI assistant) and pasted into the file. The app does not search for ingredients by name, since that would reintroduce the ranking problem this change removes.
- **A separate YAML file** (`custom_foods.yaml`), not JSON: IDs are unreadable on their own, and YAML allows a comment naming each one. It matches the other config files.
- **The file is human-owned.** The app only reads it. It is not mirrored into TinyDB (unlike `food_mappings.yaml`), so the app never rewrites it.
- **The real file is not committed.** `.gitignore` already ignores `config/*.yaml` except `config/*.example.yaml`. The repo ships `config/custom_foods.example.yaml`, and the real file lives in iCloud via a new `custom_foods_path` setting.
- **The first real `custom_foods.yaml` is written after this code exists** (by the user with an AI assistant). It is not part of this change.

---

## 2. Architecture Overview

```
custom_foods_path ─► custom_foods.load_custom_foods ─► CustomFoods ──────────────┐
                                                        │                         │
food_names ─► (minus custom foods) ─► translator.resolve_and_sync_mappings ─► mappings
                                                                                  │
food_names, mappings, logged_macros, CustomFoods ─► enricher.enrich_all_foods ◄───┘
      ├─ custom food:  enricher.get_usda_food(fdc_id)  (table "usda_foods", fetch by ID)
      │                 custom_foods.combine ─► parts + recipe macros ─► matcher.macro_distance
      └─ other food:   unchanged BAR-72 path
                                   │
                                   ▼
            EnrichmentResult (+ custom) ─► analyzer ─► reporter (+ Custom foods section)
```

| Unit | Responsibility | I/O |
| --- | --- | --- |
| `custom_foods.py` (new) | load and validate the YAML; compute weight shares and recipe macros | reads the YAML file only |
| `enricher.py` | fetch a USDA food by ID, cache it, build each custom food's match | HTTP, TinyDB |
| `matcher.py` | `macro_distance` accepts any macro set, not just a `Candidate` | none |
| `analyzer.py` | count a custom food ingredient by ingredient; report custom food use | none |
| `reporter.py` | Custom foods section (HTML) and summary line (terminal) | output |
| `cli.py` | load custom foods, keep them away from translation, pass them on | orchestration |

---

## 3. The `custom_foods.yaml` File

### 3.1 Format

```yaml
# Custom foods: fixed recipes of USDA ingredients, used instead of a USDA search.
# Amounts are relative weights (grams of a typical portion or percentages);
# the app scales them so they add up to 100%.

green_salad:
  foods: [Misch Salat Rohkost, Salat Manhattan]
  ingredients:
    - {fdc_id: 169248, amount: 60}   # lettuce, green leaf, raw
    - {fdc_id: 170393, amount: 20}   # carrots, raw
    - {fdc_id: 168409, amount: 20}   # cucumber, with peel, raw

caprese:
  foods: [Salat Caprese]
  ingredients:
    - {fdc_id: 170457, amount: 55}   # tomatoes, red, ripe, raw
    - {fdc_id: 170845, amount: 18}   # cheese, mozzarella, whole milk
    - {fdc_id: 169248, amount: 26}   # lettuce, green leaf, raw
    - {fdc_id: 171413, amount: 1}    # oil, olive, salad or cooking
```

- The top level maps a **recipe name** to a recipe. The name is only a label for the report.
- `foods`: the exact logged food names (as they appear in the MacroFactor export) that this recipe stands for.
- `ingredients`: each has an `fdc_id` (a USDA FoodData Central ID) and an `amount` (a relative weight).
- An ingredient's **share** is `amount / sum of the recipe's amounts`. Amounts don't need to add up to 100.
- The same `fdc_id` may appear in several recipes.

### 3.2 Loading: `load_custom_foods(path) -> CustomFoods`

```python
class Ingredient(TypedDict):
    fdc_id: int
    share: float                      # amount / sum of amounts; the recipe's shares sum to 1

class Recipe(TypedDict):
    name: str
    foods: list[str]
    ingredients: list[Ingredient]

class CustomFoods(TypedDict):
    recipes: dict[str, Recipe]        # recipe name -> recipe
    by_food: dict[str, str]           # logged food name -> recipe name
```

- A missing file, an empty file, or a file containing only comments gives no recipes. The feature is simply off.
- Anything else that is invalid raises `CustomFoodsError` (a `ValueError`). Its message names the recipe and the problem. The CLI prints it and exits with status 1, the same way it handles a missing USDA key. A broken recipe must not silently fall back to a USDA search.

A file is invalid when any of these hold:
- the top level is not a mapping;
- a recipe is not a mapping, or has a key other than `foods` and `ingredients` (this catches typos such as `ingredient:`);
- `foods` is missing, empty, or contains something other than non-empty strings;
- `ingredients` is missing or empty;
- an ingredient has a key other than `fdc_id` and `amount`, or lacks either one;
- an `fdc_id` is not a positive integer (a YAML boolean is rejected even though Python treats it as an int);
- an `amount` is not a positive number;
- the same `fdc_id` appears twice in one recipe (almost certainly a copy mistake);
- the same food name appears in two recipes, whether in the same recipe or in different ones.

### 3.3 Settings and file location

- New setting `custom_foods_path`, resolved with `config.resolve_path` like `mappings_path`. The default is `config/custom_foods.yaml`.
- `config/settings.example.yaml` gets the key, plus a commented iCloud example next to the existing ones.
- The CLI prints the resolved path alongside the database and mappings paths.
- `config/custom_foods.example.yaml` is committed. It contains the format comment and an active example recipe whose IDs are **verified against USDA while it is written**. A test loads it, so it can't drift into an invalid state.

---

## 4. Resolution Order and the Translator

A food is resolved in this order:

1. **Listed in a custom recipe** → the custom food. This takes precedence over everything in `food_mappings.yaml`, including `skip`.
2. **Mapped to `skip`** → skipped (unchanged).
3. **Otherwise** → the BAR-72 USDA search path (unchanged).

The translator itself is not changed. The CLI passes `resolve_and_sync_mappings` only the food names **not** claimed by a recipe:
- Gemini is never called for a custom food.
- Any existing translation of a custom food stays in the TinyDB `translations` table and in `food_mappings.yaml` (`generate_food_mappings` already keeps entries for foods outside the list it's given). It is simply unused while a recipe claims the food. If the food is later removed from the recipe, its old translation takes over again, with no Gemini call.

---

## 5. `enricher.py` Changes

### 5.1 Fetching a USDA food by ID

`fetch_usda_food(fdc_id, api_key) -> dict | None`

- `GET https://api.nal.usda.gov/fdc/v1/food/{fdc_id}?format=abridged`.
- The abridged response has `fdcId`, `description`, `dataType` and `foodNutrients`, where each nutrient is `{"number": "301", "amount": 25.0, ...}`. This was verified against the live API during design.
- It keeps the existing single 429 retry.
- Any failure returns `None`: an HTTP 404 (no such ID), a network error, or another HTTP error. A 404 logs an error naming the ID, because it means the YAML is wrong. Other failures log that the fetch failed.

The abridged nutrients use different keys from the search endpoint's (`nutrientNumber` / `value`). They are converted once to the search shape (`{"nutrientNumber": number, "value": amount}`), so the existing `extract_micros_from_usda` and `_nutrient_value` are reused unchanged.

### 5.2 Ingredient cache: `get_usda_food(fdc_id, db, api_key) -> UsdaFood | None`

```python
class UsdaFood(TypedDict):
    fdc_id: int
    usda_name: str
    usda_dataset: str
    per_100g: dict[str, float | None]
    usda_macros: dict[str, float | None]   # protein_g, fat_g, carbs_g, fiber_g
```

- The cache is a new TinyDB table, **`usda_foods`**, keyed by `fdc_id`. It is separate from the default table, which is keyed by logged food name.
- An entry holds the `UsdaFood` fields plus `last_updated`.
- **A cached entry is always reused and never invalidated.** A USDA ID always names the same food, and SR Legacy data has been frozen since 2018. Changing a recipe's amounts never causes a fetch. Adding an ingredient ID fetches only that ingredient, once. An ID shared by several recipes is fetched and stored once.
- On a fetch failure nothing is cached and `None` is returned, so the next run tries again.
- The database lives in iCloud, so a fetch on the Mac also serves the iPhone.

### 5.3 Building a custom food match

For each food claimed by a recipe, `enrich_all_foods` calls `get_usda_food` for every ingredient of its recipe. Each ingredient is fetched at most once per run, even when several foods share the recipe.

- **If any ingredient is unavailable,** the food goes into `unresolved`, and an error is logged naming the recipe and the ID. Silently dropping the ingredient would under-report the food, which is the kind of quiet error this change exists to remove.
- **Otherwise** the food goes into the new `custom` part of the result:

```python
class RecipePart(TypedDict):
    share: float
    fdc_id: int
    usda_name: str
    per_100g: dict[str, float | None]

class CustomFoodMatch(TypedDict):
    recipe: str
    parts: list[RecipePart]
    macro_distance: float | None

class EnrichmentResult(TypedDict):
    profiles: ...                          # unchanged; custom foods are NOT in it
    skipped: ...
    unresolved: ...
    low_confidence: ...                    # unchanged; custom foods are never in it
    custom: dict[str, CustomFoodMatch]     # food name -> its recipe match
```

Custom foods are kept out of `profiles` on purpose: a single combined profile could not record that one ingredient lacks a nutrient while the others have it (§6).

### 5.4 Macro check

`custom_foods.recipe_macros(ingredients: list[tuple[float, UsdaMacros]]) -> UsdaMacros` takes `(share, macros)` pairs. For each of protein, fat, carbs and fiber, the share-weighted sum over ingredients, or `None` if any ingredient lacks that macro.

The food's `macro_distance` is `matcher.macro_distance(logged_macros[food], recipe_macros)`. It is `None` when the food has no logged macros (zero logged weight) or when the recipe lacks protein, fat or carbs. The check is **per food, not per recipe**: two foods sharing `green_salad` have different logged macros.

To allow this, `matcher` gains a `UsdaMacros` TypedDict (`protein_g`, `fat_g`, `carbs_g`, `fiber_g`), and `macro_distance` takes that instead of `Candidate`. `Candidate` already has these keys, so BAR-72's callers are unaffected.

A custom food whose distance exceeds `matcher.GOOD_DISTANCE` (0.25) is still counted in full. It is only flagged in the report (§7).

---

## 6. `analyzer.py` Changes

### 6.1 Counting a custom food ingredient by ingredient

Today each analysed entry has one profile. After this change, each entry has a list of **parts**, each a `(share, per_100g)` pair:
- a normal food has one part, `(1.0, profile)`;
- a custom food has one part per ingredient.

The existing per-nutrient loop runs **per part**, with the part's weight being `total_weight_g × share`:
- If the part has a value for the nutrient (every component, for a combined nutrient), the part's weight is booked as measured and its contribution added.
- If not, the part's weight is booked as unmeasured.

The effect is that an ingredient missing a nutrient reduces coverage by exactly its own share of the weight, and the other ingredients still count. There is no zero-filling, so `coverage_pct` and the `*` floor marker stay correct. The EPA/DHA gram-to-milligram conversion stays where it is, applied per part.

An entry is analysed, and counted in `mapped_entries` and in the analysed weight, when its food is in `profiles` **or** in `custom`. Otherwise it is skipped or unresolved, as today.

### 6.2 Reporting custom food use

`CoverageResult` gains:

```python
class CustomFoodUse(TypedDict):
    name: str                         # logged food name
    grams: float                      # total logged weight in the period
    macro_distance: float | None

class CustomRecipeUse(TypedDict):
    recipe: str
    ingredients: list[dict]           # {"fdc_id", "usda_name", "share_pct"} in YAML order
    foods: list[CustomFoodUse]        # sorted by grams, descending

custom_recipes: list[CustomRecipeUse]   # sorted by total grams, descending
```

- Only foods with logged weight above 0 in the period appear, the same rule as `low_confidence_foods`. A recipe with no such food is left out.
- `share_pct` is the share × 100.

---

## 7. `reporter.py` Changes

### 7.1 HTML

A **Custom foods** section after the warnings block, at the bottom of the report, rendered only when `custom_recipes` is non-empty. For each recipe:
- the recipe name;
- its ingredients, each as `USDA name (FDC ID) — N%`;
- the foods that used it, each as `name — G g — macros off N%`. The macro part is instead:
  - `⚠ macros off N%` when the distance exceeds `GOOD_DISTANCE`;
  - `macros not checked` when the distance is `None`.

### 7.2 Terminal

One line after the existing warnings, printed only when `custom_recipes` is non-empty. It is a Rich `Text` object, because names may contain `[...]`:

```
  Custom foods: Misch Salat Rohkost → green_salad (off 12%), Salat Caprese → caprese (⚠ off 40%)
```

A `None` distance shows as `(not checked)`. The line is dim when no food is flagged and yellow when at least one is.

---

## 8. CLI

In `cmd_analyze`:

```python
custom_foods_path = resolve_path("custom_foods_path", DEFAULT_CUSTOM_FOODS, settings)
print(f"Custom foods: {custom_foods_path}")
try:
    custom_foods = load_custom_foods(custom_foods_path)
except CustomFoodsError as e:
    print(f"Error in {custom_foods_path}: {e}")
    sys.exit(1)
...
translatable = [f for f in food_names if f not in custom_foods["by_food"]]
mappings = resolve_and_sync_mappings(translatable, db_path, mappings_path, settings)
...
enrichment = enrich_all_foods(food_names, mappings, logged_macros, custom_foods, db, api_key)
resolved = len(enrichment["profiles"]) + len(enrichment["custom"])
print(f"  {resolved}/{len(food_names)} foods resolved.")
```

`count_outdated_matches` gets only the non-custom foods, so custom foods are never counted as needing a re-match.

---

## 9. Testing

All USDA HTTP calls are mocked, and no test touches Gemini.

**`tests/test_custom_foods.py`** (new):
- Loading:
  - a valid file gives recipes, shares that sum to 1, and a `by_food` index;
  - relative amounts scale correctly (e.g. 150/50 gives 0.75/0.25);
  - a missing, empty or comment-only file gives no recipes;
  - `config/custom_foods.example.yaml` loads without error.
- Each rejection in §3.2 raises `CustomFoodsError` naming the recipe: an unknown key, empty `foods` or `ingredients`, a non-positive or boolean `fdc_id`, a non-positive amount, a duplicate `fdc_id` within a recipe, and a food claimed twice.
- `recipe_macros`: the share-weighted sum; a missing macro in any ingredient gives `None` for that macro.

**`tests/test_enricher.py`** (extended):
- `fetch_usda_food`:
  - requests the abridged endpoint;
  - converts nutrients to the search shape;
  - a 404 or a network error gives `None`;
  - a 429 retries once.
- `get_usda_food`:
  - a cache hit makes no request;
  - a fetch is cached in `usda_foods`;
  - a failure caches nothing.
- `enrich_all_foods`:
  - a custom food goes into `custom` and not into `profiles`, and USDA search is never called for it;
  - a recipe beats a `skip` mapping and a normal mapping;
  - a shared ingredient is fetched once;
  - any unavailable ingredient makes the food unresolved;
  - `macro_distance` is computed per food, and is `None` without logged macros;
  - a custom food is never in `low_confidence`.
- `count_outdated_matches` is unaffected by custom foods.

**`tests/test_matcher.py`**: `macro_distance` accepts a plain `UsdaMacros` dict.

**`tests/test_analyzer.py`**:
- A custom food's nutrients are the share-weighted sum of its parts.
- An ingredient missing a nutrient books only its share of the weight as unmeasured. For example, a 25% ingredient without vitamin K gives 75% coverage for a food eaten on its own, and the floor marker applies to a low verdict.
- Combined nutrients (methionine + cysteine, EPA + DHA) are evaluated per part, with the EPA/DHA mg conversion.
- Custom entries count as mapped and toward analysed weight.
- `custom_recipes` groups foods by recipe, sorts by grams, leaves out zero-weight foods, and gives `share_pct` in YAML order.

**`tests/test_reporter.py`**:
- The HTML Custom foods section renders only when non-empty.
- The HTML shows `⚠` above `GOOD_DISTANCE` and `macros not checked` for `None`.
- The terminal line prints names containing `[...]` literally.

**`tests/test_cli.py`**:
- Custom foods are excluded from `resolve_and_sync_mappings` and passed to `enrich_all_foods`.
- An invalid YAML file exits with status 1 and a message naming the file.
- A missing file runs normally.

`tests/helpers.enrichment` gains a `custom` keyword.

---

## 10. Out of Scope

- **Writing the real `custom_foods.yaml`.** The user does this with an AI assistant once the code exists.
- **An ingredient lookup command** (e.g. `omni-pilot find "mozzarella"`). It can be added if looking up IDs by hand gets tedious.
- **Fitting shares automatically** to the logged macros, or **suggesting corrections** to a recipe that is flagged.
- **Gemini splitting mixed dishes into ingredients.**
- **Pinning a single USDA ID for a non-custom food** (BAR-72 §9). A one-ingredient recipe already does this, at the cost of listing the food in `custom_foods.yaml`.
- **Cleaning up** the old default-table cache entries of foods now claimed by a recipe. They are unused and harmless, and they become current again if the food is removed from its recipe.

---

## 11. Documentation

`CLAUDE.md` is updated:
- `custom_foods.py` is added to the architecture list. The translator and enricher bullets mention that recipe foods bypass translation and search, and the reporter bullet mentions the Custom foods section.
- The data flow diagram shows `custom_foods.yaml` feeding the CLI's translation filter and `enrich_all_foods`.
- New key invariants:
  - a custom recipe takes precedence over any mapping, including `skip`;
  - the `usda_foods` table is keyed by `fdc_id` and never invalidated;
  - custom foods are never in `profiles`: the analyzer counts them part by part so that a missing nutrient in one ingredient costs only that ingredient's share of coverage.
- The config setup note lists `custom_foods.yaml` as optional, created from `custom_foods.example.yaml`.
