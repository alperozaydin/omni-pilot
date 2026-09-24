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
