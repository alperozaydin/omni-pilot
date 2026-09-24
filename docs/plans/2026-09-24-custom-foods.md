# Custom Foods from Fixed USDA Recipes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the user define mixed dishes (salads) as fixed recipes of USDA ingredients in `custom_foods.yaml`, used instead of USDA search, with a macro check and a Custom foods section in the report.

**Architecture:** A new pure module `custom_foods.py` loads and validates the YAML and combines macros. The enricher fetches each ingredient from USDA by FDC ID (cached forever in a new TinyDB table `usda_foods`) and returns custom foods in a new `EnrichmentResult["custom"]` part. The analyzer counts a custom food ingredient by ingredient, so coverage stays honest. The reporter renders the section, and the CLI keeps recipe foods away from translation.

**Tech Stack:** Python 3.14, PyYAML, TinyDB, requests, Rich, Jinja2, pytest + pytest-mock. Run everything with `uv run`.

**Spec:** `docs/specs/2026-09-24-custom-foods-design.md` (v1.1). Read it before starting; this plan argues from it.

## Global Constraints

- **Pure-Python dependencies only.** No new dependencies at all; the app must run unchanged on iPhone (a-Shell).
- **No real network in tests.** Every USDA HTTP call is mocked, and no test sends anything to Gemini.
- **Lint.** `uv run ruff check .` must pass (line length 120, rules E/F/I/UP).
- **"Not measured" is `None`, never `0.0`.** Never add a `.get(key, 0.0)` default to the analyzer's nutrient summing.
- **Units.** `omega3_epa_mg` and `omega3_dha_mg` are stored in grams (as USDA gives them) and converted to mg only in the analyzer.
- **The real `custom_foods.yaml` is never committed.** `.gitignore` already covers `config/*.yaml` except `config/*.example.yaml`. Only `config/custom_foods.example.yaml` is added.
- **Commit messages** are a single concise summary line ending in `(BAR-75)`.
- **Branch.** All work happens on `alozay/bar-75-more-deterministic-approach-for-food-query`, which is based on the BAR-72 branch. The full suite (`uv run pytest`) passes at the start: 159 tests.

## Review Focus

These are the likeliest problems a real user will hit that the spec doesn't spell out. Each one has a test added in the task named.

1. **A YAML syntax error** (bad indentation, an unclosed bracket) must stop the run with `Error in <path>: not valid YAML …`, not a Python traceback. *Task 1*
2. **`amount: .nan` or `.inf`** (both valid YAML floats) must be rejected. Otherwise every share becomes NaN and silently poisons all nutrient totals. *Task 1*
3. **An ingredient ID USDA can't serve, shared by two logged foods,** must make both foods unresolved, with the failing ID requested only once per run. *Task 3*
4. **An abridged USDA nutrient listed without an `amount`** must become `None` (not measured), never `0.0`. *Task 2*
5. **A food newly claimed by a recipe** still has an old default-table cache entry with no `match_version`. It must not trigger the "Re-matching N cached foods" notice. *Task 6*

---

## File Structure

| File | Change | Responsibility |
| --- | --- | --- |
| `src/omni_pilot/matcher.py` | modify | `UsdaMacros` type; `macro_distance` accepts it |
| `src/omni_pilot/custom_foods.py` | create | load and validate `custom_foods.yaml`; `recipe_macros` |
| `config/custom_foods.example.yaml` | create | committed template with verified IDs |
| `src/omni_pilot/enricher.py` | modify | fetch by ID, `usda_foods` cache, custom food matches |
| `src/omni_pilot/analyzer.py` | modify | count per part; `coverage.custom_recipes` |
| `src/omni_pilot/reporter.py` | modify | terminal line and HTML Custom foods section |
| `src/omni_pilot/cli.py` | modify | load recipes, filter translation, pass `custom_foods` |
| `config/settings.example.yaml`, `Makefile`, `CLAUDE.md` | modify | setting, sync exclude, documentation |
| `tests/test_custom_foods.py` | create | loader, validation and `recipe_macros` tests |
| `tests/helpers.py`, `tests/test_{matcher,enricher,analyzer,reporter,cli}.py` | modify | new tests and fixture keys |
| `tests/test_custom_foods_integration.py` | create | end-to-end run of the real CLI with only the network faked |

---

### Task 1: Recipe loading and validation (`custom_foods.py`)

**Files:**
- Modify: `src/omni_pilot/matcher.py`, around lines 39–66 (types) and 160–177 (`macro_distance`)
- Create: `src/omni_pilot/custom_foods.py`
- Create: `config/custom_foods.example.yaml`
- Create: `tests/test_custom_foods.py`
- Modify: `tests/test_matcher.py`

**Interfaces:**
- Consumes: nothing new.
- Produces:
  - `matcher.UsdaMacros`: a TypedDict with `protein_g`, `fat_g`, `carbs_g` and `fiber_g`, each `float | None`.
  - `matcher.macro_distance(logged: LoggedMacros, macros: UsdaMacros) -> float | None`.
  - `custom_foods.CustomFoodsError(ValueError)`.
  - `custom_foods.Ingredient`: `{fdc_id: int, share: float}`.
  - `custom_foods.Recipe`: `{name: str, foods: list[str], ingredients: list[Ingredient]}`.
  - `custom_foods.CustomFoods`: `{recipes: dict[str, Recipe], by_food: dict[str, str]}`.
  - `custom_foods.no_custom_foods() -> CustomFoods`.
  - `custom_foods.load_custom_foods(path: str) -> CustomFoods`.
  - `custom_foods.recipe_macros(ingredients: list[tuple[float, UsdaMacros]]) -> UsdaMacros`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_custom_foods.py`:

```python
"""Custom food recipe loading, validation and macro combination."""
from __future__ import annotations

import re
import textwrap

import pytest

from omni_pilot.custom_foods import CustomFoodsError, load_custom_foods, recipe_macros

EXAMPLE_PATH = "config/custom_foods.example.yaml"
ONE = "[{fdc_id: 1, amount: 1}]"


def _write(tmp_path, text: str) -> str:
    path = tmp_path / "custom_foods.yaml"
    path.write_text(textwrap.dedent(text))
    return str(path)


class TestLoadCustomFoods:
    def test_loads_recipes_with_shares_and_food_index(self, tmp_path):
        path = _write(tmp_path, """
            caprese:
              foods: [Salat Caprese]
              ingredients:
                - {fdc_id: 170457, amount: 150}
                - {fdc_id: 170845, amount: 50}
            green_salad:
              foods: [Misch Salat Rohkost, Salat Manhattan]
              ingredients:
                - {fdc_id: 169249, amount: 1}
                - {fdc_id: 170457, amount: 1}
        """)

        custom = load_custom_foods(path)

        # Relative amounts are scaled to shares that sum to 1
        assert custom["recipes"]["caprese"] == {
            "name": "caprese",
            "foods": ["Salat Caprese"],
            "ingredients": [{"fdc_id": 170457, "share": 0.75}, {"fdc_id": 170845, "share": 0.25}],
        }
        # The same fdc_id may appear in several recipes
        assert custom["recipes"]["green_salad"]["ingredients"] == [
            {"fdc_id": 169249, "share": 0.5}, {"fdc_id": 170457, "share": 0.5},
        ]
        assert custom["by_food"] == {
            "Salat Caprese": "caprese", "Misch Salat Rohkost": "green_salad", "Salat Manhattan": "green_salad",
        }

    @pytest.mark.parametrize("text", [None, "", "# only a comment\n"])
    def test_missing_empty_or_comment_only_file_has_no_recipes(self, tmp_path, text):
        path = str(tmp_path / "custom_foods.yaml") if text is None else _write(tmp_path, text)
        assert load_custom_foods(path) == {"recipes": {}, "by_food": {}}

    def test_example_file_is_valid(self):
        custom = load_custom_foods(EXAMPLE_PATH)
        assert set(custom["recipes"]) == {"green_salad", "caprese"}

    @pytest.mark.parametrize(("text", "message"), [
        ("caprese: {foods: [A", "not valid YAML"),
        ("- a\n- b\n", "the top level must map recipe names to recipes"),
        ("caprese: [1, 2]\n", "recipe 'caprese': must be a mapping with 'foods' and 'ingredients'"),
        (f"caprese: {{foods: [A], ingredient: {ONE}}}\n", "recipe 'caprese': unknown key(s) ingredient"),
        (f"caprese: {{ingredients: {ONE}}}\n", "recipe 'caprese': 'foods' must be a non-empty list of food names"),
        (f"caprese: {{foods: [], ingredients: {ONE}}}\n", "'foods' must be a non-empty list of food names"),
        (f"caprese: {{foods: ['  '], ingredients: {ONE}}}\n", "every entry in 'foods' must be a non-empty food name"),
        (f"caprese: {{foods: [42], ingredients: {ONE}}}\n", "every entry in 'foods' must be a non-empty food name"),
        ("caprese: {foods: [A], ingredients: []}\n", "recipe 'caprese': 'ingredients' must be a non-empty list"),
        ("caprese: {foods: [A], ingredients: [{fdc_id: 1}]}\n",
         "ingredient 1 must have exactly 'fdc_id' and 'amount'"),
        ("caprese: {foods: [A], ingredients: [{fdc_id: 1, amount: 1, name: x}]}\n",
         "ingredient 1 must have exactly 'fdc_id' and 'amount'"),
        ("caprese: {foods: [A], ingredients: [{fdc_id: 0, amount: 1}]}\n",
         "ingredient 1: fdc_id must be a positive whole number, got 0"),
        ("caprese: {foods: [A], ingredients: [{fdc_id: true, amount: 1}]}\n",
         "ingredient 1: fdc_id must be a positive whole number, got True"),
        ("caprese: {foods: [A], ingredients: [{fdc_id: '170457', amount: 1}]}\n",
         "ingredient 1: fdc_id must be a positive whole number, got '170457'"),
        ("caprese: {foods: [A], ingredients: [{fdc_id: 1, amount: -5}]}\n",
         "ingredient 1: amount must be a positive, finite number, got -5"),
        ("caprese: {foods: [A], ingredients: [{fdc_id: 1, amount: .nan}]}\n",
         "ingredient 1: amount must be a positive, finite number, got nan"),
        ("caprese: {foods: [A], ingredients: [{fdc_id: 1, amount: .inf}]}\n",
         "ingredient 1: amount must be a positive, finite number, got inf"),
        ("caprese: {foods: [A], ingredients: [{fdc_id: 1, amount: 1}, {fdc_id: 1, amount: 2}]}\n",
         "recipe 'caprese': fdc_id 1 is listed twice"),
        (f"a: {{foods: [X], ingredients: {ONE}}}\nb: {{foods: [X], ingredients: {ONE}}}\n",
         "recipe 'b': food 'X' is already listed in recipe 'a'"),
        (f"a: {{foods: [X, X], ingredients: {ONE}}}\n", "recipe 'a': food 'X' is already listed in recipe 'a'"),
    ])
    def test_rejects_invalid_file(self, tmp_path, text, message):
        path = _write(tmp_path, text)
        with pytest.raises(CustomFoodsError, match=re.escape(message)):
            load_custom_foods(path)


