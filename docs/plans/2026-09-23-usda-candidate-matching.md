# Macro-Validated USDA Candidate Matching Implementation Plan (BAR-72)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop trusting USDA's first search hit. Choose among ~25 candidates, keeping the food the query names and letting the MacroFactor-logged macros pick its form. Flag weak matches in both reports, and re-pick every cached food once via a `match_version` stamp.

**Architecture:**
- A new pure module, `matcher.py`, owns every matching decision: logged macros, the head-word filter, macro distance, and the identity-weighted pick.
- `enricher.py` cleans queries, gathers candidates from two USDA searches, calls the matcher, and versions its TinyDB cache entries. `enrich_all_foods` gains a `logged_macros` argument and returns a `low_confidence` map.
- `analyzer.py` turns that map into a grams-sorted list plus a weight share, and `reporter.py` prints one warning line from them.
- `cli.py` computes the logged macros and prints a re-match notice.

**Tech Stack:** Python 3.14+, `pytest` + `pytest-mock` (plain `assert`, plain classes), `tinydb`, `requests`, `rich`, `jinja2`, `uv`, `ruff` (line length 120).

**Spec:** `docs/specs/2026-09-23-usda-candidate-matching-design.md` (v1.1). Read it alongside this plan. Every constant, rule and test below comes from it. Its §1 "Validated by Simulation" explains why the scoring is identity-first.

**Branch:** `alozay/bar-72-usda-matching-takes-the-first-search-result-pick-candidates` (already checked out; the spec is committed on it).

## Global Constraints

- Pure-Python dependencies only (the code runs on iPhone in a-Shell): **no new dependencies**.
- Tests never make a real USDA or Gemini request. All HTTP is mocked (`requests.get`, `search_usda` or `get_food_candidates`).
- Constants, exactly: `KCAL_FLOOR = 50.0`, `NEAR_TIE_BAND = 0.05`, `GOOD_DISTANCE = 0.25`, `IDENTITY_WEIGHT = 0.5`, `SEARCH_PAGE_SIZE = 25`, `MATCH_VERSION = 1`.
- `GENERIC_HEADS = {"snack", "beverage", "cereal", "cereal ready-to-eat", "alcoholic beverage", "carbonated beverage", "fish", "crustacean", "nut", "seed", "spice", "sauce", "salad dressing"}`.
- Weak matches are **counted** (they stay in `profiles`) and **flagged**. They are never excluded or rescaled.
- Only the USDA cache (TinyDB default table) is rewritten. The `translations` table and `food_mappings.yaml` handling are untouched.
- No new CLI command or flag.
- `ruff check src tests` must pass after every task.
- Commit messages: one concise line ending in `(BAR-72)`, no body.
- Run commands from the repo root. Test runner: `uv run pytest`.

## Review Focus

These are the inputs most likely to bite a real user that the spec implies but doesn't spell out. Each one has a test pinned in the owning task.

1. **A food or USDA name containing Rich markup such as `[red]`** must print literally in the terminal warning line, not be swallowed as a style tag. Pinned in Task 6 (`test_terminal_names_weak_matches_with_weight_share`, using `Paprika [red]`).
2. **A mapping that is empty after query cleaning** (e.g. `/` or `( )`) must make no HTTP request and leave the food unresolved, not crash or query USDA with an empty string. Pinned in Task 3 (`test_query_empty_after_cleaning_makes_no_request`).
3. **A zero-calorie logged food** (diet cola, black coffee) must not divide by zero. Pinned in Task 2 (`test_zero_calorie_food_does_not_divide_by_zero`).
4. **Re-picking a legacy cache entry** must replace it in place, never leave two entries for one food (lookups read `cached[0]`). Pinned in Task 4 (`test_legacy_entry_is_repicked_and_replaced`).
5. **The network dropping mid-way through the first re-match run** (e.g. on the iPhone) must keep the old match rather than turn the food unresolved, and must leave it unstamped so it is retried. Pinned in Task 4 (`test_failed_repick_keeps_legacy_entry_as_unverified`).

## File Structure

| File | Responsibility after this change |
|---|---|
| `src/omni_pilot/matcher.py` (new) | Every matching decision, as pure functions: logged macros per 100 g, normalisation, head-word filter, macro distance, identity-weighted pick, confidence. No I/O. |
| `src/omni_pilot/enricher.py` | USDA I/O and caching: query cleaning, multi-result search, candidate building, versioned cache entries, `low_confidence` in `EnrichmentResult`. Delegates the choice to `matcher`. |
| `src/omni_pilot/analyzer.py` | Adds `low_confidence_foods` (grams-sorted) and `low_confidence_weight_pct` to `CoverageResult`. Nutrient math is unchanged. |
| `src/omni_pilot/reporter.py` | One "Low-confidence matches" warning line, terminal and HTML. Presentation only. |
| `src/omni_pilot/cli.py` | Computes logged macros, prints the re-match notice, and passes both through. |
| `tests/test_matcher.py` (new) | Pins the matching rules, using real USDA values from the production audit. |
| `tests/test_enricher.py` | Pins search, candidates, cache versioning and the enrichment result shape. |
| `tests/test_analyzer.py`, `tests/test_reporter.py`, `tests/test_cli.py`, `tests/helpers.py` | Coverage fields, the warning line, CLI wiring; shared builders. |
| `CLAUDE.md` | Documents `matcher.py`, the new data flow and the `MATCH_VERSION` invariant. |

---

### Task 1: Matcher — logged macros and head-word filter

**Files:**
- Create: `src/omni_pilot/matcher.py`
- Create: `tests/test_matcher.py`

**Verifies:** spec §3.1, §3.2 and §3.3.

