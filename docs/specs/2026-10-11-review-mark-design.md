# Technical Specification: Review Mark for USDA Matches (BAR-82)

**Document Version:** 1.0
**Date:** 2026-10-11
**Status:** In Review
**Linear Issue:** [BAR-82](https://linear.app/knaak/issue/BAR-82/review-mark-for-usda-matches-in-the-custom-foods-skill)
**Part of:** [BAR-81](https://linear.app/knaak/issue/BAR-81/automate-the-custom-food-skill) (automate the custom food skill). BAR-84 (the daily routine) builds on this.
**Builds on:** BAR-72 (versioned USDA cache, `match_version`), BAR-79 (not-found records, the skill's `review` groups)

---

## 1. Executive Summary & Objective

### The Problem

The app judges a USDA match by macros only. On 2026-10-10 the database held 122 foods resolved by USDA search and outside any recipe; the app flagged 4 of them as weak. A wrong food whose macros happen to fit is never flagged: "Bio Frische Alpenmilch" is matched to "Milk, buttermilk, fluid, whole" with a confidence of `good`, 6% off.

The custom-foods skill covers that gap with a check-up: `recipe_tool.py review` prints every food beside its match, and the reader judges by name whether they are the same food. Nothing records the outcome. Every check-up reads all 122 again, and a reader can judge the same food differently on another day.

This blocks the daily routine planned in BAR-84, which must review only what is new.

### The Solution

- A new field, `reviewed_fdc_id`, on a searched food's database entry: the USDA ID that a reviewer read beside the food's name and judged to be the same food.
- A food counts as approved while `reviewed_fdc_id` equals its current `usda_fdc_id`. When the app picks a different match, the two differ and the food comes up for review again without any extra code.
- A new tool command, `approve`, sets the field. It is the tool's only command that writes.
- `review` lists only matches that are not approved.
- The skill's instructions gain the three outcomes of reviewing a food.

The app is not changed. It never reads the field.

### Decisions Made During Design

| Decision | Choice | Why |
| --- | --- | --- |
| What the mark stores | The approved USDA ID, not `true` | The app updates an entry in place when it re-picks a match. A boolean would stay on the entry and approve a match nobody read. |
| Where it lives | On the food's entry in the main USDA cache table | The mark sits beside the match it is about, and there is no second file to keep in step. A separate "reviewed" list was considered and dropped. |
| Foods in a custom recipe | No mark | A recipe food does not use its database match. Being in the recipe file is the mark, and `check` verifies recipes on every run. |
| Plain foods (e.g. "Boiled Eggs") | Stay on USDA search and get the mark | Pinning them with one-ingredient recipes would repeat what the database holds and fill the recipe file and the report. |
| Weak matches | Cannot be approved | The app already says their macros do not fit. They need a recipe or stay listed for the user. This also leaves `flagged` unchanged. |
| A mapping change | Drops the mark | The app removes the entry and searches the new text (`enricher.py:423`). A new lookup text is a new match to read. |
| Tests for the tool | In omni-pilot's suite, against the installed copy | `approve` writes the user's database, and the tool imports private names from `enricher.py` that only this repo's tests can guard. BAR-79 checked the tool by hand; that is no longer enough. |

---

## 2. The Field

`reviewed_fdc_id` is an integer on an entry of the main (default) table of `food_db.json`, next to the existing `usda_fdc_id`:

| Field | Set by | Meaning |
| --- | --- | --- |
| `usda_fdc_id` (exists) | The app | The USDA food the app matched. |
| `reviewed_fdc_id` (new) | The skill, through `approve` | The USDA ID judged to be the same food as the logged name. |

A searched food is in one of three states:

| State | Condition | In `review` |
| --- | --- | --- |
| **New** | No `reviewed_fdc_id` | Listed |
| **Approved** | `reviewed_fdc_id == usda_fdc_id` | Hidden |
| **Changed** | `reviewed_fdc_id` present and different | Listed, with a note |

On 2026-10-10 all 169 match entries had a `usda_fdc_id`, so every searched food can be approved.

90 of those entries have no `match_version` yet: they were matched by older logic and the app re-picks them the next time the food appears in an analysed export. An approval given before that stays valid when the re-pick chooses the same USDA food, and turns into Changed when it chooses another.

---

## 3. How the App's Writes Treat the Field

No code in `src/omni_pilot` changes. The table shows why the field stays correct through each path of `enricher.get_food_match`, the only code that writes these entries:

| Event | What the app does | The mark |
| --- | --- | --- |
| Entry is current | Reads it, writes nothing | Kept |
| Re-pick with the same lookup text (older or missing `match_version`) finds a match | `db.upsert`, which merges fields (`enricher.py:472`) | Kept. Approved if the same ID was picked, Changed if not. |
| Re-pick fails or finds nothing | Leaves the entry alone and reports the old match as `unverified` | Kept |
| Lookup text changed | `db.remove`, then a new entry or a not-found record (`enricher.py:423`) | Dropped, so the food is New |
| A not-found record turns into a match | `db.remove`, then a new entry (`enricher.py:445`) | None to drop: a not-found record cannot be approved (§4.1) |

A match never turns into a not-found record under the same lookup text: when a re-pick finds nothing, the app keeps the old match.

The mark depends on one behaviour: a re-pick with the same lookup text merges into the existing entry instead of replacing it. That becomes a documented invariant (§8) with a test (§7).

Older code on a device that has not been synced ignores the field and preserves it the same way.

---

## 4. The Tool (`recipe_tool.py`)

### 4.1 `approve`

```bash
PYTHONPATH=src uv run python $T approve "Boiled Eggs" "Watermelon, Fresh"
```

Takes one or more food names, exactly as logged. For each it copies the entry's `usda_fdc_id` into `reviewed_fdc_id` and prints what was approved:

```
approved  Boiled Eggs → Egg, whole, cooked, hard-boiled (173424)
approved  Watermelon, Fresh → Watermelon, raw (167765)
2 approved
```

A name is refused when the mark would mean nothing or contradict the app:

| Case | Message says |
| --- | --- |
| No entry with that name | Not a searched food; check the spelling against `review`. |
| The food is in a custom recipe | It is in recipe `<name>`; recipes need no approval. |
| The food is mapped to `skip` | It is skipped and not counted. |
| The entry is a not-found record | USDA has no match; it needs a recipe. |
| The match is weak (`confidence == "weak"`) | Macros are off N%; give it a recipe or leave it for the user. |
| The entry has no `usda_fdc_id` | Run `analyze` first. |

- **All or nothing:** every name is checked first. If any is refused, the command prints each refusal, writes nothing and exits 1.
- **Already approved:** a food whose mark already equals its match is reported as `already approved` and is not an error.
- **What is written:** only `reviewed_fdc_id`, with one `db.update` per food. The database is opened for writing only in this command; every other command keeps opening it read-only.

### 4.2 `review`

The "Searched in USDA" group lists only New and Changed foods, and its header counts both sides:

```
== Searched in USDA (14 to review, 108 approved): is each match the same food?
Bio Frische Alpenmilch  (750 g; logged 64 kcal, P 3.3, F 3.6, C 5.1)
    looked up as: milk, whole, fluid
    matched to  : Milk, buttermilk, fluid, whole  [good, off 6%]
```

- A Changed food gets one more line, naming the ID that was approved: `    approved before as FDC <id>; the match has changed since`.
- `review --all` also lists Approved foods, marked `(approved)` at the end of the `matched to` line. It exists so approvals can be read again.
- The other groups (Not in USDA, Skipped, Custom recipes, Not looked up yet) are unchanged.

### 4.3 Unchanged commands

`flagged`, `search`, `food` and `check` are unchanged. `flagged` lists weak matches outside recipes, and a weak match cannot be approved, so the two never overlap.

The module docstring's "Everything is read-only" becomes: every command is read-only except `approve`, which writes only the review mark.

---

## 5. The Skill's Instructions (`SKILL.md`)

- **The tool section** gains the `approve` line, and "never writes to the database" becomes "writes only the review mark".
- **The check-up section** says that `review` shows only matches not yet approved, and gives the three outcomes:

| Verdict | Action | Result |
| --- | --- | --- |
| The match is the same food, in the same form | `approve` it | Hidden from `review` until the app changes the match |
| The match is wrong and fixable | Write a recipe that passes `check` (within 25%) | In a recipe; no mark |
| It cannot be settled: impossible logged macros, no plausible recipe within 25%, or the name does not say what the food is | Change nothing | Listed for the user and shown again next time |

- **Rules for approving:**
  - Approve only when the name and the match are clearly the same food. When unsure, leave it unmarked.
  - Never approve a food to shorten the list.
  - If the user says a listed match is fine, approve it.
- **Reporting:** a check-up still lists the problems first, heaviest first. Approvals are reported as a count, with `review --all` named as the way to read them.

The first check-up after this change reads all searched foods once. Later ones read only what is new or changed.

---

## 6. Where the Work Happens

- **Skill:** `~/Projects/my-skills`, `skills/omni-pilot-custom-foods/` (`SKILL.md`, `scripts/recipe_tool.py`), on a branch for BAR-82. The installed copy in omni-pilot (`.agents/skills/`) is never edited by hand.
- **Install into omni-pilot:** `npx skills add "alperozaydin/my-skills#<branch>" --skill omni-pilot-custom-foods -y`, which records the branch in `skills-lock.json`.
- **Merge order:** merge the my-skills PR, run `npx skills add alperozaydin/my-skills --skill omni-pilot-custom-foods -y` again to drop the branch reference, commit, then merge the omni-pilot PR.
- **omni-pilot:** the new tests, the `CLAUDE.md` line, the installed skill copy and `skills-lock.json`.

---

## 7. Testing

Tests are written first. No test calls USDA or Gemini.

**`tests/test_enricher.py`** (one new test)
- A re-pick with the same lookup text keeps a field the app does not know: an entry with an older `match_version` and `reviewed_fdc_id` set is re-picked, and the field is still on the entry afterwards.

**`tests/test_recipe_tool.py`** (new)

The tests load the tool from the installed copy under `.agents/skills/`. An environment variable can point them at the my-skills working copy while the change is being written. Each test uses a temporary database, recipe file and mappings file.

- `approve` sets `reviewed_fdc_id` to the entry's `usda_fdc_id` and changes no other field or entry.
- `approve` with several names writes all of them.
- Each refusal in §4.1 exits 1, names the food and the reason, and writes nothing, including for the valid names in the same call.
- Approving an approved food is not an error and changes nothing.
- `review` hides an Approved food, lists a New one, and lists a Changed one with the note.
- The header counts match the two sides.
- `review --all` lists Approved foods with the `(approved)` marker.
- `flagged` output is the same with and without marks.

**By hand, on the real database**
- `review` before: all searched foods listed. Approve a handful, then `review` hides exactly those.
- After a real `make analyze` run the marks are still present.

**End to end:** `make test` passes with the same results as before plus the new tests.

---

## 8. Documentation

`CLAUDE.md`, Key invariants: the custom-foods skill stores `reviewed_fdc_id` on entries of the main USDA cache table. The app never reads it. A re-pick with an unchanged lookup text must keep merging into the existing entry (`upsert`), because replacing the entry would silently drop every review mark.

---

## 9. Out of Scope

- **Clearing a mark.** A wrong approval is corrected by writing a recipe, which overrides the match.
- **Running unattended.** The rules for a run with no user present, and the new skill that holds them, belong to BAR-84.
- **Two devices writing the database at once.** `approve` rewrites the database file like any app run does. Refusing a write over a changed file comes with S3 in BAR-83.
- **Showing review state in the nutrient report.** The app does not read the field.
- **Foods not in any current export.** They can be approved like any other; the tool keeps marking them "not in any export".
