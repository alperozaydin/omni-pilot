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
