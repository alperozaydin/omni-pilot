# Update-Mapping Command Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An `update-mapping FOOD QUERY` command that sets a logged food's lookup text in the database and `food_mappings.yaml` at once, through S3, so a wrong Gemini translation can be fixed (BAR-44).

**Architecture:** The data work lives in `translator.py` next to the code that owns the `translations` table: small functions to read the stored lookup texts and food names and one `set_mapping` that writes both stores and drops the food's USDA cache entry. `cli.py` gets a `cmd_update_mapping` that pulls, checks the name, compares, warns about a recipe, calls `set_mapping` and pushes. `resolve_and_sync_mappings` keeps its DB-over-YAML precedence.

**Tech Stack:** Python 3, TinyDB, PyYAML, openpyxl, argparse, pytest + pytest-mock, `make`.

**Spec:** `docs/specs/2026-10-22-update-mapping-command-design.md`

## Global Constraints

- Branch: `alozay/bar-44-manual-corrections-to-food_mappingsyaml-are-silently` from `main`. The first commit also adds the spec (untracked on `main`).
- Pure-Python only (a-Shell); no new dependencies. `difflib` and `glob` are stdlib.
- Tests never call Gemini, USDA or S3 for real: S3 through `tests.helpers.FakeS3Client` patched in as `omni_pilot.cli.S3Client`.
- Food names are compared exactly (strip only). A `QUERY` equal to `skip` in any case is stored as `skip`, which is what the enricher compares against (`mapping == "skip"`).
- The `usda_foods` table is never touched. Precedence in `resolve_and_sync_mappings` does not change, and its existing tests pass unchanged.
- Commit messages: one concise summary line, no body.
- Skill files are never edited in `.agents/skills/`; they change in the `alperozaydin/my-skills` repo (`../my-skills`).

## Review Focus

- **Wrong case in the name** (`haferflocken`): refused, nothing written, and `"Haferflocken"` is among the suggestions. Test in Task 2.
- **Umlauts, quotes and colons in a name** (`Müsli "Bio": Nuss`): the YAML round-trips the name exactly and keeps `ü` readable (`allow_unicode`). Test in Task 1.
- **The lookup text stored only in the YAML** (no `translations` row): the old value shown and compared is the YAML's, and `set_mapping` adds the database row. Test in Task 1.
- **A fresh device with no database and no YAML yet:** both files are created; the food is known from an export. Tests in Tasks 1 and 2.
- **The same value with different spacing or case of `skip`** (`" Skip "` when `skip` is stored): "already", nothing written or pushed. Test in Task 2.

---

### Task 1: Store a lookup text in both places

**Files:**
- Modify: `src/omni_pilot/translator.py` (new functions; step 1–3 of `resolve_and_sync_mappings` use `stored_mappings`; the no-Gemini-key hint at line 182)
- Modify: `src/omni_pilot/parser.py` (`generate_food_mappings` header at line 230; new `food_names_in_exports`)
- Modify: `config/food_mappings.example.yaml` (same note as the header)
- Test: `tests/test_translator.py`, `tests/test_parser.py`, `tests/test_recipe_tool.py`

**Interfaces:**
- Produces (in `omni_pilot.translator`):
  - `normalise_query(query: str) -> str` — strip; `"skip"` in any case → `"skip"`.
  - `stored_mappings(db_path: str, mappings_path: str) -> dict[str, str]` — the YAML's mappings overlaid with the `translations` table, empty values dropped (exactly steps 1–3 of `resolve_and_sync_mappings`, which now calls it). A missing database file reads as empty and is not created.
  - `cached_food_names(db_path: str) -> set[str]` — `original_name` of every main-table entry (matches and not-found records); empty when the file is missing.
  - `set_mapping(food: str, query: str, db_path: str, mappings_path: str) -> None` — upsert `{"german": food, "english": query}` in `translations`; remove the main-table entry where `original_name == food`; write the YAML as `load_food_mappings(mappings_path)` with `food` set to `query`, through `generate_food_mappings([], mappings, mappings_path)`. Creates missing folders and files.