class TestRecipeMacros:
    TOMATO = {"protein_g": 0.88, "fat_g": 0.2, "carbs_g": 3.89, "fiber_g": 1.2}
    MOZZARELLA = {"protein_g": 22.2, "fat_g": 22.1, "carbs_g": 2.4, "fiber_g": 0.0}

    def test_share_weighted_sum(self):
        macros = recipe_macros([(0.75, self.TOMATO), (0.25, self.MOZZARELLA)])
        assert macros["protein_g"] == pytest.approx(6.21)
        assert macros["fat_g"] == pytest.approx(5.675)
        assert macros["carbs_g"] == pytest.approx(3.5175)
        assert macros["fiber_g"] == pytest.approx(0.9)

    def test_macro_missing_in_any_ingredient_is_none(self):
        mozzarella = {**self.MOZZARELLA, "fiber_g": None}
        macros = recipe_macros([(0.75, self.TOMATO), (0.25, mozzarella)])
        assert macros["fiber_g"] is None
        assert macros["protein_g"] == pytest.approx(6.21)
```

Append to `tests/test_matcher.py`. It documents that `macro_distance` takes plain macros, not only a `Candidate`, which is how Task 3 calls it:

```python
def test_macro_distance_accepts_plain_macros():
    macros = {"protein_g": 6.0, "fat_g": 5.0, "carbs_g": 3.0, "fiber_g": None}
    logged = {"kcal": 74.0, "protein_g": 4.9, "fat_g": 4.2, "carbs_g": 3.0}
    assert macro_distance(logged, macros) == pytest.approx((4 * 1.1 + 9 * 0.8) / 74.0)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_custom_foods.py -v`
Expected: collection error, `ModuleNotFoundError: No module named 'omni_pilot.custom_foods'`.

- [ ] **Step 3: Add `UsdaMacros` to `matcher.py`**

Add after `class Candidate(TypedDict)`:

```python
class UsdaMacros(TypedDict):
    """Per-100 g macros of a USDA food, or of a recipe built from USDA foods."""

    protein_g: float | None
    fat_g: float | None
    carbs_g: float | None
    fiber_g: float | None
```

Replace the signature and body of `macro_distance`. A `Candidate` has these four keys too, so `pick_best`'s call is unchanged:

```python
def macro_distance(logged: LoggedMacros, macros: UsdaMacros) -> float | None:
    """How far a food's macros are from the logged ones, relative to kcal.

    EU labels count carbs without fiber while USDA counts them with it, so the
    closer of the two readings is used. None when a macro is missing.
    """
    protein, fat, carbs = macros["protein_g"], macros["fat_g"], macros["carbs_g"]
    if protein is None or fat is None or carbs is None:
        return None
    carbs_delta = abs(logged["carbs_g"] - carbs)
    if macros["fiber_g"] is not None:
        carbs_delta = min(carbs_delta, abs(logged["carbs_g"] - (carbs - macros["fiber_g"])))
    weighted = (
        4 * abs(logged["protein_g"] - protein)
        + 9 * abs(logged["fat_g"] - fat)
        + 4 * carbs_delta
    )
    return weighted / max(logged["kcal"], KCAL_FLOOR)
```

- [ ] **Step 4: Create `src/omni_pilot/custom_foods.py`**

```python
"""Custom foods: fixed recipes of USDA ingredients, read from custom_foods.yaml.

The file is human-owned: the app only reads it. A food listed in a recipe is
never translated or searched for; its nutrients come from the recipe's
ingredients, each fixed by its USDA FoodData Central ID.
"""
from __future__ import annotations

import math
import os
from typing import TypedDict

import yaml

from omni_pilot.matcher import UsdaMacros

_RECIPE_KEYS = {"foods", "ingredients"}
_INGREDIENT_KEYS = {"fdc_id", "amount"}


class CustomFoodsError(ValueError):
    """custom_foods.yaml is malformed. The message names the recipe and the problem."""


class Ingredient(TypedDict):
    fdc_id: int
    share: float  # amount / sum of the recipe's amounts; a recipe's shares sum to 1


class Recipe(TypedDict):
    name: str
    foods: list[str]
    ingredients: list[Ingredient]


class CustomFoods(TypedDict):
    recipes: dict[str, Recipe]  # recipe name -> recipe
    by_food: dict[str, str]  # logged food name -> recipe name


def no_custom_foods() -> CustomFoods:
    return {"recipes": {}, "by_food": {}}


def load_custom_foods(path: str) -> CustomFoods:
    """Load and validate custom_foods.yaml.

    A missing, empty or comment-only file means no recipes. Anything invalid
    raises CustomFoodsError rather than falling back to a USDA search, so a
    broken recipe can never go unnoticed.
    """
    if not os.path.exists(path):
        return no_custom_foods()
    try:
        with open(path) as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise CustomFoodsError(f"not valid YAML: {e}") from e
    if data is None:
        return no_custom_foods()
    if not isinstance(data, dict):
        raise CustomFoodsError("the top level must map recipe names to recipes")

    result = no_custom_foods()
    for name, body in data.items():
        recipe = _parse_recipe(str(name), body)
        for food in recipe["foods"]:
            if food in result["by_food"]:
                raise CustomFoodsError(
                    f"recipe '{recipe['name']}': food '{food}' is already listed in "
                    f"recipe '{result['by_food'][food]}'"
                )
            result["by_food"][food] = recipe["name"]
        result["recipes"][recipe["name"]] = recipe
    return result


def _parse_recipe(name: str, body: object) -> Recipe:
    def fail(problem: str) -> CustomFoodsError:
        return CustomFoodsError(f"recipe '{name}': {problem}")

    if not isinstance(body, dict):
        raise fail("must be a mapping with 'foods' and 'ingredients'")
    unknown = sorted(str(key) for key in body if key not in _RECIPE_KEYS)
    if unknown:
        raise fail(f"unknown key(s) {', '.join(unknown)}")

    foods = body.get("foods")
    if not isinstance(foods, list) or not foods:
        raise fail("'foods' must be a non-empty list of food names")
    if not all(isinstance(food, str) and food.strip() for food in foods):
        raise fail("every entry in 'foods' must be a non-empty food name")

    ingredients = body.get("ingredients")
    if not isinstance(ingredients, list) or not ingredients:
        raise fail("'ingredients' must be a non-empty list")
    amounts: list[tuple[int, float]] = []
    for position, ingredient in enumerate(ingredients, start=1):
        if not isinstance(ingredient, dict) or set(ingredient) != _INGREDIENT_KEYS:
            raise fail(f"ingredient {position} must have exactly 'fdc_id' and 'amount'")
        fdc_id, amount = ingredient["fdc_id"], ingredient["amount"]
        # bool is an int in Python, but "fdc_id: true" is a typo, not an ID
        if isinstance(fdc_id, bool) or not isinstance(fdc_id, int) or fdc_id <= 0:
            raise fail(f"ingredient {position}: fdc_id must be a positive whole number, got {fdc_id!r}")
        if (
            isinstance(amount, bool) or not isinstance(amount, (int, float))
            or not math.isfinite(amount) or amount <= 0
        ):
            raise fail(f"ingredient {position}: amount must be a positive, finite number, got {amount!r}")
        if any(fdc_id == seen for seen, _ in amounts):
            raise fail(f"fdc_id {fdc_id} is listed twice")
        amounts.append((fdc_id, float(amount)))

    total = sum(amount for _, amount in amounts)
    return Recipe(
        name=name,
        foods=list(foods),
        ingredients=[Ingredient(fdc_id=fdc_id, share=amount / total) for fdc_id, amount in amounts],
    )


def recipe_macros(ingredients: list[tuple[float, UsdaMacros]]) -> UsdaMacros:
    """Share-weighted macros of a recipe, from (share, macros) pairs.

    A macro that any ingredient lacks is None: a partial sum would look like a
    real, lower value.
    """
    def combined(key: str) -> float | None:
        values = [macros[key] for _, macros in ingredients]
        if any(value is None for value in values):
            return None
        return sum(share * value for (share, _), value in zip(ingredients, values))

    return UsdaMacros(
        protein_g=combined("protein_g"),
        fat_g=combined("fat_g"),
        carbs_g=combined("carbs_g"),
        fiber_g=combined("fiber_g"),
    )
```

- [ ] **Step 5: Create `config/custom_foods.example.yaml`**

All IDs below were verified against USDA's `/food/{id}` endpoint during planning:

```yaml
# Custom foods: fixed recipes of USDA ingredients, used instead of a USDA search.
#
# Copy this file to the path set as custom_foods_path in settings.yaml
# (default: config/custom_foods.yaml). The file is optional: without it,
# every food goes through translation and USDA search as usual.
#
# Each recipe has:
#   foods:        the exact food names from the MacroFactor export that it
#                 stands for. A food may appear in only one recipe, and a recipe
#                 overrides any mapping in food_mappings.yaml, including "skip".
#   ingredients:  USDA FoodData Central foods, each by FDC ID, with an amount.
#                 Amounts are relative weights (grams of a typical portion, or
#                 percentages); the app scales them so they add up to 100%.
#
# Find FDC IDs at https://fdc.nal.usda.gov. Prefer "SR Legacy" foods, which
# have the most complete nutrient data, and name each one in a comment. The
# HTML report lists every ingredient's USDA name, so a wrong ID shows up
# there, and it compares each recipe's macros with what you logged
# ("macros off N%").

green_salad:
  foods: [Misch Salat Rohkost]
  ingredients:
    - {fdc_id: 169249, amount: 60}   # Lettuce, green leaf, raw
    - {fdc_id: 170393, amount: 20}   # Carrots, raw
    - {fdc_id: 168409, amount: 20}   # Cucumber, with peel, raw

caprese:
  foods: [Salat Caprese]
  ingredients:
    - {fdc_id: 170457, amount: 55}   # Tomatoes, red, ripe, raw, year round average
    - {fdc_id: 170845, amount: 18}   # Cheese, mozzarella, whole milk
    - {fdc_id: 169249, amount: 26}   # Lettuce, green leaf, raw
    - {fdc_id: 171413, amount: 1}    # Oil, olive, salad or cooking
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_custom_foods.py tests/test_matcher.py -v`
Expected: all PASS.

- [ ] **Step 7: Run the full suite and lint**

Run: `uv run pytest -q && uv run ruff check .`
Expected: all pass, `All checks passed!`

- [ ] **Step 8: Commit**

```bash
git add src/omni_pilot/matcher.py src/omni_pilot/custom_foods.py config/custom_foods.example.yaml tests/test_custom_foods.py tests/test_matcher.py
git commit -m "feat: load and validate custom food recipes (BAR-75)"
```

---

### Task 2: Fetch USDA foods by ID, cached in `usda_foods`

**Files:**
- Modify: `src/omni_pilot/enricher.py` (imports, constants, `search_usda`'s request block, and new functions after `extract_micros_from_usda`)
- Modify: `tests/test_enricher.py`

**Interfaces:**
- Consumes: `matcher.UsdaMacros` (Task 1).
- Produces:
  - `enricher.USDA_FOOD_URL = "https://api.nal.usda.gov/fdc/v1/food/{fdc_id}"`.
  - `enricher.USDA_FOODS_TABLE = "usda_foods"`.
  - `enricher.UsdaFood`: a TypedDict `{fdc_id: int, usda_name: str, usda_dataset: str, per_100g: dict[str, float | None], usda_macros: UsdaMacros}`.
  - `enricher.fetch_usda_food(fdc_id: int, api_key: str) -> dict | None`, which returns the food in the search-hit shape (`fdcId`, `description`, `dataType`, and `foodNutrients` as `[{"nutrientNumber", "value"}]`).
  - `enricher.get_usda_food(fdc_id: int, db: TinyDB, api_key: str) -> UsdaFood | None`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_enricher.py`, extend the import from `omni_pilot.enricher` with `fetch_usda_food` and `get_usda_food`, then add:

```python
# An abridged /food/{id} response, as USDA returns it (trimmed).
ABRIDGED_TOMATO = {
    "fdcId": 170457,
    "description": "Tomatoes, red, ripe, raw, year round average",
    "dataType": "SR Legacy",
    "publicationDate": "2019-04-01",
    "foodNutrients": [
        {"number": "203", "name": "Protein", "amount": 0.88, "unitName": "G"},
        {"number": "204", "name": "Total lipid (fat)", "amount": 0.2, "unitName": "G"},
        {"number": "205", "name": "Carbohydrate, by difference", "amount": 3.89, "unitName": "G"},
        {"number": "291", "name": "Fiber, total dietary", "amount": 1.2, "unitName": "G"},
        {"number": "430", "name": "Vitamin K (phylloquinone)", "amount": 7.9, "unitName": "UG"},
        # Listed without an amount: not measured
        {"number": "418", "name": "Vitamin B-12", "unitName": "UG"},
    ],
}


class TestFetchUsdaFood:
    def test_requests_the_abridged_food_and_returns_it_in_search_shape(self, mocker):
        mock_get = mocker.patch("omni_pilot.enricher.requests.get")
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = ABRIDGED_TOMATO

        food = fetch_usda_food(170457, "fake-key")

        assert mock_get.call_args.args[0] == "https://api.nal.usda.gov/fdc/v1/food/170457"
        assert mock_get.call_args.kwargs["params"] == {"api_key": "fake-key", "format": "abridged"}
        assert food["fdcId"] == 170457
        assert food["description"] == "Tomatoes, red, ripe, raw, year round average"
        assert food["dataType"] == "SR Legacy"
        micros = extract_micros_from_usda(food)
        assert micros["vitamin_k_mcg"] == 7.9
        # A nutrient listed without an amount is not measured, never 0.0
        assert micros["b12_cobalamin_mcg"] is None

    def test_unknown_id_returns_none(self, mocker):
        mock_get = mocker.patch("omni_pilot.enricher.requests.get")
        mock_get.return_value.status_code = 404
        assert fetch_usda_food(999999999, "fake-key") is None

    def test_server_error_returns_none(self, mocker):
        mock_get = mocker.patch("omni_pilot.enricher.requests.get")
        mock_get.return_value.status_code = 500
        mock_get.return_value.raise_for_status.side_effect = requests.HTTPError("500")
        assert fetch_usda_food(170457, "fake-key") is None

    def test_request_failure_returns_none(self, mocker):
        mocker.patch("omni_pilot.enricher.requests.get", side_effect=requests.ConnectionError("offline"))
        assert fetch_usda_food(170457, "fake-key") is None

    def test_rate_limit_is_retried_once(self, mocker):
        limited = mocker.Mock(status_code=429)
        ok = mocker.Mock(status_code=200)
        ok.json.return_value = ABRIDGED_TOMATO
        mock_get = mocker.patch("omni_pilot.enricher.requests.get", side_effect=[limited, ok])
        mocker.patch("omni_pilot.enricher.time.sleep")

        assert fetch_usda_food(170457, "fake-key")["fdcId"] == 170457
        assert mock_get.call_count == 2


class TestGetUsdaFood:
    TOMATO_HIT = _usda_hit(170457, "Tomatoes, red, ripe, raw", 0.88, 0.2, 3.89, vitamin_a=42.0)

    def test_fetches_extracts_and_caches_by_id(self, mocker, tmp_path):
        db = TinyDB(str(tmp_path / "test_db.json"))
        mocker.patch("omni_pilot.enricher.fetch_usda_food", return_value=self.TOMATO_HIT)

        food = get_usda_food(170457, db, "fake-key")

        assert food["fdc_id"] == 170457
        assert food["usda_name"] == "Tomatoes, red, ripe, raw"
        assert food["usda_dataset"] == "SR Legacy"
        assert food["per_100g"]["vitamin_a_mcg"] == 42.0
        assert food["usda_macros"] == {"protein_g": 0.88, "fat_g": 0.2, "carbs_g": 3.89, "fiber_g": None}
        cached = db.table("usda_foods").all()
        assert len(cached) == 1
        assert cached[0]["fdc_id"] == 170457
        assert "last_updated" in cached[0]
        # The food-name cache (default table) is untouched
        assert db.all() == []

    def test_cached_food_is_used_without_fetching(self, mocker, tmp_path):
        db = TinyDB(str(tmp_path / "test_db.json"))
        mocker.patch("omni_pilot.enricher.fetch_usda_food", return_value=self.TOMATO_HIT)
        get_usda_food(170457, db, "fake-key")
        mock_fetch = mocker.patch("omni_pilot.enricher.fetch_usda_food")

        food = get_usda_food(170457, db, "fake-key")

        mock_fetch.assert_not_called()
        assert food["usda_name"] == "Tomatoes, red, ripe, raw"
        assert food["per_100g"]["vitamin_a_mcg"] == 42.0

    def test_failed_fetch_caches_nothing(self, mocker, tmp_path):
        db = TinyDB(str(tmp_path / "test_db.json"))
        mocker.patch("omni_pilot.enricher.fetch_usda_food", return_value=None)

        assert get_usda_food(170457, db, "fake-key") is None
        assert db.table("usda_foods").all() == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_enricher.py -v -k "FetchUsdaFood or GetUsdaFood"`
Expected: `ImportError: cannot import name 'fetch_usda_food'`.

- [ ] **Step 3: Implement**

In `src/omni_pilot/enricher.py`, change the matcher import to:

```python
from omni_pilot.matcher import Candidate, LoggedMacros, UsdaMacros
```

Add the constants below `USDA_SEARCH_URL`:

```python
USDA_FOOD_URL = "https://api.nal.usda.gov/fdc/v1/food/{fdc_id}"
# Ingredients of custom foods, keyed by FDC ID. An ID always names the same
# food, so entries here are never invalidated.
USDA_FOODS_TABLE = "usda_foods"
```

Add the type below `class FoodMatch(TypedDict)`:

```python
class UsdaFood(TypedDict):
    fdc_id: int
    usda_name: str
    usda_dataset: str
    per_100g: dict[str, float | None]
    usda_macros: UsdaMacros
```

Add this helper above `search_usda`, and use it inside `search_usda` in place of the inline `requests.get` / 429 block. The `try`/`except requests.RequestException` stays in `search_usda`.

```python
def _usda_get(url: str, params: dict) -> requests.Response:
    """GET from the USDA API, waiting and retrying once on a rate limit (429)."""
    resp = requests.get(url, params=params, timeout=30)
    if resp.status_code == 429:
        logger.warning("USDA API rate limit hit, waiting 5 seconds...")
        time.sleep(5)
        resp = requests.get(url, params=params, timeout=30)
    return resp
```

Once edited, `search_usda`'s request block reads:

```python
    try:
        resp = _usda_get(USDA_SEARCH_URL, params)
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.error("USDA API request failed for '%s': %s", cleaned, e)
        return None
```

Add after `extract_micros_from_usda`:

```python
def _usda_macros(usda_food: dict) -> UsdaMacros:
    return UsdaMacros(
        protein_g=_nutrient_value(usda_food, "203"),
        fat_g=_nutrient_value(usda_food, "204"),
        carbs_g=_nutrient_value(usda_food, "205"),
        fiber_g=_nutrient_value(usda_food, "291"),
    )


def fetch_usda_food(fdc_id: int, api_key: str) -> dict | None:
    """Fetch one USDA food by its FoodData Central ID.

    The abridged response names nutrients "number"/"amount"; they are
    converted to the search endpoint's "nutrientNumber"/"value" so the
    search-hit helpers read the result unchanged. Returns None on any
    failure; a 404 means the ID itself is wrong.
    """
    params = {"api_key": api_key, "format": "abridged"}
    try:
        resp = _usda_get(USDA_FOOD_URL.format(fdc_id=fdc_id), params)
        if resp.status_code == 404:
            logger.error("USDA has no food with FDC ID %s — check custom_foods.yaml", fdc_id)
            return None
        resp.raise_for_status()
        food = resp.json()
    except requests.RequestException as e:
        logger.error("USDA request failed for FDC ID %s: %s", fdc_id, e)
        return None
    return {
        "fdcId": food.get("fdcId", fdc_id),
        "description": food.get("description", ""),
        "dataType": food.get("dataType", ""),
        "foodNutrients": [
            {"nutrientNumber": str(n.get("number", "")), "value": n.get("amount")}
            for n in food.get("foodNutrients", [])
        ],
    }


def get_usda_food(fdc_id: int, db: TinyDB, api_key: str) -> UsdaFood | None:
    """Get a USDA food by ID from the usda_foods cache, fetching it on a miss.

    A cached entry is always reused. A failed fetch caches nothing, so it is
    retried on the next run.
    """
    table = db.table(USDA_FOODS_TABLE)
    cached = table.search(Query().fdc_id == fdc_id)
    if cached:
        entry = cached[0]
        return UsdaFood(
            fdc_id=entry["fdc_id"],
            usda_name=entry["usda_name"],
            usda_dataset=entry["usda_dataset"],
            per_100g=entry["per_100g"],
            usda_macros=entry["usda_macros"],
        )

    raw = fetch_usda_food(fdc_id, api_key)
    if raw is None:
        return None
    food = UsdaFood(
        fdc_id=fdc_id,
        usda_name=raw["description"],
        usda_dataset=raw["dataType"],
        per_100g=extract_micros_from_usda(raw),
        usda_macros=_usda_macros(raw),
    )
    table.insert({**food, "last_updated": str(date.today())})
    return food
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_enricher.py -v`
Expected: all PASS, including the existing `TestSearchUsda` tests, which prove the `_usda_get` refactor changed nothing.

- [ ] **Step 5: Run the full suite and lint, then commit**

Run: `uv run pytest -q && uv run ruff check .`
Expected: all pass.

```bash
git add src/omni_pilot/enricher.py tests/test_enricher.py
git commit -m "feat: fetch USDA foods by FDC ID with a permanent cache (BAR-75)"
```

---

### Task 3: Custom food matches in `enrich_all_foods`

**Files:**
- Modify: `src/omni_pilot/enricher.py` (imports, `EnrichmentResult`, new TypedDicts, new helpers, `enrich_all_foods`)
- Modify: `tests/helpers.py`
- Modify: `tests/test_enricher.py`

