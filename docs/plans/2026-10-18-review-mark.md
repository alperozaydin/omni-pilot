# Review Mark for USDA Matches Implementation Plan (BAR-82)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the custom-foods skill record which USDA matches were read and judged correct (`reviewed_fdc_id`), so `review` lists only new or changed matches.

**Architecture:** The mark is one integer field on a food's entry in the main table of `food_db.json`. The app never reads it and needs no code change; one enricher test pins the in-place update it relies on. The skill's `recipe_tool.py` gains an `approve` command (its only write) and `review` hides approved matches. The skill lives in the `my-skills` repo and is installed into omni-pilot with `npx skills`; its tests live in omni-pilot's suite.

**Tech Stack:** Python 3.14, TinyDB, PyYAML, pytest + pytest-mock, ruff, `npx skills`.

**Spec:** `docs/specs/2026-10-11-review-mark-design.md` (approved 2026-10-18). Read it first; this plan argues from it.

## Global Constraints

- **Two repos.** Tool and skill text change only in `~/Projects/my-skills/skills/omni-pilot-custom-foods/`. The installed copy in omni-pilot (`.agents/skills/omni-pilot-custom-foods/`) is never edited by hand; it changes only through `npx skills add`.
- **No change under `src/omni_pilot/`.**
- **The field:** `reviewed_fdc_id`, an `int` on a main-table entry. Approved means `reviewed_fdc_id == usda_fdc_id`.
- **`approve` is the tool's only write**, it writes only `reviewed_fdc_id`, and it writes nothing when any name in the call is refused. Every other command keeps opening the database with `access_mode="r"`.
- **No network in tests.** Nothing in the new tests calls USDA or Gemini (`approve` and `review` make no HTTP calls).
- **Ruff:** omni-pilot's `uv run ruff check .` also lints the installed tool under `.agents/`, so the tool must pass `E, F, I, UP` at line length 120.
- **Commits:** one concise summary line, no body. omni-pilot style `feat: … (BAR-82)` / `test: …` / `docs: …`; my-skills style `omni-pilot-custom-foods: … (BAR-82)`.
- **Your real data is read-only during this work.** The by-hand checks run on a copy of the database.

## Review Focus

Inputs the spec implies but its own test list does not cover, most likely first. Each has a test in Task 2.

1. **The same name twice in one `approve` call** → approved once, counted once.
2. **A name containing a comma** ("Watermelon, Fresh", passed as one shell argument) → approved like any other name.
3. **A food mapped to `skip` in the database's translations table, not in `food_mappings.yaml`** → refused as skipped. The database wins over the file, as in the app.
4. **No database file yet** → `approve` stops with "No food database found." and does not create an empty database.
5. **Approving a Changed food again** → the mark moves to the new ID and the food is reported as `approved`, not `already approved`.

## Work in progress elsewhere (check before starting)

BAR-44 is open on branch `alozay/bar-44-map-command` in my-skills and on a local branch in omni-pilot. It edits the same "Check-up" section of `SKILL.md` and adds a `map` command to the app. This plan branches from my-skills `origin/main`. Whichever of the two merges second resolves the `SKILL.md` conflict. If BAR-44 is already merged when you start, write the "match is wrong and fixable" outcome in Task 4 as "fix it with `map` or a recipe".

---

### Task 1: Pin the in-place re-pick, document the invariant, set up both branches

**Files:**
- Modify: `tests/test_enricher.py` (class `TestGetFoodMatch`, after `test_legacy_entry_is_repicked_and_replaced`, ~line 401)
- Modify: `CLAUDE.md` (section "Key invariants")
- Commit: `docs/plans/2026-10-18-review-mark.md`, `docs/specs/2026-10-11-review-mark-design.md` (status line)

**Interfaces:**
- Consumes: `get_food_match(food_name, usda_query, logged, db, api_key)` and the test helpers `_candidate`, `EGG_LOGGED`, `TestGetFoodMatch._legacy_entry` already in `tests/test_enricher.py`.
- Produces: a my-skills worktree at `~/Projects/my-skills-bar-82` on branch `alozay/bar-82-review-mark`, used by Tasks 2 to 5.

- [ ] **Step 1: Bring the omni-pilot branch up to date**

