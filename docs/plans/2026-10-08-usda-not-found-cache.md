# Cache USDA "Not Found" Results Implementation Plan (BAR-79)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A food that USDA has no match for is searched once and answered from the cache for 30 days, while a failed USDA request is never cached; the report names the two cases separately.

**Architecture:** `get_food_candidates` reports any failed search as an incomplete set, so `get_food_match` can tell "not in USDA" (no hits, complete) from "lookup failed" (no hits, incomplete). A not-found result is upserted into the main USDA cache table as a record without a profile. `EnrichmentResult.unresolved` splits into `not_in_usda` and `lookup_failed`, carried through analyzer coverage into both reports. The custom-foods skill's `review` command, in the separate `my-skills` repo, gets its own group for these records.

**Tech Stack:** Python 3, TinyDB, requests, Rich, Jinja2, pytest + pytest-mock, `uv`.

**Spec:** `docs/specs/2026-10-08-usda-not-found-cache-design.md`

## Global Constraints

- Work on branch `alozay/bar-79-when-the-item-is-skipped-because-not-found-in-usda-query-it` (already checked out; the spec is committed on it).
- Only an HTTP 200 search with no hits is cached; a failed request (network error, timeout, any 4xx/5xx) is never cached.
- Not-found record shape, exactly: `{"original_name", "usda_query", "not_found": True, "match_version", "last_updated"}` — no `per_100g`, `usda_name` or `confidence`.
- `NOT_FOUND_TTL_DAYS = 30`; a record is fresh when `_is_current(entry)` and its age in days is `< NOT_FOUND_TTL_DAYS`.
- `get_food_match` returns `FoodMatch | LookupMiss` where `LookupMiss = Literal["not_in_usda", "lookup_failed"]`; it never returns `None`.
- Report copy, exactly: summary `"{mapped}/{total} entries analyzed ({skipped} skipped, {n} not in USDA, {m} lookup failed)"`; warnings `"⚠ Not in USDA: {foods} — fix their mapping in food_mappings.yaml"` and `"⚠ Lookup failed: {foods} — retried next run"`, red in the terminal.
- Tests never make a real HTTP call: USDA is always mocked; Gemini is never called live.
- Pure-Python only (the app also runs in a-Shell on iPhone); no new dependencies.
- Commit messages: one concise line, `feat:`/`fix:`/`docs:` prefix, ending in `(BAR-79)`.
- The installed skill copy in `.agents/skills/` is never edited by hand; it changes only through `npx skills`.

## Review Focus

- **A match replacing a not-found record:** TinyDB's `upsert` merges fields, so a match written over a not-found record would keep `not_found: True` and be read as not-found next run. Expect the record to become a clean match. Pinned in Task 1 (`test_not_found_record_is_replaced_by_a_later_match`).
- **A not-found record with a missing or garbled `last_updated`** (hand-edited DB, odd sync): expect it treated as expired and searched again, not a crash. Pinned in Task 1 (`test_not_found_record_with_bad_date_is_searched_again`).
- **Food names containing `[...]` in the new terminal warning lines:** Rich would parse them as markup and drop them. Expect them printed literally. Pinned in Task 3 (`test_terminal_prints_brackets_in_food_names_literally`).
- **A food remapped to `skip` after it was cached as not found:** expect it reported as skipped, with no lookup. Pinned in Task 2 (`test_skip_wins_over_a_not_found_record`).
- **An outdated not-found record after a `MATCH_VERSION` bump:** expect it counted in the "Re-matching N cached foods" message like any other outdated entry. Pinned in Task 1 (`test_outdated_not_found_record_is_counted`).

---

### Task 1: Cache "not found" in `get_food_match`

**Files:**
- Modify: `src/omni_pilot/enricher.py` (imports; constants near `MATCH_VERSION` line 21; `get_food_candidates` lines 203-217; new `_is_fresh_not_found` after `_is_current` line 349; `get_food_match` lines 361-434; `enrich_all_foods` line 537)
- Modify: `CLAUDE.md` (Key invariants)
- Test: `tests/test_enricher.py`

**Interfaces:**
- Consumes: `search_usda(query, api_key) -> list[dict] | None` (unchanged), `_is_current(entry) -> bool`, `matcher.pick_best` (returns `None` only for an empty candidate list).
- Produces:
  - `NOT_FOUND_TTL_DAYS: int = 30`
  - `LookupMiss = Literal["not_in_usda", "lookup_failed"]`
  - `get_food_candidates(query, api_key) -> tuple[list[Candidate], bool]` — `([], False)` when search 1 fails, `([], True)` when it finds nothing.
  - `get_food_match(food_name, usda_query, logged, db, api_key) -> FoodMatch | LookupMiss`
  - `enrich_all_foods` still returns `unresolved` in this task (any `LookupMiss` goes there); Task 2 splits it.

- [ ] **Step 1: Update the existing tests whose expectations change**

In `tests/test_enricher.py`, add at the top with the other imports:

```python
from datetime import date, timedelta
```

and add `NOT_FOUND_TTL_DAYS` to the `from omni_pilot.enricher import (...)` list.

In `class TestGetFoodCandidates`, replace `test_failed_query_search_returns_nothing` with:

```python
    def test_failed_query_search_returns_nothing_and_is_incomplete(self, mocker):
        # USDA could not be asked: nothing is known about the food.
        mock_search = mocker.patch("omni_pilot.enricher.search_usda", return_value=None)
        assert get_food_candidates("chickpeas, canned", "fake-key") == ([], False)
        mock_search.assert_called_once()

    def test_query_search_with_no_results_is_complete(self, mocker):
        # USDA answered and has no such food: that answer is final.
        mock_search = mocker.patch("omni_pilot.enricher.search_usda", return_value=[])
        assert get_food_candidates("chickpeas, canned", "fake-key") == ([], True)
        mock_search.assert_called_once()
```

In `class TestGetFoodMatch`, replace `test_changed_query_refetches` and `test_returns_none_when_usda_has_no_results` with:

```python
    def test_changed_query_refetches(self, mocker, tmp_path):
        db = TinyDB(str(tmp_path / "test_db.json"))
        db.insert(self._legacy_entry())
        mocker.patch("omni_pilot.enricher.get_food_candidates", return_value=([], True))

        match = get_food_match("Boiled Eggs", "egg, whole, raw", EGG_LOGGED, db, "fake-key")

        # The old entry belonged to a different query, so it is not kept.
        assert match == "not_in_usda"
        [entry] = db.all()
        assert entry["usda_query"] == "egg, whole, raw"
        assert entry["not_found"] is True
```