**Interfaces:**
- Consumes:
  - from Task 1: `CustomFoods`, `Recipe`, `no_custom_foods()`, `recipe_macros()` and `matcher.macro_distance`;
  - from Task 2: `get_usda_food` and `UsdaFood`.
- Produces:
  - `enricher.RecipePart`: `{share: float, fdc_id: int, usda_name: str, per_100g: dict[str, float | None]}`.
  - `enricher.CustomFoodMatch`: `{recipe: str, parts: list[RecipePart], macro_distance: float | None}`.
  - `EnrichmentResult["custom"]: dict[str, CustomFoodMatch]`. Custom foods are never in `profiles`.
  - `enrich_all_foods(food_names, mappings, logged_macros, db, api_key, custom_foods: CustomFoods | None = None) -> EnrichmentResult`.
  - `tests.helpers.enrichment(..., custom=...)`.

- [ ] **Step 1: Update the test helper**

In `tests/helpers.py`, change the import and `enrichment`:

```python
from omni_pilot.enricher import CustomFoodMatch, EnrichmentResult, LowConfidenceMatch


def enrichment(
    profiles: dict[str, dict[str, float | None]] | None = None,
    *,
    skipped: Iterable[str] = (),
    unresolved: Iterable[str] = (),
    low_confidence: dict[str, LowConfidenceMatch] | None = None,
    custom: dict[str, CustomFoodMatch] | None = None,
) -> EnrichmentResult:
    """Build an EnrichmentResult, defaulting the parts a test doesn't care about."""
    return {
        "profiles": dict(profiles or {}),
        "skipped": set(skipped),
        "unresolved": set(unresolved),
        "low_confidence": dict(low_confidence or {}),
        "custom": dict(custom or {}),
    }
```

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_enricher.py`, and add `from omni_pilot.matcher import GOOD_DISTANCE` to its imports:

```python
TOMATO_HIT = _usda_hit(1, "Tomatoes, red, ripe, raw", 0.88, 0.2, 3.89, vitamin_a=42.0)
MOZZARELLA_HIT = _usda_hit(2, "Cheese, mozzarella, whole milk", 22.2, 22.1, 2.4, vitamin_a=179.0)
CAPRESE_FOODS = {
    "recipes": {
        "caprese": {
            "name": "caprese",
            "foods": ["Salat Caprese", "Caprese to go"],
            "ingredients": [{"fdc_id": 1, "share": 0.75}, {"fdc_id": 2, "share": 0.25}],
        },
    },
    "by_food": {"Salat Caprese": "caprese", "Caprese to go": "caprese"},
}
CAPRESE_LOGGED = {"kcal": 74.0, "protein_g": 4.9, "fat_g": 4.2, "carbs_g": 3.0}


class TestEnrichCustomFoods:
    def _fetch(self, mocker, foods: dict[int, dict]):
        return mocker.patch(
            "omni_pilot.enricher.fetch_usda_food", side_effect=lambda fdc_id, api_key: foods.get(fdc_id),
        )

    def test_custom_food_is_built_from_its_recipe_without_searching(self, mocker, tmp_path):
        db = TinyDB(str(tmp_path / "test_db.json"))
        self._fetch(mocker, {1: TOMATO_HIT, 2: MOZZARELLA_HIT})
        mock_search = mocker.patch("omni_pilot.enricher.search_usda")

        result = enrich_all_foods(["Salat Caprese"], {}, {}, db, "fake-key", custom_foods=CAPRESE_FOODS)

        assert result["profiles"] == {}
        match = result["custom"]["Salat Caprese"]
        assert match["recipe"] == "caprese"
        assert [(p["share"], p["fdc_id"], p["usda_name"]) for p in match["parts"]] == [
            (0.75, 1, "Tomatoes, red, ripe, raw"),
            (0.25, 2, "Cheese, mozzarella, whole milk"),
        ]
        assert match["parts"][1]["per_100g"]["vitamin_a_mcg"] == 179.0
        mock_search.assert_not_called()

    def test_recipe_overrides_skip_and_usda_mappings(self, mocker, tmp_path):
        db = TinyDB(str(tmp_path / "test_db.json"))
        self._fetch(mocker, {1: TOMATO_HIT, 2: MOZZARELLA_HIT})
        mock_match = mocker.patch("omni_pilot.enricher.get_food_match")
        mappings = {"Salat Caprese": "skip", "Caprese to go": "caprese salad"}

        result = enrich_all_foods(
            ["Caprese to go", "Salat Caprese"], mappings, {}, db, "fake-key", custom_foods=CAPRESE_FOODS,
        )

        assert set(result["custom"]) == {"Salat Caprese", "Caprese to go"}
        assert result["skipped"] == set()
        mock_match.assert_not_called()

    def test_unavailable_ingredient_makes_every_food_of_the_recipe_unresolved(self, mocker, tmp_path):
        db = TinyDB(str(tmp_path / "test_db.json"))
        mock_fetch = self._fetch(mocker, {1: TOMATO_HIT})  # FDC ID 2 cannot be fetched

        result = enrich_all_foods(
            ["Caprese to go", "Salat Caprese"], {}, {}, db, "fake-key", custom_foods=CAPRESE_FOODS,
        )

        assert result["unresolved"] == {"Salat Caprese", "Caprese to go"}
        assert result["custom"] == {}
        # The failing ID is asked for once per run, not once per food
        assert sorted(call.args[0] for call in mock_fetch.call_args_list) == [1, 2]

    def test_shared_ingredients_are_fetched_once(self, mocker, tmp_path):
        db = TinyDB(str(tmp_path / "test_db.json"))
        mock_fetch = self._fetch(mocker, {1: TOMATO_HIT, 2: MOZZARELLA_HIT})
        custom = {
            "recipes": {
                **CAPRESE_FOODS["recipes"],
                "tomato_salad": {
                    "name": "tomato_salad", "foods": ["Tomatensalat"], "ingredients": [{"fdc_id": 1, "share": 1.0}],
                },
            },
            "by_food": {**CAPRESE_FOODS["by_food"], "Tomatensalat": "tomato_salad"},
        }

        enrich_all_foods(
            ["Caprese to go", "Salat Caprese", "Tomatensalat"], {}, {}, db, "fake-key", custom_foods=custom,
        )

        assert mock_fetch.call_count == 2

    def test_macro_check_is_per_food(self, mocker, tmp_path):
        db = TinyDB(str(tmp_path / "test_db.json"))
        self._fetch(mocker, {1: TOMATO_HIT, 2: MOZZARELLA_HIT})

        result = enrich_all_foods(
            ["Caprese to go", "Salat Caprese"], {}, {"Salat Caprese": CAPRESE_LOGGED}, db, "fake-key",
            custom_foods=CAPRESE_FOODS,
        )

        # Recipe per 100 g: P 6.21, F 5.675, C 3.5175 (the fixtures have no fiber)
        expected = (4 * 1.31 + 9 * 1.475 + 4 * 0.5175) / 74.0
        assert result["custom"]["Salat Caprese"]["macro_distance"] == pytest.approx(expected)
        # No logged macros: not checked
        assert result["custom"]["Caprese to go"]["macro_distance"] is None

    def test_badly_matching_recipe_is_counted_but_not_low_confidence(self, mocker, tmp_path):
        db = TinyDB(str(tmp_path / "test_db.json"))
        self._fetch(mocker, {1: TOMATO_HIT, 2: MOZZARELLA_HIT})
        logged = {"Salat Caprese": {"kcal": 300.0, "protein_g": 30.0, "fat_g": 20.0, "carbs_g": 1.0}}

        result = enrich_all_foods(["Salat Caprese"], {}, logged, db, "fake-key", custom_foods=CAPRESE_FOODS)

        assert result["custom"]["Salat Caprese"]["macro_distance"] > GOOD_DISTANCE
        assert result["low_confidence"] == {}

    def test_without_custom_foods_nothing_is_custom(self, mocker, tmp_path):
        db = TinyDB(str(tmp_path / "test_db.json"))
        mocker.patch("omni_pilot.enricher.search_usda", return_value=[])

        result = enrich_all_foods(["Salat Caprese"], {}, {}, db, "fake-key")

        assert result["custom"] == {}
        assert result["unresolved"] == {"Salat Caprese"}
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_enricher.py -v -k EnrichCustomFoods`
Expected: `ImportError: cannot import name 'CustomFoodMatch'` (raised through `tests/helpers.py`).

- [ ] **Step 4: Implement**

Add these imports to `src/omni_pilot/enricher.py`:

```python
from omni_pilot.custom_foods import CustomFoods, Recipe, no_custom_foods, recipe_macros
```

Add the types above `class EnrichmentResult`:

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
```

Add this field at the end of `EnrichmentResult`:

```python
    # Foods covered by a custom recipe, built from its ingredients. They are
    # never in profiles: the analyzer counts them ingredient by ingredient, so
    # an ingredient missing a nutrient costs only its own share of coverage.
    custom: dict[str, CustomFoodMatch]
```

Add the helpers above `enrich_all_foods`:

```python
def _recipe_ingredients(
    recipe: Recipe, db: TinyDB, api_key: str, fetched: dict[int, UsdaFood | None],
) -> list[tuple[float, UsdaFood]] | None:
    """Each ingredient's share and USDA food, or None if any is unavailable.

    `fetched` memoises lookups for the run, so an ID that failed isn't
    requested again for the next food sharing the recipe.
    """
    ingredients = []
    for ingredient in recipe["ingredients"]:
        fdc_id = ingredient["fdc_id"]
        if fdc_id not in fetched:
            fetched[fdc_id] = get_usda_food(fdc_id, db, api_key)
        food = fetched[fdc_id]
        if food is None:
            logger.error("Recipe '%s': USDA food %s is unavailable", recipe["name"], fdc_id)
            return None
        ingredients.append((ingredient["share"], food))
    return ingredients


def _custom_food_match(
    recipe_name: str, ingredients: list[tuple[float, UsdaFood]], logged: LoggedMacros | None,
) -> CustomFoodMatch:
    macros = recipe_macros([(share, food["usda_macros"]) for share, food in ingredients])
    return CustomFoodMatch(
        recipe=recipe_name,
        parts=[
            RecipePart(share=share, fdc_id=food["fdc_id"], usda_name=food["usda_name"], per_100g=food["per_100g"])
            for share, food in ingredients
        ],
        macro_distance=matcher.macro_distance(logged, macros) if logged is not None else None,
    )
```

Replace `enrich_all_foods`:

```python
def enrich_all_foods(
    food_names: list[str],
    mappings: dict[str, str],
    logged_macros: dict[str, LoggedMacros],
    db: TinyDB,
    api_key: str,
    custom_foods: CustomFoods | None = None,
) -> EnrichmentResult:
    """Enrich all foods with USDA micro data.

    A food covered by a custom recipe is built from the recipe's ingredients
    and goes into custom, whatever its mapping says (even "skip"). Otherwise:
    resolved foods go into profiles, foods mapped to "skip" into skipped, and
    foods whose USDA lookup failed into unresolved. Resolved foods whose match
    is weak are also listed in low_confidence.
    """
    if custom_foods is None:
        custom_foods = no_custom_foods()
    result: EnrichmentResult = {
        "profiles": {}, "skipped": set(), "unresolved": set(), "low_confidence": {}, "custom": {},
    }
    fetched: dict[int, UsdaFood | None] = {}

    for food_name in food_names:
        recipe_name = custom_foods["by_food"].get(food_name)
        if recipe_name is not None:
            ingredients = _recipe_ingredients(custom_foods["recipes"][recipe_name], db, api_key, fetched)
            if ingredients is None:
                result["unresolved"].add(food_name)
                logger.warning(
                    "Could not resolve '%s': an ingredient of recipe '%s' is unavailable",
                    food_name, recipe_name,
                )
            else:
                result["custom"][food_name] = _custom_food_match(
                    recipe_name, ingredients, logged_macros.get(food_name),
                )
            continue

        mapping = mappings.get(food_name, "")

        if mapping == "skip":
            result["skipped"].add(food_name)
            logger.info("Skipping '%s' (mapped to 'skip')", food_name)
            continue

        # Use the mapping if provided, otherwise use the original name
        query = mapping if mapping else food_name
        match = get_food_match(food_name, query, logged_macros.get(food_name), db, api_key)

        if match is None:
            result["unresolved"].add(food_name)
            logger.warning(
                "Could not resolve '%s' (query: '%s') — marking as unresolved",
                food_name, query,
            )
            continue

        result["profiles"][food_name] = match["per_100g"]
        if match["confidence"] == "weak":
            result["low_confidence"][food_name] = LowConfidenceMatch(
                usda_name=match["usda_name"],
                macro_distance=match["macro_distance"],
            )

    return result
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_enricher.py -v`
Expected: all PASS.

- [ ] **Step 6: Run the full suite and lint, then commit**

Run: `uv run pytest -q && uv run ruff check .`
Expected: all pass. Analyzer, reporter and CLI tests use `tests.helpers.enrichment`, which now includes `custom`.

```bash
git add src/omni_pilot/enricher.py tests/helpers.py tests/test_enricher.py
git commit -m "feat: build custom foods from recipes in enrichment (BAR-75)"
```

---

### Task 4: Count custom foods ingredient by ingredient in the analyzer

**Files:**
- Modify: `src/omni_pilot/analyzer.py` (TypedDicts, `analyze` loop and return)
- Modify: `tests/test_analyzer.py`

**Interfaces:**
- Consumes: `EnrichmentResult["custom"]` and `CustomFoodMatch` / `RecipePart` from Task 3.
- Produces:
  - `analyzer.CustomIngredient`: `{fdc_id: int, usda_name: str, share_pct: float}`.
  - `analyzer.CustomFoodUse`: `{name: str, grams: float, macro_distance: float | None}`.
  - `analyzer.CustomRecipeUse`: `{recipe: str, ingredients: list[CustomIngredient], foods: list[CustomFoodUse]}`.
  - `AnalysisResult["coverage"]["custom_recipes"]: list[CustomRecipeUse]`, sorted by the recipe's total grams descending; each recipe's foods are sorted by grams descending.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_analyzer.py`:

```python
def _caprese(macro_distance: float | None = 0.1) -> dict:
    """A CustomFoodMatch: 75% tomatoes, 25% mozzarella (which has no vitamin K value)."""
    return {
        "recipe": "caprese",
        "macro_distance": macro_distance,
        "parts": [
            {"share": 0.75, "fdc_id": 170457, "usda_name": "Tomatoes",
             "per_100g": {"vitamin_k_mcg": 8.0, "omega3_epa_mg": 0.0, "omega3_dha_mg": 0.0}},
            {"share": 0.25, "fdc_id": 170845, "usda_name": "Mozzarella",
             "per_100g": {"vitamin_k_mcg": None, "omega3_epa_mg": 0.01, "omega3_dha_mg": 0.02}},
        ],
    }


class TestCustomFoods:
    REF_RANGES = {
        "demographic": {},
        "nutrients": {
            "vitamin_k_mcg": {"name": "Vitamin K", "unit": "mcg", "type": "ai", "ai": 120},
            "omega3_epa_dha_mg": {"name": "EPA+DHA", "unit": "mg", "type": "ai", "ai": 250},
        },
    }

    def test_nutrients_are_the_share_weighted_sum_of_parts(self):
        entries = [{"date": "2026-08-09", "food_name": "Salat Caprese", "total_weight_g": 200.0}]

        result = analyze(entries, enrichment(custom={"Salat Caprese": _caprese()}), self.REF_RANGES)

        # 150 g of tomatoes at 8 mcg/100 g; the mozzarella has no vitamin K value
        vitamin_k = result["nutrients"]["vitamin_k_mcg"]
        assert vitamin_k["daily_avg"] == pytest.approx(12.0)
        # Only the mozzarella's 50 g of 200 g is unmeasured
        assert vitamin_k["coverage_pct"] == 75.0
        assert vitamin_k["is_floor"] is True
        # Combined nutrient per part, EPA/DHA converted from g to mg: 50 g x 30 mg/100 g
        epa_dha = result["nutrients"]["omega3_epa_dha_mg"]
        assert epa_dha["daily_avg"] == pytest.approx(15.0)
        assert epa_dha["coverage_pct"] == 100.0

    def test_custom_entries_count_as_analysed(self):
        entries = [
            {"date": "2026-08-09", "food_name": "Salat Caprese", "total_weight_g": 200.0},
            {"date": "2026-08-09", "food_name": "Feta Salat", "total_weight_g": 200.0},
        ]
        low_confidence = {"Feta Salat": {"usda_name": "Cheese, feta", "macro_distance": 3.9}}

        result = analyze(
            entries,
            enrichment(
                {"Feta Salat": {"vitamin_k_mcg": 1.0}},
                low_confidence=low_confidence,
                custom={"Salat Caprese": _caprese()},
            ),
            self.REF_RANGES,
        )

        coverage = result["coverage"]
        assert coverage["mapped_entries"] == 2
        assert coverage["unresolved_entries"] == 0
        # The custom food's weight is in the analysed-weight denominator
        assert coverage["low_confidence_weight_pct"] == 50.0

    def test_custom_recipes_group_foods_by_recipe(self):
        green_salad = {
            "recipe": "green_salad",
            "macro_distance": None,
            "parts": [{"share": 1.0, "fdc_id": 169249, "usda_name": "Lettuce", "per_100g": {"vitamin_k_mcg": 126.0}}],
        }
        entries = [
            {"date": "2026-08-09", "food_name": "Salat Caprese", "total_weight_g": 264.0},
            {"date": "2026-08-09", "food_name": "Misch Salat Rohkost", "total_weight_g": 250.0},
            {"date": "2026-08-10", "food_name": "Misch Salat Rohkost", "total_weight_g": 250.0},
            {"date": "2026-08-10", "food_name": "Salat Manhattan", "total_weight_g": 120.0},
            {"date": "2026-08-10", "food_name": "Salat Unused", "total_weight_g": 0.0},
        ]
        custom = {
            "Salat Caprese": _caprese(0.4),
            "Misch Salat Rohkost": {**green_salad, "macro_distance": 0.12},
            "Salat Manhattan": green_salad,
            "Salat Unused": green_salad,
        }

        result = analyze(entries, enrichment(custom=custom), self.REF_RANGES)

        recipes = result["coverage"]["custom_recipes"]
        assert [r["recipe"] for r in recipes] == ["green_salad", "caprese"]  # 620 g before 264 g
        assert recipes[0]["foods"] == [
            {"name": "Misch Salat Rohkost", "grams": 500.0, "macro_distance": 0.12},
            {"name": "Salat Manhattan", "grams": 120.0, "macro_distance": None},
        ]  # the 0 g food is left out
        assert recipes[1]["ingredients"] == [
            {"fdc_id": 170457, "usda_name": "Tomatoes", "share_pct": 75.0},
            {"fdc_id": 170845, "usda_name": "Mozzarella", "share_pct": 25.0},
        ]

    def test_no_custom_foods_gives_empty_list(self):
        entries = [{"date": "2026-08-09", "food_name": "Eggs", "total_weight_g": 100.0}]
        result = analyze(entries, enrichment({"Eggs": {"vitamin_k_mcg": 0.3}}), self.REF_RANGES)
        assert result["coverage"]["custom_recipes"] == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_analyzer.py -v -k TestCustomFoods`
Expected: FAIL. Custom foods are counted as unresolved (`daily_avg` 0.0), and `KeyError: 'custom_recipes'`.

- [ ] **Step 3: Implement**

In `src/omni_pilot/analyzer.py`, add these TypedDicts after `LowConfidenceFood`:

```python
class CustomIngredient(TypedDict):
    fdc_id: int
    usda_name: str
    share_pct: float


class CustomFoodUse(TypedDict):
    name: str
    grams: float
    macro_distance: float | None


class CustomRecipeUse(TypedDict):
    recipe: str
    ingredients: list[CustomIngredient]  # in recipe order
    foods: list[CustomFoodUse]  # by grams, descending
```

Add this field to `CoverageResult`:

```python
    custom_recipes: list[CustomRecipeUse]
```

Add the helpers above `analyze`:

```python
def _food_parts(
    food_name: str, enrichment: EnrichmentResult,
) -> list[tuple[float, dict[str, float | None]]] | None:
    """A food's nutrient sources with their weight shares, or None if it isn't analysed.

    A USDA-matched food is one part; a custom food is one part per recipe
    ingredient.
    """
    profile = enrichment["profiles"].get(food_name)
    if profile is not None:
        return [(1.0, profile)]
    custom = enrichment["custom"].get(food_name)
    if custom is not None:
        return [(part["share"], part["per_100g"]) for part in custom["parts"]]
    return None


def _custom_recipe_uses(
    custom: dict, weights_g: dict[str, float],
) -> list[CustomRecipeUse]:
    """Group the custom foods eaten in the period by recipe, heaviest first."""
    by_recipe: dict[str, CustomRecipeUse] = {}
    for food_name, grams in weights_g.items():
        match = custom[food_name]
        use = by_recipe.get(match["recipe"])
        if use is None:
            use = by_recipe[match["recipe"]] = CustomRecipeUse(
                recipe=match["recipe"],
                ingredients=[
                    CustomIngredient(fdc_id=part["fdc_id"], usda_name=part["usda_name"], share_pct=part["share"] * 100)
                    for part in match["parts"]
                ],
                foods=[],
            )
        use["foods"].append(CustomFoodUse(name=food_name, grams=grams, macro_distance=match["macro_distance"]))
    for use in by_recipe.values():
        use["foods"].sort(key=lambda food: (-food["grams"], food["name"]))
    return sorted(
        by_recipe.values(),
        key=lambda use: (-sum(food["grams"] for food in use["foods"]), use["recipe"]),
    )
