# Technical Specification: Cache USDA "Not Found" Results (BAR-79)

**Document Version:** 1.1 (review fix: not-in-USDA advice points to a custom recipe, because a `food_mappings.yaml` edit is overridden by the DB translation and reverted)
**Date:** 2026-10-08
**Status:** In Review
**Linear Issue:** [BAR-79](https://linear.app/knaak/issue/BAR-79/when-the-item-is-skipped-because-not-found-in-usda-query-it-will-be)
**Builds on:** BAR-72 (versioned USDA cache, `match_version`), BAR-42 (skipped vs unresolved), BAR-78 (USDA timeouts)

---

## 1. Executive Summary & Objective

### The Problem

When USDA has no match for a food's query, `get_food_match` returns `None` and caches nothing (`enricher.py:394-400`). The next `make analyze` has no cache entry for the food, searches USDA again and gets the same empty answer, on every run.

Two different events produce that `None` today:

- **Not in USDA:** the search ran (HTTP 200) and returned no foods. Asking again gives the same answer.
- **Lookup failed:** the request failed (network error, timeout, any 4xx/5xx). USDA never answered, so asking again can succeed.

`search_usda` already tells these apart (`[]` vs `None`), but `get_food_candidates` merges them (`if not hits: return [], True`). The report merges them too, under one "Unresolved" line. On 2026-10-07 four foods (Basmatireis, Bio Vollkorn, Braun Linsen, Himbeeren) were listed as unresolved because of a failed lookup (the BAR-78 stall), not because USDA lacked them; a later run resolved all four. Retrying them was right.

Gemini is not affected: a translation is cached once it is returned, and only foods with no translation yet are sent to Gemini.

### The Solution

- Keep the two outcomes apart from the search up to the report.
- Cache **not in USDA** as a record in the main USDA cache table, reused for 30 days, so later runs make no USDA call for that food.
- Never cache a **lookup failed**; the food is retried next run, as today.
- Split the report's "Unresolved" line into **Not in USDA** (give it a custom recipe) and **Lookup failed** (retried next run).
- Show not-found foods as their own group in the custom-foods skill's `review` command.

### Decisions Made During Design

| Decision | Choice | Why |
| --- | --- | --- |
| What gets cached | Only an HTTP 200 search with no hits | A failed request says nothing about the food; caching it would hide foods that resolve on the next run (the 2026-10-07 case). |
| Where the record lives | Main USDA cache table, same `original_name` key as matches | Keeps one record per food; `upsert`, the mapping-change removal and `match_version` all work on it unchanged. A separate table was considered for safety with older code and rejected (§7). |
| Expiry | 30 days, then searched again | Covers USDA adding a food later at one search per missing food per month. |
| Report | Two lines instead of "Unresolved" | A not-found food needs a custom recipe (a `food_mappings.yaml` edit is overridden by the DB translation); a failed lookup fixes itself. One label hid that. |
| Recipe ingredient failures | Reported as **lookup failed** | Same as today's `unresolved`; a wrong FDC ID (404) is already named in the log. |

---

## 2. The Three Lookup Outcomes

### 2.1 `get_food_candidates`

The returned `complete` flag widens from "the head-word search did not fail" to "**no** search failed":

| Search 1 | Head-word search | Returns |
| --- | --- | --- |
| fails (`None`) | not run | `([], False)` — today `([], True)` |
| no hits (`[]`) | not run | `([], True)` (unchanged) |
| hits | fails | `(candidates, False)` (unchanged) |
| hits | runs | `(candidates, True)` (unchanged) |

`matcher.pick_best` returns `None` only for an empty candidate list, so the outcome follows from the pair:

| candidates | complete | Outcome |
| --- | --- | --- |
| some | either | match (an incomplete pick is cached unstamped, as today) |
| none | `True` | **not in USDA** |
| none | `False` | **lookup failed** |

An empty query after `clean_query` already returns `[]` from `search_usda`, so it counts as not in USDA; it is deterministic, so caching it is correct.

### 2.2 `get_food_match` return type

```python
LookupMiss = Literal["not_in_usda", "lookup_failed"]

def get_food_match(...) -> FoodMatch | LookupMiss: ...
```

`None` is no longer returned.

---

## 3. The Not-Found Record

Written with `db.upsert(..., Food.original_name == food_name)` into the main (default) table:

```python
{
    "original_name": food_name,
    "usda_query": usda_query,
    "not_found": True,
    "match_version": MATCH_VERSION,
    "last_updated": str(date.today()),
}
```

It has no `per_100g`, `usda_name` or `confidence`.

A new constant `NOT_FOUND_TTL_DAYS = 30` in `enricher.py`. A not-found record is **fresh** when `_is_current(entry)` holds and `date.today() - date.fromisoformat(entry["last_updated"])` is under `NOT_FOUND_TTL_DAYS` days.

---

## 4. `get_food_match` Flow

### 4.1 Reading the cache (entry with the same `usda_query`)

| Cached entry | Action |
| --- | --- |
| match, `_is_current` | reuse (unchanged) |
| match, outdated | search; the entry is the stale match (unchanged) |
| not-found, fresh | return `"not_in_usda"`, **no USDA call** |
| not-found, expired or outdated | search; remember the entry as the stale not-found record |

A different `usda_query` removes the entry and searches the new query (unchanged, `enricher.py:392`); this applies to not-found records as well, so fixing a mapping triggers a new search.

### 4.2 After the search

| Outcome | No previous entry | Stale match | Stale not-found record |
| --- | --- | --- | --- |
| match | upsert the match | upsert the match | upsert the match (replaces the record) |
| not in USDA | upsert a not-found record; return `"not_in_usda"` | keep the old match as `unverified`, entry unchanged (as today) | refresh the record (`last_updated`, `match_version`); return `"not_in_usda"` |
| lookup failed | write nothing; return `"lookup_failed"` | keep the old match as `unverified`, entry unchanged (as today) | write nothing; return `"not_in_usda"` (USDA's last answer) |

A stale match whose refresh finds nothing keeps its profile: a known profile is worth more than USDA's sudden "no", and the entry stays unstamped so it is retried, exactly as today.

### 4.3 `count_outdated_matches`

Unchanged. An outdated not-found record (older `match_version`) is counted like any other outdated entry. An expired but current-version record is not counted; the message is about `MATCH_VERSION` re-picks.

---

## 5. `EnrichmentResult` and `enrich_all_foods`

`unresolved: set[str]` is replaced by:

```python
not_in_usda: set[str]     # the USDA search ran and found nothing
lookup_failed: set[str]   # USDA could not be asked, or a recipe ingredient could not be fetched
```

The docstring keeps its point, now for three facts: skipped, not in USDA and lookup failed are different, so none is encoded as `None` in `profiles`.

`enrich_all_foods` sorts by `get_food_match`'s return value. A custom recipe whose ingredient is unavailable goes to `lookup_failed`. Recipe foods are checked first and `continue` before `get_food_match` (`enricher.py:511-524`), so they never get a not-found record; a stale not-found record for a food that later gets a recipe is simply ignored. The `usda_foods` table and `get_usda_food` are unchanged.

---

## 6. Analyzer, Reports and CLI

### 6.1 `analyzer.py`

`CoverageResult.unresolved_entries` / `unresolved_foods` become `not_in_usda_entries` / `not_in_usda_foods` and `lookup_failed_entries` / `lookup_failed_foods`. Counting is unchanged: skipped, not-in-USDA and lookup-failed foods are all excluded from coverage on both sides and differ only in their labels.

### 6.2 `reporter.py`

Summary line, terminal and HTML:

```
120/130 entries analyzed (5 skipped, 3 not in USDA, 2 lookup failed)
```

Warnings, terminal (red) and HTML:

```
⚠ Not in USDA: Braun Linsen — give them a custom recipe in custom_foods.yaml
⚠ Lookup failed: Himbeeren — retried next run
```

Each line appears only when its list is non-empty.

### 6.3 CLI

The `N/M foods resolved.` progress line is unchanged.

---

## 7. Compatibility with Older Code

The iPhone and Mac share `food_db.json` through iCloud. Older code that reaches a not-found record (with its query still current) calls `_match_from_entry`, either to reuse it or to fall back to it after a re-pick finds nothing,, which reads `entry["per_100g"]`, and stops with `KeyError: 'per_100g'` before writing anything. The database is untouched and no report is produced; running current code (`make sync-iphone`, or checking out the latest branch) fixes it.

This is accepted: the user always runs and syncs the latest code. The record is never given a dummy `per_100g`, because older code would then count the food as eaten with no nutrients and produce a silently wrong report.

---

## 8. Custom-Foods Skill (`my-skills` repo)

The skill's `scripts/recipe_tool.py` reads every main-table entry with `db.all()`. Its `review` command would list a not-found record under "Searched in USDA" as `matched to: None [?]`.

- **Change:** in `review`, records with `not_found` go to their own group, printed after "Searched in USDA":
  ```
  == Not in USDA (n): give them a recipe
  Braun Linsen  (420 g; logged ...)
      looked up as: lentils, mature seeds, raw
  ```
  `flagged` (filters on `confidence == "weak"`) and `check` (reads names only) need no change. `SKILL.md` mentions the new group where it describes `review`.
- **Where:** `~/Projects/my-skills`, `skills/omni-pilot-custom-foods/`, on a feature branch for BAR-79, pushed to `alperozaydin/my-skills`. The installed copy in omni-pilot (`.agents/skills/`, linked from `.claude/skills`) is never edited by hand.
- **Install:** omni-pilot installs the updated skill with `npx skills`, which also updates `skills-lock.json`. The plan first checks whether `npx skills add` can install from a branch; if not, the `my-skills` PR is merged first and `npx skills update` is run here.

---

## 9. Testing

Tests are written first. USDA HTTP is always mocked; Gemini is never called live.

**`tests/test_enricher.py`**
- `get_food_candidates`: search 1 failing returns `([], False)`; search 1 returning `[]` returns `([], True)`.
- A not-found search writes the §3 record and returns `"not_in_usda"`; a second call makes no HTTP request and returns `"not_in_usda"`.
- A failed search writes nothing and returns `"lookup_failed"`; a second call requests USDA again.
- A not-found record with `last_updated` 31 days ago is searched again; one 29 days ago is not.
- A not-found record with an older `match_version` is searched again.
- A changed mapping removes the not-found record and searches the new query.
- A not-found record whose new search finds a match is replaced by the match (one record for the food).
- A stale match whose new search finds nothing is returned as `unverified` and the entry is unchanged.
- A stale not-found record whose new search fails stays unchanged and returns `"not_in_usda"`.
- `enrich_all_foods` puts foods into `not_in_usda` and `lookup_failed`; a recipe with an unavailable ingredient goes to `lookup_failed`.
- A food with both a not-found record and a recipe is built from the recipe.

**`tests/test_analyzer.py`** — counts and food lists for both groups; neither affects coverage.

**`tests/test_reporter.py`** — summary line and both warning lines, terminal and HTML; no line when its list is empty.

**`tests/test_integration.py`, `tests/test_cli.py`, `tests/test_custom_foods*.py`** — fixtures renamed from `unresolved`; otherwise unchanged and passing.

**Skill script** — the `my-skills` repo has no test suite; `recipe_tool.py review` is checked by hand against the real database once a not-found record exists.

**End to end** — `make test` passes; two consecutive real `make analyze` runs show no USDA request for foods cached as not found on the second run.

---

## 10. Out of Scope

- Stale matches whose refresh finds nothing are still retried every run (§4.2); rare, and unchanged from today.
- A query that USDA rejects with a 400 for its own text keeps failing and is retried every run; the log names the query.
- Separating a wrong recipe FDC ID (404) from a network failure in the report.
- Gemini translation caching (already cached once returned).

---

## 11. Documentation

`CLAUDE.md`:
- Enricher description: `EnrichmentResult` has `not_in_usda` and `lookup_failed` instead of `unresolved`.
- Reporter description: the two warning lines.
- Key invariants: not-found records live in the main USDA cache table, are written only for an HTTP 200 search with no hits (never for a failed request), expire after `NOT_FOUND_TTL_DAYS`; older code reading one stops with `KeyError: 'per_100g'`, fixed by running current code.