and replace `test_failed_repick_keeps_legacy_entry_as_unverified` with a version parametrized over both no-match outcomes:

```python
    @pytest.mark.parametrize("complete", [True, False], ids=["not-in-usda", "lookup-failed"])
    def test_failed_repick_keeps_legacy_entry_as_unverified(self, mocker, tmp_path, complete):
        db = TinyDB(str(tmp_path / "test_db.json"))
        db.insert(self._legacy_entry())
        mocker.patch("omni_pilot.enricher.get_food_candidates", return_value=([], complete))

        match = get_food_match("Boiled Eggs", "egg, whole, cooked, hard-boiled", EGG_LOGGED, db, "fake-key")

        assert match["per_100g"] == {"vitamin_a_mcg": 1.0}
        assert match["confidence"] == "unverified"
        # Unchanged and not stamped: the next run tries again, and a known
        # profile is never replaced by a not-found record.
        assert db.all() == [self._legacy_entry()]
```

- [ ] **Step 2: Write the failing tests for the not-found cache**

Add a new class after `TestGetFoodMatch` in `tests/test_enricher.py`:

```python
class TestNotFoundCache:
    FOOD = "Braun Linsen"
    QUERY = "lentils, mature seeds, raw"

    def _not_found(self, days_old: int = 0, match_version: int = MATCH_VERSION, **extra) -> dict:
        return {
            "original_name": self.FOOD,
            "usda_query": self.QUERY,
            "not_found": True,
            "match_version": match_version,
            "last_updated": str(date.today() - timedelta(days=days_old)),
            **extra,
        }

    def _db(self, tmp_path, *entries: dict) -> TinyDB:
        db = TinyDB(str(tmp_path / "test_db.json"))
        for entry in entries:
            db.insert(entry)
        return db

    def test_no_results_writes_a_not_found_record(self, mocker, tmp_path):
        db = self._db(tmp_path)
        mocker.patch("omni_pilot.enricher.get_food_candidates", return_value=([], True))

        assert get_food_match(self.FOOD, self.QUERY, None, db, "fake-key") == "not_in_usda"

        assert db.all() == [self._not_found()]

    def test_fresh_not_found_record_is_used_without_searching(self, mocker, tmp_path):
        mock_search = mocker.patch("omni_pilot.enricher.search_usda", return_value=[])
        db = self._db(tmp_path)

        get_food_match(self.FOOD, self.QUERY, None, db, "fake-key")
        mock_search.reset_mock()
        assert get_food_match(self.FOOD, self.QUERY, None, db, "fake-key") == "not_in_usda"

        mock_search.assert_not_called()

    def test_failed_lookup_caches_nothing_and_is_retried(self, mocker, tmp_path):
        mock_search = mocker.patch("omni_pilot.enricher.search_usda", return_value=None)
        db = self._db(tmp_path)

        assert get_food_match(self.FOOD, self.QUERY, None, db, "fake-key") == "lookup_failed"
        assert db.all() == []
        get_food_match(self.FOOD, self.QUERY, None, db, "fake-key")

        assert mock_search.call_count == 2

    def test_not_found_record_younger_than_ttl_is_used(self, mocker, tmp_path):
        db = self._db(tmp_path, self._not_found(days_old=NOT_FOUND_TTL_DAYS - 1))
        mock_candidates = mocker.patch("omni_pilot.enricher.get_food_candidates")

        assert get_food_match(self.FOOD, self.QUERY, None, db, "fake-key") == "not_in_usda"

        mock_candidates.assert_not_called()

    def test_expired_not_found_record_is_searched_again_and_refreshed(self, mocker, tmp_path):
        db = self._db(tmp_path, self._not_found(days_old=NOT_FOUND_TTL_DAYS))
        mock_candidates = mocker.patch("omni_pilot.enricher.get_food_candidates", return_value=([], True))

        assert get_food_match(self.FOOD, self.QUERY, None, db, "fake-key") == "not_in_usda"

        mock_candidates.assert_called_once()
        assert db.all() == [self._not_found()]

    def test_outdated_not_found_record_is_searched_again(self, mocker, tmp_path):
        db = self._db(tmp_path, self._not_found(match_version=MATCH_VERSION - 1))
        mock_candidates = mocker.patch("omni_pilot.enricher.get_food_candidates", return_value=([], True))

        get_food_match(self.FOOD, self.QUERY, None, db, "fake-key")

        mock_candidates.assert_called_once()
        assert db.all() == [self._not_found()]

    def test_not_found_record_with_bad_date_is_searched_again(self, mocker, tmp_path):
        db = self._db(tmp_path, {**self._not_found(), "last_updated": "garbled"})
        mock_candidates = mocker.patch("omni_pilot.enricher.get_food_candidates", return_value=([], True))

        assert get_food_match(self.FOOD, self.QUERY, None, db, "fake-key") == "not_in_usda"

        mock_candidates.assert_called_once()
        assert db.all() == [self._not_found()]

    def test_changed_mapping_drops_the_record_and_searches_the_new_query(self, mocker, tmp_path):
        db = self._db(tmp_path, self._not_found())
        mocker.patch("omni_pilot.enricher.get_food_candidates", return_value=([
            _candidate(7, "Lentils, raw", 24.6, 1.1, 63.4),
        ], True))

        match = get_food_match(self.FOOD, "lentils, raw", None, db, "fake-key")

        assert match["usda_name"] == "Lentils, raw"
        [entry] = db.all()
        assert entry["usda_query"] == "lentils, raw"
        assert "not_found" not in entry

    def test_not_found_record_is_replaced_by_a_later_match(self, mocker, tmp_path):
        # TinyDB's upsert merges fields; a leftover not_found flag would make
        # the next run read the match as not found.
        db = self._db(tmp_path, self._not_found(days_old=NOT_FOUND_TTL_DAYS))
        mocker.patch("omni_pilot.enricher.get_food_candidates", return_value=([
            _candidate(7, "Lentils, raw", 24.6, 1.1, 63.4),
        ], True))

        match = get_food_match(self.FOOD, self.QUERY, None, db, "fake-key")

        assert match["usda_name"] == "Lentils, raw"
        [entry] = db.all()
        assert "not_found" not in entry
        assert entry["usda_fdc_id"] == 7
        assert entry["match_version"] == MATCH_VERSION

    def test_expired_record_whose_refresh_fails_keeps_its_answer(self, mocker, tmp_path):
        expired = self._not_found(days_old=NOT_FOUND_TTL_DAYS + 5)
        db = self._db(tmp_path, expired)
        mocker.patch("omni_pilot.enricher.get_food_candidates", return_value=([], False))

        # USDA could not be asked; its last answer still stands.
        assert get_food_match(self.FOOD, self.QUERY, None, db, "fake-key") == "not_in_usda"

        assert db.all() == [expired]

    def test_query_empty_after_cleaning_is_not_in_usda(self, mocker, tmp_path):
        mock_get = mocker.patch("omni_pilot.enricher._SESSION.get")
        db = self._db(tmp_path)

        assert get_food_match("Slash", "/", None, db, "fake-key") == "not_in_usda"

        mock_get.assert_not_called()
        assert db.all()[0]["not_found"] is True

    def test_outdated_not_found_record_is_counted(self, tmp_path):
        db = self._db(tmp_path, self._not_found(match_version=MATCH_VERSION - 1))
        assert count_outdated_matches([self.FOOD], {self.FOOD: self.QUERY}, db) == 1

    def test_fresh_not_found_record_is_not_counted(self, tmp_path):
        db = self._db(tmp_path, self._not_found())
        assert count_outdated_matches([self.FOOD], {self.FOOD: self.QUERY}, db) == 0
```