```

In `analyze`:

1. Extend the docstring's coverage paragraph with: "A custom food is counted part by part, each part weighing its share of the entry, so an ingredient missing a nutrient books only its own share as unmeasured."
2. Remove `profiles = enrichment["profiles"]`. Next to `low_confidence_weight_g`, add `custom_weight_g: dict[str, float] = defaultdict(float)`.
3. Replace the loop body from `food_micros = profiles.get(food_name)` through the end of the nutrient loop with:

```python
        parts = _food_parts(food_name, enrichment)

        if parts is None:
            # Skipped and unresolved foods are both excluded from coverage on
            # both sides; they differ only in how the report labels them.
            if food_name in enrichment["skipped"]:
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
        analysed_weight_g += total_weight_g
        if total_weight_g > 0 and food_name in enrichment["low_confidence"]:
            low_confidence_weight_g[food_name] += total_weight_g
        if total_weight_g > 0 and food_name in enrichment["custom"]:
            custom_weight_g[food_name] += total_weight_g

        for share, food_micros in parts:
            part_weight_g = total_weight_g * share
            for nutrient_key in nutrient_keys:
                component_keys = COMBINED_NUTRIENTS.get(nutrient_key, [nutrient_key])
                component_values = [food_micros.get(ck) for ck in component_keys]

                if any(value is None for value in component_values):
                    unmeasured_weight_g[nutrient_key] += part_weight_g
                    continue

                measured_weight_g[nutrient_key] += part_weight_g
                # USDA provides EPA and DHA in grams, but our reference target is in mg
                contribution = sum(
                    value * 1000.0 if ck in ("omega3_epa_mg", "omega3_dha_mg") else value
                    for ck, value in zip(component_keys, component_values)
                )
                daily_totals[entry_date][nutrient_key] += contribution * (part_weight_g / 100.0)
```

`total_weight_g * 1.0` is exactly `total_weight_g`, so every non-custom result is unchanged bit for bit.

4. In the returned `CoverageResult`, add:

```python
            custom_recipes=_custom_recipe_uses(enrichment["custom"], custom_weight_g),
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_analyzer.py -v`
Expected: all PASS, including every existing coverage and floor test.

- [ ] **Step 5: Run the full suite and lint, then commit**

Run: `uv run pytest -q && uv run ruff check .`
Expected: all pass.

```bash
git add src/omni_pilot/analyzer.py tests/test_analyzer.py
git commit -m "feat: count custom foods ingredient by ingredient in analysis (BAR-75)"
```

---

### Task 5: Custom foods in the terminal and HTML reports

**Files:**
- Modify: `src/omni_pilot/reporter.py` (import, new helpers, terminal warnings block, `HTML_TEMPLATE`, `generate_html_report`)
- Modify: `tests/test_reporter.py`

**Interfaces:**
- Consumes: `coverage["custom_recipes"]` (Task 4) and `matcher.GOOD_DISTANCE`.
- Produces: `reporter._macro_check(distance: float | None, prefix: str = "") -> str` and `reporter._custom_foods_line(coverage: dict) -> tuple[str, bool] | None`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_reporter.py`, add `"custom_recipes": [],` to the `coverage` dict of `_make_analysis_result()`, right after `"low_confidence_weight_pct": 0.0,`. Then add:

```python
def _make_analysis_result_with_custom_foods() -> dict:
    result = _make_analysis_result()
    result["coverage"]["custom_recipes"] = [
        {
            "recipe": "green_salad",
            "ingredients": [
                {"fdc_id": 169249, "usda_name": "Lettuce, green leaf, raw", "share_pct": 60.0},
                {"fdc_id": 170393, "usda_name": "Carrots, raw", "share_pct": 40.0},
            ],
            "foods": [
                {"name": "Misch Salat Rohkost", "grams": 750.0, "macro_distance": 0.12},
                {"name": "Salat [Manhattan]", "grams": 120.0, "macro_distance": None},
            ],
        },
        {
            "recipe": "caprese",
            "ingredients": [
                {"fdc_id": 170457, "usda_name": "Tomatoes, red, ripe, raw, year round average", "share_pct": 100.0},
            ],
            "foods": [{"name": "Salat Caprese", "grams": 264.0, "macro_distance": 0.4}],
        },
    ]
    return result


class TestCustomFoodsReport:
    SETTINGS = {"output": {"show_amino_acids": True, "show_ok_nutrients": True}}

    def test_terminal_names_each_food_with_recipe_and_macro_check(self, capsys, monkeypatch):
        monkeypatch.setenv("COLUMNS", "300")
        print_terminal_report(_make_analysis_result_with_custom_foods(), self.SETTINGS)
        out = capsys.readouterr().out
        assert (
            "Custom foods: Misch Salat Rohkost → green_salad (off 12%), "
            "Salat [Manhattan] → green_salad (not checked), "
            "Salat Caprese → caprese (⚠ off 40%)"
        ) in out

    def test_terminal_omits_line_without_custom_foods(self, capsys):
        print_terminal_report(_make_analysis_result(), self.SETTINGS)
        assert "Custom foods" not in capsys.readouterr().out

    def test_html_lists_recipes_ingredients_and_foods(self, tmp_path):
        html_path = str(tmp_path / "report.html")
        generate_html_report(_make_analysis_result_with_custom_foods(), html_path)
        with open(html_path) as f:
            html = f.read()
        assert "<h2>Custom foods</h2>" in html
        assert "<h3>green_salad</h3>" in html
        assert "Lettuce, green leaf, raw (FDC 169249) — 60%" in html
        assert "Misch Salat Rohkost — 750 g — macros off 12%" in html
        assert "Salat [Manhattan] — 120 g — macros not checked" in html
        assert "Salat Caprese — 264 g — ⚠ macros off 40%" in html
        # The section comes after the warnings, at the bottom of the report
        assert html.index("<h2>Custom foods</h2>") > html.index('<div class="warnings">')

    def test_html_omits_section_without_custom_foods(self, tmp_path):
        html_path = str(tmp_path / "report.html")
        generate_html_report(_make_analysis_result(), html_path)
        with open(html_path) as f:
            assert "Custom foods" not in f.read()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_reporter.py -v -k TestCustomFoodsReport`
Expected: the two "lists"/"names" tests FAIL (the text is missing); the two "omits" tests pass.

- [ ] **Step 3: Implement**

In `src/omni_pilot/reporter.py`, add the import:

```python
from omni_pilot.matcher import GOOD_DISTANCE
```

Add after `_low_confidence_line`:

```python
def _macro_check(distance: float | None, prefix: str = "") -> str:
    """How a custom food's recipe macros compare with the logged ones."""
    if distance is None:
        return f"{prefix}not checked"
    text = f"{prefix}off {distance * 100:.0f}%"
    return f"⚠ {text}" if distance > GOOD_DISTANCE else text


def _custom_foods_line(coverage: dict) -> tuple[str, bool] | None:
    """Name each custom food with its recipe and macro check, and whether any is off."""
    recipes = coverage["custom_recipes"]
    if not recipes:
        return None
    described = []
    flagged = False
    for recipe in recipes:
        for food in recipe["foods"]:
            distance = food["macro_distance"]
            described.append(f"{food['name']} → {recipe['recipe']} ({_macro_check(distance)})")
            flagged = flagged or (distance is not None and distance > GOOD_DISTANCE)
    return f"Custom foods: {', '.join(described)}", flagged
```

In `print_terminal_report`, after the low-confidence block at the end:

```python
    custom_foods_line = _custom_foods_line(coverage)
    if custom_foods_line:
        line, flagged = custom_foods_line
        # Text, not a markup string: food names can contain "[...]".
        console.print(Text(f"  {line}", style="yellow" if flagged else "dim"))
```

In `HTML_TEMPLATE`, add these CSS rules after `.warnings p { ... }`:

```css
        .custom-foods { margin-top: 2rem; padding: 1rem; background: #16213e; border-radius: 8px; font-size: 0.9rem; }
        .custom-foods h2 { font-size: 1.1rem; margin-bottom: 0.5rem; }
        .custom-foods h3 { font-size: 1rem; margin: 1rem 0 0.3rem; color: #ccd6f6; }
        .custom-foods ul { margin: 0.2rem 0 0.5rem 1.5rem; }
        .custom-foods .used-for { color: #8892b0; }
```

Also in `HTML_TEMPLATE`, add this block between the warnings block's closing `{% endif %}` and `</body>`:

```html
    {% if custom_recipes %}
    <div class="custom-foods">
        <h2>Custom foods</h2>
        {% for r in custom_recipes %}
        <h3>{{ r.recipe }}</h3>
        <ul>
            {% for i in r.ingredients %}
            <li>{{ i.usda_name }} (FDC {{ i.fdc_id }}) — {{ "%.0f"|format(i.share_pct) }}%</li>
            {% endfor %}
        </ul>
        <p class="used-for">Used for:</p>
        <ul>
            {% for f in r.foods %}
            <li>{{ f.name }} — {{ "%.0f"|format(f.grams) }} g — {{ f.check }}</li>
            {% endfor %}
        </ul>
        {% endfor %}
    </div>
    {% endif %}
```

In `generate_html_report`, add this argument to `template.render(...)`:

```python
        custom_recipes=[
            {
                "recipe": recipe["recipe"],
                "ingredients": recipe["ingredients"],
                "foods": [
                    {**food, "check": _macro_check(food["macro_distance"], "macros ")}
                    for food in recipe["foods"]
                ],
            }
            for recipe in coverage["custom_recipes"]
        ],
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_reporter.py -v`
Expected: all PASS.

- [ ] **Step 5: Run the full suite and lint, then commit**

Run: `uv run pytest -q && uv run ruff check .`
Expected: all pass. `tests/test_integration.py` runs the real analyzer, so its results carry `custom_recipes`.

```bash
git add src/omni_pilot/reporter.py tests/test_reporter.py
git commit -m "feat: show custom foods and their macro check in reports (BAR-75)"
```

---

### Task 6: CLI wiring, settings, sync exclude and docs

**Files:**
- Modify: `src/omni_pilot/cli.py`
- Modify: `tests/test_cli.py`
- Modify: `config/settings.example.yaml`
- Modify: `Makefile` (the `sync-iphone` target)
- Modify: `CLAUDE.md`

**Interfaces:**
- Consumes: `load_custom_foods` and `CustomFoodsError` (Task 1), and `enrich_all_foods(..., custom_foods=...)` with `EnrichmentResult["custom"]` (Task 3).
- Produces: `cli.DEFAULT_CUSTOM_FOODS = "config/custom_foods.yaml"` and the `custom_foods_path` setting.

- [ ] **Step 1: Write the failing tests**

In `tests/test_cli.py`, add this below the imports. Without it, a developer's real `config/custom_foods.yaml` would leak into every CLI test:

```python
CAPRESE_RECIPE = "caprese:\n  foods: [Salat Caprese]\n  ingredients:\n    - {fdc_id: 170457, amount: 1}\n"


@pytest.fixture(autouse=True)
def _custom_foods_in_tmp(monkeypatch, tmp_path):
    """Point the default custom_foods path into tmp_path; it's absent unless a test writes it."""
    monkeypatch.setattr("omni_pilot.cli.DEFAULT_CUSTOM_FOODS", str(tmp_path / "custom_foods.yaml"))
```

Add to `class TestCLIAnalyze`, after `test_analyze_is_silent_when_cache_is_current`:

```python
    def test_analyze_keeps_custom_foods_out_of_translation(self, mocker, tmp_path, capsys):
        _, settings_path, ref_ranges_path = self._write_config(tmp_path, {"Reis": "rice, cooked"})
        (tmp_path / "custom_foods.yaml").write_text(CAPRESE_RECIPE)
        mocker.patch("omni_pilot.cli.parse_food_log", return_value=[food_entry("Reis"), food_entry("Salat Caprese")])
        mock_resolve = mocker.patch(
            "omni_pilot.cli.resolve_and_sync_mappings", return_value={"Reis": "rice, cooked"},
        )
        mock_enrich = mocker.patch("omni_pilot.cli.enrich_all_foods", return_value=enrichment())

        self._run(mocker, settings_path, ref_ranges_path)

        assert f"Custom foods: {tmp_path / 'custom_foods.yaml'}" in capsys.readouterr().out
        assert mock_resolve.call_args.args[0] == ["Reis"]
        assert mock_enrich.call_args.args[0] == ["Reis", "Salat Caprese"]
        assert mock_enrich.call_args.kwargs["custom_foods"]["by_food"] == {"Salat Caprese": "caprese"}

    def test_analyze_exits_on_invalid_custom_foods(self, mocker, tmp_path, capsys):
        _, settings_path, ref_ranges_path = self._write_config(tmp_path, {})
        (tmp_path / "custom_foods.yaml").write_text("caprese: {foods: [], ingredients: []}\n")
        mock_enrich = mocker.patch("omni_pilot.cli.enrich_all_foods")

        with pytest.raises(SystemExit) as exc_info:
            self._run(mocker, settings_path, ref_ranges_path)

        assert exc_info.value.code == 1
        assert f"Error in {tmp_path / 'custom_foods.yaml'}: recipe 'caprese'" in capsys.readouterr().out
        mock_enrich.assert_not_called()

    def test_custom_food_with_stale_cache_entry_is_not_rematched(self, mocker, tmp_path, capsys):
        db_path, settings_path, ref_ranges_path = self._write_config(tmp_path, {"Salat Caprese": "caprese salad"})
        (tmp_path / "custom_foods.yaml").write_text(CAPRESE_RECIPE)
        db = TinyDB(db_path)
        # Left over from before the recipe existed: same query, no match_version
        db.insert({"original_name": "Salat Caprese", "usda_query": "caprese salad", "per_100g": {}})
        db.close()
        mocker.patch("omni_pilot.cli.parse_food_log", return_value=[food_entry("Salat Caprese")])
        mocker.patch("omni_pilot.cli.enrich_all_foods", return_value=enrichment())

        self._run(mocker, settings_path, ref_ranges_path)

        assert "Re-matching" not in capsys.readouterr().out

    def test_analyze_counts_custom_foods_as_resolved(self, mocker, tmp_path, capsys):
        _, settings_path, ref_ranges_path = self._write_config(tmp_path, {"Reis": "rice, cooked"})
        (tmp_path / "custom_foods.yaml").write_text(CAPRESE_RECIPE)
        mocker.patch("omni_pilot.cli.parse_food_log", return_value=[food_entry("Reis"), food_entry("Salat Caprese")])
        caprese = {"recipe": "caprese", "parts": [], "macro_distance": None}
        mocker.patch(
            "omni_pilot.cli.enrich_all_foods",
            return_value=enrichment({"Reis": {}}, custom={"Salat Caprese": caprese}),
        )

        self._run(mocker, settings_path, ref_ranges_path)

        assert "2/2 foods resolved." in capsys.readouterr().out
```

Every existing CLI test now runs with no custom foods file (the fixture points at an absent one), which covers the spec's "a missing file runs normally" case.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -v`
Expected: `AttributeError: <module 'omni_pilot.cli'> has no attribute 'DEFAULT_CUSTOM_FOODS'` in every test (from the autouse fixture).

- [ ] **Step 3: Implement the CLI**

In `src/omni_pilot/cli.py`, add the import:

```python
from omni_pilot.custom_foods import CustomFoodsError, load_custom_foods
```

Add the constant next to the other defaults:

```python
DEFAULT_CUSTOM_FOODS = "config/custom_foods.yaml"
```

In `cmd_analyze`, replace the block from `db_path = ...` up to (but not including) `# 1. Parse Excel Food Log` with:

```python
    db_path = resolve_path("database_path", DEFAULT_DB, settings)
    mappings_path = resolve_path("mappings_path", DEFAULT_MAPPINGS, settings)
    custom_foods_path = resolve_path("custom_foods_path", DEFAULT_CUSTOM_FOODS, settings)

    print(f"Database: {db_path}")
    print(f"Mappings: {mappings_path}")
    print(f"Custom foods: {custom_foods_path}")

    api_key = settings.get("usda_api_key", "")
    if not api_key:
        print("Error: No USDA API key in settings.yaml.")
        sys.exit(1)

    try:
        custom_foods = load_custom_foods(custom_foods_path)
    except CustomFoodsError as e:
        print(f"Error in {custom_foods_path}: {e}")
        sys.exit(1)

```

Replace step 2's call with:

```python
    # 2. Resolve & Sync Mappings (Auto-Translate with Gemini if new foods found).
    # Foods covered by a custom recipe need no translation, so Gemini never sees them.
    translatable = [f for f in food_names if f not in custom_foods["by_food"]]
    mappings = resolve_and_sync_mappings(translatable, db_path, mappings_path, settings)
```

In step 3, change the three affected lines to:

```python
    outdated = count_outdated_matches(translatable, mappings, db)
```

```python
    enrichment = enrich_all_foods(food_names, mappings, logged_macros, db, api_key, custom_foods=custom_foods)
    resolved = len(enrichment["profiles"]) + len(enrichment["custom"])
    print(f"  {resolved}/{len(food_names)} foods resolved.")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -v`
Expected: all PASS.

- [ ] **Step 5: Update settings, Makefile and CLAUDE.md**

In `config/settings.example.yaml`, replace the storage-paths block with:

```yaml
# Storage paths (supports ~ and absolute/relative paths)
database_path: "db/food_db.json"
mappings_path: "config/food_mappings.yaml"
# Optional custom food recipes (see config/custom_foods.example.yaml)
custom_foods_path: "config/custom_foods.yaml"
# Example for macOS iCloud sync:
# database_path: "~/Library/Mobile Documents/com~apple~CloudDocs/OmniPilot/db/food_db.json"
# mappings_path: "~/Library/Mobile Documents/com~apple~CloudDocs/OmniPilot/config/food_mappings.yaml"
# custom_foods_path: "~/Library/Mobile Documents/com~apple~CloudDocs/OmniPilot/config/custom_foods.yaml"
```

In `Makefile`'s `sync-iphone` target, add this line after `--exclude='config/supplements.yaml' \`. The target runs `rsync --delete`, which would otherwise delete a `custom_foods.yaml` kept only in iCloud:

```make
		--exclude='config/custom_foods.yaml' \
```

In `CLAUDE.md`, make these edits:

1. Replace the "Config files must be created…" sentence with:
   "Config files must be created from templates before first run (`config/settings.yaml`, `config/supplements.yaml`, `config/food_mappings.yaml` from their `*.example.yaml` counterparts). `config/custom_foods.yaml` is optional (template: `config/custom_foods.example.yaml`); like the other personal config files it is gitignored, excluded from `make sync-iphone`, and can live in iCloud via `custom_foods_path`. `config/settings.yaml` requires a USDA FoodData Central API key; a Gemini API key is optional but enables automatic food translation."
2. In the Architecture intro, change "(plus `matcher.py`, used by the enricher)" to "(plus `matcher.py`, used by the enricher, and `custom_foods.py`)".
3. At the end of the `enricher.py` bullet, append:
   "Foods listed in a custom recipe (see `custom_foods.py`) bypass this path entirely: each recipe ingredient is fetched by FDC ID (`fetch_usda_food`, abridged `/food/{id}` endpoint), cached forever in the `usda_foods` table, and the food lands in `EnrichmentResult.custom` with its parts and a per-food `macro_distance` — never in `profiles` or `low_confidence`."
4. After the `matcher.py` sub-bullet, add this sub-bullet:
   "   - **`custom_foods.py`** — Loads and validates the human-owned `custom_foods.yaml`: recipes of USDA ingredients (FDC ID + relative amount, scaled to shares) and the logged food names each recipe stands for. Invalid files raise `CustomFoodsError`, which stops the CLI. `recipe_macros` gives a recipe's share-weighted macros for the macro check. The CLI keeps recipe foods out of `resolve_and_sync_mappings`, so Gemini never translates them."
5. In the `analyzer.py` bullet, after "…booked as unmeasured rather than zero-filled.", insert: "A custom food is counted part by part (one part per recipe ingredient, weighing its share of the entry), so an ingredient missing a nutrient costs only its own share of coverage; `coverage.custom_recipes` lists the recipes used, their ingredients and each food's grams and macro check."
6. In the `reporter.py` bullet, after the low-confidence sentence, insert: "A Custom foods line (terminal) and section at the bottom of the HTML report show each recipe, its ingredients and the foods that used it, with `macros off N%` (⚠ above `GOOD_DISTANCE`)."
7. Replace the data flow block with:

```
xlsx → parser.parse_food_log → entries
entries → parser.extract_unique_foods → food_names
custom_foods.yaml → custom_foods.load_custom_foods → custom_foods
food_names minus custom foods → translator.resolve_and_sync_mappings (TinyDB + YAML + Gemini) → mappings
entries → matcher.logged_macros_per_100g → logged_macros
food_names, mappings, logged_macros, custom_foods → enricher.enrich_all_foods (TinyDB cache + USDA API + matcher) → EnrichmentResult
entries, EnrichmentResult, ref_ranges, supplements → analyzer.analyze → AnalysisResult
AnalysisResult → reporter.print_terminal_report / generate_html_report
```

8. Append these to "Key invariants":
   - "A custom recipe takes precedence over any mapping for its foods, including `skip`. Custom foods are never in `EnrichmentResult.profiles`: the analyzer counts them part by part, which is what keeps a missing nutrient in one ingredient from zero-filling or hiding the others."
   - "The `usda_foods` table (custom-food ingredients) is keyed by `fdc_id` and never invalidated — an FDC ID always names the same food. A failed ingredient fetch caches nothing and makes every food of that recipe unresolved for the run."
9. In the last invariant, change "USDA nutrient profiles (default table, written by `enricher.py`)" to "USDA nutrient profiles (default table keyed by food name, and `usda_foods` keyed by FDC ID, both written by `enricher.py`)".

- [ ] **Step 6: Run the full suite and lint**

Run: `uv run pytest -q && uv run ruff check .`
Expected: all pass.

Do **not** run `make analyze` as a smoke test. The local `config/settings.yaml` points `database_path` and `mappings_path` at the user's production iCloud files and has a Gemini key, so a run would write translations and cache entries into real data. The user does the first real run once they have written their `custom_foods.yaml`.

- [ ] **Step 7: Commit**

```bash
git add src/omni_pilot/cli.py tests/test_cli.py config/settings.example.yaml Makefile CLAUDE.md
git commit -m "feat: wire custom foods into the analyze command (BAR-75)"
```