- Produces (in `omni_pilot.parser`): `food_names_in_exports(paths: Iterable[str]) -> set[str]` — union of `extract_unique_foods(parse_food_log(p))`.

- [ ] **Step 1: Write the failing tests** in `tests/test_translator.py`

```python
def _db(tmp_path, translations=(), cache=()) -> str:
    path = tmp_path / "db" / "food_db.json"
    path.parent.mkdir(exist_ok=True)
    with TinyDB(path) as db:
        db.table("translations").insert_multiple(list(translations))
        db.insert_multiple(list(cache))
    return str(path)

def test_set_mapping_fixes_a_wrong_translation_for_good(tmp_path):  # BAR-44
    db = _db(tmp_path, [{"german": "Haferflocken", "english": "oat milk"}])
    yml = tmp_path / "food_mappings.yaml"
    generate_food_mappings(["Haferflocken"], {"Haferflocken": "oat milk"}, str(yml))
    set_mapping("Haferflocken", "cereals, oats, dry", db, str(yml))
    written = yml.read_bytes()
    mappings, untranslated = resolve_and_sync_mappings(["Haferflocken"], db, str(yml), {})
    assert mappings == {"Haferflocken": "cereals, oats, dry"} and untranslated == set()
    assert yml.read_bytes() == written
    assert load_food_mappings(str(yml)) == {"Haferflocken": "cereals, oats, dry"}

@pytest.mark.parametrize("entry", [
    {"original_name": "Haferflocken", "usda_query": "oat milk", "per_100g": {}, "reviewed_fdc_id": 1},
    {"original_name": "Haferflocken", "usda_query": "oat milk", "not_found": True, "deferred_query": "oat milk"},
])
def test_set_mapping_drops_the_food_cache_entry_only(tmp_path, entry):
    other = {"original_name": "Milch", "usda_query": "milk", "per_100g": {}}
    db = _db(tmp_path, cache=[entry, other])
    with TinyDB(db) as d:
        d.table("usda_foods").insert({"fdc_id": 170457})
    set_mapping("Haferflocken", "cereals, oats, dry", db, str(tmp_path / "m.yaml"))
    with TinyDB(db) as d:
        assert [e["original_name"] for e in d.all()] == ["Milch"]
        assert d.table("usda_foods").all() == [{"fdc_id": 170457}]

def test_set_mapping_keeps_umlauts_quotes_and_colons(tmp_path):
    name = 'Müsli "Bio": Nuss'
    yml = tmp_path / "m.yaml"
    set_mapping(name, "muesli", _db(tmp_path), str(yml))
    assert load_food_mappings(str(yml)) == {name: "muesli"}
    assert "Müsli" in yml.read_text()

def test_set_mapping_creates_missing_files(tmp_path):
    db, yml = tmp_path / "db" / "food_db.json", tmp_path / "config" / "m.yaml"
    set_mapping("Haferflocken", "skip", str(db), str(yml))
    assert stored_mappings(str(db), str(yml)) == {"Haferflocken": "skip"}

def test_mappings_file_header_points_to_update_mapping(tmp_path):
    yml = tmp_path / "m.yaml"
    set_mapping("Haferflocken", "skip", _db(tmp_path), str(yml))
    header = yml.read_text().split("mappings:")[0]
    assert "make update-mapping" in header and "skip" in header and "omni_pilot import" not in header

@pytest.mark.parametrize("raw, stored", [
    ("  Skip ", "skip"), ("SKIP", "skip"), (" cereals, oats ", "cereals, oats"), ("Skipjack tuna", "Skipjack tuna"),
])
def test_normalise_query(raw, stored):
    assert normalise_query(raw) == stored

def test_stored_mappings_database_wins_and_yaml_fills_gaps(tmp_path):
    db = _db(tmp_path, [{"german": "A", "english": "from db"}])
    yml = tmp_path / "m.yaml"
    generate_food_mappings([], {"A": "from yaml", "B": "only yaml"}, str(yml))
    assert stored_mappings(db, str(yml)) == {"A": "from db", "B": "only yaml"}

def test_set_mapping_adds_the_database_row_for_a_yaml_only_food(tmp_path):
    db, yml = _db(tmp_path), tmp_path / "m.yaml"
    generate_food_mappings([], {"B": "only yaml"}, str(yml))
    set_mapping("B", "new", db, str(yml))
    with TinyDB(db) as d:
        assert d.table("translations").all() == [{"german": "B", "english": "new"}]

def test_cached_food_names_reads_the_main_table_only(tmp_path):
    db = _db(tmp_path, [{"german": "T", "english": "t"}],
             [{"original_name": "M", "per_100g": {}}, {"original_name": "N", "not_found": True}])
    assert cached_food_names(db) == {"M", "N"}
    assert cached_food_names(str(tmp_path / "missing.json")) == set()
```