Note: `MATCH_VERSION` is currently `1`, so `MATCH_VERSION - 1` is `0`, the same as a missing stamp — that is the intended "older" case.

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_enricher.py -v`
Expected: FAIL — `ImportError: cannot import name 'NOT_FOUND_TTL_DAYS'` (collection error for the whole file).

- [ ] **Step 4: Implement**

In `src/omni_pilot/enricher.py`:

Change the typing import (line 8) to:

```python
from typing import Literal, TypedDict
```

After `MATCH_VERSION = 1` (line 21) add:

```python
# Days a "not in USDA" answer is reused before the food is searched again,
# in case USDA has added it since.
NOT_FOUND_TTL_DAYS = 30
```

Next to the other result types (after `class FoodMatch`), add:

```python
# Why get_food_match has no match: USDA answered and has no such food
# ("not_in_usda", cached), or USDA could not be asked ("lookup_failed", never cached).
LookupMiss = Literal["not_in_usda", "lookup_failed"]
```

Replace the start of `get_food_candidates` and its docstring paragraph about completeness:

```python
def get_food_candidates(query: str, api_key: str) -> tuple[list[Candidate], bool]:
    """Collect USDA candidates for a query, ranked, without duplicates.

    Hits for the query itself come first, then hits for its head words alone
    (e.g. "chickpea"), which surface forms the full query ranks out of view.

    Also returns whether the set is complete: False when any search failed
    outright (as opposed to running and finding nothing). A pick made from an
    incomplete set should not be trusted as final, and no candidates from an
    incomplete set mean USDA could not be asked, not that it has no such food.
    """
    hits = search_usda(query, api_key)
    if hits is None:
        return [], False
    if not hits:
        return [], True
```

(The rest of the function is unchanged.)

After `_is_current`, add:

```python
def _is_fresh_not_found(entry: dict) -> bool:
    """Whether a not-found record can still be trusted without asking USDA again."""
    try:
        checked = date.fromisoformat(entry["last_updated"])
    except (KeyError, TypeError, ValueError):
        return False
    return _is_current(entry) and (date.today() - checked).days < NOT_FOUND_TTL_DAYS
```

Replace `get_food_match` up to (not including) `candidate = pick["candidate"]` with:

```python
def get_food_match(
    food_name: str,
    usda_query: str,
    logged: LoggedMacros | None,
    db: TinyDB,
    api_key: str,
) -> FoodMatch | LookupMiss:
    """Get the USDA match and per-100g micro profile for a food.

    A cache entry is reused only when both its query is current and its
    match_version is at least the current one (see `_is_current`). An entry
    picked by older matching logic is re-picked, but kept (as "unverified")
    if the re-pick fails, so a network hiccup never turns a known food into
    an unresolved one.

    A search that ran and found nothing is cached as a not-found record
    (`not_found: True`, no profile) and answered from the cache for
    NOT_FOUND_TTL_DAYS. A failed request caches nothing, so it is retried
    next run. Without a match, returns "not_in_usda" or "lookup_failed".

    A pick made from an incomplete candidate set (the head-word search
    failed) is cached but left without `match_version`, so it is treated as
    outdated and retried on the next run instead of being trusted as final.
    """
    Food = Query()
    stale_entry = None
    cached = db.search(Food.original_name == food_name)
    if cached:
        entry = cached[0]
        if entry.get("usda_query") == usda_query:
            if entry.get("not_found"):
                if _is_fresh_not_found(entry):
                    return "not_in_usda"
            elif _is_current(entry):
                return _match_from_entry(entry)
            stale_entry = entry
        else:
            # Mapping changed: the old entry describes a different query
            logger.info("Mapping changed for '%s', refetching...", food_name)
            db.remove(Food.original_name == food_name)

    candidates, complete = get_food_candidates(usda_query, api_key)
    pick = matcher.pick_best(candidates, usda_query, logged)
    if pick is None:
        if stale_entry is not None and not stale_entry.get("not_found"):
            logger.warning("Re-matching '%s' failed; keeping its previous USDA match", food_name)
            return _match_from_entry(stale_entry, confidence="unverified")
        if not complete:
            # USDA could not be asked; an expired not-found record is still its last answer
            return "not_in_usda" if stale_entry is not None else "lookup_failed"
        db.upsert({
            "original_name": food_name,
            "usda_query": usda_query,
            "not_found": True,
            "match_version": MATCH_VERSION,
            "last_updated": str(date.today()),
        }, Food.original_name == food_name)
        return "not_in_usda"

    if stale_entry is not None and stale_entry.get("not_found"):
        # upsert merges fields, so the not_found flag must go with the record
        db.remove(Food.original_name == food_name)