---

### Task 7: End-to-end integration test

**Files:**
- Create: `tests/test_custom_foods_integration.py`

**Interfaces:**
- Consumes: the whole pipeline through `omni_pilot.cli.main`, as wired in Tasks 1–6.
- Produces: nothing new. This task only adds a test.

This test runs the **real** `analyze` command end to end: a real `.xlsx` file, real config files, a real TinyDB, the real translator, enricher, analyzer and reporters, and a real HTML file. The only thing faked is the network. USDA is replaced by a small in-memory stand-in that records every request, and any Gemini call fails the test. Everything lives in `tmp_path`, so the user's iCloud files are never touched.

It covers the user's actual workflow in three runs:
1. **First run.** Recipe ingredients are fetched by ID once each; custom foods never reach USDA search or Gemini; the numbers and the HTML section are right.
2. **Second run, nothing changed.** No USDA requests at all, because everything is cached.
3. **Amounts tuned in the YAML.** Still no USDA requests; the nutrients and the macro check change, and the ⚠ disappears.

- [ ] **Step 1: Write the test**

Create `tests/test_custom_foods_integration.py`:

```python
"""End-to-end: the real analyze command with custom foods. Only the network is faked."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import openpyxl
import pytest
import yaml
from tinydb import TinyDB

import omni_pilot.cli as cli
from omni_pilot.enricher import USDA_SEARCH_URL

REF_RANGES_PATH = str(Path(__file__).resolve().parents[1] / "config" / "reference_ranges.yaml")


def _nutrients(**by_number: float) -> list[dict]:
    """Abridged /food/{id} nutrients from {"n203": 0.88, ...}."""
    return [{"number": key[1:], "amount": value} for key, value in by_number.items()]


# Abridged /food/{id} responses. Values are real SR Legacy figures, trimmed.
USDA_FOODS = {
    170457: {"fdcId": 170457, "description": "Tomatoes, red, ripe, raw, year round average", "dataType": "SR Legacy",
             "foodNutrients": _nutrients(n203=0.88, n204=0.2, n205=3.89, n291=1.2, n301=10.0, n430=7.9)},
    # No vitamin K (430) value: exercises partial coverage
    170845: {"fdcId": 170845, "description": "Cheese, mozzarella, whole milk", "dataType": "SR Legacy",
             "foodNutrients": _nutrients(n203=22.2, n204=22.1, n205=2.4, n291=0.0, n301=505.0)},
    169249: {"fdcId": 169249, "description": "Lettuce, green leaf, raw", "dataType": "SR Legacy",
             "foodNutrients": _nutrients(n203=1.36, n204=0.15, n205=2.87, n291=1.3, n301=36.0, n430=126.3)},
}
# A search hit (search endpoint shape) for the one food that goes through USDA search.
RICE_HIT = {
    "fdcId": 900001, "description": "Rice, white, long-grain, regular, enriched, cooked", "dataType": "SR Legacy",
    "foodNutrients": [
        {"nutrientNumber": number, "value": value}
        for number, value in (("203", 2.69), ("204", 0.28), ("205", 28.17), ("291", 0.4), ("301", 10.0), ("430", 0.0))
    ],
}


class FakeUsda:
    """Stands in for USDA FoodData Central (search and fetch-by-ID), recording every request."""

    def __init__(self):
        self.searches: list[str] = []
        self.fetched_ids: list[int] = []

    def __call__(self, url, params=None, timeout=None):
        response = Mock(status_code=200)
        if url == USDA_SEARCH_URL:
            self.searches.append(params["query"])
            response.json.return_value = {"foods": [RICE_HIT]}
        else:
            fdc_id = int(url.rsplit("/", 1)[1])
            self.fetched_ids.append(fdc_id)
            response.json.return_value = USDA_FOODS[fdc_id]
        return response


def _write_export(path: Path) -> None:
    """A minimal MacroFactor export: the "Food Log" sheet in the parser's column layout."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Food Log"
    ws.append(["Date", "Time", "Food Name", "Serving Size", "Serving Qty", "Serving Weight (g)",
               "Calories (kcal)", "Fat (g)", "Carbs (g)", "Protein (g)"])
    ws.append(["2026-08-09", "12:00", "Salat Caprese", "g", 1, 264, 195.0, 11.1, 7.9, 12.9])
    ws.append(["2026-08-09", "13:00", "Misch Salat Rohkost", "g", 1, 250, 48.0, 1.0, 4.0, 3.0])
    ws.append(["2026-08-09", "19:00", "Reis", "g", 1, 200, 260.0, 0.6, 56.3, 5.4])
    ws.append(["2026-08-09", "20:00", "Wasser", "ml", 1, 500, 0.0, 0.0, 0.0, 0.0])
    wb.save(path)


def _write_custom_foods(path: Path, tomato: int, mozzarella: int) -> None:
    path.write_text(
        "caprese:\n"
        "  foods: [Salat Caprese]\n"
        "  ingredients:\n"
        f"    - {{fdc_id: 170457, amount: {tomato}}}\n"
        f"    - {{fdc_id: 170845, amount: {mozzarella}}}\n"
        "green_salad:\n"
        "  foods: [Misch Salat Rohkost]\n"
        "  ingredients:\n"
        "    - {fdc_id: 169249, amount: 100}\n"
    )


@pytest.fixture
def workspace(tmp_path, monkeypatch, mocker):
    """Config, export and database in tmp_path; USDA faked; Gemini forbidden."""
    xlsx = tmp_path / "export.xlsx"
    _write_export(xlsx)
    _write_custom_foods(tmp_path / "custom_foods.yaml", tomato=75, mozzarella=25)
    (tmp_path / "food_mappings.yaml").write_text(yaml.dump({"mappings": {
        "Reis": "rice, white, long-grain, regular, cooked",
        "Wasser": "skip",
        # A translation from before the recipe existed: must be ignored, and kept
        "Salat Caprese": "caprese salad",
    }}))
    (tmp_path / "settings.yaml").write_text(yaml.dump({
        "usda_api_key": "fake-usda-key",  # no gemini_api_key
        "database_path": str(tmp_path / "food_db.json"),
        "mappings_path": str(tmp_path / "food_mappings.yaml"),
        "custom_foods_path": str(tmp_path / "custom_foods.yaml"),
    }))
    monkeypatch.chdir(tmp_path)  # reports/ and the default supplements path resolve inside tmp_path

    usda = FakeUsda()
    mocker.patch("omni_pilot.enricher.requests.get", side_effect=usda)
    mocker.patch("omni_pilot.enricher.time.sleep")
    mocker.patch(
        "omni_pilot.translator.requests.post",
        side_effect=AssertionError("Gemini must never be called in tests"),
    )
    analyze_spy = mocker.spy(cli, "analyze")

    def run():
        mocker.patch("sys.argv", [
            "omni_pilot", "analyze", str(xlsx),
            "--settings", str(tmp_path / "settings.yaml"),
            "--ref-ranges", REF_RANGES_PATH,
            "--html",
        ])
        cli.main()
        return analyze_spy.spy_return, (tmp_path / "reports" / "latest.html").read_text()

    return tmp_path, usda, run


def test_custom_foods_end_to_end(workspace, capsys):
    tmp_path, usda, run = workspace

    # --- Run 1: ingredients fetched once by ID; custom foods never searched or translated
    result, html = run()
    out = capsys.readouterr().out

    assert sorted(usda.fetched_ids) == [169249, 170457, 170845]
    assert usda.searches and all("rice" in query for query in usda.searches)
    assert "3/4 foods resolved." in out
    assert "Custom foods: Misch Salat Rohkost → green_salad" in out

    nutrients = result["nutrients"]
    # Caprese: 198 g tomato x 10 + 66 g mozzarella x 505; lettuce 250 g x 36; rice 200 g x 10 (mg/100 g)
    assert nutrients["calcium_mg"]["daily_avg"] == pytest.approx(463.1)
    # Only the mozzarella's 66 g lacks vitamin K: 648 of 714 analysed grams measured
    assert nutrients["vitamin_k_mcg"]["coverage_pct"] == 90.8
    assert result["coverage"]["skipped_foods"] == ["Wasser"]
    assert result["coverage"]["unresolved_foods"] == []
    assert result["coverage"]["low_confidence_foods"] == []

    assert "<h2>Custom foods</h2>" in html
    assert "Cheese, mozzarella, whole milk (FDC 170845) — 25%" in html
    assert "Salat Caprese — 264 g — ⚠ macros off 27%" in html
    assert "Misch Salat Rohkost — 250 g — macros off" in html

    db = TinyDB(str(tmp_path / "food_db.json"))
    assert sorted(entry["fdc_id"] for entry in db.table("usda_foods").all()) == [169249, 170457, 170845]
    db.close()
    # The old translation stays in the YAML, unused while the recipe claims the food
    mappings = yaml.safe_load((tmp_path / "food_mappings.yaml").read_text())["mappings"]
    assert mappings["Salat Caprese"] == "caprese salad"

    # --- Run 2: nothing changed, so USDA isn't contacted at all
    fetched, searched = len(usda.fetched_ids), len(usda.searches)
    run()
    assert (len(usda.fetched_ids), len(usda.searches)) == (fetched, searched)

    # --- Run 3: amounts tuned in the YAML; recomputed from the cache, no USDA requests
    _write_custom_foods(tmp_path / "custom_foods.yaml", tomato=82, mozzarella=18)
    result, html = run()

    assert (len(usda.fetched_ids), len(usda.searches)) == (fetched, searched)
    # 216.48 g tomato x 10 + 47.52 g mozzarella x 505 + lettuce 90 + rice 20
    assert result["nutrients"]["calcium_mg"]["daily_avg"] == pytest.approx(371.62)
    assert "Salat Caprese — 264 g — macros off 4%" in html
    assert "⚠ macros off" not in html
```

- [ ] **Step 2: Run the test to verify it passes**

Run: `uv run pytest tests/test_custom_foods_integration.py -v`
Expected: PASS.

Tasks 1–6 already built everything, so this test should pass the first time. If it fails, the failure is a real integration bug between tasks. Debug it with superpowers:systematic-debugging; don't loosen the assertions. The expected figures were worked out by hand from the fixture values:
- calcium, run 1: 19.8 + 333.3 + 90 + 20 = 463.1;
- vitamin K coverage: 648 / 714 = 90.8%;
- Caprese macro distance: 0.271 at 75/25 and 0.036 at 82/18, against the logged P 4.89 / F 4.20 / C 2.99 per 100 g.

- [ ] **Step 3: Check that the test can fail**

Temporarily change `enrich_all_foods` so it skips the custom-food branch (for example, `recipe_name = None`), then run the test again.
Expected: FAIL. The caprese is then searched and translated, so the Gemini guard or the search assertion trips. Revert the change and confirm the test passes again.

- [ ] **Step 4: Run the full suite and lint, then commit**

Run: `uv run pytest -q && uv run ruff check .`
Expected: all pass.

```bash
git add tests/test_custom_foods_integration.py
git commit -m "test: add end-to-end custom foods integration test (BAR-75)"
```