Extend `test_resolve_and_sync_mappings_missing_key_graceful` (uses `capsys`) with `assert "make update-mapping" in capsys.readouterr().out`.

In `tests/test_parser.py`:

```python
def test_food_names_in_exports_unions_every_file(tmp_path):
    a = write_food_log(tmp_path / "a.xlsx", [food_row("Apfel"), food_row("Haferflocken")])
    b = write_food_log(tmp_path / "b.xlsx", [food_row("Haferflocken"), food_row("Milch")])
    assert food_names_in_exports([a, b]) == {"Apfel", "Haferflocken", "Milch"}
```

In `tests/test_recipe_tool.py` (class with the other `pending` tests):

```python
def test_a_food_whose_lookup_text_changed_is_no_longer_pending(self, ws, run):
    ws.add(EGGS)
    _, before = run("pending")
    set_mapping("Boiled Eggs", "egg, whole, cooked, hard-boiled", str(ws.db_path),
                str(ws.root / "config/food_mappings.yaml"))
    _, after = run("pending")
    assert "Boiled Eggs" in before
    assert after == "Nothing to review.\n"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_translator.py tests/test_parser.py tests/test_recipe_tool.py -q`
Expected: FAIL with `ImportError` for `set_mapping` / `food_names_in_exports`.

- [ ] **Step 3: Implement the functions listed under Interfaces**

Open TinyDB with `with TinyDB(...) as db:` in the new functions so the file is closed before the CLI pushes it. `stored_mappings` and `cached_food_names` check `os.path.exists(db_path)` first rather than letting TinyDB create the file.

New header in `generate_food_mappings` (exact copy):

```
# Written by Omni Pilot: hand edits are overwritten on the next run.
# To change a food's USDA lookup text: make update-mapping FOOD="..." QUERY="..."
# (without make: python -m omni_pilot.cli update-mapping "FOOD" "QUERY").
# QUERY=skip leaves a food out of the micronutrient analysis.
```

Put the same four lines (as comments) at the top of `config/food_mappings.example.yaml`, replacing its current `# Map food descriptions…` lines.

The no-Gemini-key hint (`translator.py:182`) becomes: `  To set a food's lookup text: make update-mapping FOOD="..." QUERY="..."`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest -q`
Expected: all pass, including the unchanged `resolve_and_sync_mappings` tests.

- [ ] **Step 5: Commit**

```bash
git add docs/specs/2026-10-22-update-mapping-command-design.md docs/plans/2026-10-24-update-mapping-command.md \
  src/omni_pilot/translator.py src/omni_pilot/parser.py config/food_mappings.example.yaml \
  tests/test_translator.py tests/test_parser.py tests/test_recipe_tool.py