```

(Everything from `candidate = pick["candidate"]` to the end of the function is unchanged.)

In `enrich_all_foods`, change `if match is None:` (line 537) to:

```python
        if isinstance(match, str):
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_enricher.py -v`
Expected: all PASS.

Then run the full suite: `uv run pytest`
Expected: all PASS (other modules still see `unresolved`).

- [ ] **Step 6: Document the invariant in `CLAUDE.md`**

In `CLAUDE.md` under "### Key invariants", after the bullet that starts "A USDA cache entry is reused when its `usda_query` matches…", add:

```markdown
- A USDA search that ran and found nothing is cached as a not-found record in the same table (`not_found: True`, `usda_query`, `match_version`, `last_updated`, no `per_100g`) and answered from the cache for `NOT_FOUND_TTL_DAYS` (30); after that, after a `MATCH_VERSION` bump or after a mapping change, the food is searched again. A failed request (network error, timeout, any 4xx/5xx) is never cached — `get_food_candidates` reports it as an incomplete set — so the food is retried next run. A match written over a not-found record removes it first, because `upsert` merges fields. Older code reading a not-found record stops with `KeyError: 'per_100g'`; run current code.
```

- [ ] **Step 7: Commit**

```bash
git add src/omni_pilot/enricher.py tests/test_enricher.py CLAUDE.md
git commit -m "feat: cache USDA not-found results, never failed lookups (BAR-79)"
```

---

### Task 2: Split `EnrichmentResult.unresolved` into `not_in_usda` and `lookup_failed`

**Files:**
- Modify: `src/omni_pilot/enricher.py` (`EnrichmentResult` lines 45-60; `enrich_all_foods` lines 487-552)
- Modify: `tests/helpers.py`
- Modify: `CLAUDE.md` (enricher description)
- Test: `tests/test_enricher.py`, `tests/test_analyzer.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: `get_food_match(...) -> FoodMatch | LookupMiss` from Task 1.
- Produces:
  - `EnrichmentResult` keys: `profiles`, `skipped`, `not_in_usda: set[str]`, `lookup_failed: set[str]`, `low_confidence`, `custom` (no `unresolved`).
  - `tests.helpers.enrichment(profiles=None, *, skipped=(), not_in_usda=(), lookup_failed=(), low_confidence=None, custom=None)`.
  - The analyzer is unchanged in this task: it still counts every non-skipped, unanalysed food as `unresolved_*` coverage (Task 3 splits that).

- [ ] **Step 1: Update the test builder**

Replace `tests/helpers.py`'s `enrichment` with:

```python
def enrichment(
    profiles: dict[str, dict[str, float | None]] | None = None,
    *,
    skipped: Iterable[str] = (),
    not_in_usda: Iterable[str] = (),
    lookup_failed: Iterable[str] = (),
    low_confidence: dict[str, LowConfidenceMatch] | None = None,
    custom: dict[str, CustomFoodMatch] | None = None,
) -> EnrichmentResult:
    """Build an EnrichmentResult, defaulting the parts a test doesn't care about."""
    return {
        "profiles": dict(profiles or {}),
        "skipped": set(skipped),
        "not_in_usda": set(not_in_usda),
        "lookup_failed": set(lookup_failed),
        "low_confidence": dict(low_confidence or {}),
        "custom": dict(custom or {}),
    }
```

Rename the builder's callers (they describe a failed lookup):
- `tests/test_analyzer.py` line 110 and line 544: `unresolved={"Lachs"}` → `lookup_failed={"Lachs"}`.
- `tests/test_cli.py` line 163: `unresolved={"Lachs"}` → `lookup_failed={"Lachs"}`.

- [ ] **Step 2: Write the failing enricher tests**

In `tests/test_enricher.py`, `class TestEnrichAllFoods`:

In `test_skips_foods_mapped_to_skip`, replace `assert result["unresolved"] == set()` with:

```python
        assert result["not_in_usda"] == set()
        assert result["lookup_failed"] == set()
```

Replace `test_failed_usda_lookup_is_unresolved_not_skipped` with:

```python
    def test_food_usda_does_not_have_is_not_in_usda(self, mocker, tmp_path):
        # Only the network seam is faked, so the real get_food_match and
        # enrich_all_foods produce the shape the pipeline actually sees.
        db = TinyDB(str(tmp_path / "test_db.json"))
        mocker.patch("omni_pilot.enricher.search_usda", return_value=[])

        result = enrich_all_foods(["Lachs"], {"Lachs": "salmon"}, {}, db, "fake-key")

        assert result["not_in_usda"] == {"Lachs"}
        assert result["lookup_failed"] == set()
        assert result["skipped"] == set()
        assert "Lachs" not in result["profiles"]

    def test_failed_usda_request_is_lookup_failed(self, mocker, tmp_path):
        db = TinyDB(str(tmp_path / "test_db.json"))
        mocker.patch("omni_pilot.enricher.search_usda", return_value=None)

        result = enrich_all_foods(["Lachs"], {"Lachs": "salmon"}, {}, db, "fake-key")

        assert result["lookup_failed"] == {"Lachs"}
        assert result["not_in_usda"] == set()
        assert result["skipped"] == set()

    def test_skip_wins_over_a_not_found_record(self, mocker, tmp_path):
        db = TinyDB(str(tmp_path / "test_db.json"))
        db.insert({
            "original_name": "Lachs", "usda_query": "salmon", "not_found": True,
            "match_version": MATCH_VERSION, "last_updated": "2026-10-01",
        })
        mock_search = mocker.patch("omni_pilot.enricher.search_usda")

        result = enrich_all_foods(["Lachs"], {"Lachs": "skip"}, {}, db, "fake-key")

        assert result["skipped"] == {"Lachs"}
        assert result["not_in_usda"] == set()
        mock_search.assert_not_called()
```

In `test_profiles_never_contain_none`, keep the body; it now exercises `not_in_usda`.

In `class TestEnrichCustomFoods`:

Rename `test_unavailable_ingredient_makes_every_food_of_the_recipe_unresolved` to `test_unavailable_ingredient_makes_every_food_of_the_recipe_lookup_failed` and replace its assertion `assert result["unresolved"] == {...}` with:

```python
        assert result["lookup_failed"] == {"Salat Caprese", "Caprese to go"}
        assert result["not_in_usda"] == set()
```

