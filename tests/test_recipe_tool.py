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

    def test_lists_an_approved_match_that_turned_weak(self, ws, run):
        # Same USDA food, but a re-pick now scores it weak: it needs a recipe or the user again.
        ws.add({**EGGS, "confidence": "weak", "macro_distance": 0.34, "reviewed_fdc_id": 173424})

        code, out = run("review")

        assert code == 0
        assert "(1 to review, 0 approved)" in out
        assert "approved before as FDC 173424; the match is now weak" in out