git commit -m "feat: set a food's lookup text in the database and mappings file at once (BAR-44)"
```

---

### Task 2: The `update-mapping` command

**Files:**
- Modify: `src/omni_pilot/cli.py` (new `cmd_update_mapping`, subparser, `_push_or_exit` shared with `cmd_push`)
- Modify: `src/omni_pilot/sync.py` (`EXPORTS_DIR = "data"`, used as `pull_exports`' default)
- Modify: `Makefile`, `README.md` (line 153 and the commands section), `CLAUDE.md`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `normalise_query`, `stored_mappings`, `cached_food_names`, `set_mapping` from `omni_pilot.translator`; `food_names_in_exports` from `omni_pilot.parser` (Task 1).
- Produces: `cmd_update_mapping(args: argparse.Namespace) -> None`; `_push_or_exit(client, files, state_file) -> list[str]` (the `try … except` of `cmd_push` around `push`, conflict hint included, returning the uploaded labels); `sync.EXPORTS_DIR`.

**Order and messages** (exact copy; `X` the food, `Q` the query):

1. Strip `FOOD`, `normalise_query(QUERY)`. Either empty → `Error: FOOD and QUERY must not be empty.`, exit 1.
2. `_s3_context(settings)`; `S3ConfigError` → `Error: {e}`, exit 1. With a context: `pull` then `pull_exports(client)`, `S3Error`/`SyncError` → `Error: {e}`, exit 1, then `S3: pulled from {bucket}`. Without: `S3: not configured — local files only`.
3. Known names = `cached_food_names` ∪ `stored_mappings` keys ∪ YAML keys (`load_food_mappings`) ∪ recipe foods (`load_custom_foods(...)["by_food"]`, empty on `CustomFoodsError`). Only if `X` is not among them: ∪ `food_names_in_exports(sorted(glob(EXPORTS_DIR/*.xlsx)))`. Still not → `Error: Unknown food "X".`, plus ` Did you mean: "A", "B"?` when `difflib.get_close_matches(X, names, n=3)` finds any; exit 1.
4. `old = stored_mappings(...).get(X)`. `old == Q` → `"X" is already "Q" — nothing changed.`, exit 0, no push.
5. `X` in a recipe → `Note: "X" is in the custom recipe "R", which takes precedence: the new lookup text has no effect while the recipe lists it.` On `CustomFoodsError` → `Note: custom_foods.yaml is invalid ({e}), so it was not checked for a recipe.`
6. `set_mapping`, then `"X": "old" → "Q"` or `"X": "Q" (set)`.
7. With S3: `_push_or_exit`, printing `  {label}: uploaded` per label. Last line: `Takes effect on the next analyze.`

- [ ] **Step 1: Write the failing tests** in `tests/test_cli.py`, class `TestCLIUpdateMapping`, with the `_isolated` / `settings` / `FULL_S3` set-up of `TestCLIPullPush` (`monkeypatch.chdir(tmp_path)`, so `data/` is `tmp_path / "data"`), and a `run(mocker, *argv)` that returns the exit code (0 when `main()` returns).

```python
DB_WITH_OAT_MILK = json.dumps({"_default": {}, "translations": {"1": {"german": "Haferflocken", "english": "oat milk"}}})

def test_changes_the_lookup_text_on_local_files(...):
    # self.db written from DB_WITH_OAT_MILK; no s3
    code = run(..., "update-mapping", "Haferflocken", "cereals, oats, dry", "--settings", settings)
    assert code == 0
    assert '"Haferflocken": "oat milk" → "cereals, oats, dry"' in out
    assert "S3: not configured — local files only" in out and "Takes effect on the next analyze." in out
    assert stored_mappings(str(self.db), str(self.mappings)) == {"Haferflocken": "cereals, oats, dry"}
    assert load_food_mappings(str(self.mappings)) == {"Haferflocken": "cereals, oats, dry"}

def test_sets_the_first_lookup_text_of_a_food_only_in_an_export(...):
    # no database, no mappings file; data/export.xlsx logs "Haferflocken"
    assert code == 0 and '"Haferflocken": "cereals, oats, dry" (set)' in out

def test_does_not_read_exports_when_the_database_knows_the_food(...):
    spy = mocker.patch("omni_pilot.cli.food_names_in_exports")
    ...
    spy.assert_not_called()

def test_unknown_or_wrongly_cased_name_suggests_and_writes_nothing(...):
    before = self.db.read_bytes()
    code = run(..., "haferflocken", "x", ...)
    assert code == 1 and 'Unknown food "haferflocken"' in out and '"Haferflocken"' in out
    assert self.db.read_bytes() == before and not self.mappings.exists()

@pytest.mark.parametrize("food, query", [("  ", "x"), ("Haferflocken", "   ")])
def test_empty_food_or_query_exits_1(...):
    assert code == 1 and "must not be empty" in out

def test_same_value_writes_and_pushes_nothing(...):
    # S3 fake holds a db where Haferflocken is "skip"; QUERY " Skip "
    assert code == 0 and '"Haferflocken" is already "skip"' in out
    assert fake.puts == []

def test_food_in_a_recipe_is_written_with_a_note(...):
    # self.recipes = CAPRESE_RECIPE; "Salat Caprese" is known from the recipe only
    assert code == 0 and 'custom recipe "caprese", which takes precedence' in out
    assert stored_mappings(...)["Salat Caprese"] == "skip"

def test_invalid_recipes_file_only_skips_the_recipe_check(...):
    # self.recipes = "caprese: [", Haferflocken in the database
    assert code == 0 and "so it was not checked for a recipe" in out

def test_with_s3_pulls_first_and_pushes_the_change(...):
    # fake: food_db.json = DB_WITH_OAT_MILK, food_mappings.yaml; no local files
    assert code == 0
    first_put = next(i for i, c in enumerate(fake.calls) if c[0] == "put")
    assert ("get", "food_db.json") in fake.calls[:first_put] and ("list", "exports/") in fake.calls[:first_put]
    assert {p["key"] for p in fake.puts} >= {"food_db.json", "food_mappings.yaml"}
    assert b"cereals, oats, dry" in fake.objects["food_db.json"][0]

def test_failed_pull_exits_1_and_writes_nothing(...):
    fake.fail_get_on = "food_db.json"
    assert code == 1 and "S3 not reachable" in out and not self.db.exists() and fake.puts == []

def test_push_conflict_keeps_the_local_change_and_hints_pull(...):
    fake.put = mocker.Mock(side_effect=S3Error("conflict", "food_db.json changed in S3"))
    assert code == 1 and "make pull" in out
    assert stored_mappings(str(self.db), str(self.mappings))["Haferflocken"] == "cereals, oats, dry"

def test_incomplete_s3_settings_exits_1(...):
    assert code == 1 and "region" in out

@pytest.mark.skipif(shutil.which("make") is None, reason="make not installed")
@pytest.mark.parametrize("args", [["QUERY=x"], ["FOOD=x"]])
def test_make_target_needs_food_and_query(args):
    done = subprocess.run(["make", "-n", "update-mapping", *args], cwd=REPO_ROOT, capture_output=True, text=True)
    assert done.returncode != 0 and "Usage: make update-mapping" in done.stderr
```

`...` stands for the fixture arguments and set-up named in the comments; `out` is `capsys.readouterr().out`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -k UpdateMapping -q`
Expected: FAIL (`invalid choice: 'update-mapping'`, exit 2; the make test fails with no such target).

- [ ] **Step 3: Implement `cmd_update_mapping` and `_push_or_exit` in `cli.py`**

Subparser: `update-mapping` with positional `food` and `query` and `--settings`, help `Set a food's USDA lookup text`. `cmd_push` switches to `_push_or_exit`; its output stays the same.

Makefile (add to `.PHONY` and `help`: `make update-mapping FOOD=<name> QUERY=<text>  - Set a food's USDA lookup text ("skip" excludes it)`):

```make
update-mapping:
ifndef FOOD
	$(error FOOD is not set. Usage: make update-mapping FOOD="Haferflocken" QUERY="cereals, oats, dry")
endif
ifndef QUERY
	$(error QUERY is not set. Usage: make update-mapping FOOD="Haferflocken" QUERY="cereals, oats, dry")
endif
	PYTHONPATH=src uv run python -m omni_pilot.cli update-mapping "$(FOOD)" "$(QUERY)"
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest -q && uv run ruff check .`
Expected: all pass, no lint errors.

- [ ] **Step 5: Update the docs**

- `README.md`: line 153 names only `config/custom_foods.yaml` for pull, edit, push, and adds that a lookup text changes with `make update-mapping FOOD=… QUERY=…` (pulls and pushes by itself). Add the command, with both examples from spec §1 and the a-Shell form, to the commands section.
- `CLAUDE.md`: the command in Commands; `update-mapping` in the `cli.py` paragraph next to `pull`/`push`; in the `translator.py` description and the TinyDB invariant, `food_mappings.yaml` is a copy the app writes and changes go through `update-mapping` (which also removes the food's main-table cache entry, review and defer marks with it); the "hand edit … means pull, edit, push" sentence keeps only `custom_foods.yaml`; in the `enricher.py` description, `not_in_usda` "needs a custom recipe or a better lookup text (`update-mapping`)".

- [ ] **Step 6: Commit**

```bash
git add src/omni_pilot/cli.py src/omni_pilot/sync.py Makefile README.md CLAUDE.md tests/test_cli.py
git commit -m "feat: add update-mapping command to change a food's lookup text (BAR-44)"
```

---

### Task 3: Skills (my-skills PR)

**Files** (in `../my-skills`, branch `alozay/bar-44-update-mapping`):
- Modify: `skills/omni-pilot-custom-foods/SKILL.md` (Check-up "Not in USDA" bullet, line 73; the "editing its lookup text … does not work" sentence, line 108; verdict table)
- Modify: `skills/omni-pilot-daily-review/SKILL.md` (line 41)
- In omni-pilot: `skills-lock.json`, `.agents/skills/…` (written by `npx skills`, never by hand)

- [ ] **Step 1: Edit the custom-foods skill**

- Replace the line 108 sentence: when the stored lookup text (`looked up as:` in `review`) is the wrong food, or the food should not count (a supplement), run `make update-mapping FOOD="…" QUERY="…"` (`QUERY=skip` to exclude). When the lookup text is right but USDA search or the matcher picks the wrong food, or the food is a mixed dish, use a recipe, which pins exact FDC IDs. The new text takes effect on the next `make analyze`.
- Verdict table: a row "The lookup text names the wrong food, or the food should not count → `update-mapping` → re-matched on the next analyze".
- "Not in USDA" bullet: a recipe, or a better lookup text when the current one is odd; drop "see below why editing the mapping does not work".
- Ordering rule: the command pulls first, which replaces unpushed local changes, so run it before other edits or after `make push`. Report it like a recipe (before → after).

- [ ] **Step 2: Edit the daily-review skill**

Line 41 becomes: "Run `analyze`, `update-mapping`, or edit `config/food_mappings.yaml`."

- [ ] **Step 3: Commit, push, open the my-skills PR**

```bash
git -C ../my-skills switch -c alozay/bar-44-update-mapping
git -C ../my-skills commit -am "feat: use update-mapping for wrong translations and skips (BAR-44)"
git -C ../my-skills push -u origin alozay/bar-44-update-mapping
```

- [ ] **Step 4: Install the branch into omni-pilot and run the tool tests**

```bash
npx skills add "alperozaydin/my-skills#alozay/bar-44-update-mapping" --skill omni-pilot-custom-foods -y
npx skills add "alperozaydin/my-skills#alozay/bar-44-update-mapping" --skill omni-pilot-daily-review -y
uv run pytest tests/test_recipe_tool.py -q
```

Expected: `skills-lock.json` shows `"ref": "alozay/bar-44-update-mapping"` for both; tests pass.

- [ ] **Step 5: Commit**

```bash
git add skills-lock.json .agents/skills
git commit -m "chore: install skills from the bar-44 my-skills branch"
```

Merge order (after review): merge the my-skills PR; rerun both `npx skills add alperozaydin/my-skills --skill <name> -y` (drops the ref); commit; then merge the omni-pilot PR.

---

### Final verification

- [ ] `uv run pytest -q` and `uv run ruff check .` pass.
- [ ] By hand, against S3: `make update-mapping FOOD="<a real food>" QUERY="<better text>"` prints the change and the uploaded files; `make analyze FILE=…` searches the food again under the new text; `config/food_mappings.yaml` shows it; `make pull` on a second checkout gets it.