In `test_without_custom_foods_nothing_is_custom`, replace `assert result["unresolved"] == {"Salat Caprese"}` with:

```python
        assert result["not_in_usda"] == {"Salat Caprese"}
```

Add to `class TestEnrichCustomFoods`:

```python
    def test_recipe_wins_over_a_not_found_record(self, mocker, tmp_path):
        db = TinyDB(str(tmp_path / "test_db.json"))
        db.insert({
            "original_name": "Salat Caprese", "usda_query": "Salat Caprese", "not_found": True,
            "match_version": MATCH_VERSION, "last_updated": "2026-10-01",
        })
        self._fetch(mocker, {1: TOMATO_HIT, 2: MOZZARELLA_HIT})

        result = enrich_all_foods(["Salat Caprese"], {}, {}, db, "fake-key", custom_foods=CAPRESE_FOODS)

        assert result["custom"]["Salat Caprese"]["recipe"] == "caprese"
        assert result["not_in_usda"] == set()
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_enricher.py -v -k "EnrichAllFoods or EnrichCustomFoods"`
Expected: FAIL with `KeyError: 'not_in_usda'` / `KeyError: 'lookup_failed'`.

- [ ] **Step 4: Implement**

In `src/omni_pilot/enricher.py`, replace the start of `EnrichmentResult` through `unresolved: set[str]` with:

```python
class EnrichmentResult(TypedDict):
    """Outcome of enriching a set of foods.

    "Deliberately skipped", "USDA has no such food" and "USDA could not be
    asked" are different facts and are kept in separate sets, so profiles
    never carries None for any of them.
    """

    profiles: dict[str, dict[str, float | None]]
    skipped: set[str]
    # The USDA search ran and found nothing: the mapping needs fixing.
    not_in_usda: set[str]
    # USDA could not be asked, or a recipe ingredient could not be fetched:
    # retried next run.
    lookup_failed: set[str]
```

In `enrich_all_foods`:

Replace the docstring's middle sentence and the result initialisation:

```python
    """Enrich all foods with USDA micro data.

    A food covered by a custom recipe is built from the recipe's ingredients
    and goes into custom, whatever its mapping says (even "skip"). Otherwise:
    resolved foods go into profiles, foods mapped to "skip" into skipped,
    foods USDA has no match for into not_in_usda, and foods whose lookup
    failed into lookup_failed. Resolved foods whose match is weak are also
    listed in low_confidence.
    """
    if custom_foods is None:
        custom_foods = no_custom_foods()
    result: EnrichmentResult = {
        "profiles": {}, "skipped": set(), "not_in_usda": set(), "lookup_failed": set(),
        "low_confidence": {}, "custom": {},
    }
```

In the recipe branch, change `result["unresolved"].add(food_name)` to:

```python
                result["lookup_failed"].add(food_name)
```

Replace the `if isinstance(match, str):` block from Task 1 with:

```python
        if match == "not_in_usda":
            result["not_in_usda"].add(food_name)
            logger.warning("USDA has no match for '%s' (query: '%s')", food_name, query)
            continue
        if match == "lookup_failed":
            result["lookup_failed"].add(food_name)
            logger.warning("Could not look up '%s' (query: '%s') — retried next run", food_name, query)
            continue
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest`
Expected: all PASS.

- [ ] **Step 6: Update the enricher description in `CLAUDE.md`**

In `CLAUDE.md`, in the `enricher.py` bullet, replace

```
`enrich_all_foods` returns an `EnrichmentResult` with four parts: `profiles` (resolved foods only, never `None`), `skipped` (mapped to `"skip"`), `unresolved` (USDA lookup failed) and `low_confidence` (resolved foods whose match fits the logged macros poorly — still in `profiles` and counted, but named in the report). Keep `skipped` and `unresolved` distinct — the report labels them differently, and encoding both as `None` once made failed lookups read as deliberate skips.
```

with

```
`enrich_all_foods` returns an `EnrichmentResult` with these parts: `profiles` (resolved foods only, never `None`), `skipped` (mapped to `"skip"`), `not_in_usda` (the USDA search ran and found nothing — fix the mapping), `lookup_failed` (USDA could not be asked, or a recipe ingredient could not be fetched — retried next run) and `low_confidence` (resolved foods whose match fits the logged macros poorly — still in `profiles` and counted, but named in the report). Keep `skipped`, `not_in_usda` and `lookup_failed` distinct — the report labels them differently, and encoding them alike once made failed lookups read as deliberate skips, and later network failures read as missing foods.
```

Also, in the last Key invariants bullet ("A failed ingredient fetch caches nothing and makes every food of that recipe unresolved for the run."), replace "unresolved" with "`lookup_failed`".

- [ ] **Step 7: Commit**

```bash
git add src/omni_pilot/enricher.py tests/helpers.py tests/test_enricher.py tests/test_analyzer.py tests/test_cli.py CLAUDE.md
git commit -m "feat: split unresolved foods into not in USDA and lookup failed (BAR-79)"
```

---

### Task 3: Analyzer coverage and both reports

**Files:**
- Modify: `src/omni_pilot/analyzer.py` (`CoverageResult` lines 50-60; counting lines 163-197; result lines 310-320)
- Modify: `src/omni_pilot/reporter.py` (`_coverage_line` lines 66-72; terminal warnings lines 201-208; HTML template lines 308-313; render kwargs line 368)
- Modify: `CLAUDE.md` (reporter description)
- Test: `tests/test_analyzer.py`, `tests/test_reporter.py`, `tests/test_custom_foods_integration.py`

**Interfaces:**
- Consumes: `EnrichmentResult["not_in_usda"]`, `EnrichmentResult["lookup_failed"]` from Task 2.
- Produces: `CoverageResult` keys `not_in_usda_entries: int`, `lookup_failed_entries: int`, `not_in_usda_foods: list[str]`, `lookup_failed_foods: list[str]` (sorted), replacing `unresolved_entries` / `unresolved_foods`.

- [ ] **Step 1: Write the failing analyzer tests**

In `tests/test_analyzer.py`, replace `test_failed_lookup_reported_as_unresolved_not_skipped` with:

```python
    def test_missing_and_failed_foods_are_reported_apart_from_skipped(self):
        entries = [
            self._make_entry("Eggs", "2026-07-13", 200.0),
            self._make_entry("Lachs", "2026-07-13", 300.0),
            self._make_entry("Lachs", "2026-07-14", 100.0),
            self._make_entry("Braun Linsen", "2026-07-13", 80.0),
            self._make_entry("Water", "2026-07-13", 500.0),
        ]
        result = analyze(
            entries,
            enrichment(
                {"Eggs": {"vitamin_a_mcg": 149.0, "calcium_mg": 50.0}},
                skipped={"Water"},
                not_in_usda={"Braun Linsen"},
                lookup_failed={"Lachs"},
            ),
            self._make_ref_ranges(),
        )

        coverage = result["coverage"]
        # "You told it to ignore this", "USDA has no such food" and "USDA could
        # not be asked" are different report lines.
        assert coverage["mapped_entries"] == 1
        assert coverage["skipped_entries"] == 1
        assert coverage["skipped_foods"] == ["Water"]
        assert coverage["not_in_usda_entries"] == 1
        assert coverage["not_in_usda_foods"] == ["Braun Linsen"]
        assert coverage["lookup_failed_entries"] == 2
        assert coverage["lookup_failed_foods"] == ["Lachs"]
        # Neither counts toward coverage: only Eggs' weight is analysed.
        assert result["nutrients"]["vitamin_a_mcg"]["coverage_pct"] == 100.0
```

In the custom-foods coverage test (around line 630), replace `assert coverage["unresolved_entries"] == 0` with:

```python
        assert coverage["not_in_usda_entries"] == 0
        assert coverage["lookup_failed_entries"] == 0
```

In `tests/test_custom_foods_integration.py` line 152, replace `assert result["coverage"]["unresolved_foods"] == []` with:

```python
    assert result["coverage"]["not_in_usda_foods"] == []
    assert result["coverage"]["lookup_failed_foods"] == []
```

- [ ] **Step 2: Write the failing reporter tests**

In `tests/test_reporter.py`, in `_make_analysis_result()`'s `"coverage"`, replace the two `unresolved_*` keys with:

```python
            "not_in_usda_entries": 3,
            "lookup_failed_entries": 1,
            "skipped_foods": ["Quick Add", "Dessert, Prepared"],
            "not_in_usda_foods": ["Unknown Thing"],
            "lookup_failed_foods": ["Himbeeren"],
```

(keeping the existing `"skipped_foods"` line only once.)

In `test_html_shows_warnings_block_for_low_confidence_alone`, replace `result["coverage"]["unresolved_foods"] = []` with:

```python
        result["coverage"]["not_in_usda_foods"] = []
        result["coverage"]["lookup_failed_foods"] = []
```

Add a new class at the end of the file:

```python
class TestMissingFoodWarnings:
    SETTINGS = {"output": {"show_amino_acids": True, "show_ok_nutrients": True}}

    def test_terminal_shows_counts_and_both_warning_lines(self, capsys, monkeypatch):
        monkeypatch.setenv("COLUMNS", "300")
        print_terminal_report(_make_analysis_result(), self.SETTINGS)
        out = capsys.readouterr().out
        assert "207/223 entries analyzed (12 skipped, 3 not in USDA, 1 lookup failed)" in out
        assert "⚠ Not in USDA: Unknown Thing — fix their mapping in food_mappings.yaml" in out
        assert "⚠ Lookup failed: Himbeeren — retried next run" in out
        assert "Unresolved" not in out

    def test_terminal_omits_lines_with_no_foods(self, capsys, monkeypatch):
        monkeypatch.setenv("COLUMNS", "300")
        result = _make_analysis_result()
        result["coverage"]["not_in_usda_foods"] = []
        result["coverage"]["lookup_failed_foods"] = []
        print_terminal_report(result, self.SETTINGS)
        out = capsys.readouterr().out
        assert "Not in USDA:" not in out
        assert "Lookup failed:" not in out

    def test_terminal_prints_brackets_in_food_names_literally(self, capsys, monkeypatch):
        monkeypatch.setenv("COLUMNS", "300")
        result = _make_analysis_result()
        result["coverage"]["not_in_usda_foods"] = ["Reis [bio]"]
        result["coverage"]["lookup_failed_foods"] = ["Tee [grün]"]
        print_terminal_report(result, self.SETTINGS)
        out = capsys.readouterr().out
        assert "Not in USDA: Reis [bio]" in out
        assert "Lookup failed: Tee [grün]" in out

    def test_html_shows_counts_and_both_warning_lines(self, tmp_path):
        html_path = str(tmp_path / "report.html")
        generate_html_report(_make_analysis_result(), html_path)
        with open(html_path) as f:
            html = f.read()
        assert "207/223 entries analyzed (12 skipped, 3 not in USDA, 1 lookup failed)" in html
        assert "⚠ Not in USDA: Unknown Thing — fix their mapping in food_mappings.yaml" in html
        assert "⚠ Lookup failed: Himbeeren — retried next run" in html
        assert "Unresolved" not in html

    def test_html_omits_lines_with_no_foods(self, tmp_path):
        result = _make_analysis_result()
        result["coverage"]["not_in_usda_foods"] = []
        result["coverage"]["lookup_failed_foods"] = []
        html_path = str(tmp_path / "report.html")
        generate_html_report(result, html_path)
        with open(html_path) as f:
            html = f.read()
        assert "Not in USDA:" not in html
        assert "Lookup failed:" not in html
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_analyzer.py tests/test_reporter.py tests/test_custom_foods_integration.py -v`
Expected: FAIL with `KeyError: 'not_in_usda_entries'` (analyzer) and `KeyError: 'unresolved_entries'` (reporter).

- [ ] **Step 4: Implement the analyzer**

In `src/omni_pilot/analyzer.py`, in `CoverageResult` replace `unresolved_entries: int` and `unresolved_foods: list[str]` so the fields read:

```python
    total_food_entries: int
    mapped_entries: int
    skipped_entries: int
    not_in_usda_entries: int
    lookup_failed_entries: int
    skipped_foods: list[str]
    not_in_usda_foods: list[str]
    lookup_failed_foods: list[str]
```

Replace the counters (lines 166-168):

```python
    skipped_entries = 0
    not_in_usda_entries = 0
    lookup_failed_entries = 0
    skipped_food_names: set[str] = set()
    not_in_usda_food_names: set[str] = set()
    lookup_failed_food_names: set[str] = set()
```