```bash
cd ~/Projects/omni-pilot
git checkout alozay/bar-82-review-mark-for-usda-matches-in-the-custom-foods-skill
git merge origin/main
```
Expected: a merge commit, no conflicts (main's newer commits did not touch the spec, the plan, `enricher.py` or the skill).

- [ ] **Step 2: Create the my-skills worktree from origin/main**

The main my-skills checkout may be on the BAR-44 branch; a worktree leaves it alone.

```bash
cd ~/Projects/my-skills
git fetch
git worktree add ../my-skills-bar-82 -b alozay/bar-82-review-mark origin/main
```
Expected: `Preparing worktree (new branch 'alozay/bar-82-review-mark')`.

- [ ] **Step 3: Write the enricher test**

In `tests/test_enricher.py`, inside `class TestGetFoodMatch`, directly after `test_legacy_entry_is_repicked_and_replaced`:

```python
    def test_repick_keeps_fields_the_app_does_not_write(self, mocker, tmp_path):
        # The custom-foods skill stores reviewed_fdc_id on these entries (BAR-82).
        # A re-pick must update the entry in place, or every review mark is lost.
        db = TinyDB(str(tmp_path / "test_db.json"))
        db.insert({**self._legacy_entry(), "usda_fdc_id": 2, "reviewed_fdc_id": 2})
        mocker.patch("omni_pilot.enricher.get_food_candidates", return_value=([
            _candidate(3, "Egg, whole, cooked, hard-boiled", 12.6, 10.6, 1.1),
        ], True))

        get_food_match("Boiled Eggs", "egg, whole, cooked, hard-boiled", EGG_LOGGED, db, "fake-key")

        [entry] = db.all()
        assert entry["usda_fdc_id"] == 3
        assert entry["reviewed_fdc_id"] == 2
```

- [ ] **Step 4: Run it**

Run: `uv run pytest tests/test_enricher.py -k repick_keeps_fields -v`
Expected: PASS. This test pins existing behaviour (`db.upsert` merges fields), so it passes without code changes. To see that it guards something, temporarily change `db.upsert(entry_to_cache, …)` near `enricher.py:472` to `db.remove(Food.original_name == food_name); db.insert(entry_to_cache)`, run again, see `KeyError: 'reviewed_fdc_id'`, then revert with `git checkout src/omni_pilot/enricher.py`.

- [ ] **Step 5: Add the invariant to CLAUDE.md**

In `CLAUDE.md`, section "Key invariants", add this bullet after the bullet that starts "A USDA search that ran and found nothing is cached as a not-found record":

```markdown
- The custom-foods skill stores `reviewed_fdc_id` on entries of the main USDA cache table: the USDA ID it read beside the food's name and judged to be the same food (approved while it equals `usda_fdc_id`). The app never reads the field. A re-pick with an unchanged lookup text must keep merging into the existing entry (`upsert`), because replacing the entry would silently drop every review mark (`test_repick_keeps_fields_the_app_does_not_write`). A changed lookup text removes the entry, and with it the mark, on purpose.
```

- [ ] **Step 6: Run the full suite**

Run: `uv run pytest -q`
Expected: the same failures as on `main` before this work, if any, plus one more passing test. (On a fresh clone without `data/*.xlsx`, three parser tests fail for that reason; they are not caused by this work.)

- [ ] **Step 7: Commit**

```bash
git add tests/test_enricher.py CLAUDE.md docs/plans/2026-10-18-review-mark.md docs/specs/2026-10-11-review-mark-design.md
git commit -m "test: pin that a re-pick keeps the review mark (BAR-82)"
```

---

### Task 2: `approve` command, with its tests

**Files:**
- Create: `~/Projects/omni-pilot/tests/test_recipe_tool.py`
- Modify: `~/Projects/my-skills-bar-82/skills/omni-pilot-custom-foods/scripts/recipe_tool.py`

**Interfaces:**
- Consumes: from the tool, `main()`, `_settings()`, `_open_db()`, `load_custom_foods`, `load_food_mappings`, `resolve_path`, `DEFAULT_DB`, `DEFAULT_MAPPINGS`, `DEFAULT_CUSTOM_FOODS`.
- Produces, used by Task 3:
  - `REVIEWED = "reviewed_fdc_id"` (module constant in the tool)
  - `_is_approved(entry: dict) -> bool`
  - `_mappings(settings: dict, db: TinyDB) -> dict[str, str]` (the file's mappings, overridden by the database's translations)
  - test fixtures `tool`, `ws` (a `Workspace`), `run(*args) -> tuple[int, str]`, and helper `_entry(...)`.

While Tasks 2 and 3 are in progress, run the tests against the worktree copy:

```bash
export RECIPE_TOOL=~/Projects/my-skills-bar-82/skills/omni-pilot-custom-foods/scripts/recipe_tool.py
```

- [ ] **Step 1: Write the test file with the harness and the `approve` tests**

Create `tests/test_recipe_tool.py`:

```python
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
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `uv run pytest tests/test_recipe_tool.py -v` (with `RECIPE_TOOL` exported as above)
Expected: every `TestApprove` test fails with `argument command: invalid choice: 'approve'` (exit code 2 from argparse, so `assert code == 0` / `== 1` fails).

- [ ] **Step 3: Implement `approve` in the worktree copy of the tool**

All edits in `~/Projects/my-skills-bar-82/skills/omni-pilot-custom-foods/scripts/recipe_tool.py`.

(a) The import line `from tinydb import TinyDB` becomes:

```python
from tinydb import Query, TinyDB
```

(b) After `N_NUTRIENTS = len(USDA_NUTRIENT_MAP)` add:

```python
REVIEWED = "reviewed_fdc_id"  # the USDA ID a reviewer judged to be the logged food


def _is_approved(entry: dict) -> bool:
    """Whether the entry's current match is the one that was approved."""
    return entry.get(REVIEWED) is not None and entry.get(REVIEWED) == entry.get("usda_fdc_id")
```

(c) After `_open_db` add a helper, and use it in `cmd_review` (replacing its two `mappings = …` / `mappings.update(…)` lines with `mappings = _mappings(settings, db)`):

```python
def _mappings(settings: dict, db: TinyDB) -> dict[str, str]:
    """Each food's lookup text: food_mappings.yaml, overridden by the database's translations, as in the app."""
    mappings = load_food_mappings(resolve_path("mappings_path", DEFAULT_MAPPINGS, settings))
    mappings.update({e["german"]: e["english"] for e in db.table("translations")})
    return mappings
```

(d) After `cmd_check` add:

```python
def _approval_problem(name: str, entry: dict | None, custom: dict, mappings: dict[str, str]) -> str | None:
    """Why a food's match cannot be approved, or None if it can."""
    if name in custom["by_food"]:
        return f"is in recipe {custom['by_food'][name]}; recipes need no approval"
    if mappings.get(name, "").strip().lower() == "skip":
        return "is mapped to skip and not counted"
    if entry is None:
        return "is not a searched food; check the spelling against `review`"
    if entry.get("not_found"):
        return "has no USDA match; it needs a recipe"
    if entry.get("confidence") == "weak":
        distance = entry.get("macro_distance")
        off = "?" if distance is None else f"{distance:.0%}"
        return f"is a weak match (macros off {off}); give it a recipe or leave it for the user"
    if entry.get("usda_fdc_id") is None:
        return "has no USDA ID yet; run analyze first"
    return None


def cmd_approve(args: argparse.Namespace) -> None:
    """Mark each named food's current USDA match as read and right. All names or none."""
    settings = _settings()
    path = resolve_path("database_path", DEFAULT_DB, settings)
    if not os.path.exists(path):
        sys.exit("No food database found.")
    custom = load_custom_foods(resolve_path("custom_foods_path", DEFAULT_CUSTOM_FOODS, settings))
    names = list(dict.fromkeys(args.foods))
    with TinyDB(path) as db:
        mappings = _mappings(settings, db)
        matches = {e["original_name"]: e for e in db.all()}
        problems = [
            f"✗ {name}: {problem}" for name in names
            if (problem := _approval_problem(name, matches.get(name), custom, mappings))
        ]
        if problems:
            print("\n".join(problems))
            print("Nothing approved.")
            sys.exit(1)
        approved = 0
        for name in names:
            entry = matches[name]
            match = f"{name} → {entry['usda_name']} ({entry['usda_fdc_id']})"
            if _is_approved(entry):
                print(f"already approved  {match}")
                continue
            db.update({REVIEWED: entry["usda_fdc_id"]}, Query().original_name == name)
            print(f"approved  {match}")
            approved += 1
    print(f"{approved} approved")
```

(e) In `main()`, after the `check` sub-parser block, add:

```python
    p = sub.add_parser("approve", help="mark foods whose USDA match was read and is the right food")
    p.add_argument("foods", nargs="+", help="food names exactly as logged")
    p.set_defaults(func=cmd_approve)
```

(f) In the module docstring, add to the Commands list after the `check [FILE]` lines:

```
    approve NAME...  mark each food's USDA match as read and right (the review mark)
```

and replace the paragraph that starts "Everything is read-only:" with:

```
Every command is read-only except `approve`, which writes only the review mark
(`reviewed_fdc_id`) and writes nothing when any name is refused. Ingredients
fetched here are not cached, so the app still fetches and caches them itself.
```

- [ ] **Step 4: Run the tests to see them pass**

Run: `uv run pytest tests/test_recipe_tool.py -v`
Expected: all `TestApprove` tests PASS.

- [ ] **Step 5: Lint**

Run: `uv run ruff check tests/test_recipe_tool.py "$RECIPE_TOOL"`
Expected: `All checks passed!`

- [ ] **Step 6: Commit in both repos**

```bash
cd ~/Projects/my-skills-bar-82
git add skills/omni-pilot-custom-foods/scripts/recipe_tool.py
git commit -m "omni-pilot-custom-foods: add approve command for the review mark (BAR-82)"
cd ~/Projects/omni-pilot
git add tests/test_recipe_tool.py
git commit -m "test: cover the recipe tool's approve command (BAR-82)"
```

The omni-pilot suite fails on these tests until Task 5 installs the new tool; that is expected on this branch.

---

### Task 3: `review` lists only matches not yet approved

**Files:**
- Modify: `~/Projects/my-skills-bar-82/skills/omni-pilot-custom-foods/scripts/recipe_tool.py` (`cmd_review`, `main`)
- Modify: `~/Projects/omni-pilot/tests/test_recipe_tool.py` (new class `TestReview`)

**Interfaces:**
- Consumes: `REVIEWED`, `_is_approved` (Task 2); fixtures `ws`, `run`, entries `EGGS`, `MELON`, helper `_entry` (Task 2).
- Produces: `review --all`; the header `== Searched in USDA (N to review, M approved): is each match the same food?`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_recipe_tool.py`:

```python
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
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_recipe_tool.py::TestReview -v`
Expected: the first three FAIL (old header `== Searched in USDA (2): …`, no note, `--all` rejected by argparse); `test_flagged_ignores_review_marks` PASSES already.

- [ ] **Step 3: Implement**

In the worktree tool's `cmd_review`, replace the block that prints the "Searched in USDA" group, from `print(f"== Searched in USDA ({len(groups['searched'])}): is each match the same food?")` through the loop's `print(f"    matched to  : …")` line, with:

```python
    approved = [name for name in groups["searched"] if _is_approved(matches[name])]
    print(f"== Searched in USDA ({len(groups['searched']) - len(approved)} to review, {len(approved)} approved):"
          " is each match the same food?")
    for name in groups["searched"]:
        e = matches[name]
        is_approved = name in approved
        if is_approved and not args.all:
            continue
        distance = e.get("macro_distance")
        off = "" if distance is None else f", off {distance:.0%}"
        print(header(name))
        print(f"    looked up as: {e.get('usda_query')}")
        print(f"    matched to  : {e.get('usda_name')}  [{e.get('confidence', '?')}{off}]"
              + ("  (approved)" if is_approved else ""))
        if e.get(REVIEWED) is not None and not is_approved:
            print(f"    approved before as FDC {e[REVIEWED]}; the match has changed since")
```

In `main()`, in the `review` sub-parser block, add before `p.set_defaults(func=cmd_review)`:

```python
    p.add_argument("--all", action="store_true", help="also list matches already approved")
```

and change its help text to `"foods and their matches not yet approved, for a check-up"`. In the module docstring, change the `review` line to:

```
    review [--all]   every logged food and what the app matched it to, for a check-up;
                     approved matches are hidden unless --all is given
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_recipe_tool.py -v`
Expected: all PASS.

- [ ] **Step 5: Lint and commit in both repos**

```bash
cd ~/Projects/omni-pilot && uv run ruff check tests/test_recipe_tool.py "$RECIPE_TOOL"
cd ~/Projects/my-skills-bar-82
git add skills/omni-pilot-custom-foods/scripts/recipe_tool.py
git commit -m "omni-pilot-custom-foods: review hides approved matches (BAR-82)"
cd ~/Projects/omni-pilot
git add tests/test_recipe_tool.py
git commit -m "test: cover review with approved and changed matches (BAR-82)"
```

---

### Task 4: The skill's instructions

**Files:**
- Modify: `~/Projects/my-skills-bar-82/skills/omni-pilot-custom-foods/SKILL.md`

**Interfaces:**
- Consumes: the commands from Tasks 2 and 3 (`approve NAME...`, `review --all`).

- [ ] **Step 1: Update "The tool" section**

Replace this line:

```
client and macro check, and never writes to the database. Run it from the
```

with these two:

```
client and macro check. Only `approve` writes to the database, and only the
review mark. Run it from the
```

Then in the command block replace the `review` line and add two lines after it:

```bash
PYTHONPATH=src uv run python $T review                  # matches not yet approved, every other food (check-up)
PYTHONPATH=src uv run python $T review --all            # same, including approved matches
PYTHONPATH=src uv run python $T approve "Boiled Eggs"   # mark matches you read and found right
```

- [ ] **Step 2: Add the review mark to the "Check-up" section**

After the paragraph that ends "read every entry, and list for the user, heaviest first:" and its bullet list, and before the paragraph that starts "Foods with \"not in any export\"", insert:

````markdown
`review` hides matches already approved, so a check-up reads only foods that
are new or whose match the app has changed since it was approved (shown with
"approved before as FDC …"). For each food you read:

| Verdict | Do | Result |
|---|---|---|
| The match is the same food, in the same form | `approve` it | Hidden until the app changes the match |
| The match is wrong and fixable | A recipe that passes `check` (within 25%) | In a recipe; no mark needed |
| Cannot be settled: impossible logged macros, no plausible recipe within 25%, or the name does not say what the food is | Nothing | Listed for the user; shown again next time |

- Approve only when the name and the match are clearly the same food. When
  unsure, leave it unmarked. Never approve a food to shorten the list.
- Approve all right matches in one call: `approve "Food A" "Food B" ...`. If any
  name is refused, nothing is written; fix the list and run it again.
- `approve` refuses weak matches, foods in a recipe, skipped foods and foods
  not in USDA: those need a recipe or the user, not a mark.
- If the user says a listed match is fine, approve it.
- Report approvals as a count ("approved 97 matches") and mention that
  `review --all` shows them.
````

If BAR-44 is already merged into this branch's base (see "Work in progress elsewhere"), the second table row's "Do" cell reads: "`map` it to a better search text, or a recipe that passes `check` (within 25%)".

- [ ] **Step 3: Check the result reads as one piece**

Run: `sed -n '/## The tool/,/## Workflow/p' ~/Projects/my-skills-bar-82/skills/omni-pilot-custom-foods/SKILL.md`
Expected: the tool section mentions `approve` and `review --all`; the check-up section has the table once; no remaining claim that the tool "never writes".

- [ ] **Step 4: Commit**

```bash
cd ~/Projects/my-skills-bar-82
git add skills/omni-pilot-custom-foods/SKILL.md
git commit -m "omni-pilot-custom-foods: describe the review mark in the check-up (BAR-82)"
```

---

### Task 5: Install into omni-pilot and check by hand

**Files:**
- Modify (by `npx skills`, not by hand): `.agents/skills/omni-pilot-custom-foods/SKILL.md`, `.agents/skills/omni-pilot-custom-foods/scripts/recipe_tool.py`, `skills-lock.json`

- [ ] **Step 1: Push the my-skills branch**

```bash
cd ~/Projects/my-skills-bar-82 && git push -u origin alozay/bar-82-review-mark
```

- [ ] **Step 2: Install the branch into omni-pilot**

```bash
cd ~/Projects/omni-pilot
npx skills add "alperozaydin/my-skills#alozay/bar-82-review-mark" --list
```
Expected: the "Source: … @ alozay/bar-82-review-mark" line names the branch. Then:

```bash
npx skills add "alperozaydin/my-skills#alozay/bar-82-review-mark" --skill omni-pilot-custom-foods -y
git diff --stat
```
Expected: changes in the two skill files and `skills-lock.json` (with `"ref": "alozay/bar-82-review-mark"`), nothing else.

- [ ] **Step 3: Run the full suite against the installed copy**

```bash
unset RECIPE_TOOL
uv run pytest -q
uv run ruff check .
```
Expected: every test in `tests/test_recipe_tool.py` passes against the installed copy; the rest of the suite is as in Task 1 Step 6; ruff reports `All checks passed!`.

- [ ] **Step 4: Check by hand on a copy of the real database**

This never touches your real files. It copies the database, mappings and recipes into a scratch folder with settings pointing there:

```bash
S=$(mktemp -d)
PYTHONPATH=src uv run python - "$S" <<'EOF'
import shutil, sys
from pathlib import Path
import yaml
from omni_pilot.config import load_settings, resolve_path
settings = load_settings("config/settings.yaml")
root = Path(sys.argv[1])
for rel in ("config", "db"):
    (root / rel).mkdir()
for key, rel in (("database_path", "db/food_db.json"), ("mappings_path", "config/food_mappings.yaml"),
                 ("custom_foods_path", "config/custom_foods.yaml")):
    src = Path(resolve_path(key, rel, settings))
    if src.exists():
        shutil.copy(src, root / rel)
    settings[key] = rel
for rel in ("config/reference_ranges.yaml", "config/supplements.yaml"):
    if Path(rel).exists():
        shutil.copy(rel, root / rel)
(root / "config/settings.yaml").write_text(yaml.safe_dump(settings))
EOF
T=$PWD/.agents/skills/omni-pilot-custom-foods/scripts/recipe_tool.py
X="$PWD/data/*.xlsx"
cd "$S"
PYTHONPATH=~/Projects/omni-pilot/src uv run --project ~/Projects/omni-pilot python $T review --exports "$X" | head -3
```
Expected: `== Searched in USDA (122 to review, 0 approved): …` (the number is whatever the copy holds; all to review, none approved).

```bash
PYTHONPATH=~/Projects/omni-pilot/src uv run --project ~/Projects/omni-pilot python $T approve "Boiled Eggs" "Watermelon, Fresh"
PYTHONPATH=~/Projects/omni-pilot/src uv run --project ~/Projects/omni-pilot python $T review --exports "$X" | head -3
PYTHONPATH=~/Projects/omni-pilot/src uv run --project ~/Projects/omni-pilot python $T review --exports "$X" | grep -c "Boiled Eggs\|Watermelon, Fresh"
```
Expected: `2 approved`; the header now reads `(120 to review, 2 approved)`; the grep count is `0`.

Then run the app once on the copy (needs network and uses your USDA key; it writes only to `$S`):

```bash
PYTHONPATH=~/Projects/omni-pilot/src uv run --project ~/Projects/omni-pilot python -m omni_pilot.cli analyze "$(ls -t ~/Projects/omni-pilot/data/MacroFactor-*.xlsx | head -1)"
PYTHONPATH=~/Projects/omni-pilot/src uv run --project ~/Projects/omni-pilot python - <<'EOF'
from tinydb import TinyDB
db = TinyDB("db/food_db.json", access_mode="r")
print({e["original_name"]: (e.get("usda_fdc_id"), e.get("reviewed_fdc_id"))
       for e in db.all() if e["original_name"] in ("Boiled Eggs", "Watermelon, Fresh")})
EOF
cd ~/Projects/omni-pilot && rm -rf "$S"
```
Expected: both foods still carry `reviewed_fdc_id`. Where the run re-picked a food (Watermelon is one of the older entries), the pair is either equal (still approved) or different (it would now show as Changed in `review`).

- [ ] **Step 5: Commit the installed skill**

```bash
git add .agents/skills/omni-pilot-custom-foods skills-lock.json
git commit -m "chore: install custom-foods skill with the review mark (BAR-82)"
```

- [ ] **Step 6: Hand over for merging (ask the user before opening PRs)**

Merge order (from the earlier skill installs):
1. Open and merge the my-skills PR for `alozay/bar-82-review-mark`.
2. In omni-pilot run `npx skills add alperozaydin/my-skills --skill omni-pilot-custom-foods -y` again to drop the branch `ref` from `skills-lock.json`; commit `chore: install custom-foods skill from main (BAR-82)`.
3. Push the omni-pilot branch, open its PR and merge it.
4. Remove the worktree: `cd ~/Projects/my-skills && git worktree remove ../my-skills-bar-82`.
5. Set BAR-82 to Done with the PR links.
