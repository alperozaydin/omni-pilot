"""Tests for the custom-foods skill's recipe_tool.py.

They run the installed copy under .agents/skills. Set RECIPE_TOOL to another
copy's path (e.g. a my-skills working copy) to test that one instead.
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest
import yaml
from tinydb import TinyDB

from omni_pilot.enricher import USDA_FOODS_TABLE, USDA_NUTRIENT_MAP
from omni_pilot.translator import set_mapping

INSTALLED_TOOL = (
    Path(__file__).resolve().parents[1] / ".agents/skills/omni-pilot-custom-foods/scripts/recipe_tool.py"
)


@pytest.fixture(scope="module")
def tool():
    path = Path(os.environ.get("RECIPE_TOOL", INSTALLED_TOOL)).expanduser()
    spec = importlib.util.spec_from_file_location("recipe_tool", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _entry(name: str, fdc_id: int | None, usda_name: str, **fields) -> dict:
    """A main-table match entry as the enricher writes it."""
    entry = {
        "original_name": name,
        "usda_query": name.lower(),
        "usda_name": usda_name,
        "usda_fdc_id": fdc_id,
        "usda_dataset": "SR Legacy",
        "per_100g": {"vitamin_a_mcg": 1.0},
        "confidence": "good",
        "macro_distance": 0.05,
        "match_version": 1,
        **fields,
    }
    if fdc_id is None:
        del entry["usda_fdc_id"]
    return entry


EGGS = _entry("Boiled Eggs", 173424, "Egg, whole, cooked, hard-boiled")
MELON = _entry("Watermelon, Fresh", 167765, "Watermelon, raw")


class Workspace:
    """A temporary omni-pilot root with settings, database, recipes and mappings."""

    def __init__(self, root: Path):
        self.root = root
        (root / "config").mkdir()
        (root / "config/settings.yaml").write_text(yaml.safe_dump({
            "usda_api_key": "fake-key",
            "database_path": "db/food_db.json",
            "mappings_path": "config/food_mappings.yaml",
            "custom_foods_path": "config/custom_foods.yaml",
        }))
        self.db_path = root / "db/food_db.json"

    def add(self, *entries: dict) -> None:
        self.db_path.parent.mkdir(exist_ok=True)
        with TinyDB(self.db_path) as db:
            db.insert_multiple(entries)

    def translate(self, name: str, english: str) -> None:
        self.db_path.parent.mkdir(exist_ok=True)
        with TinyDB(self.db_path) as db:
            db.table("translations").insert({"german": name, "english": english})

    def recipe(self, recipe_name: str, *foods: str) -> None:
        (self.root / "config/custom_foods.yaml").write_text(yaml.safe_dump({
            recipe_name: {"foods": list(foods), "ingredients": [{"fdc_id": 170457, "amount": 1}]},
        }))

    def skip(self, *foods: str) -> None:
        (self.root / "config/food_mappings.yaml").write_text(
            yaml.safe_dump({"mappings": {food: "skip" for food in foods}})
        )

    def entries(self) -> dict[str, dict]:
        with TinyDB(self.db_path) as db:
            return {e["original_name"]: dict(e) for e in db.all()}


@pytest.fixture
def ws(tmp_path) -> Workspace:
    return Workspace(tmp_path)


@pytest.fixture
def run(tool, tmp_path, monkeypatch, capsys):
    """Run the tool from the workspace root; returns (exit code, stdout)."""
    monkeypatch.chdir(tmp_path)

    def _run(*args: str) -> tuple[int, str]:
        monkeypatch.setattr(sys, "argv", ["recipe_tool.py", *args])
        try:
            tool.main()
            code = 0
        except SystemExit as e:
            code = e.code if isinstance(e.code, int) else 1
        return code, capsys.readouterr().out

    return _run


class TestApprove:
    def test_copies_the_match_id_into_the_review_mark(self, ws, run):
        ws.add(EGGS)

        code, out = run("approve", "Boiled Eggs")

        assert code == 0
        assert "approved  Boiled Eggs → Egg, whole, cooked, hard-boiled (173424)" in out
        assert "1 approved" in out
        entry = ws.entries()["Boiled Eggs"]
        assert entry["reviewed_fdc_id"] == 173424
        assert {k: v for k, v in entry.items() if k != "reviewed_fdc_id"} == EGGS

    def test_approves_every_named_food_including_names_with_commas(self, ws, run):
        other = _entry("Jasmin-Reis", 168930, "Rice, white, long-grain, regular, raw, unenriched")
        ws.add(EGGS, MELON, other)

        code, out = run("approve", "Boiled Eggs", "Watermelon, Fresh")

        assert code == 0
        assert "2 approved" in out
        entries = ws.entries()
        assert entries["Boiled Eggs"]["reviewed_fdc_id"] == 173424
        assert entries["Watermelon, Fresh"]["reviewed_fdc_id"] == 167765
        assert "reviewed_fdc_id" not in entries["Jasmin-Reis"]

    def test_a_name_given_twice_is_approved_once(self, ws, run):
        ws.add(EGGS)

        code, out = run("approve", "Boiled Eggs", "Boiled Eggs")

        assert code == 0
        assert out.count("approved  Boiled Eggs") == 1
        assert "1 approved" in out

    def test_approving_an_approved_food_changes_nothing(self, ws, run):
        ws.add({**EGGS, "reviewed_fdc_id": 173424})

        code, out = run("approve", "Boiled Eggs")

        assert code == 0
        assert "already approved  Boiled Eggs" in out
        assert "0 approved" in out
        assert ws.entries()["Boiled Eggs"] == {**EGGS, "reviewed_fdc_id": 173424}

    def test_approving_a_changed_food_moves_the_mark_to_the_new_match(self, ws, run):
        ws.add({**EGGS, "reviewed_fdc_id": 999})

        code, out = run("approve", "Boiled Eggs")

        assert code == 0
        assert "approved  Boiled Eggs" in out
        assert "already approved" not in out
        assert ws.entries()["Boiled Eggs"]["reviewed_fdc_id"] == 173424

    def test_without_a_database_it_stops_and_creates_none(self, ws, run):
        code, _ = run("approve", "Boiled Eggs")

        assert code == 1
        assert not ws.db_path.exists()

    @pytest.mark.parametrize(("setup", "name", "reason"), [
        (lambda ws: None, "Boiled Egs", "not a searched food"),
        (
            lambda ws: (ws.add(_entry("Salat Caprese", 170457, "Tomatoes, red, ripe, raw")),
                        ws.recipe("caprese", "Salat Caprese")),
            "Salat Caprese", "in recipe caprese",
        ),
        (
            lambda ws: (ws.add(_entry("Mineralwasser", 174158, "Beverages, water, bottled")),
                        ws.skip("Mineralwasser")),
            "Mineralwasser", "skip",
        ),
        (
            lambda ws: (ws.add(_entry("Magnesium", 174158, "Beverages, water, bottled")),
                        ws.translate("Magnesium", "skip")),
            "Magnesium", "skip",
        ),
        (
            lambda ws: ws.add({"original_name": "Braun Linsen", "usda_query": "lentils", "not_found": True,
                               "match_version": 1, "last_updated": "2026-10-01"}),
            "Braun Linsen", "no USDA match",
        ),
        (
            lambda ws: ws.add(_entry("Risotto Alla Milanese", 168943, "Rice and vermicelli mix",
                                     confidence="weak", macro_distance=0.34)),
            "Risotto Alla Milanese", "weak match (macros off 34%)",
        ),
        (
            lambda ws: ws.add(_entry("Haferflocken", None, "Cereals, oats, regular and quick, not fortified, dry")),
            "Haferflocken", "run analyze first",
        ),
    ], ids=["unknown-name", "in-a-recipe", "skipped-in-file", "skipped-in-database", "not-found",
            "weak", "no-usda-id"])
    def test_a_refused_name_stops_the_whole_call(self, ws, run, setup, name, reason):
        ws.add(EGGS)
        setup(ws)

        code, out = run("approve", "Boiled Eggs", name)

        assert code == 1
        assert f"✗ {name}: " in out
        assert reason in out
        assert "Nothing approved." in out
        assert all("reviewed_fdc_id" not in e for e in ws.entries().values())


LENTILS_NOT_FOUND = {"original_name": "Braun Linsen", "usda_query": "lentils", "not_found": True,
                     "match_version": 1, "last_updated": "2026-10-01"}


class TestDefer:
    def test_marks_a_match_with_its_usda_id(self, ws, run):
        ws.add(EGGS)

        code, out = run("defer", "Boiled Eggs")

        assert code == 0
        assert "left  Boiled Eggs → Egg, whole, cooked, hard-boiled (173424)" in out
        assert "1 left" in out
        assert ws.entries()["Boiled Eggs"] == {**EGGS, "deferred_fdc_id": 173424}

    def test_marks_a_not_found_record_with_its_query(self, ws, run):
        ws.add(LENTILS_NOT_FOUND)

        code, out = run("defer", "Braun Linsen")

        assert code == 0
        assert "left  Braun Linsen (not in USDA: lentils)" in out
        assert ws.entries()["Braun Linsen"] == {**LENTILS_NOT_FOUND, "deferred_query": "lentils"}

    def test_defers_every_named_food_including_names_with_commas(self, ws, run):
        other = _entry("Jasmin-Reis", 168930, "Rice, white, long-grain, regular, raw, unenriched")
        ws.add(EGGS, MELON, other)

        code, out = run("defer", "Boiled Eggs", "Watermelon, Fresh")

        assert code == 0
        assert "2 left" in out
        entries = ws.entries()
        assert entries["Boiled Eggs"]["deferred_fdc_id"] == 173424
        assert entries["Watermelon, Fresh"]["deferred_fdc_id"] == 167765
        assert "deferred_fdc_id" not in entries["Jasmin-Reis"]

    @pytest.mark.parametrize(("flag", "reason"), [("--impossible", "impossible"), ("--no-macros", "no_macros")])
    def test_reason_flags_write_the_reason(self, ws, run, flag, reason):
        ws.add(EGGS)

        code, _ = run("defer", flag, "Boiled Eggs")

        assert code == 0
        assert ws.entries()["Boiled Eggs"] == {**EGGS, "deferred_fdc_id": 173424, "deferred_reason": reason}

    def test_without_a_flag_a_stale_reason_is_removed(self, ws, run):
        ws.add({**EGGS, "deferred_fdc_id": 999, "deferred_reason": "impossible"})

        code, _ = run("defer", "Boiled Eggs")

        assert code == 0
        assert ws.entries()["Boiled Eggs"] == {**EGGS, "deferred_fdc_id": 173424}

    def test_deferring_a_deferred_food_changes_nothing(self, ws, run):
        ws.add({**EGGS, "deferred_fdc_id": 173424})

        code, out = run("defer", "Boiled Eggs")

        assert code == 0
        assert "already left  Boiled Eggs" in out
        assert "0 left" in out
        assert ws.entries()["Boiled Eggs"] == {**EGGS, "deferred_fdc_id": 173424}

    @pytest.mark.parametrize(("setup", "name", "reason"), [
        (lambda ws: None, "Boiled Egs", "not a searched food"),
        (
            lambda ws: (ws.add(_entry("Salat Caprese", 170457, "Tomatoes, red, ripe, raw")),
                        ws.recipe("caprese", "Salat Caprese")),
            "Salat Caprese", "in recipe caprese",
        ),
        (
            lambda ws: (ws.add(_entry("Mineralwasser", 174158, "Beverages, water, bottled")),
                        ws.skip("Mineralwasser")),
            "Mineralwasser", "skip",
        ),
        (
            lambda ws: ws.add(_entry("Gouda", 171242, "Cheese, gouda", reviewed_fdc_id=171242)),
            "Gouda", "is approved; nothing to leave",
        ),
        (
            lambda ws: ws.add(_entry("Haferflocken", None, "Cereals, oats, regular and quick, not fortified, dry")),
            "Haferflocken", "run analyze first",
        ),
    ], ids=["unknown-name", "in-a-recipe", "skipped", "approved", "no-usda-id"])
    def test_a_refused_name_stops_the_whole_call(self, ws, run, setup, name, reason):
        ws.add(EGGS)
        setup(ws)

        code, out = run("defer", "Boiled Eggs", name)

        assert code == 1
        assert f"✗ {name}: " in out
        assert reason in out
        assert "Nothing written." in out
        assert all(not any(k.startswith("deferred_") for k in e) for e in ws.entries().values())

    def test_the_flags_cannot_be_combined(self, ws, run):
        ws.add(EGGS)

        code, _ = run("defer", "--impossible", "--no-macros", "Boiled Eggs")

        assert code != 0
        assert ws.entries()["Boiled Eggs"] == EGGS


def _export(ws, *rows: list) -> None:
    from tests.helpers import write_food_log

    (ws.root / "data").mkdir(exist_ok=True)
    write_food_log(ws.root / "data/export.xlsx", list(rows))


# Boiled eggs as logged: 100 g with plausible macros, and with impossible ones (200 g of macros in 100 g).
EGGS_ROW = ["2026-10-15", "08:00", "Boiled Eggs", "g", 100, 1, 155.0, 10.6, 1.1, 12.6]
EGGS_IMPOSSIBLE_ROW = ["2026-10-15", "08:00", "Boiled Eggs", "g", 100, 1, 1200.0, 100.0, 50.0, 50.0]


class TestPending:
    def test_lists_new_changed_weak_and_not_found_foods(self, ws, run):
        weak = _entry("Risotto Alla Milanese", 168943, "Rice and vermicelli mix",
                      confidence="weak", macro_distance=0.34)
        ws.add(EGGS, {**MELON, "reviewed_fdc_id": 999}, weak, LENTILS_NOT_FOUND)

        code, out = run("pending")

        assert code == 0
        assert "== Searched in USDA (3): is each match the same food?" in out
        assert "== Not in USDA (1): give them a recipe" in out
        for name in ("Boiled Eggs", "Watermelon, Fresh", "Risotto Alla Milanese", "Braun Linsen"):
            assert name in out

    def test_hides_approved_deferred_recipe_and_skipped_foods(self, ws, run):
        ws.add(
            EGGS,
            _entry("Gouda", 171242, "Cheese, gouda", reviewed_fdc_id=171242),
            {**MELON, "deferred_fdc_id": 167765},
            _entry("Salat Caprese", 170457, "Tomatoes, red, ripe, raw"),
            _entry("Mineralwasser", 174158, "Beverages, water, bottled"),
            {**LENTILS_NOT_FOUND, "deferred_query": "lentils"},
        )
        ws.recipe("caprese", "Salat Caprese")
        ws.skip("Mineralwasser")

        code, out = run("pending")

        assert code == 0
        assert "== Searched in USDA (1)" in out
        assert "Boiled Eggs" in out
        for name in ("Gouda", "Watermelon, Fresh", "Salat Caprese", "Mineralwasser", "Braun Linsen", "Not in USDA"):
            assert name not in out

    def test_nothing_to_review(self, ws, run):
        ws.add(_entry("Gouda", 171242, "Cheese, gouda", reviewed_fdc_id=171242))

        code, out = run("pending")

        assert code == 0
        assert out == "Nothing to review.\n"

    def test_a_deferred_food_returns_when_its_match_changes(self, ws, run):
        ws.add({**EGGS, "deferred_fdc_id": 999}, {**LENTILS_NOT_FOUND, "deferred_query": "old text"})

        _, out = run("pending")

        assert "Boiled Eggs" in out
        assert "Braun Linsen" in out

    def test_impossible_reason_returns_once_the_weight_is_fixed(self, ws, run):
        ws.add({**EGGS, "deferred_fdc_id": 173424, "deferred_reason": "impossible"})
        _export(ws, EGGS_ROW)

        _, out = run("pending")

        assert "Boiled Eggs" in out
        assert "    left for the user before (impossible logged macros); the logged macros are fine now" in out

    def test_impossible_reason_returns_when_only_older_exports_hold_the_wrong_weight(self, ws, run):
        # S3 keeps every export, so the wrong entries stay in the old ones after the fix.
        from tests.helpers import write_food_log

        ws.add({**EGGS, "deferred_fdc_id": 173424, "deferred_reason": "impossible"})
        (ws.root / "data").mkdir()
        wrong = [[f"2026-10-{day:02d}", "08:00", "Boiled Eggs", "g", 20, 1, 155.0, 10.6, 1.1, 36.9]
                 for day in range(1, 11)]
        write_food_log(ws.root / "data/2026-10-01_2026-10-10.xlsx", wrong)
        write_food_log(ws.root / "data/2026-10-11_2026-10-11.xlsx",
                       [["2026-10-11", "08:00", "Boiled Eggs", "g", 200, 1, 310.0, 21.2, 2.2, 25.2]])

        _, out = run("pending")

        assert "Boiled Eggs" in out
        assert "the logged macros are fine now" in out

    def test_impossible_reason_stays_while_the_macros_are_impossible(self, ws, run):
        ws.add({**EGGS, "deferred_fdc_id": 173424, "deferred_reason": "impossible"})
        _export(ws, EGGS_IMPOSSIBLE_ROW)

        _, out = run("pending")

        assert out == "Nothing to review.\n"

    def test_impossible_reason_without_logged_macros_stays_deferred(self, ws, run):
        ws.add({**EGGS, "deferred_fdc_id": 173424, "deferred_reason": "impossible"})

        _, out = run("pending")

        assert out == "Nothing to review.\n"

    def test_no_macros_reason_returns_once_an_export_has_the_food(self, ws, run):
        ws.add({**EGGS, "deferred_fdc_id": 173424, "deferred_reason": "no_macros"})
        _, before = run("pending")
        _export(ws, EGGS_ROW)

        _, after = run("pending")

        assert before == "Nothing to review.\n"
        assert "Boiled Eggs" in after
        assert "    left for the user before (no logged macros); an export has them now" in after

    def test_an_approved_food_is_not_pending_even_if_deferred(self, ws, run):
        ws.add({**EGGS, "deferred_fdc_id": 173424, "reviewed_fdc_id": 173424})

        _, out = run("pending")

        assert out == "Nothing to review.\n"

    def test_a_food_whose_lookup_text_changed_is_no_longer_pending(self, ws, run):
        ws.add(EGGS)
        _, before = run("pending")

        set_mapping("Boiled Eggs", "egg, whole, cooked, hard-boiled", str(ws.db_path),
                    str(ws.root / "config/food_mappings.yaml"))
        _, after = run("pending")

        assert "Boiled Eggs" in before
        assert after == "Nothing to review.\n"

    def test_entry_without_usda_id_is_not_pending(self, ws, run):
        ws.add(_entry("Haferflocken", None, "Cereals, oats, regular and quick, not fortified, dry"))

        _, out = run("pending")

        assert out == "Nothing to review.\n"

    def test_lists_an_approved_match_that_turned_weak(self, ws, run):
        ws.add({**EGGS, "confidence": "weak", "macro_distance": 0.34, "reviewed_fdc_id": 173424})

        _, out = run("pending")

        assert "Boiled Eggs" in out
        assert "approved before as FDC 173424; the match is now weak" in out

    def test_limit_keeps_the_heaviest(self, ws, run):
        ws.add(_entry("Light", 1, "Light food"), _entry("Heavy", 2, "Heavy food"), _entry("Middle", 3, "Middle food"))
        _export(ws, *[["2026-10-15", "12:00", name, "g", grams, 1, 100.0, 1.0, 10.0, 10.0]
                      for name, grams in (("Light", 100), ("Heavy", 300), ("Middle", 200))])

        code, out = run("pending", "--limit", "2")

        assert code == 0
        assert "== Searched in USDA (2)" in out
        assert out.index("Heavy") < out.index("Middle")
        assert "Light" not in out
        assert out.rstrip().endswith("2 of 3 shown; the rest wait for the next run")


class TestReview:
    def test_hides_approved_matches_and_counts_both_sides(self, ws, run):
        ws.add({**EGGS, "reviewed_fdc_id": 173424}, MELON)

        code, out = run("review")

        assert code == 0
        assert "== Searched in USDA (1 to review, 1 approved): is each match the same food?" in out
        assert "Watermelon, Fresh" in out
        assert "Boiled Eggs" not in out

    def test_lists_a_changed_match_with_the_approved_id(self, ws, run):
        ws.add({**EGGS, "usda_fdc_id": 172187, "usda_name": "Egg, whole, raw, fresh", "reviewed_fdc_id": 173424})

        code, out = run("review")

        assert code == 0
        assert "(1 to review, 0 approved)" in out
        assert "Boiled Eggs" in out
        assert "approved before as FDC 173424; the match has changed since" in out

    def test_all_lists_approved_matches_marked(self, ws, run):
        ws.add({**EGGS, "reviewed_fdc_id": 173424}, MELON)

        code, out = run("review", "--all")

        assert code == 0
        assert "(1 to review, 1 approved)" in out
        assert "matched to  : Egg, whole, cooked, hard-boiled  [good, off 5%]  (approved)" in out
        assert "matched to  : Watermelon, raw  [good, off 5%]\n" in out

    def test_flagged_ignores_review_marks(self, ws, run):
        weak = _entry("Risotto Alla Milanese", 168943, "Rice and vermicelli mix",
                      confidence="weak", macro_distance=0.34)
        ws.add(weak, EGGS)
        _, before = run("flagged")
        run("approve", "Boiled Eggs")

        _, after = run("flagged")

        assert "Risotto Alla Milanese" in before
        assert after == before

    def test_marks_a_food_left_for_the_user(self, ws, run):
        ws.add({**EGGS, "deferred_fdc_id": 173424}, {**LENTILS_NOT_FOUND, "deferred_query": "lentils"})

        code, out = run("review")

        assert code == 0
        assert ("matched to  : Egg, whole, cooked, hard-boiled  [good, off 5%]\n"
                "    left for the user by the daily review\n") in out
        assert "    looked up as: lentils\n    left for the user by the daily review\n" in out

    def test_a_stale_deferral_is_not_marked(self, ws, run):
        ws.add({**EGGS, "deferred_fdc_id": 999})

        _, out = run("review")

        assert "Boiled Eggs" in out
        assert "left for the user" not in out

    def test_lists_an_approved_match_that_turned_weak(self, ws, run):
        # Same USDA food, but a re-pick now scores it weak: it needs a recipe or the user again.
        ws.add({**EGGS, "confidence": "weak", "macro_distance": 0.34, "reviewed_fdc_id": 173424})

        code, out = run("review")

        assert code == 0
        assert "(1 to review, 0 approved)" in out
        assert "approved before as FDC 173424; the match is now weak" in out


def _ingredient(fdc_id: int, usda_name: str, protein: float, fat: float, carbs: float) -> dict:
    """A usda_foods entry, as the enricher caches a recipe ingredient."""
    return {
        "fdc_id": fdc_id, "usda_name": usda_name, "usda_dataset": "SR Legacy",
        "per_100g": {"vitamin_a_mcg": 1.0},
        "usda_macros": {"protein_g": protein, "fat_g": fat, "carbs_g": carbs, "fiber_g": None},
    }


# Photo-logged: 1 x 100 g placeholder, 232 g of protein, fat and carbs.
BOWL_ROW = ["2026-10-15", "19:00", "Chicken Bowl", "bowl", 1, 100, 1192.0, 52.0, 114.0, 66.0]
# A real weight: 200 g of grilled chicken.
CHICKEN_ROW = ["2026-10-15", "12:00", "Grilled Chicken", "g", 200, 1, 330.0, 7.0, 0.0, 62.0]
# The bowl's macros per 100 g of a 700 g dish, and plain chicken breast.
BOWL_MIX = _ingredient(1, "Chicken, rice and cream (mix)", 66 / 7, 52 / 7, 114 / 7)
CHICKEN = _ingredient(2, "Chicken breast, cooked", 31.0, 3.5, 0.0)


class TestPlaceholderWeights:
    """Photo-logged meals: a 100 g placeholder weight with the macros of the whole meal."""

    @pytest.fixture
    def export(self, ws):
        from tests.helpers import write_food_log

        (ws.root / "data").mkdir()
        write_food_log(ws.root / "data/export.xlsx", [BOWL_ROW, CHICKEN_ROW])

    def _recipes(self, ws, recipes: dict[str, tuple[int, list[str]]]) -> None:
        (ws.root / "config/custom_foods.yaml").write_text(yaml.safe_dump({
            name: {"foods": foods, "ingredients": [{"fdc_id": fdc_id, "amount": 1}]}
            for name, (fdc_id, foods) in recipes.items()
        }))
        ws.db_path.parent.mkdir(exist_ok=True)
        with TinyDB(ws.db_path) as db:
            db.table("usda_foods").insert_multiple([BOWL_MIX, CHICKEN])

    def test_check_fits_a_placeholder_dish_with_a_fitting_recipe(self, ws, run, export):
        self._recipes(ws, {"chicken_bowl": (1, ["Chicken Bowl"])})

        code, out = run("check")

        assert code == 0
        assert ("  ✓ Chicken Bowl: logged 1192 kcal, P 66.0, F 52.0, C 114.0 over 1 serving "
                "with a placeholder weight → fitted 700 g, off 0%") in out
        assert "IMPOSSIBLE" not in out

    def test_check_flags_a_placeholder_dish_with_a_wrong_recipe(self, ws, run, export):
        self._recipes(ws, {"chicken_only": (2, ["Chicken Bowl"])})

        _, out = run("check")

        assert "  ⚠ Chicken Bowl: logged 1192 kcal" in out

    def test_check_of_a_food_with_real_weights_is_unchanged(self, ws, run, export):
        self._recipes(ws, {"chicken": (2, ["Grilled Chicken"])})

        _, out = run("check")

        assert "  ✓ Grilled Chicken: logged 165 kcal, P 31.0, F 3.5, C 0.0 → off 0%" in out

    def test_review_does_not_call_a_placeholder_dish_impossible(self, ws, run, export):
        ws.add(_entry("Chicken Bowl", 7, "Chicken breast tenders, breaded", confidence="weak", macro_distance=0.19,
                      usda_macros={"protein_g": 16.4, "fat_g": 12.9, "carbs_g": 17.6, "fiber_g": 0.0}))

        _, out = run("review")

        assert "IMPOSSIBLE" not in out
        assert ("Chicken Bowl  (1 serving with a placeholder weight; logged 1192 kcal, P 66.0, F 52.0, C 114.0)"
                in out)

    def test_flagged_shows_the_fitted_grams(self, ws, run, export):
        ws.add(_entry("Chicken Bowl", 7, "Chicken breast tenders, breaded", confidence="weak", macro_distance=0.19,
                      usda_macros={"protein_g": 16.4, "fat_g": 12.9, "carbs_g": 17.6, "fiber_g": 0.0}))

        _, out = run("flagged")

        assert ("    logged   : 1192 kcal, P 66.0, F 52.0, C 114.0 over 1 serving with a placeholder weight"
                in out)
        assert "    USDA pick: Chicken breast tenders, breaded (off 19%) → fitted 456 g" in out


class TestNutrientCount:
    """Retinol is stored for vitamin A's upper limit only: never counted or listed as missing."""

    def test_food_leaves_retinol_out_of_count_and_missing_list(self, ws, run):
        per_100g = {key: 1.0 for key in USDA_NUTRIENT_MAP}
        per_100g.update(retinol_mcg=None, tryptophan_g=None)
        ws.db_path.parent.mkdir(exist_ok=True)
        with TinyDB(ws.db_path) as db:
            db.table(USDA_FOODS_TABLE).insert({
                "fdc_id": 168462, "usda_name": "Arugula, raw", "usda_dataset": "SR Legacy",
                "per_100g": per_100g,
                "usda_macros": {"protein_g": 2.6, "fat_g": 0.7, "carbs_g": 3.7, "fiber_g": 1.6},
            })

        code, out = run("food", "168462")

        assert code == 0
        assert "37/38 nutrients" in out
        assert "lacks: tryptophan_g\n" in out
        assert "retinol" not in out

    def test_search_counts_without_retinol(self, ws, tool, run, monkeypatch):
        hit = {"fdcId": 175167, "description": "Beef, variety meats and by-products, liver, raw",
               "dataType": "SR Legacy",
               "foodNutrients": [{"nutrientNumber": number, "value": 1.0} for number in USDA_NUTRIENT_MAP.values()]}
        monkeypatch.setattr(tool, "search_usda", lambda query, key: [hit])

        code, out = run("search", "beef liver")

        assert code == 0
        assert "38/38" in out