Replace the `if parts is None:` block:

```python
        if parts is None:
            # Skipped, not-in-USDA and failed foods are all excluded from
            # coverage on both sides; they differ only in how the report
            # labels them.
            if food_name in enrichment["skipped"]:
                skipped_entries += 1
                skipped_food_names.add(food_name)
            elif food_name in enrichment["not_in_usda"]:
                not_in_usda_entries += 1
                not_in_usda_food_names.add(food_name)
            else:
                lookup_failed_entries += 1
                lookup_failed_food_names.add(food_name)
            continue
```

In the `CoverageResult(...)` construction, replace the two `unresolved_*` arguments:

```python
            skipped_entries=skipped_entries,
            not_in_usda_entries=not_in_usda_entries,
            lookup_failed_entries=lookup_failed_entries,
            skipped_foods=sorted(skipped_food_names),
            not_in_usda_foods=sorted(not_in_usda_food_names),
            lookup_failed_foods=sorted(lookup_failed_food_names),
```

- [ ] **Step 5: Implement the reporter**

In `src/omni_pilot/reporter.py`, replace `_coverage_line`:

```python
def _coverage_line(coverage: dict) -> str:
    """Build the coverage stats line."""
    mapped = coverage["mapped_entries"]
    total = coverage["total_food_entries"]
    skipped = coverage["skipped_entries"]
    not_in_usda = coverage["not_in_usda_entries"]
    lookup_failed = coverage["lookup_failed_entries"]
    return (
        f"{mapped}/{total} entries analyzed "
        f"({skipped} skipped, {not_in_usda} not in USDA, {lookup_failed} lookup failed)"
    )
```

Replace the terminal `if coverage["unresolved_foods"]:` block:

```python
    # Text, not markup strings: food names can contain "[...]".
    if coverage["not_in_usda_foods"]:
        foods_str = ", ".join(coverage["not_in_usda_foods"])
        console.print(Text(
            f"  ⚠ Not in USDA: {foods_str} — fix their mapping in food_mappings.yaml", style="red",
        ))
    if coverage["lookup_failed_foods"]:
        foods_str = ", ".join(coverage["lookup_failed_foods"])
        console.print(Text(f"  ⚠ Lookup failed: {foods_str} — retried next run", style="red"))
```

In `HTML_TEMPLATE`, replace the warnings block's condition and the `Unresolved` line:

```
    {% if skipped_foods or not_in_usda_foods or lookup_failed_foods or low_confidence_line %}
    <div class="warnings">
        {% if skipped_foods %}<p>⚠ Skipped: {{ skipped_foods|join(", ") }}</p>{% endif %}
        {% if not_in_usda_foods %}<p>⚠ Not in USDA: {{ not_in_usda_foods|join(", ") }} — fix their mapping in food_mappings.yaml</p>{% endif %}
        {% if lookup_failed_foods %}<p>⚠ Lookup failed: {{ lookup_failed_foods|join(", ") }} — retried next run</p>{% endif %}
        {% if low_confidence_line %}<p>⚠ {{ low_confidence_line }}</p>{% endif %}
    </div>
    {% endif %}
```

In `generate_html_report`'s `template.render(...)`, replace `unresolved_foods=coverage["unresolved_foods"],` with:

```python
        not_in_usda_foods=coverage["not_in_usda_foods"],
        lookup_failed_foods=coverage["lookup_failed_foods"],
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest`
Expected: all PASS. Then confirm nothing still names the old keys:

Run: `grep -rn "unresolved" src tests`
Expected: only `src/omni_pilot/translator.py` (its Gemini log message, unrelated).

- [ ] **Step 7: Update the reporter description in `CLAUDE.md`**

In the `reporter.py` bullet, replace "After the skipped/unresolved warnings it prints" with:

```
After the warnings for skipped foods, foods not in USDA ("fix their mapping") and failed lookups ("retried next run") it prints
```

- [ ] **Step 8: Commit**

```bash
git add src/omni_pilot/analyzer.py src/omni_pilot/reporter.py tests/test_analyzer.py tests/test_reporter.py tests/test_custom_foods_integration.py CLAUDE.md
git commit -m "feat: report foods not in USDA apart from failed lookups (BAR-79)"
```

---

### Task 4: Custom-foods skill — "Not in USDA" group in `review` (my-skills repo)

**Files (in `~/Projects/my-skills`):**
- Modify: `skills/omni-pilot-custom-foods/scripts/recipe_tool.py` (`cmd_review`, lines 145-200)
- Modify: `skills/omni-pilot-custom-foods/SKILL.md` (Check-up list)

**Files (in omni-pilot):**
- Modify (via `npx skills` only): `.agents/skills/omni-pilot-custom-foods/`, `skills-lock.json`

**Interfaces:**
- Consumes: the not-found record shape from Task 1 (`not_found: True`, `usda_query`, no `usda_name`).
- Produces: nothing other tasks use.

- [ ] **Step 1: Branch in my-skills**

```bash
cd ~/Projects/my-skills
git checkout main && git pull --ff-only
git checkout -b alozay/bar-79-not-in-usda-review-group
```

- [ ] **Step 2: Change `cmd_review`**

In `skills/omni-pilot-custom-foods/scripts/recipe_tool.py`, `cmd_review`:

Replace the `groups` line:

```python
    groups: dict[str, list[str]] = {
        "custom": [], "skipped": [], "searched": [], "not in usda": [], "not looked up": [],
    }
```

Replace the `elif name in matches:` branch:

```python
        elif name in matches:
            # A not-found record (BAR-79) has a query but no match
            groups["not in usda" if matches[name].get("not_found") else "searched"].append(name)
```

After the `for name in groups["searched"]:` loop (before the "Skipped, not counted" print), add:

```python
    if groups["not in usda"]:
        print(f"\n== Not in USDA ({len(groups['not in usda'])}): fix their mapping")
        for name in groups["not in usda"]:
            print(header(name))
            print(f"    looked up as: {matches[name].get('usda_query')}")
```

- [ ] **Step 3: Mention it in `SKILL.md`**

In `skills/omni-pilot-custom-foods/SKILL.md`, in the Check-up bullet list, after the "**Skipped foods that are real food**" bullet, add:

```markdown
- **Not in USDA** (its own group in `review`): USDA found nothing for the
  mapped query; fix the mapping in `food_mappings.yaml`, or give it a recipe.
```

- [ ] **Step 4: Check `review` by hand against a copy of the real database**

From the omni-pilot repo root (the script imports `omni_pilot`), with a scratch copy so the real DB is never written:

```bash
cd /Users/seric/Projects/omni-pilot
S=/private/tmp/claude-501/-Users-seric-Projects-omni-pilot/77b18f31-2b0e-4943-9c64-a12476d0ba7e/scratchpad/bar79-review
mkdir -p "$S"
PYTHONPATH=src uv run python - "$S" <<'EOF'
import os, shutil, sys
sys.path.insert(0, os.path.expanduser("~/Projects/my-skills/skills/omni-pilot-custom-foods/scripts"))
from tinydb import Query, TinyDB
import recipe_tool
from omni_pilot.config import resolve_path

scratch = sys.argv[1]
real = resolve_path("database_path", recipe_tool.DEFAULT_DB, recipe_tool._settings())
copy = os.path.join(scratch, "food_db.json")
shutil.copyfile(real, copy)
db = TinyDB(copy)
db.update({"not_found": True, "usda_query": "zzqx no such food"}, Query().original_name == "Himbeeren")
for key in ("per_100g", "usda_name", "confidence"):
    db.update(lambda doc, k=key: doc.pop(k, None), Query().original_name == "Himbeeren")
db.close()
recipe_tool._open_db = lambda settings: TinyDB(copy, access_mode="r")
sys.argv = ["recipe_tool", "review"]
recipe_tool.main()
EOF
```

Expected: a `== Not in USDA (1): fix their mapping` group listing `Himbeeren` with `looked up as: zzqx no such food`, and `Himbeeren` absent from "Searched in USDA".

- [ ] **Step 5: Commit and push my-skills**

```bash
cd ~/Projects/my-skills
git add skills/omni-pilot-custom-foods/scripts/recipe_tool.py skills/omni-pilot-custom-foods/SKILL.md
git commit -m "omni-pilot-custom-foods: list not-in-USDA foods in review (BAR-79)"
git push -u origin alozay/bar-79-not-in-usda-review-group
```

- [ ] **Step 6: Find out whether `npx skills` can install from a branch**

```bash
cd /Users/seric/Projects/omni-pilot
npx skills add --help
```

Look for a ref/branch option or an `owner/repo#ref` source form.

- [ ] **Step 7: Install the updated skill into omni-pilot**

If a branch install is supported, install from the branch (exact form per Step 6), e.g.:

```bash
npx skills add alperozaydin/my-skills#alozay/bar-79-not-in-usda-review-group --skill omni-pilot-custom-foods
```

If it is not supported: stop and ask the user to merge the my-skills PR (`gh pr create` in `~/Projects/my-skills` first), then run:

```bash
npx skills update
```

Either way, verify the installed copy matches the branch and the lock file changed:

```bash
diff -r ~/Projects/my-skills/skills/omni-pilot-custom-foods .agents/skills/omni-pilot-custom-foods && echo SAME
git diff --stat skills-lock.json
```

Expected: `SAME`, and `skills-lock.json` shows a changed `computedHash`. If `skills.json` now names a branch source, note it for the user: it must point back at `main` once the my-skills PR is merged.

- [ ] **Step 8: Commit the installed skill**

```bash
git add .agents/skills/omni-pilot-custom-foods skills-lock.json skills.json
git commit -m "chore: update omni-pilot-custom-foods skill for not-in-USDA foods (BAR-79)"
```

---

## Final Verification

- [ ] **Step 1: Full test suite and lint**

Run: `make test && make lint`
Expected: all tests PASS, no lint errors.

- [ ] **Step 2: Two real runs against a scratch copy of the database**

Uses the real USDA API but a scratch database and mappings, so the real iCloud DB and `food_mappings.yaml` are untouched; no `--html`, so `reports/` is untouched.

```bash
cd /Users/seric/Projects/omni-pilot
S=/private/tmp/claude-501/-Users-seric-Projects-omni-pilot/77b18f31-2b0e-4943-9c64-a12476d0ba7e/scratchpad/bar79-e2e
mkdir -p "$S"
PYTHONPATH=src uv run python - "$S" <<'EOF'
import os, shutil, sys, yaml
from tinydb import Query, TinyDB
from omni_pilot.config import resolve_path

scratch = sys.argv[1]
settings = yaml.safe_load(open("config/settings.yaml"))
db_copy = os.path.join(scratch, "food_db.json")
map_copy = os.path.join(scratch, "food_mappings.yaml")
shutil.copyfile(resolve_path("database_path", "db/food_db.json", settings), db_copy)
shutil.copyfile(resolve_path("mappings_path", "config/food_mappings.yaml", settings), map_copy)
settings["database_path"], settings["mappings_path"] = db_copy, map_copy
yaml.safe_dump(settings, open(os.path.join(scratch, "settings.yaml"), "w"))
# The DB's translations win over the YAML, so remap there.
db = TinyDB(db_copy)
db.table("translations").update({"english": "zzqx no such food"}, Query().german == "Himbeeren")
db.close()
EOF
for run in 1 2; do
  PYTHONPATH=src uv run python -m omni_pilot.cli analyze data/MacroFactor-20261007224210.xlsx \
    --settings "$S/settings.yaml" > "$S/run$run.log" 2>&1
done
grep -c "No USDA results for query: 'zzqx" "$S/run1.log" "$S/run2.log"
grep "Not in USDA\|Lookup failed\|entries analyzed" "$S/run2.log"
```

Expected:
- `run1.log` contains `No USDA results for query: 'zzqx no such food'`; `run2.log` does not (no USDA request on the second run).
- Both runs show `⚠ Not in USDA: Himbeeren — fix their mapping in food_mappings.yaml` and a coverage line with `1 not in USDA` (more if other foods have no USDA match).
- No `Lookup failed` line unless the network actually failed.

If `Himbeeren` is not in that export, pick any food that `grep` shows in `run1.log`'s "Enriching" phase and remap that one instead.

- [ ] **Step 3: Report to the user**

Summarise: tests, the two runs' evidence, the my-skills branch and PR state, and whether `skills.json` needs pointing back at `main` after the my-skills merge.