**Interfaces:**
- Consumes: nothing from other tasks. Entries are dicts with `food_name`, `total_weight_g`, `calories_kcal`, `protein_g`, `fat_g`, `carbs_g` (the parser's `FoodEntry`).
- Produces (used by Tasks 2–4):
  - `LoggedMacros` (TypedDict: `kcal`, `protein_g`, `fat_g`, `carbs_g`: all `float`);
  - `Candidate` (TypedDict: `fdc_id: int`, `description: str`, `data_type: str`, `rank: int`, `protein_g`/`fat_g`/`carbs_g`/`fiber_g: float | None`, `raw: dict`);
  - `Pick` (TypedDict: `candidate: Candidate`, `macro_distance: float | None`, `confidence: str`);
  - `logged_macros_per_100g(entries: list[dict]) -> dict[str, LoggedMacros]`;
  - `head_words(query: str) -> tuple[list[str], int]`;
  - `matches_head(description: str, head: list[str], head_segments: int) -> bool`;
  - private `_segments(text: str) -> list[list[str]]`, used again in Task 2;
  - the constants `KCAL_FLOOR`, `NEAR_TIE_BAND`, `GOOD_DISTANCE`, `IDENTITY_WEIGHT`, `GENERIC_HEADS`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_matcher.py`:

```python
"""Matcher tests. Candidate macros are real USDA values seen in the production audit."""
from __future__ import annotations

import pytest

from omni_pilot.matcher import (
    head_words,
    logged_macros_per_100g,
    matches_head,
)


def _candidate(
    description: str,
    protein: float | None,
    fat: float | None,
    carbs: float | None,
    fiber: float | None = None,
    *,
    data_type: str = "SR Legacy",
    rank: int = 0,
) -> dict:
    return {
        "fdc_id": 1000 + rank,
        "description": description,
        "data_type": data_type,
        "rank": rank,
        "protein_g": protein,
        "fat_g": fat,
        "carbs_g": carbs,
        "fiber_g": fiber,
        "raw": {"description": description},
    }


def _logged(kcal: float, protein: float, fat: float, carbs: float) -> dict:
    return {"kcal": kcal, "protein_g": protein, "fat_g": fat, "carbs_g": carbs}


def _entry(food_name: str, grams: float, kcal: float, protein: float, fat: float, carbs: float) -> dict:
    return {
        "food_name": food_name, "total_weight_g": grams, "calories_kcal": kcal,
        "protein_g": protein, "fat_g": fat, "carbs_g": carbs,
    }


class TestLoggedMacrosPer100g:
    def test_weights_entries_by_grams(self):
        entries = [
            _entry("Reis", 100.0, 130.0, 3.0, 0.0, 28.0),
            _entry("Reis", 300.0, 450.0, 9.0, 3.0, 90.0),
        ]
        macros = logged_macros_per_100g(entries)["Reis"]
        assert macros["kcal"] == pytest.approx(145.0)
        assert macros["protein_g"] == pytest.approx(3.0)
        assert macros["fat_g"] == pytest.approx(0.75)
        assert macros["carbs_g"] == pytest.approx(29.5)

    def test_ignores_entries_without_weight(self):
        entries = [
            _entry("Reis", 0.0, 500.0, 50.0, 50.0, 50.0),
            _entry("Reis", 100.0, 130.0, 3.0, 0.0, 28.0),
        ]
        assert logged_macros_per_100g(entries)["Reis"]["kcal"] == pytest.approx(130.0)

    def test_food_with_no_weight_is_absent(self):
        entries = [_entry("Quick Add", 0.0, 500.0, 0.0, 0.0, 0.0)]
        assert logged_macros_per_100g(entries) == {}


class TestHeadWords:
    def test_first_segment_names_the_food(self):
        assert head_words("chickpeas, mature seeds, cooked, boiled") == (["chickpea"], 1)

    def test_parentheses_are_dropped_and_plurals_singularised(self):
        assert head_words("tomatoes (roma), raw") == (["tomato"], 1)
        assert head_words("berries, mixed, frozen") == (["berry"], 1)

    def test_word_ending_in_double_s_is_not_singularised(self):
        assert head_words("glass noodles, cooked") == (["glass", "noodle"], 1)

    def test_generic_first_segment_takes_the_second_too(self):
        assert head_words("fish, salmon, atlantic, raw") == (["fish", "salmon"], 2)
        assert head_words("carbonated beverage, cola") == (["carbonated", "beverage", "cola"], 2)

    def test_empty_query_has_no_head(self):
        assert head_words("") == ([], 0)


class TestMatchesHead:
    def test_rejects_a_different_food_with_the_same_words_later(self):
        head, segments = head_words("chickpeas, mature seeds, cooked, boiled, without salt")
        assert not matches_head("Lentils, mature seeds, cooked, boiled, without salt", head, segments)
        assert matches_head(
            "Chickpeas (garbanzo beans, bengal gram), mature seeds, canned, drained solids", head, segments
        )

    def test_rejects_food_that_only_mentions_the_head_in_passing(self):
        head, segments = head_words("beef, ground, 80% lean meat / 20% fat, raw")
        assert not matches_head("Lebanon bologna, beef", head, segments)
        assert matches_head("Beef, ground, 80% lean meat / 20% fat, raw", head, segments)

    def test_rejects_brand_prefixed_dish(self):
        head, segments = head_words("rice, white, long-grain, regular, raw")
        assert not matches_head("ON THE BORDER, Mexican rice", head, segments)

    def test_generic_candidate_prefix_counts_as_part_of_the_name(self):
        head, segments = head_words("tea, iced, sweetened with sugar")
        assert matches_head("Beverages, tea, instant, lemon, sweetened, prepared with water", head, segments)
        assert not matches_head("Sweeteners, for baking, contains sugar and sucralose", head, segments)

    def test_generic_query_head_matches_reordered_usda_name(self):
        head, segments = head_words("carbonated beverage, cola")
        assert matches_head("Beverages, carbonated, cola, regular", head, segments)

    def test_empty_foundation_segment_is_ignored(self):
        head, segments = head_words("chickpeas, dry")
        assert matches_head("Chickpeas, (garbanzo beans, bengal gram), dry", head, segments)
```

`_candidate` and `_logged` are unused until Task 2. They are defined now so Task 2 only appends test classes.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_matcher.py -v`
Expected: collection error, `ModuleNotFoundError: No module named 'omni_pilot.matcher'`.

- [ ] **Step 3: Implement**

Create `src/omni_pilot/matcher.py`:

```python
"""Choose the USDA search hit that matches what was actually eaten.

USDA search ranks by keywords, so its first hit is often the wrong food or the
wrong form of the right food. MacroFactor logs protein, fat and carbs for every
entry, and those logged macros are the only ground truth for what a food really
was. The matcher first keeps candidates that are the food the query names, then
lets the logged macros decide between its forms (raw vs cooked, fat level,
canned vs boiled) without letting them swap in a different food.
"""
from __future__ import annotations

import re
from collections import defaultdict
from typing import TypedDict

# Distances are divided by at least this many kcal, so tiny absolute
# differences in very low-calorie foods (tomatoes, diet drinks) stay small.
KCAL_FLOOR = 50.0
# Candidates scoring within this much of the best count as tied.
NEAR_TIE_BAND = 0.05
# A match whose macro distance is at or below this is "good"; above it, "weak".
GOOD_DISTANCE = 0.25
# Score penalty for a candidate containing none of the query's words. Large
# enough that a different food with closer macros (camembert for feta) loses.
IDENTITY_WEIGHT = 0.5
# First segments too broad to identify a food on their own ("Snacks, ...",
# "Beverages, ..."); the second segment is read as part of the name.
GENERIC_HEADS = {
    "snack", "beverage", "cereal", "cereal ready-to-eat", "alcoholic beverage",
    "carbonated beverage", "fish", "crustacean", "nut", "seed", "spice",
    "sauce", "salad dressing",
}

_STOP_WORDS = {"and", "or", "with", "in", "of"}
_WORD_RE = re.compile(r"[a-z0-9-]+")
_PARENS_RE = re.compile(r"\([^)]*\)")


class LoggedMacros(TypedDict):
    """Macros per 100 g, as logged in MacroFactor."""

    kcal: float
    protein_g: float
    fat_g: float
    carbs_g: float


class Candidate(TypedDict):
    """One USDA search hit, normalised for matching."""

    fdc_id: int
    description: str
    data_type: str
    rank: int
    protein_g: float | None
    fat_g: float | None
    carbs_g: float | None
    fiber_g: float | None
    raw: dict


class Pick(TypedDict):
    candidate: Candidate
    macro_distance: float | None
    confidence: str


def logged_macros_per_100g(entries: list[dict]) -> dict[str, LoggedMacros]:
    """Average each food's logged macros per 100 g, weighted by grams eaten.

    Entries without a positive weight are ignored, so a food with no weight at
    all gets no entry.
    """
    totals: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0, 0.0, 0.0, 0.0])
    for entry in entries:
        grams = entry["total_weight_g"]
        if grams <= 0:
            continue
        food_totals = totals[entry["food_name"]]
        food_totals[0] += grams
        food_totals[1] += entry["calories_kcal"]
        food_totals[2] += entry["protein_g"]
        food_totals[3] += entry["fat_g"]
        food_totals[4] += entry["carbs_g"]

    return {
        food_name: LoggedMacros(
            kcal=100.0 * kcal / grams,
            protein_g=100.0 * protein / grams,
            fat_g=100.0 * fat / grams,
            carbs_g=100.0 * carbs / grams,
        )
        for food_name, (grams, kcal, protein, fat, carbs) in totals.items()
    }


def _singular(word: str) -> str:
    if word.endswith("ies") and len(word) > 4:
        return word[:-3] + "y"
    if word.endswith("oes"):
        return word[:-2]
    if word.endswith("s") and not word.endswith("ss") and len(word) > 3:
        return word[:-1]
    return word


def _segments(text: str) -> list[list[str]]:
    """Split a USDA-style description into comma segments of normalised words.

    Parenthesised text is dropped, and segments left empty by that are removed.
    """
    text = _PARENS_RE.sub("", text.lower())
    segments = []
    for raw_segment in text.split(","):
        words = [_singular(w) for w in _WORD_RE.findall(raw_segment) if w not in _STOP_WORDS]
        if words:
            segments.append(words)
    return segments


def head_words(query: str) -> tuple[list[str], int]:
    """Return the words naming the food, and how many segments they span.

    That is the query's first segment, or its first two when the first is
    generic ("snacks, trail mix").
    """
    segments = _segments(query)
    if not segments:
        return [], 0
    if " ".join(segments[0]) in GENERIC_HEADS and len(segments) > 1:
        return segments[0] + segments[1], 2
    return list(segments[0]), 1


def matches_head(description: str, head: list[str], head_segments: int) -> bool:
    """Whether a candidate is the food the query names.

    At least one head word must name the candidate itself (its first segment,
    widened by one when that segment is generic), and every head word must
    appear within its first head_segments + 1 segments. This rejects hits that
    only mention the food in passing, like "Lebanon bologna, beef" for "beef".
    """
    segments = _segments(description)
    if not segments:
        return False
    name = list(segments[0])
    if " ".join(segments[0]) in GENERIC_HEADS and len(segments) > 1:
        name += segments[1]
    prefix = {w for segment in segments[: head_segments + 1] for w in segment}
    return any(w in name for w in head) and all(w in prefix for w in head)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_matcher.py -v`
Expected: 14 passed.

- [ ] **Step 5: Lint and run the full suite**

Run: `uv run ruff check src tests && uv run pytest -q`
Expected: `All checks passed!`, then all tests pass (94 existing + 14 new = 108).

- [ ] **Step 6: Commit**

```bash
git add src/omni_pilot/matcher.py tests/test_matcher.py
git commit -m "feat: add matcher with logged macros and head-word filter (BAR-72)"
```

---

### Task 2: Matcher — macro distance and identity-weighted pick

**Files:**
- Modify: `src/omni_pilot/matcher.py` (append after `matches_head`)
- Modify: `tests/test_matcher.py` (extend the import, append two classes)

**Verifies:** spec §3.4 and §3.5.

**Interfaces:**
- Consumes (Task 1): `LoggedMacros`, `Candidate`, `Pick`, `_segments`, `head_words`, `matches_head` and the constants.
- Produces (used by Task 4):
  - `macro_distance(logged: LoggedMacros, candidate: Candidate) -> float | None`;
  - `pick_best(candidates: list[Candidate], query: str, logged: LoggedMacros | None) -> Pick | None`. `Pick["confidence"]` is exactly one of `"good"`, `"weak"` or `"no_macros"`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_matcher.py`, replace the import block with:

```python
from omni_pilot.matcher import (
    GOOD_DISTANCE,
    head_words,
    logged_macros_per_100g,
    macro_distance,
    matches_head,
    pick_best,
)
```

Append to the end of `tests/test_matcher.py`:

```python
class TestMacroDistance:
    def test_distance_is_calorie_weighted_and_relative_to_logged_kcal(self):
        logged = _logged(200.0, 10.0, 10.0, 10.0)
        candidate = _candidate("X", 11.0, 9.0, 12.0)
        # (4*1 + 9*1 + 4*2) / 200
        assert macro_distance(logged, candidate) == pytest.approx(21.0 / 200.0)

    def test_uses_fiber_free_carbs_when_closer(self):
        # EU labels exclude fiber: logged 16.1 g matches USDA's 22.5 g minus 6.4 g fiber.
        logged = _logged(120.0, 7.05, 2.77, 16.1)
        candidate = _candidate("Chickpeas, canned", 7.05, 2.77, 22.5, fiber=6.4)
        assert macro_distance(logged, candidate) == pytest.approx(0.0)

    def test_low_calorie_food_uses_kcal_floor(self):
        logged = _logged(13.0, 1.0, 0.0, 2.0)
        candidate = _candidate("Tomatoes, raw", 1.0, 0.0, 4.0)
        assert macro_distance(logged, candidate) == pytest.approx(8.0 / 50.0)

    def test_zero_calorie_food_does_not_divide_by_zero(self):
        logged = _logged(0.0, 0.0, 0.0, 0.0)
        candidate = _candidate("Beverages, carbonated, cola, without caffeine", 0.0, 0.0, 1.0)
        assert macro_distance(logged, candidate) == pytest.approx(4.0 / 50.0)

    def test_none_when_candidate_lacks_a_macro(self):
        logged = _logged(100.0, 10.0, 5.0, 5.0)
        assert macro_distance(logged, _candidate("X", 10.0, None, 5.0)) is None


class TestPickBest:
    def test_named_food_beats_different_food_with_closer_macros(self):
        # "Feta (Schaf- & Ziegenmilch)": camembert's macros are closer, but it is not feta.
        candidates = [
            _candidate("Cheese, camembert", 19.8, 24.3, 0.46, 0.0, rank=0),
            _candidate("Cheese, feta", 14.2, 21.5, 3.88, 0.0, rank=1),
        ]
        pick = pick_best(candidates, "cheese, feta", _logged(288.2, 18.6, 24.5, 0.0))
        assert pick["candidate"]["description"] == "Cheese, feta"
        assert pick["confidence"] == "good"

    def test_macros_switch_raw_to_cooked(self):
        # "Reis XXL" is logged by cooked weight although the query says raw.
        candidates = [
            _candidate("Rice, white, long-grain, regular, raw, enriched", 7.13, 0.66, 80.0, 1.3, rank=0),
            _candidate("Rice, white, long-grain, regular, enriched, cooked", 2.69, 0.28, 28.2, 0.4, rank=1),
        ]
        pick = pick_best(candidates, "rice, white, long-grain, regular, raw", _logged(141.1, 3.3, 1.3, 28.9))
        assert pick["candidate"]["description"] == "Rice, white, long-grain, regular, enriched, cooked"
        assert pick["confidence"] == "good"
        assert pick["macro_distance"] == pytest.approx(0.1022, abs=1e-3)

    def test_lentils_never_chosen_for_chickpeas(self):
        candidates = [
            _candidate("Lentils, mature seeds, cooked, boiled, without salt", 9.02, 0.38, 20.1, 7.9, rank=0),
            _candidate(
                "Chickpeas (garbanzo beans, bengal gram), mature seeds, cooked, boiled, without salt",
                8.86, 2.59, 27.4, 7.6, rank=1,
            ),
        ]
        pick = pick_best(
            candidates, "chickpeas, mature seeds, cooked, boiled, without salt", _logged(120.0, 6.5, 2.3, 14.0)
        )
        assert pick["candidate"]["description"].startswith("Chickpeas")

    def test_named_colour_beats_closer_other_colour(self):
        # "Paprika salat": green pepper is closer on macros, red is what was eaten.
        candidates = [
            _candidate("Peppers, sweet, green, raw", 0.86, 0.17, 4.64, 1.7, rank=0),
            _candidate("Peppers, sweet, red, raw", 0.99, 0.3, 6.03, 2.1, rank=1),
        ]
        pick = pick_best(candidates, "peppers, sweet, red, raw", _logged(31.1, 1.2, 1.4, 2.9))
        assert pick["candidate"]["description"] == "Peppers, sweet, red, raw"
        # 0.297 > GOOD_DISTANCE: the salad's oil makes it an imperfect match, so it is flagged.
        assert pick["confidence"] == "weak"

    def test_query_words_break_near_ties(self):
        # "Hähnchen Brustfilet": the breast entry, not generic or light meat.
        candidates = [
            _candidate("Chicken, broilers or fryers, meat only, raw", 21.4, 3.08, 0.0, 0.0, rank=0),
            _candidate(
                "Chicken, broiler or fryers, breast, skinless, boneless, meat only, raw", 22.5, 2.62, 0.0, 0.0, rank=1
            ),
            _candidate("Chicken, broilers or fryers, light meat, meat only, raw", 23.2, 1.65, 0.0, 0.0, rank=2),
        ]
        pick = pick_best(
            candidates, "chicken, broilers or fryers, breast, meat only, raw", _logged(104.1, 22.0, 1.7, 0.0)
        )
        assert "breast" in pick["candidate"]["description"]

    def test_sr_legacy_wins_a_near_tie_over_foundation(self):
        candidates = [
            _candidate("Beef, top sirloin steak, raw", 22.0, 3.0, 0.0, data_type="Foundation", rank=0),
            _candidate("Beef, top sirloin steak, lean, raw", 22.0, 3.2, 0.0, data_type="SR Legacy", rank=1),
        ]
        pick = pick_best(candidates, "beef, top sirloin steak, raw", _logged(110.0, 22.0, 3.0, 0.0))
        assert pick["candidate"]["data_type"] == "SR Legacy"

    def test_no_candidate_matching_head_falls_back_as_weak(self):
        candidates = [_candidate("Fish, tuna salad", 16.0, 9.3, 9.4, rank=0)]
        pick = pick_best(candidates, "caprese salad", _logged(74.0, 5.0, 4.0, 3.0))
        assert pick["candidate"]["description"] == "Fish, tuna salad"
        assert pick["confidence"] == "weak"

    def test_fallback_is_weak_even_when_macros_are_close(self):
        candidates = [_candidate("Fish, tuna salad", 5.0, 4.0, 3.0, rank=0)]
        pick = pick_best(candidates, "caprese salad", _logged(74.0, 5.0, 4.0, 3.0))
        assert pick["macro_distance"] <= GOOD_DISTANCE
        assert pick["confidence"] == "weak"

    def test_without_logged_macros_takes_first_matching_candidate(self):
        candidates = [
            _candidate("Lentils, raw", 24.6, 1.1, 63.4, rank=0),
            _candidate("Chickpeas, raw", 20.5, 6.0, 63.0, rank=1),
        ]
        pick = pick_best(candidates, "chickpeas, raw", None)
        assert pick["candidate"]["description"] == "Chickpeas, raw"
        assert pick["confidence"] == "no_macros"
        assert pick["macro_distance"] is None

    def test_no_scorable_candidate_is_weak_without_distance(self):
        candidates = [_candidate("Ketchup, restaurant", None, None, None, rank=0)]
        pick = pick_best(candidates, "ketchup", _logged(100.0, 0.0, 0.0, 21.0))
        assert pick["candidate"]["description"] == "Ketchup, restaurant"
        assert pick["confidence"] == "weak"
        assert pick["macro_distance"] is None

    def test_no_candidates_returns_none(self):
        assert pick_best([], "anything", _logged(100.0, 1.0, 1.0, 1.0)) is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_matcher.py -v`
Expected: collection error, `ImportError: cannot import name 'macro_distance'`.

- [ ] **Step 3: Implement**

Append to `src/omni_pilot/matcher.py`:

```python
def _word_overlap(query_words: set[str], description: str) -> float:
    if not query_words:
        return 0.0
    description_words = {w for segment in _segments(description) for w in segment}
    return len(query_words & description_words) / len(query_words)


def macro_distance(logged: LoggedMacros, candidate: Candidate) -> float | None:
    """How far a candidate's macros are from the logged ones, relative to kcal.

    EU labels count carbs without fiber while USDA counts them with it, so the
    closer of the two readings is used. None when the candidate lacks a macro.
    """
    protein, fat, carbs = candidate["protein_g"], candidate["fat_g"], candidate["carbs_g"]
    if protein is None or fat is None or carbs is None:
        return None
    carbs_delta = abs(logged["carbs_g"] - carbs)
    if candidate["fiber_g"] is not None:
        carbs_delta = min(carbs_delta, abs(logged["carbs_g"] - (carbs - candidate["fiber_g"])))
    weighted = (
        4 * abs(logged["protein_g"] - protein)
        + 9 * abs(logged["fat_g"] - fat)
        + 4 * carbs_delta
    )
    return weighted / max(logged["kcal"], KCAL_FLOOR)


def pick_best(
    candidates: list[Candidate],
    query: str,
    logged: LoggedMacros | None,
) -> Pick | None:
    """Pick the candidate that is the named food and best fits the logged macros."""
    if not candidates:
        return None

    head, head_segments = head_words(query)
    pool = [c for c in candidates if matches_head(c["description"], head, head_segments)]
    no_head_match = not pool
    if no_head_match:
        pool = list(candidates)
    pool.sort(key=lambda c: c["rank"])

    if logged is None:
        return Pick(candidate=pool[0], macro_distance=None, confidence="no_macros")

    query_words = {w for segment in _segments(query) for w in segment}
    scored = []
    for candidate in pool:
        distance = macro_distance(logged, candidate)
        if distance is None:
            continue
        score = distance + IDENTITY_WEIGHT * (1.0 - _word_overlap(query_words, candidate["description"]))
        scored.append((candidate, distance, score))
    if not scored:
        return Pick(candidate=pool[0], macro_distance=None, confidence="weak")

    best_score = min(score for _, _, score in scored)
    tied = [s for s in scored if s[2] <= best_score + NEAR_TIE_BAND]
    # SR Legacy carries far more micronutrients than Foundation, so it wins ties.
    chosen, distance, _ = min(
        tied, key=lambda s: (s[0]["data_type"] != "SR Legacy", s[2], s[0]["rank"])
    )
    confidence = "good" if distance <= GOOD_DISTANCE and not no_head_match else "weak"
    return Pick(candidate=chosen, macro_distance=distance, confidence=confidence)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_matcher.py -v`
Expected: 30 passed.

- [ ] **Step 5: Lint and run the full suite**

Run: `uv run ruff check src tests && uv run pytest -q`
Expected: `All checks passed!`, then 124 passed.

- [ ] **Step 6: Commit**

```bash
git add src/omni_pilot/matcher.py tests/test_matcher.py
git commit -m "feat: pick USDA candidates by identity-weighted macro distance (BAR-72)"
```

---

### Task 3: Enricher — query cleaning and multi-result candidate search

**Files:**
- Modify: `src/omni_pilot/enricher.py`: imports, new constants, replace `search_usda`, add `clean_query`, `_nutrient_value`, `get_food_candidates`, and adapt 3 lines in `get_food_micros`
- Modify: `tests/test_enricher.py`

**Verifies:** spec §4.1.

**Interfaces:**
- Consumes (Tasks 1–2): `matcher.head_words`, `matcher.Candidate`.
- Produces (used by Task 4):
  - `SEARCH_PAGE_SIZE = 25`;
  - `clean_query(query: str) -> str`;
  - `search_usda(query: str, api_key: str) -> list[dict]`. It **changes** from returning `dict | None` to a list, and is `[]` on no results, request failure, or a query that is empty after cleaning;
  - `get_food_candidates(query: str, api_key: str) -> list[Candidate]`.
- **Transitional:** `get_food_micros` keeps its signature and simply takes `search_usda(...)[0]`, so the pipeline keeps working between tasks. Task 4 deletes it.

- [ ] **Step 1: Write the failing tests**

In `tests/test_enricher.py`, replace everything above `class TestUsdaNutrientMap:` with:

```python
from __future__ import annotations

import pytest
import requests
from tinydb import TinyDB

from omni_pilot.enricher import (
    SEARCH_PAGE_SIZE,
    USDA_NUTRIENT_MAP,
    clean_query,
    enrich_all_foods,
    extract_micros_from_usda,
    get_food_candidates,
    get_food_micros,
    search_usda,
)


def _usda_hit(
    fdc_id: int,
    description: str,
    protein: float = 10.0,
    fat: float = 5.0,
    carbs: float = 20.0,
    *,
    data_type: str = "SR Legacy",
    vitamin_a: float = 149.0,
) -> dict:
    return {
        "fdcId": fdc_id,
        "description": description,
        "dataType": data_type,
        "foodNutrients": [
            {"nutrientNumber": "203", "value": protein},
            {"nutrientNumber": "204", "value": fat},
            {"nutrientNumber": "205", "value": carbs},
            {"nutrientNumber": "320", "value": vitamin_a},
        ],
    }


```

Insert these classes immediately above `class TestGetFoodMicros:`:

```python
class TestSearchUsda:
    def test_cleans_query_and_requests_a_full_page(self, mocker):
        mock_get = mocker.patch("omni_pilot.enricher.requests.get")
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = {"foods": [{"fdcId": 1}]}

        foods = search_usda("beef, ground, 80% lean meat / 20% fat, raw", "fake-key")

        params = mock_get.call_args.kwargs["params"]
        assert params["query"] == "beef, ground, 80% lean meat 20% fat, raw"
        assert params["pageSize"] == SEARCH_PAGE_SIZE
        assert foods == [{"fdcId": 1}]

    def test_request_failure_returns_empty_list(self, mocker):
        mocker.patch(
            "omni_pilot.enricher.requests.get", side_effect=requests.ConnectionError("offline")
        )
        assert search_usda("honey", "fake-key") == []

    def test_query_empty_after_cleaning_makes_no_request(self, mocker):
        mock_get = mocker.patch("omni_pilot.enricher.requests.get")
        assert search_usda("( / )", "fake-key") == []
        mock_get.assert_not_called()


class TestCleanQuery:
    @pytest.mark.parametrize(("query", "expected"), [
        ("olives, ripe, canned (small-extra large)", "olives, ripe, canned small-extra large"),
        ("beef, ground, 80% lean meat / 20% fat, raw", "beef, ground, 80% lean meat 20% fat, raw"),
        ("honey", "honey"),
    ])
    def test_strips_characters_usda_rejects(self, query, expected):
        assert clean_query(query) == expected


class TestGetFoodCandidates:
    def test_query_hits_rank_before_head_word_hits_and_duplicates_drop(self, mocker):
        by_query = {
            "chickpeas, cooked, boiled": [_usda_hit(1, "Lentils, cooked"), _usda_hit(2, "Chickpeas, cooked")],
            "chickpea": [_usda_hit(2, "Chickpeas, cooked"), _usda_hit(3, "Chickpeas, canned")],
        }
        mocker.patch("omni_pilot.enricher.search_usda", side_effect=lambda q, key: by_query[q])

        candidates = get_food_candidates("chickpeas, cooked, boiled", "fake-key")

        assert [c["fdc_id"] for c in candidates] == [1, 2, 3]
        assert [c["rank"] for c in candidates] == [0, 1, 2]
        assert candidates[0]["protein_g"] == 10.0
        assert candidates[0]["fiber_g"] is None

    def test_single_word_query_is_searched_once(self, mocker):
        mock_search = mocker.patch("omni_pilot.enricher.search_usda", return_value=[_usda_hit(1, "Honey")])
        get_food_candidates("Honey", "fake-key")
        mock_search.assert_called_once_with("Honey", "fake-key")

    def test_failed_head_word_search_keeps_query_hits(self, mocker):
        by_query = {"chickpeas, canned": [_usda_hit(3, "Chickpeas, canned")], "chickpea": []}
        mocker.patch("omni_pilot.enricher.search_usda", side_effect=lambda q, key: by_query[q])
        assert [c["fdc_id"] for c in get_food_candidates("chickpeas, canned", "fake-key")] == [3]

    def test_failed_query_search_returns_nothing(self, mocker):
        mock_search = mocker.patch("omni_pilot.enricher.search_usda", return_value=[])
        assert get_food_candidates("chickpeas, canned", "fake-key") == []
        mock_search.assert_called_once()


```

Then update the existing tests for `search_usda`'s new list return:
- In `TestGetFoodMicros.test_queries_usda_when_not_cached`, wrap the `mock_search.return_value = {...}` dict in a list: `mock_search.return_value = [{...}]`.
- In `TestGetFoodMicros.test_returns_none_when_usda_has_no_results`, change `mock_search.return_value = None` to `mock_search.return_value = []`.
- In `TestEnrichAllFoods.test_failed_usda_lookup_is_unresolved_not_skipped` and `test_profiles_never_contain_none`, change `mocker.patch("omni_pilot.enricher.search_usda", return_value=None)` to `return_value=[]`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_enricher.py -v`
Expected: collection error, `ImportError: cannot import name 'SEARCH_PAGE_SIZE'`.

- [ ] **Step 3: Implement**

In `src/omni_pilot/enricher.py`, replace everything above `class EnrichmentResult` (module docstring, imports and the `logger` line) with:

```python
"""USDA FoodData Central enrichment with TinyDB caching."""
from __future__ import annotations

import logging
import re
import time
from datetime import date
from typing import TypedDict

import requests
from tinydb import Query, TinyDB

from omni_pilot import matcher
from omni_pilot.matcher import Candidate

logger = logging.getLogger(__name__)

SEARCH_PAGE_SIZE = 25
# USDA's search endpoint rejects "/" and intermittently rejects brackets (HTTP 400).
_UNSAFE_QUERY_CHARS = re.compile(r"[()\[\]{}/\\]")
```

Replace the whole `search_usda` function with:

```python
def clean_query(query: str) -> str:
    """Strip characters USDA's search endpoint rejects, collapsing whitespace."""
    return " ".join(_UNSAFE_QUERY_CHARS.sub(" ", query).split())


def search_usda(query: str, api_key: str) -> list[dict]:
    """Search USDA FoodData Central (SR Legacy and Foundation datasets).

    Returns up to SEARCH_PAGE_SIZE foods in USDA's ranking order, or an empty
    list when there are no results or the request fails.
    """
    cleaned = clean_query(query)
    if not cleaned:
        logger.warning("Empty USDA query after cleaning: '%s'", query)
        return []

    params = {
        "api_key": api_key,
        "query": cleaned,
        "dataType": "SR Legacy,Foundation",
        "pageSize": SEARCH_PAGE_SIZE,
    }

    try:
        resp = requests.get(USDA_SEARCH_URL, params=params, timeout=30)
        if resp.status_code == 429:
            logger.warning("USDA API rate limit hit, waiting 5 seconds...")
            time.sleep(5)
            resp = requests.get(USDA_SEARCH_URL, params=params, timeout=30)
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.error("USDA API request failed for '%s': %s", cleaned, e)
        return []

    foods = resp.json().get("foods", [])
    if not foods:
        logger.warning("No USDA results for query: '%s'", cleaned)
    return foods


def _nutrient_value(usda_food: dict, number: str) -> float | None:
    for fn in usda_food.get("foodNutrients", []):
        if str(fn.get("nutrientNumber", "")) == number and fn.get("value") is not None:
            return float(fn["value"])
    return None


def get_food_candidates(query: str, api_key: str) -> list[Candidate]:
    """Collect USDA candidates for a query, ranked, without duplicates.

    Hits for the query itself come first, then hits for its head words alone
    (e.g. "chickpea"), which surface forms the full query ranks out of view.
    """
    hits = search_usda(query, api_key)
    if not hits:
        return []
    head, _ = matcher.head_words(query)
    head_query = " ".join(head)
    if head_query and head_query != clean_query(query).lower():
        hits = hits + search_usda(head_query, api_key)

    candidates: list[Candidate] = []
    seen_ids: set = set()
    for hit in hits:
        fdc_id = hit.get("fdcId")
        if fdc_id in seen_ids:
            continue
        seen_ids.add(fdc_id)
        candidates.append(Candidate(
            fdc_id=fdc_id,
            description=hit.get("description", ""),
            data_type=hit.get("dataType", ""),
            rank=len(candidates),
            protein_g=_nutrient_value(hit, "203"),
            fat_g=_nutrient_value(hit, "204"),
            carbs_g=_nutrient_value(hit, "205"),
            fiber_g=_nutrient_value(hit, "291"),
            raw=hit,
        ))
    return candidates
```

In `get_food_micros`, replace:

```python
    # Query USDA
    usda_food = search_usda(usda_query, api_key)
    if usda_food is None:
        return None
```

with:

```python
    # Query USDA
    hits = search_usda(usda_query, api_key)
    if not hits:
        return None
    usda_food = hits[0]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_enricher.py -v`
Expected: 22 passed (the 12 existing tests plus 10 new).

- [ ] **Step 5: Lint and run the full suite**

Run: `uv run ruff check src tests && uv run pytest -q`
Expected: `All checks passed!`, then 134 passed.

- [ ] **Step 6: Commit**

```bash
git add src/omni_pilot/enricher.py tests/test_enricher.py
git commit -m "feat: clean USDA queries and gather ranked search candidates (BAR-72)"
```

---

### Task 4: Enricher — versioned cache, macro-validated matches, CLI wiring

**Files:**
- Modify: `src/omni_pilot/enricher.py`: new constant and types; replace `get_food_micros` with `get_food_match`; add `count_outdated_matches`; replace `enrich_all_foods`
- Modify: `src/omni_pilot/cli.py`: imports and the enrichment call
- Modify: `tests/helpers.py`
- Modify: `tests/test_enricher.py`, `tests/test_cli.py`

**Verifies:** spec §4.2–§4.4, §6 and §7.

**Interfaces:**
- Consumes: `matcher.pick_best`, `matcher.LoggedMacros`, `matcher.logged_macros_per_100g` (Tasks 1–2); `get_food_candidates`, `extract_micros_from_usda` (Task 3).
- Produces (used by Tasks 5–6):
  - `MATCH_VERSION = 1`;
  - `LowConfidenceMatch` (TypedDict: `usda_name: str`, `macro_distance: float | None`);
  - `EnrichmentResult` gains `low_confidence: dict[str, LowConfidenceMatch]`;
  - `FoodMatch` (TypedDict: `per_100g`, `usda_name`, `confidence`, `macro_distance`);
  - `get_food_match(food_name, usda_query, logged, db, api_key) -> FoodMatch | None`;
  - `count_outdated_matches(food_names, mappings, db) -> int`;
  - `enrich_all_foods(food_names, mappings, logged_macros, db, api_key) -> EnrichmentResult`. The **new third positional argument** is `logged_macros`;
  - test helpers `enrichment(..., low_confidence=None)` and `food_entry(food_name, total_weight_g=100.0, date="2026-08-01", *, calories_kcal=100.0, protein_g=10.0, fat_g=5.0, carbs_g=15.0)`.

- [ ] **Step 1: Update the test helpers**

In `tests/helpers.py`:
- change the import to `from omni_pilot.enricher import EnrichmentResult, LowConfidenceMatch`;
- add the keyword parameter `low_confidence: dict[str, LowConfidenceMatch] | None = None,` after `unresolved`;
- add `"low_confidence": dict(low_confidence or {}),` to the returned dict.

The file then reads:

```python
"""Shared builders for tests."""
from __future__ import annotations

from collections.abc import Iterable

from omni_pilot.enricher import EnrichmentResult, LowConfidenceMatch


def enrichment(
    profiles: dict[str, dict[str, float | None]] | None = None,
    *,
    skipped: Iterable[str] = (),
    unresolved: Iterable[str] = (),
    low_confidence: dict[str, LowConfidenceMatch] | None = None,
) -> EnrichmentResult:
    """Build an EnrichmentResult, defaulting the parts a test doesn't care about."""
    return {
        "profiles": dict(profiles or {}),
        "skipped": set(skipped),
        "unresolved": set(unresolved),
        "low_confidence": dict(low_confidence or {}),
    }


def food_entry(
    food_name: str,
    total_weight_g: float = 100.0,
    date: str = "2026-08-01",
    *,
    calories_kcal: float = 100.0,
    protein_g: float = 10.0,
    fat_g: float = 5.0,
    carbs_g: float = 15.0,
) -> dict:
    """Build a parsed food-log entry with the fields the pipeline reads."""
    return {
        "date": date,
        "food_name": food_name,
        "total_weight_g": total_weight_g,
        "calories_kcal": calories_kcal,
        "protein_g": protein_g,
        "fat_g": fat_g,
        "carbs_g": carbs_g,
    }
```

(`LowConfidenceMatch` doesn't exist yet, so this import fails until Step 4. That is expected.)

- [ ] **Step 2: Write the failing enricher tests**

In `tests/test_enricher.py`, replace the `from omni_pilot.enricher import (...)` block with:

```python
from omni_pilot.enricher import (
    MATCH_VERSION,
    SEARCH_PAGE_SIZE,
    USDA_NUTRIENT_MAP,
    clean_query,
    count_outdated_matches,
    enrich_all_foods,
    extract_micros_from_usda,
    get_food_candidates,
    get_food_match,
    search_usda,
)
```

Directly after the `_usda_hit` function, add:

```python
def _candidate(fdc_id: int, description: str, protein: float, fat: float, carbs: float, rank: int = 0) -> dict:
    """A matcher Candidate, as get_food_candidates would build it."""
    return {
        "fdc_id": fdc_id, "description": description, "data_type": "SR Legacy", "rank": rank,
        "protein_g": protein, "fat_g": fat, "carbs_g": carbs, "fiber_g": None,
        "raw": _usda_hit(fdc_id, description, protein, fat, carbs),
    }


EGG_LOGGED = {"kcal": 155.0, "protein_g": 12.6, "fat_g": 10.6, "carbs_g": 1.1}
```

Replace the whole of `class TestGetFoodMicros:` and `class TestEnrichAllFoods:` (from `class TestGetFoodMicros:` to the end of the file) with:

```python
class TestGetFoodMatch:
    def _legacy_entry(self) -> dict:
        """A cache entry written before macro matching existed."""
        return {
            "original_name": "Boiled Eggs",
            "usda_query": "egg, whole, cooked, hard-boiled",
            "usda_name": "Egg, whole, raw",
            "per_100g": {"vitamin_a_mcg": 1.0},
            "confidence": "mapped",
        }

    def test_current_cache_entry_is_used_without_searching(self, mocker, tmp_path):
        db = TinyDB(str(tmp_path / "test_db.json"))
        db.insert({
            **self._legacy_entry(), "usda_name": "Egg, whole, cooked, hard-boiled",
            "per_100g": {"vitamin_a_mcg": 149.0}, "confidence": "good",
            "match_version": MATCH_VERSION, "macro_distance": 0.01,
        })
        mock_candidates = mocker.patch("omni_pilot.enricher.get_food_candidates")

        match = get_food_match("Boiled Eggs", "egg, whole, cooked, hard-boiled", EGG_LOGGED, db, "fake-key")

        assert match["per_100g"] == {"vitamin_a_mcg": 149.0}
        assert match["confidence"] == "good"
        mock_candidates.assert_not_called()

    def test_picks_by_macros_and_caches_the_match(self, mocker, tmp_path):
        db = TinyDB(str(tmp_path / "test_db.json"))
        mocker.patch("omni_pilot.enricher.get_food_candidates", return_value=[
            _candidate(1, "Egg, whole, raw, frozen", 12.3, 9.5, 0.8, rank=0),
            _candidate(2, "Egg, whole, cooked, hard-boiled", 12.6, 10.6, 1.1, rank=1),
        ])

        match = get_food_match("Boiled Eggs", "egg, whole, cooked, hard-boiled", EGG_LOGGED, db, "fake-key")

        assert match["usda_name"] == "Egg, whole, cooked, hard-boiled"
        assert match["confidence"] == "good"
        assert match["per_100g"]["vitamin_a_mcg"] == 149.0
        [entry] = db.all()
        assert entry["usda_fdc_id"] == 2
        assert entry["match_version"] == MATCH_VERSION
        assert entry["macro_distance"] == pytest.approx(0.0)
        assert entry["usda_macros"] == {"protein_g": 12.6, "fat_g": 10.6, "carbs_g": 1.1, "fiber_g": None}

    def test_legacy_entry_is_repicked_and_replaced(self, mocker, tmp_path):
        db = TinyDB(str(tmp_path / "test_db.json"))
        db.insert(self._legacy_entry())
        mocker.patch("omni_pilot.enricher.get_food_candidates", return_value=[
            _candidate(2, "Egg, whole, cooked, hard-boiled", 12.6, 10.6, 1.1),
        ])

        match = get_food_match("Boiled Eggs", "egg, whole, cooked, hard-boiled", EGG_LOGGED, db, "fake-key")

        assert match["usda_name"] == "Egg, whole, cooked, hard-boiled"
        # Replaced in place: a second entry would shadow or be shadowed by cached[0].
        [entry] = db.all()
        assert entry["match_version"] == MATCH_VERSION
        assert entry["usda_name"] == "Egg, whole, cooked, hard-boiled"

    def test_failed_repick_keeps_legacy_entry_as_unverified(self, mocker, tmp_path):
        db = TinyDB(str(tmp_path / "test_db.json"))
        db.insert(self._legacy_entry())
        mocker.patch("omni_pilot.enricher.get_food_candidates", return_value=[])

        match = get_food_match("Boiled Eggs", "egg, whole, cooked, hard-boiled", EGG_LOGGED, db, "fake-key")

        assert match["per_100g"] == {"vitamin_a_mcg": 1.0}
        assert match["confidence"] == "unverified"
        # Not stamped: the next run tries again.
        [entry] = db.all()
        assert "match_version" not in entry

    def test_changed_query_refetches(self, mocker, tmp_path):
        db = TinyDB(str(tmp_path / "test_db.json"))
        db.insert(self._legacy_entry())
        mocker.patch("omni_pilot.enricher.get_food_candidates", return_value=[])

        match = get_food_match("Boiled Eggs", "egg, whole, raw", EGG_LOGGED, db, "fake-key")

        # The old entry belonged to a different query, so it is not kept.
        assert match is None
        assert db.all() == []

    def test_returns_none_when_usda_has_no_results(self, mocker, tmp_path):
        db = TinyDB(str(tmp_path / "test_db.json"))
        mocker.patch("omni_pilot.enricher.get_food_candidates", return_value=[])
        assert get_food_match("Unknown Food", "unknown food", None, db, "fake-key") is None


class TestCountOutdatedMatches:
    def test_counts_only_same_query_entries_from_older_versions(self, tmp_path):
        db = TinyDB(str(tmp_path / "test_db.json"))
        db.insert({"original_name": "Legacy", "usda_query": "egg", "per_100g": {}})
        db.insert({"original_name": "Current", "usda_query": "milk", "per_100g": {}, "match_version": MATCH_VERSION})
        db.insert({"original_name": "Remapped", "usda_query": "old query", "per_100g": {}})
        mappings = {"Legacy": "egg", "Current": "milk", "Remapped": "new query", "Water": "skip"}

        count = count_outdated_matches(["Legacy", "Current", "Remapped", "Water", "New"], mappings, db)

        assert count == 1


class TestEnrichAllFoods:
    def test_skips_foods_mapped_to_skip(self, mocker, tmp_path):
        db = TinyDB(str(tmp_path / "test_db.json"))
        mappings = {"Quick Add": "skip", "Boiled Eggs": "boiled egg"}
        mock_match = mocker.patch("omni_pilot.enricher.get_food_match")
        mock_match.return_value = {
            "per_100g": {"vitamin_a_mcg": 149.0}, "usda_name": "Egg", "confidence": "good", "macro_distance": 0.01,
        }

        result = enrich_all_foods(["Quick Add", "Boiled Eggs"], mappings, {}, db, "fake-key")

        assert result["skipped"] == {"Quick Add"}
        assert result["unresolved"] == set()
        assert "Quick Add" not in result["profiles"]
        assert result["profiles"]["Boiled Eggs"] == {"vitamin_a_mcg": 149.0}
        # get_food_match should only be called for Boiled Eggs
        mock_match.assert_called_once()

    def test_failed_usda_lookup_is_unresolved_not_skipped(self, mocker, tmp_path):
        # Only the network seam is faked, so the real get_food_match and
        # enrich_all_foods produce the shape the pipeline actually sees.
        db = TinyDB(str(tmp_path / "test_db.json"))
        mocker.patch("omni_pilot.enricher.search_usda", return_value=[])

        result = enrich_all_foods(["Lachs"], {"Lachs": "salmon"}, {}, db, "fake-key")

        assert result["unresolved"] == {"Lachs"}
        assert result["skipped"] == set()
        assert "Lachs" not in result["profiles"]

    def test_profiles_never_contain_none(self, mocker, tmp_path):
        db = TinyDB(str(tmp_path / "test_db.json"))
        mocker.patch("omni_pilot.enricher.search_usda", return_value=[])
        mappings = {"Quick Add": "skip", "Lachs": "salmon"}

        result = enrich_all_foods(["Quick Add", "Lachs"], mappings, {}, db, "fake-key")

        # Neither outcome may leak into profiles as a None value — that
        # overloading is what made the two indistinguishable.
        assert None not in result["profiles"].values()
        assert result["profiles"] == {}

    def test_uses_original_name_and_logged_macros(self, mocker, tmp_path):
        db = TinyDB(str(tmp_path / "test_db.json"))
        mock_match = mocker.patch("omni_pilot.enricher.get_food_match")
        mock_match.return_value = {
            "per_100g": {"vitamin_a_mcg": 149.0}, "usda_name": "Egg", "confidence": "good", "macro_distance": 0.01,
        }

        enrich_all_foods(["Boiled Eggs"], {"Boiled Eggs": ""}, {"Boiled Eggs": EGG_LOGGED}, db, "fake-key")

        mock_match.assert_called_once_with("Boiled Eggs", "Boiled Eggs", EGG_LOGGED, db, "fake-key")

    def test_weak_match_is_counted_and_listed_as_low_confidence(self, mocker, tmp_path):
        db = TinyDB(str(tmp_path / "test_db.json"))
        mocker.patch("omni_pilot.enricher.get_food_candidates", return_value=[
            _candidate(1, "Cheese, feta", 14.2, 21.5, 3.9),
        ])
        # A feta salad: far fewer calories than feta itself.
        logged = {"Salzlakenkaese salat": {"kcal": 54.0, "protein_g": 4.0, "fat_g": 3.0, "carbs_g": 2.0}}

        result = enrich_all_foods(
            ["Salzlakenkaese salat"], {"Salzlakenkaese salat": "cheese, feta"}, logged, db, "fake-key"
        )

        assert "Salzlakenkaese salat" in result["profiles"]
        weak = result["low_confidence"]["Salzlakenkaese salat"]
        assert weak["usda_name"] == "Cheese, feta"
        assert weak["macro_distance"] > 1.0

    def test_good_match_is_not_low_confidence(self, mocker, tmp_path):
        db = TinyDB(str(tmp_path / "test_db.json"))
        mocker.patch("omni_pilot.enricher.get_food_candidates", return_value=[
            _candidate(2, "Egg, whole, cooked, hard-boiled", 12.6, 10.6, 1.1),
        ])

        result = enrich_all_foods(
            ["Boiled Eggs"], {"Boiled Eggs": "egg, whole, cooked, hard-boiled"}, {"Boiled Eggs": EGG_LOGGED},
            db, "fake-key",
        )

        assert result["low_confidence"] == {}
```

- [ ] **Step 3: Write the failing CLI tests**

In `tests/test_cli.py`:
- replace `from tests.helpers import enrichment` with:

  ```python
  from omni_pilot.enricher import MATCH_VERSION
  from tests.helpers import enrichment, food_entry
  ```

- replace each of the three occurrences of `return_value=[{"food_name": "Apfel", "total_weight_g": 100.0, "date": "2026-08-01"}],` with `return_value=[food_entry("Apfel")],`;
- in `test_analyze_reports_resolved_count_from_profiles_only`, replace the `return_value=[ {...} for name in ("Apfel", "Wasser", "Lachs") ],` comprehension with `return_value=[food_entry(name) for name in ("Apfel", "Wasser", "Lachs")],`.

This is needed because the CLI now reads each entry's macros, and the old hand-built entries lacked them.

Append these methods to `class TestCLIAnalyze:` (at the end of the file, indented as class members):

```python
    def _write_config(self, tmp_path, mappings: dict) -> tuple[str, str, str]:
        db_path = str(tmp_path / "db.json")
        mappings_path = str(tmp_path / "mappings.yaml")
        settings_path = str(tmp_path / "settings.yaml")
        ref_ranges_path = str(tmp_path / "ref_ranges.yaml")
        with open(mappings_path, "w") as f:
            yaml.dump({"mappings": mappings}, f)
        with open(settings_path, "w") as f:
            yaml.dump({"usda_api_key": "fake_key", "database_path": db_path, "mappings_path": mappings_path}, f)
        with open(ref_ranges_path, "w") as f:
            yaml.dump({"nutrients": {}}, f)
        return db_path, settings_path, ref_ranges_path

    def _run(self, mocker, settings_path: str, ref_ranges_path: str) -> None:
        mocker.patch("omni_pilot.cli.analyze", return_value={})
        mocker.patch("omni_pilot.cli.print_terminal_report")
        mocker.patch("sys.argv", [
            "omni_pilot", "analyze", "data/MacroFactor-example.xlsx",
            "--settings", settings_path, "--ref-ranges", ref_ranges_path,
        ])
        main()

    def test_analyze_passes_logged_macros_to_enrichment(self, mocker, tmp_path):
        _, settings_path, ref_ranges_path = self._write_config(tmp_path, {"Reis": "rice, cooked"})
        mocker.patch("omni_pilot.cli.parse_food_log", return_value=[
            food_entry("Reis", 200.0, calories_kcal=260.0, protein_g=6.0, fat_g=0.0, carbs_g=56.0),
        ])
        mock_enrich = mocker.patch("omni_pilot.cli.enrich_all_foods", return_value=enrichment())

        self._run(mocker, settings_path, ref_ranges_path)

        logged = mock_enrich.call_args.args[2]
        assert logged["Reis"] == {"kcal": 130.0, "protein_g": 3.0, "fat_g": 0.0, "carbs_g": 28.0}

    def test_analyze_announces_rematching_of_outdated_cache(self, mocker, tmp_path, capsys):
        db_path, settings_path, ref_ranges_path = self._write_config(
            tmp_path, {"Reis": "rice, cooked", "Milch": "milk"}
        )
        db = TinyDB(db_path)
        db.insert({"original_name": "Reis", "usda_query": "rice, cooked", "per_100g": {}})
        db.insert({"original_name": "Milch", "usda_query": "milk", "per_100g": {}, "match_version": MATCH_VERSION})
        db.close()
        mocker.patch("omni_pilot.cli.parse_food_log", return_value=[food_entry("Reis"), food_entry("Milch")])
        mocker.patch("omni_pilot.cli.enrich_all_foods", return_value=enrichment())

        self._run(mocker, settings_path, ref_ranges_path)

        assert "Re-matching 1 cached foods with updated USDA matching..." in capsys.readouterr().out

    def test_analyze_is_silent_when_cache_is_current(self, mocker, tmp_path, capsys):
        _, settings_path, ref_ranges_path = self._write_config(tmp_path, {"Reis": "rice, cooked"})
        mocker.patch("omni_pilot.cli.parse_food_log", return_value=[food_entry("Reis")])
        mocker.patch("omni_pilot.cli.enrich_all_foods", return_value=enrichment())

        self._run(mocker, settings_path, ref_ranges_path)

        assert "Re-matching" not in capsys.readouterr().out
```

- [ ] **Step 4: Run the tests to verify they fail**

Run: `uv run pytest tests/test_enricher.py tests/test_cli.py -v`
Expected: collection errors, `ImportError: cannot import name 'LowConfidenceMatch'` (from `tests/helpers.py`) and `cannot import name 'MATCH_VERSION'`.

- [ ] **Step 5: Implement the enricher**

In `src/omni_pilot/enricher.py`:

Change `from omni_pilot.matcher import Candidate` to `from omni_pilot.matcher import Candidate, LoggedMacros`.

Replace:

```python
SEARCH_PAGE_SIZE = 25
```

with:

```python
# Bump whenever the candidate-picking logic changes in a way that should
# re-pick cached foods: entries stamped with an older version are re-matched.
MATCH_VERSION = 1
SEARCH_PAGE_SIZE = 25
```

Replace the `EnrichmentResult` class with these three classes:

```python
class LowConfidenceMatch(TypedDict):
    usda_name: str
    macro_distance: float | None


class EnrichmentResult(TypedDict):
    """Outcome of enriching a set of foods.

    "Deliberately skipped" and "USDA lookup failed" are different facts and are
    kept in separate sets, so profiles never carries None for either.
    """

    profiles: dict[str, dict[str, float | None]]
    skipped: set[str]
    unresolved: set[str]
    # Resolved foods whose USDA match fits the logged macros poorly. They stay
    # in profiles and are counted; the report names them.
    low_confidence: dict[str, LowConfidenceMatch]


class FoodMatch(TypedDict):
    per_100g: dict[str, float | None]
    usda_name: str
    confidence: str  # "good" | "weak" | "no_macros" | "unverified"
    macro_distance: float | None
```

Replace everything from `def get_food_micros(` to the end of the file with:

```python
def _match_from_entry(entry: dict, confidence: str | None = None) -> FoodMatch:
    return FoodMatch(
        per_100g=entry["per_100g"],
        usda_name=entry.get("usda_name", ""),
        confidence=confidence or entry.get("confidence", ""),
        macro_distance=entry.get("macro_distance"),
    )


def get_food_match(
    food_name: str,
    usda_query: str,
    logged: LoggedMacros | None,
    db: TinyDB,
    api_key: str,
) -> FoodMatch | None:
    """Get the USDA match and per-100g micro profile for a food.

    A cache entry is reused only when both its query and its match_version are
    current. An entry picked by older matching logic is re-picked, but kept
    (as "unverified") if the re-pick fails, so a network hiccup never turns a
    known food into an unresolved one. Returns None if the food cannot be
    resolved at all.
    """
    Food = Query()
    stale_entry = None
    cached = db.search(Food.original_name == food_name)
    if cached:
        entry = cached[0]
        if entry.get("usda_query") == usda_query:
            if entry.get("match_version", 0) == MATCH_VERSION:
                return _match_from_entry(entry)
            stale_entry = entry
        else:
            # Mapping changed: the old entry describes a different query
            logger.info("Mapping changed for '%s', refetching...", food_name)
            db.remove(Food.original_name == food_name)

    pick = matcher.pick_best(get_food_candidates(usda_query, api_key), usda_query, logged)
    if pick is None:
        if stale_entry is not None:
            logger.warning("Re-matching '%s' failed; keeping its previous USDA match", food_name)
            return _match_from_entry(stale_entry, confidence="unverified")
        return None

    candidate = pick["candidate"]
    micros = extract_micros_from_usda(candidate["raw"])
    db.upsert({
        "original_name": food_name,
        "usda_query": usda_query,
        "usda_name": candidate["description"],
        "usda_fdc_id": candidate["fdc_id"],
        "usda_dataset": candidate["data_type"],
        "per_100g": micros,
        "confidence": pick["confidence"],
        "match_version": MATCH_VERSION,
        "macro_distance": pick["macro_distance"],
        "usda_macros": {
            "protein_g": candidate["protein_g"],
            "fat_g": candidate["fat_g"],
            "carbs_g": candidate["carbs_g"],
            "fiber_g": candidate["fiber_g"],
        },
        "last_updated": str(date.today()),
    }, Food.original_name == food_name)

    return FoodMatch(
        per_100g=micros,
        usda_name=candidate["description"],
        confidence=pick["confidence"],
        macro_distance=pick["macro_distance"],
    )


def count_outdated_matches(food_names: list[str], mappings: dict[str, str], db: TinyDB) -> int:
    """Count cached foods whose query is current but whose match_version is not."""
    Food = Query()
    outdated = 0
    for food_name in food_names:
        mapping = mappings.get(food_name, "")
        if mapping == "skip":
            continue
        query = mapping if mapping else food_name
        cached = db.search(Food.original_name == food_name)
        if (
            cached
            and cached[0].get("usda_query") == query
            and cached[0].get("match_version", 0) != MATCH_VERSION
        ):
            outdated += 1
    return outdated


def enrich_all_foods(
    food_names: list[str],
    mappings: dict[str, str],
    logged_macros: dict[str, LoggedMacros],
    db: TinyDB,
    api_key: str,
) -> EnrichmentResult:
    """Enrich all foods with USDA micro data.

    Resolved foods go into profiles, foods mapped to "skip" into skipped, and
    foods whose USDA lookup failed into unresolved. Resolved foods whose match
    is weak are also listed in low_confidence.
    """
    result: EnrichmentResult = {
        "profiles": {}, "skipped": set(), "unresolved": set(), "low_confidence": {},
    }

    for food_name in food_names:
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

The old `"direct"`/`"mapped"` confidence computation is gone. Nothing read it.

- [ ] **Step 6: Wire the CLI**

In `src/omni_pilot/cli.py`, replace:

```python
from omni_pilot.enricher import enrich_all_foods
```

with:

```python
from omni_pilot.enricher import count_outdated_matches, enrich_all_foods
from omni_pilot.matcher import logged_macros_per_100g
```

and replace:

```python
    db = TinyDB(db_path)
    enrichment = enrich_all_foods(food_names, mappings, db, api_key)
```

with:

```python
    db = TinyDB(db_path)
    outdated = count_outdated_matches(food_names, mappings, db)
    if outdated:
        print(f"  Re-matching {outdated} cached foods with updated USDA matching...")
    logged_macros = logged_macros_per_100g(entries)
    enrichment = enrich_all_foods(food_names, mappings, logged_macros, db, api_key)
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `uv run pytest tests/test_enricher.py tests/test_cli.py -v`
Expected: all pass.

- [ ] **Step 8: Lint and run the full suite**

Run: `uv run ruff check src tests && uv run pytest -q`
Expected: `All checks passed!`, then 143 passed.

- [ ] **Step 9: Commit**

```bash
git add src/omni_pilot/enricher.py src/omni_pilot/cli.py tests/helpers.py tests/test_enricher.py tests/test_cli.py
git commit -m "feat: version USDA cache entries and match foods by logged macros (BAR-72)"
```

---

### Task 5: Analyzer — low-confidence foods and their weight share

**Files:**
- Modify: `src/omni_pilot/analyzer.py`: `CoverageResult`, a new `LowConfidenceFood`, and `analyze`
- Modify: `tests/test_analyzer.py` (append one class)

**Verifies:** spec §5.1.

**Interfaces:**
- Consumes (Task 4): `EnrichmentResult["low_confidence"]: dict[str, {"usda_name": str, "macro_distance": float | None}]`; the `enrichment(..., low_confidence=...)` test helper.
- Produces (used by Task 6):
  - `result["coverage"]["low_confidence_foods"]: list[{"name": str, "usda_name": str, "macro_distance": float | None, "grams": float}]`, sorted by grams descending, then by name;
  - `result["coverage"]["low_confidence_weight_pct"]: float`, rounded to 1 decimal, `0.0` when nothing was analysed.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_analyzer.py`:

```python
class TestLowConfidenceCoverage:
    REF_RANGES = {
        "demographic": {},
        "nutrients": {
            "vitamin_a_mcg": {"name": "Vitamin A", "unit": "mcg", "type": "rda", "rda": 900, "ul": 3000},
        },
    }

    def test_weak_foods_listed_by_grams_eaten(self):
        entries = [
            {"date": "2026-08-09", "food_name": "Caprese", "total_weight_g": 200.0},
            {"date": "2026-08-09", "food_name": "Feta Salat", "total_weight_g": 300.0},
            {"date": "2026-08-10", "food_name": "Feta Salat", "total_weight_g": 200.0},
            {"date": "2026-08-10", "food_name": "Eggs", "total_weight_g": 300.0},
        ]
        profiles = {name: {"vitamin_a_mcg": 10.0} for name in ("Caprese", "Feta Salat", "Eggs")}
        low_confidence = {
            "Caprese": {"usda_name": "Fish, tuna salad", "macro_distance": 1.5},
            "Feta Salat": {"usda_name": "Cheese, feta", "macro_distance": None},
        }

        result = analyze(entries, enrichment(profiles, low_confidence=low_confidence), self.REF_RANGES)

        coverage = result["coverage"]
        assert coverage["low_confidence_foods"] == [
            {"name": "Feta Salat", "usda_name": "Cheese, feta", "macro_distance": None, "grams": 500.0},
            {"name": "Caprese", "usda_name": "Fish, tuna salad", "macro_distance": 1.5, "grams": 200.0},
        ]
        # 700 g of 1000 g analysed
        assert coverage["low_confidence_weight_pct"] == 70.0

    def test_weak_foods_still_count_toward_nutrients(self):
        entries = [{"date": "2026-08-09", "food_name": "Caprese", "total_weight_g": 200.0}]
        low_confidence = {"Caprese": {"usda_name": "Fish, tuna salad", "macro_distance": 1.5}}

        result = analyze(
            entries,
            enrichment({"Caprese": {"vitamin_a_mcg": 10.0}}, low_confidence=low_confidence),
            self.REF_RANGES,
        )

        assert result["nutrients"]["vitamin_a_mcg"]["daily_avg"] == pytest.approx(20.0)

    def test_denominator_excludes_skipped_and_unresolved_weight(self):
        entries = [
            {"date": "2026-08-09", "food_name": "Caprese", "total_weight_g": 100.0},
            {"date": "2026-08-09", "food_name": "Eggs", "total_weight_g": 100.0},
            {"date": "2026-08-09", "food_name": "Water", "total_weight_g": 500.0},
            {"date": "2026-08-09", "food_name": "Lachs", "total_weight_g": 300.0},
        ]
        low_confidence = {"Caprese": {"usda_name": "Fish, tuna salad", "macro_distance": 1.5}}

        result = analyze(
            entries,
            enrichment(
                {"Caprese": {"vitamin_a_mcg": 10.0}, "Eggs": {"vitamin_a_mcg": 10.0}},
                skipped={"Water"}, unresolved={"Lachs"}, low_confidence=low_confidence,
            ),
            self.REF_RANGES,
        )

        assert result["coverage"]["low_confidence_weight_pct"] == 50.0

    def test_no_weak_foods_and_no_analysed_weight(self):
        entries = [{"date": "2026-08-09", "food_name": "Water", "total_weight_g": 500.0}]
        result = analyze(entries, enrichment(skipped={"Water"}), self.REF_RANGES)
        assert result["coverage"]["low_confidence_foods"] == []
        assert result["coverage"]["low_confidence_weight_pct"] == 0.0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_analyzer.py::TestLowConfidenceCoverage -v`
Expected: 3 failed with `KeyError: 'low_confidence_foods'`; `test_weak_foods_still_count_toward_nutrients` already passes, because weak foods are ordinary profiles.

- [ ] **Step 3: Implement**

In `src/omni_pilot/analyzer.py`, replace the `CoverageResult` class with:

```python
class LowConfidenceFood(TypedDict):
    name: str
    usda_name: str
    macro_distance: float | None
    grams: float


class CoverageResult(TypedDict):
    total_food_entries: int
    mapped_entries: int
    skipped_entries: int
    unresolved_entries: int
    skipped_foods: list[str]
    unresolved_foods: list[str]
    low_confidence_foods: list[LowConfidenceFood]
    low_confidence_weight_pct: float
```

In `analyze`, replace:

```python
    unmeasured_weight_g: dict[str, float] = defaultdict(float)
    dates: set[str] = set()
```

with:

```python
    unmeasured_weight_g: dict[str, float] = defaultdict(float)
    # Consumed grams of all analysed entries, and of weakly matched foods
    analysed_weight_g = 0.0
    low_confidence_weight_g: dict[str, float] = defaultdict(float)
    dates: set[str] = set()
```

Replace:

```python
        total_weight_g = entry["total_weight_g"]
        scale_factor = total_weight_g / 100.0
```

with:

```python
        total_weight_g = entry["total_weight_g"]
        scale_factor = total_weight_g / 100.0
        analysed_weight_g += total_weight_g
        if food_name in enrichment["low_confidence"]:
            low_confidence_weight_g[food_name] += total_weight_g
```

Immediately before the final `return AnalysisResult(`, insert:

```python
    low_confidence_foods = sorted(
        (
            LowConfidenceFood(
                name=food_name,
                usda_name=enrichment["low_confidence"][food_name]["usda_name"],
                macro_distance=enrichment["low_confidence"][food_name]["macro_distance"],
                grams=grams,
            )
            for food_name, grams in low_confidence_weight_g.items()
        ),
        key=lambda food: (-food["grams"], food["name"]),
    )
    low_confidence_weight_pct = (
        round(100.0 * sum(low_confidence_weight_g.values()) / analysed_weight_g, 1)
        if analysed_weight_g > 0 else 0.0
    )

```

and in the `CoverageResult(...)` call, after `unresolved_foods=sorted(unresolved_food_names),`, add:

```python
            low_confidence_foods=low_confidence_foods,
            low_confidence_weight_pct=low_confidence_weight_pct,
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_analyzer.py tests/test_integration.py -v`
Expected: all pass.

- [ ] **Step 5: Lint and run the full suite**

Run: `uv run ruff check src tests && uv run pytest -q`
Expected: `All checks passed!`, then 147 passed.

- [ ] **Step 6: Commit**

```bash
git add src/omni_pilot/analyzer.py tests/test_analyzer.py
git commit -m "feat: report low-confidence foods and their weight share in coverage (BAR-72)"
```

---

### Task 6: Reporter — the low-confidence warning line

**Files:**
- Modify: `src/omni_pilot/reporter.py`: add `_low_confidence_line`, print it in the terminal, render it in the HTML template
- Modify: `tests/test_reporter.py`: fixture keys, a new fixture, a new test class

**Verifies:** spec §5.2.

**Interfaces:**
- Consumes (Task 5): `coverage["low_confidence_foods"]`, `coverage["low_confidence_weight_pct"]`.
- Produces: user-facing output only.

- [ ] **Step 1: Write the failing tests**

In `tests/test_reporter.py`, in `_make_analysis_result`'s `"coverage"` dict, add after `"unresolved_foods": ["Unknown Thing"],`:

```python
            "low_confidence_foods": [],
            "low_confidence_weight_pct": 0.0,
```

Directly above `def _make_analysis_result_without_floor() -> dict:`, add:

```python
def _make_analysis_result_with_low_confidence() -> dict:
    result = _make_analysis_result()
    result["coverage"]["low_confidence_foods"] = [
        {"name": "Salzlakenkaese salat", "usda_name": "Cheese, feta", "macro_distance": 3.96, "grams": 2084.0},
        {"name": "Paprika [red]", "usda_name": "Fish, tuna salad", "macro_distance": None, "grams": 164.0},
    ]
    result["coverage"]["low_confidence_weight_pct"] = 18.4
    return result


```

Append to the end of the file:

```python
class TestLowConfidenceWarning:
    def test_terminal_names_weak_matches_with_weight_share(self, capsys, monkeypatch):
        monkeypatch.setenv("COLUMNS", "300")
        settings = {"output": {"show_amino_acids": True, "show_ok_nutrients": True}}
        print_terminal_report(_make_analysis_result_with_low_confidence(), settings)
        out = capsys.readouterr().out
        assert "Low-confidence matches (18% of analysed weight)" in out
        assert "Salzlakenkaese salat → Cheese, feta (off 396%)" in out
        # No distance: no "(off ...)", and brackets in names are printed, not parsed as markup
        assert "Paprika [red] → Fish, tuna salad" in out
        assert "Fish, tuna salad (off" not in out

    def test_terminal_omits_warning_when_all_matches_are_good(self, capsys):
        settings = {"output": {"show_amino_acids": True, "show_ok_nutrients": True}}
        print_terminal_report(_make_analysis_result(), settings)
        assert "Low-confidence" not in capsys.readouterr().out

    def test_html_names_weak_matches(self, tmp_path):
        html_path = str(tmp_path / "report.html")
        generate_html_report(_make_analysis_result_with_low_confidence(), html_path)
        with open(html_path) as f:
            html = f.read()
        assert "Low-confidence matches (18% of analysed weight)" in html
        assert "Salzlakenkaese salat → Cheese, feta (off 396%)" in html

    def test_html_omits_warning_when_all_matches_are_good(self, tmp_path):
        html_path = str(tmp_path / "report.html")
        generate_html_report(_make_analysis_result(), html_path)
        with open(html_path) as f:
            assert "Low-confidence" not in f.read()

    def test_html_shows_warnings_block_for_low_confidence_alone(self, tmp_path):
        result = _make_analysis_result_with_low_confidence()
        result["coverage"]["skipped_foods"] = []
        result["coverage"]["unresolved_foods"] = []
        html_path = str(tmp_path / "report.html")
        generate_html_report(result, html_path)
        with open(html_path) as f:
            assert "Low-confidence matches" in f.read()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_reporter.py::TestLowConfidenceWarning -v`
Expected: `test_terminal_names_weak_matches_with_weight_share`, `test_html_names_weak_matches` and `test_html_shows_warnings_block_for_low_confidence_alone` fail (text not found); the two "omits" tests pass.

- [ ] **Step 3: Implement**

In `src/omni_pilot/reporter.py`, directly above `def print_terminal_report(`, add:

```python
def _low_confidence_line(coverage: dict) -> str | None:
    """Name the foods whose USDA match fits the logged macros poorly, if any."""
    foods = coverage["low_confidence_foods"]
    if not foods:
        return None
    described = []
    for food in foods:
        text = f"{food['name']} → {food['usda_name']}"
        if food["macro_distance"] is not None:
            text += f" (off {food['macro_distance'] * 100:.0f}%)"
        described.append(text)
    share = coverage["low_confidence_weight_pct"]
    return f"Low-confidence matches ({share:.0f}% of analysed weight): {', '.join(described)}"


```

At the end of `print_terminal_report`, after the `⚠ Unresolved foods` block, add:

```python
    low_confidence_line = _low_confidence_line(coverage)
    if low_confidence_line:
        # Text, not a markup string: food and USDA names can contain "[...]".
        console.print(Text(f"  ⚠ {low_confidence_line}", style="yellow"))
```

In `HTML_TEMPLATE`, replace:

```html
    {% if skipped_foods or unresolved_foods %}
    <div class="warnings">
        {% if skipped_foods %}<p>⚠ Skipped: {{ skipped_foods|join(", ") }}</p>{% endif %}
        {% if unresolved_foods %}<p>⚠ Unresolved: {{ unresolved_foods|join(", ") }}</p>{% endif %}
    </div>
```

with:

```html
    {% if skipped_foods or unresolved_foods or low_confidence_line %}
    <div class="warnings">
        {% if skipped_foods %}<p>⚠ Skipped: {{ skipped_foods|join(", ") }}</p>{% endif %}
        {% if unresolved_foods %}<p>⚠ Unresolved: {{ unresolved_foods|join(", ") }}</p>{% endif %}
        {% if low_confidence_line %}<p>⚠ {{ low_confidence_line }}</p>{% endif %}
    </div>
```

In `generate_html_report`'s `template.render(...)` call, after `unresolved_foods=coverage["unresolved_foods"],`, add:

```python
        low_confidence_line=_low_confidence_line(coverage),
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_reporter.py -v`
Expected: all pass.

To confirm the markup test does its job: temporarily change the new `console.print(Text(...))` line to `console.print(f"  ⚠ {low_confidence_line}", style="yellow")` and rerun. `test_terminal_names_weak_matches_with_weight_share` must fail (Rich swallows `[red]`). Then revert to the `Text(...)` version.

- [ ] **Step 5: Lint and run the full suite**

Run: `uv run ruff check src tests && uv run pytest -q`
Expected: `All checks passed!`, then 152 passed.

- [ ] **Step 6: Commit**

```bash
git add src/omni_pilot/reporter.py tests/test_reporter.py
git commit -m "feat: show low-confidence USDA matches in terminal and HTML reports (BAR-72)"
```

---

### Task 7: Documentation and end-to-end verification

**Files:**
- Modify: `CLAUDE.md`

**Verifies:** spec §7 (rollout) and §10.

**Interfaces:**
- Consumes: everything above.
- Produces: documentation, plus a verified real run against a **copy** of the production DB.

- [ ] **Step 1: Update `CLAUDE.md`**

1. Replace `runs a fixed autonomous pipeline through five modules in \`src/omni_pilot/\`:` with `runs a fixed autonomous pipeline through five modules in \`src/omni_pilot/\` (plus \`matcher.py\`, used by the enricher):`.

2. Replace the whole `3. **\`enricher.py\`** — …` list item with:

```markdown
3. **`enricher.py`** — Looks up each mapped food name in USDA FoodData Central (SR Legacy / Foundation datasets), extracting a fixed set of nutrients (`USDA_NUTRIENT_MAP`: vitamins, minerals, omega-3s, amino acids) per 100g. It never trusts USDA's first search hit: `get_food_candidates` collects up to 25 hits for the query plus hits for its head words alone, and `matcher.pick_best` chooses among them (see `matcher.py` below). Queries are passed through `clean_query` first, because USDA's search returns HTTP 400 for `/` and, intermittently, for brackets. Results are cached in TinyDB (`db/food_db.json`) keyed by original food name + the USDA query used, so a changed mapping invalidates and refetches the cache entry. `enrich_all_foods` returns an `EnrichmentResult` with four parts: `profiles` (resolved foods only, never `None`), `skipped` (mapped to `"skip"`), `unresolved` (USDA lookup failed) and `low_confidence` (resolved foods whose match fits the logged macros poorly — still in `profiles` and counted, but named in the report). Keep `skipped` and `unresolved` distinct — the report labels them differently, and encoding both as `None` once made failed lookups read as deliberate skips.
   - **`matcher.py`** — Pure functions (no I/O) that pick the USDA candidate matching what was eaten. `logged_macros_per_100g` turns MacroFactor's logged kcal/protein/fat/carbs into per-100g figures per food. `pick_best` keeps only candidates that *are* the named food (head-word filter on the start of the USDA description, so "Lebanon bologna, beef" never matches `beef, …`), then scores each by macro distance plus a penalty for missing query words — food identity first (feta beats camembert even with worse macros), macros deciding the form (raw vs cooked). Matches with macro distance above `GOOD_DISTANCE` are `weak`. The thresholds were tuned by simulating against the production DB (spec: `docs/specs/2026-09-23-usda-candidate-matching-design.md`); re-run such a simulation before changing them.
```

3. In the `5. **\`reporter.py\`** — …` item, after `via a Jinja2 template.`, insert: ` After the skipped/unresolved warnings it prints a "Low-confidence matches" line naming weakly matched foods and their share of analysed weight (from \`coverage.low_confidence_foods\` / \`low_confidence_weight_pct\`, computed in \`analyzer.py\`).`

4. In the data flow block, replace the line `food_names, mappings → enricher.enrich_all_foods (TinyDB cache + USDA API) → EnrichmentResult` with:

```
entries → matcher.logged_macros_per_100g → logged_macros
food_names, mappings, logged_macros → enricher.enrich_all_foods (TinyDB cache + USDA API + matcher) → EnrichmentResult
```

5. Under "Key invariants", insert this bullet immediately before the `- TinyDB (\`db/food_db.json\`) is the source of truth…` bullet:

```markdown
- A USDA cache entry is reused only when both its `usda_query` and its `match_version` equal the current ones. Bump `MATCH_VERSION` in `enricher.py` whenever the candidate-picking logic changes in a way that should re-pick cached foods — the next `analyze` then re-matches every cached food once. A failed re-pick keeps the old entry (unstamped, retried next run) rather than deleting it, and entries are written with `upsert` so a food never has two cache entries.
```

- [ ] **Step 2: Full suite and lint**

Run: `uv run ruff check src tests && uv run pytest -q`
Expected: `All checks passed!`, then 152 passed.

- [ ] **Step 3: End-to-end run against a copy of the production DB**

This makes real USDA calls (about 250) but **no** Gemini calls, and never touches the production files. Work in a scratch directory outside the repo (e.g. your session scratchpad), called `$SMOKE` below:

```bash
PROD="$HOME/Library/Mobile Documents/iCloud~is~workflow~my~workflows/Documents/omni-pilot"
SMOKE=<scratch dir>
cp "$PROD/db/food_db.json" "$SMOKE/food_db.json"
cp "$PROD/config/food_mappings.yaml" "$SMOKE/food_mappings.yaml"
shasum "$PROD/db/food_db.json" > "$SMOKE/prod_before.sha"
uv run python - "$SMOKE" <<'EOF'
import sys, yaml
smoke = sys.argv[1]
settings = yaml.safe_load(open("config/settings.yaml"))
settings.pop("gemini_api_key", None)  # never send real prompts to Gemini from a verification run
settings["database_path"] = f"{smoke}/food_db.json"
settings["mappings_path"] = f"{smoke}/food_mappings.yaml"
yaml.safe_dump(settings, open(f"{smoke}/settings.yaml", "w"))
EOF
PYTHONPATH=src uv run python -m omni_pilot.cli analyze data/MacroFactor-20260817231558.xlsx --settings "$SMOKE/settings.yaml"
```

Expected:
- `Re-matching N cached foods with updated USDA matching...`, with N > 0;
- the normal report;
- a yellow `⚠ Low-confidence matches (…% of analysed weight): …` line naming mostly mixed dishes (e.g. `Salzlakenkaese salat`, `Salat Caprese`, `Königsgemüse`) if they appear in that export.

Then check the copied DB and rerun:

```bash
uv run python - "$SMOKE" <<'EOF'
import json, sys
db = json.load(open(f"{sys.argv[1]}/food_db.json"))["_default"]
names = [e["original_name"] for e in db.values()]
assert len(names) == len(set(names)), "duplicate cache entries"
stamped = [e for e in db.values() if e.get("match_version") == 1]
print(f"{len(stamped)} entries stamped with match_version 1, no duplicates")
for e in stamped:
    if e["original_name"] in ("Kichererbsen", "Reis XXL", "Hähnchen Brustfilet", "Patros Natur"):
        print(e["original_name"], "->", e["usda_name"], e["confidence"], round(e["macro_distance"] or 0, 2))
EOF
PYTHONPATH=src uv run python -m omni_pilot.cli analyze data/MacroFactor-20260817231558.xlsx --settings "$SMOKE/settings.yaml" | grep -c "Re-matching" || true
shasum -c "$SMOKE/prod_before.sha"
```

Expected:
- stamped entries and no duplicates. Whichever of the four sample foods are in that export show the spec's picks: Reis XXL → cooked rice, Hähnchen Brustfilet → the breast entry, Patros Natur → *Cheese, feta*;
- the second run prints `0` (no re-match notice);
- `shasum -c` reports `OK`: the production DB is untouched.

If an expected pick differs, stop and report it rather than tuning constants. The constants are spec decisions.

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: document USDA candidate matching and the match_version invariant (BAR-72)"
```

- [ ] **Step 5: Hand off**

Report the Step 3 output (N, the low-confidence line, the sample picks) to the user. **Do not** run `analyze` against the production iCloud DB. Doing so performs the one-time re-match rollout, which is the user's call. Also remind the user that the iPhone runs the iCloud copy of `src/`, which needs this code synced before it re-picks anything (old code there keeps working with the new entries meanwhile; spec §7).
