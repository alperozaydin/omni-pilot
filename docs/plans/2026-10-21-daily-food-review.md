# Daily Food Review Routine: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Claude cloud routine reviews new foods every morning at 9:00 Berlin time. It approves right matches, gives wrong ones a new recipe, leaves the rest for the user, saves to S3, and sends a push notification only on a change or a failure.

**Architecture:**
- `recipe_tool.py` in the custom-foods skill gains two commands: `pending` (the routine's to-do list) and `defer` (the "left for the user" mark). `review` gains one note line.
- A new skill, `omni-pilot-daily-review`, holds the unattended procedure.
- Both skills live in `alperozaydin/my-skills` (working copy `~/Projects/my-skills`) and are installed into omni-pilot with `npx skills`.
- The tool's tests live in omni-pilot's `tests/test_recipe_tool.py` and run against the my-skills working copy through `RECIPE_TOOL` while the work is in progress.
- The routine is created with the `schedule` skill once both PRs are merged.

**Tech Stack:** Python 3, TinyDB, pytest, `npx skills`, Claude Code routines (`RemoteTrigger`).

**Spec:** `docs/specs/2026-10-21-daily-food-review-design.md`

## Global Constraints

- **Mark fields** (main table of `food_db.json`, written only by `recipe_tool.py defer`, never read by the app):
  - `deferred_fdc_id`: the entry's `usda_fdc_id`, for a match.
  - `deferred_query`: the entry's `usda_query`, for a not-found record.
  - `deferred_reason`: `"impossible"` or `"no_macros"`, only with the matching flag.
- **No change to `src/omni_pilot`.**
- **Per-run limit:** 20 foods, heaviest first, by total logged grams across all exports (the order `review` uses).
- **The routine only appends recipes,** under `# --- Added by the daily review ---` at the end of `custom_foods.yaml`. It never edits, re-tunes, renames or removes an existing recipe, including recipes from earlier runs, and never adds a name to an existing `foods:` list.
- **Notification:** only when changes were saved (≥1 approved or fixed, push succeeded) or the run failed. Never for "nothing new", and never for "only left for the user".
- **Schedule:** `CRON_TZ=Europe/Berlin 0 9 * * *`, Default environment `env_012yKZUDBbA311mCUfG7tawS`, repository `alperozaydin/omni-pilot` on `main`, the routine's own notification setting off.
- **Skills workflow:**
  - The installed copy in `.agents/skills/` is never hand-edited.
  - Install from the branch with `npx skills add "alperozaydin/my-skills#<branch>" --skill <name> -y`.
  - Merge order: my-skills PR, reinstall from `main` (drops the ref), commit, then the omni-pilot PR.
- **Tests:** no live USDA, Gemini or S3 calls.
- **Commits:** one summary line, no body, no attribution lines.

## Review Focus

1. **A food name with a comma passed to `defer`** ("Watermelon, Fresh") is one name, not two. Test in Task 1 (`test_defers_every_named_food_including_names_with_commas`).
2. **A deferred food that the user later approves by hand** leaves `pending`, and `review` shows it as approved, not "left". Test in Task 2 (`test_an_approved_food_is_not_pending_even_if_deferred`).
3. **A food deferred as impossible that is no longer in any export** stays deferred: an absent figure is not a fixed one. Test in Task 2 (`test_impossible_reason_without_logged_macros_stays_deferred`).
4. **A legacy entry without `usda_fdc_id`** is never pending, since nothing can be done with it until `analyze` runs. Test in Task 2 (`test_entry_without_usda_id_is_not_pending`).
5. **A previously approved match that turned weak** is pending again. Test in Task 2 (`test_lists_an_approved_match_that_turned_weak`).

---

### Task 1: `defer` command

**Files:**
- Modify: `~/Projects/my-skills/skills/omni-pilot-custom-foods/scripts/recipe_tool.py`
- Test: `tests/test_recipe_tool.py` (omni-pilot), new `class TestDefer`

**Interfaces:**
- Produces, in `recipe_tool.py`:
  - `DEFERRED_FDC = "deferred_fdc_id"`, `DEFERRED_QUERY = "deferred_query"`, `DEFERRED_REASON = "deferred_reason"`
  - `_is_deferred(entry: dict) -> bool`: true when the mark matches the entry's current state. For a not-found record, `entry.get(DEFERRED_QUERY)` equals `entry["usda_query"]`. Otherwise, `entry.get(DEFERRED_FDC)` is not `None` and equals `entry.get("usda_fdc_id")`.
  - `_defer_problem(name: str, entry: dict | None, custom: dict, mappings: dict[str, str]) -> str | None`
  - `cmd_defer(args)`, with subparser `defer`: `foods` (nargs `+`), and the mutually exclusive flags `--impossible` / `--no-macros`.

Setup: create the branch `alozay/bar-84-daily-review` in `~/Projects/my-skills` from an up-to-date `main` (the working copy is currently on `alozay/bar-44-map-command`; check that it's clean first). Run tests with `RECIPE_TOOL=~/Projects/my-skills/skills/omni-pilot-custom-foods/scripts/recipe_tool.py uv run pytest tests/test_recipe_tool.py`.

- [ ] **Step 1: Write the failing tests** in `class TestDefer`, using the existing `ws` / `run` fixtures and `_entry`:
  - `test_marks_a_match_with_its_usda_id`: `ws.add(EGGS)`, then `run("defer", "Boiled Eggs")`. Expect exit 0, `"left  Boiled Eggs → Egg, whole, cooked, hard-boiled (173424)"` and `"1 left"` in the output. The entry equals `{**EGGS, "deferred_fdc_id": 173424}`.
  - `test_marks_a_not_found_record_with_its_query`: a not-found record for `"Braun Linsen"` with `usda_query` `"lentils"`. Expect `"left  Braun Linsen (not in USDA: lentils)"`, and the entry gains only `deferred_query == "lentils"`.
  - `test_defers_every_named_food_including_names_with_commas`: `EGGS`, `MELON` and one other entry. `run("defer", "Boiled Eggs", "Watermelon, Fresh")` marks exactly those two.
  - `test_reason_flags_write_the_reason`, parametrized over `("--impossible", "impossible")` and `("--no-macros", "no_macros")`: `deferred_reason` is written alongside `deferred_fdc_id`.
  - `test_without_a_flag_a_stale_reason_is_removed`: an entry with `deferred_fdc_id: 999, deferred_reason: "impossible"`. After `defer` with no flag, the entry has `deferred_fdc_id == 173424` and no `deferred_reason` key.
  - `test_deferring_a_deferred_food_changes_nothing`: an entry already marked at `173424` with no reason. Expect exit 0, `"already left  Boiled Eggs"` and `"0 left"`, and the entry is unchanged.
  - `test_a_refused_name_stops_the_whole_call`, parametrized with `ids=["unknown-name", "in-a-recipe", "skipped", "approved", "no-usda-id"]`:
    - unknown name → `"not a searched food"`
    - in a recipe → `"in recipe caprese"`
    - mapped to skip → `"skip"`
    - approved (`reviewed_fdc_id == usda_fdc_id`) → `"is approved; nothing to leave"`
    - no `usda_fdc_id` and not a not-found record → `"run analyze first"`

    Each case expects exit 1, `"✗ <name>: "` and the reason in the output, `"Nothing written."`, and no `deferred_*` key on any entry.
  - `test_the_flags_cannot_be_combined`: `run("defer", "--impossible", "--no-macros", "Boiled Eggs")` exits non-zero and writes nothing.

- [ ] **Step 2: Run the tests and see them fail.**
  - Run: `RECIPE_TOOL=… uv run pytest tests/test_recipe_tool.py::TestDefer -v`
  - Expected: FAIL (`invalid choice: 'defer'`).

- [ ] **Step 3: Implement `_is_deferred`, `_defer_problem` and `cmd_defer`**, modelled on `_approval_problem` / `cmd_approve`:
  - All names are checked first; nothing is written on any refusal.
  - Names are deduplicated.
  - Each food gets one `db.update`.
  - Without a flag, a stale `deferred_reason` is removed with `tinydb.operations.delete`.
  - The reuse of `approve`'s refusal texts for unknown, recipe and skip is deliberate.
  - Update the module docstring: add the `defer NAME... [--impossible | --no-macros]` line, and say that every command is read-only except `approve` and `defer`, which write only their mark.

- [ ] **Step 4: Run the tests and see them pass,** together with the rest of the file.
  - Run: `RECIPE_TOOL=… uv run pytest tests/test_recipe_tool.py -v`
  - Expected: all PASS.

- [ ] **Step 5: Commit both repos.**
  - my-skills: `omni-pilot-custom-foods: defer command for foods left for the user (BAR-84)`
  - omni-pilot, `tests/test_recipe_tool.py`: `test: defer marks foods left for the user (BAR-84)`

---

### Task 2: `pending` command

**Files:**
- Modify: `~/Projects/my-skills/skills/omni-pilot-custom-foods/scripts/recipe_tool.py`
- Test: `tests/test_recipe_tool.py`, new `class TestPending`

**Interfaces:**
- Consumes: `_is_deferred`, `DEFERRED_REASON` (Task 1), `_is_approved`, `_implausible`, `Logged`.
- Produces:
  - `_reason_gone(entry: dict, name: str, logged: Logged) -> bool`:
    - `"impossible"`: the name has per-100 g figures that are not `_implausible`, or it is now only in `logged.weight_unknown`.
    - `"no_macros"`: `name in logged`.
    - No reason: `False`.
  - `_is_pending(name: str, entry: dict, custom: dict, mappings: dict[str, str], logged: Logged) -> bool`:
    - Never pending: a food in a recipe or mapped to skip; an entry with neither `not_found` nor `usda_fdc_id`; an approved entry.
    - Otherwise pending when the entry is not `_is_deferred`, or when `_reason_gone` is true.
  - `cmd_pending(args)`, with subparser `pending`: `--exports` as in `review`, and `--limit N` (int, default none).
  - The `review` per-food printing moves into module-level helpers shared by `review` and `pending`, with no change to `review`'s output:
    - `_food_header(name: str, logged: Logged, check_macros: bool = True) -> str`
    - `_print_searched(name: str, entry: dict, is_approved: bool) -> None`

- [ ] **Step 1: Write the failing tests** in `class TestPending`:
  - `test_lists_new_changed_weak_and_not_found_foods`: entries for a new match, a changed match (`reviewed_fdc_id` differs), a weak match and a not-found record.
    - Expect `"== Searched in USDA (3): is each match the same food?"` and `"== Not in USDA (1): give them a recipe"`, with all four names in the output.
  - `test_hides_approved_deferred_recipe_and_skipped_foods`: one of each, plus `EGGS`. Only `"Boiled Eggs"` is listed.
  - `test_nothing_to_review`: only an approved entry. Expect exit 0 and output exactly `"Nothing to review.\n"`.
  - `test_a_deferred_food_returns_when_its_match_changes`: `deferred_fdc_id: 999` with `usda_fdc_id: 173424` → listed. Also a not-found record with `deferred_query: "old text"` → listed.
  - `test_impossible_reason_returns_once_the_weight_is_fixed`: an export (`tests.helpers.write_food_log`) whose per-100 g macros for the food are plausible, and an entry deferred at its current ID with `deferred_reason: "impossible"`.
    - Expect it listed, with the line `"    left for the user before (impossible logged macros); the logged macros are fine now"`.
    - The same entry with an export holding impossible macros (e.g. 200 g of macros in 100 g) is not listed.
  - `test_impossible_reason_without_logged_macros_stays_deferred`: no export at all → not listed.
  - `test_no_macros_reason_returns_once_an_export_has_the_food`: listed with `"    left for the user before (no logged macros); an export has them now"`. Without the export → not listed.
  - `test_an_approved_food_is_not_pending_even_if_deferred`: `deferred_fdc_id` and `reviewed_fdc_id` both equal to `usda_fdc_id` → not listed.
  - `test_entry_without_usda_id_is_not_pending`: `_entry("Haferflocken", None, …)` → not listed.
  - `test_lists_an_approved_match_that_turned_weak`: `confidence="weak"` and `reviewed_fdc_id == usda_fdc_id` → listed.
  - `test_limit_keeps_the_heaviest`: an export logging three foods at 300 g, 200 g and 100 g, all pending.
    - `pending --limit 2` lists the 300 g and 200 g foods, not the 100 g one, and ends with `"2 of 3 shown; the rest wait for the next run"`.
    - The group headers count the shown foods.

- [ ] **Step 2: Run the tests and see them fail.**
  - Run: `RECIPE_TOOL=… uv run pytest tests/test_recipe_tool.py::TestPending -v`
  - Expected: FAIL (`invalid choice: 'pending'`).

- [ ] **Step 3: Implement `_reason_gone`, `_is_pending`, the two print helpers and `cmd_pending`.**
  - Gather names the way `review` does, sort them by `(-grams, name.lower())`, keep the pending ones, then apply `--limit` to that combined list.
  - Print the two groups in the order searched, then not in USDA. Leave out an empty group.
  - Each food block is the `review` block, plus the "left for the user before (…)" line for a returning deferred food.
  - The reason texts: `impossible logged macros` → `the logged macros are fine now`; `no logged macros` → `an export has them now`.
  - Add the docstring line: `pending [--limit N]   foods the daily review must still read, heaviest first`.

- [ ] **Step 4: Run the tests and see them pass,** for the whole file. The existing `TestReview` tests passing shows the move into shared helpers left `review`'s output unchanged.
  - Run: `RECIPE_TOOL=… uv run pytest tests/test_recipe_tool.py -v`
  - Expected: all PASS.

- [ ] **Step 5: Commit both repos.**
  - my-skills: `omni-pilot-custom-foods: pending lists foods the daily review must read (BAR-84)`
  - omni-pilot: `test: pending lists only unsettled foods (BAR-84)`

---

### Task 3: `review` note and the custom-foods skill text

**Files:**
- Modify: `~/Projects/my-skills/skills/omni-pilot-custom-foods/scripts/recipe_tool.py` (`_print_searched` and the not-in-USDA block of `cmd_review`)
- Modify: `~/Projects/my-skills/skills/omni-pilot-custom-foods/SKILL.md`
- Test: `tests/test_recipe_tool.py`, `class TestReview`

**Interfaces:**
- Consumes: `_is_deferred` (Task 1).

- [ ] **Step 1: Write the failing tests** in `TestReview`:
  - `test_marks_a_food_left_for_the_user`: a match deferred at its current ID, and a not-found record deferred at its current query. Both stay listed, each followed by the line `"    left for the user by the daily review"`.
  - `test_a_stale_deferral_is_not_marked`: `deferred_fdc_id: 999` → no such line.

- [ ] **Step 2: Run the tests and see them fail.**
  - Run: `RECIPE_TOOL=… uv run pytest tests/test_recipe_tool.py::TestReview -v`
  - Expected: the two new tests FAIL.

- [ ] **Step 3: Implement the note line,** after the `matched to` / `looked up as` line, only while `_is_deferred(entry)` holds.

- [ ] **Step 4: Update the custom-foods `SKILL.md`.**
  - **Tool block:** add two lines:
    - `$T pending` (foods the daily review has not settled yet)
    - `$T defer "Food"` (used by the daily review)
  - **Write sentence:** "Only `approve` writes…" becomes: "`approve` and `defer` write only their mark".
  - **Check-up section:** add one sentence. Foods marked "left for the user by the daily review" are those the daily routine could not settle. Settle them as usual: approve, recipe, or tell the user.

- [ ] **Step 5: Run the tests and see them pass,** for the whole file.
  - Run: `RECIPE_TOOL=… uv run pytest tests/test_recipe_tool.py -v`
  - Expected: all PASS.

- [ ] **Step 6: Commit both repos.**
  - my-skills: `omni-pilot-custom-foods: review marks foods left by the daily review (BAR-84)`
  - omni-pilot: `test: review marks foods left for the user (BAR-84)`

---

### Task 4: The `omni-pilot-daily-review` skill

**Files:**
- Create: `~/Projects/my-skills/skills/omni-pilot-daily-review/SKILL.md`

**Interfaces:**
- Consumes:
  - `pending --limit 20`, `approve`, `defer [--impossible | --no-macros]`, `check`, `search` and `food` (Tasks 1–3, and the existing commands).
  - The custom-foods skill at `.agents/skills/omni-pilot-custom-foods/SKILL.md`.

- [ ] **Step 1: Write `SKILL.md`.**
  - **Frontmatter:**
    - `name: omni-pilot-daily-review`
    - The description says it is only for the unattended daily routine in the omni-pilot repo (the routine's prompt names it), and that a conversation with the user uses `omni-pilot-custom-foods` instead.
  - **Body, in the order of spec §2–§7:**
    - **Setup:** `cp config/settings.example.yaml config/settings.yaml` unless it exists, `uv sync`, `make pull`.
    - **Nothing new:** `pending --limit 20`, then stop on `Nothing to review.`.
    - **Settle each food:** follow the verdict table and rules of spec §3, including:
      - approve only when the food is clearly the same;
      - new recipes only, appended under the section marker, each with the `# Daily review YYYY-MM-DD: assumed typical recipe (…)` comment, naming the copied recipe when it is the same dish;
      - impossible macros → `defer --impossible`;
      - no logged macros → approve or `defer --no-macros`, never a recipe;
      - otherwise unsettled → `defer`.

      The skill points to the custom-foods skill for recipe building ("Workflow" steps 3–5, "Choosing ingredients", "Tuning amounts", "Placeholder weights"). It does not copy them.
    - **Check:** `check` must exit 0.
    - **Push**, with the one retry of spec §5.
    - **The report** in the spec §6 format.
    - **Notification:** `PushNotification` per spec §7, both texts.
    - **Never:** ask the user anything, run `analyze`, edit `food_mappings.yaml`, commit or push to git, or edit an existing recipe.
    - **Failure:** on any failure, report the step and the error, send the failure push, and stop.

- [ ] **Step 2: Read the skill against the spec,** section by section. Every rule in spec §2–§7 must appear, and nothing may contradict the custom-foods skill. In particular, its "do not retry the push" rule is for runs with the user present; the daily skill states its own retry rule explicitly.

- [ ] **Step 3: Commit (my-skills).**
  - `omni-pilot-daily-review: unattended daily review of new foods (BAR-84)`
  - Push the branch and open the my-skills PR.

---

### Task 5: Install into omni-pilot, enricher test, docs

**Files:**
- Modify: `skills.json`, `skills-lock.json`, `.agents/skills/` (installed copies)
- Test: `tests/test_enricher.py`, `class TestNotFoundCache`
- Modify: `CLAUDE.md`

- [ ] **Step 1: Write the test** `test_refreshed_not_found_record_keeps_fields_the_app_does_not_write`: `self._not_found(days_old=NOT_FOUND_TTL_DAYS, deferred_query=self.QUERY)`, searched again with nothing found. The record afterwards equals `self._not_found(deferred_query=self.QUERY)`.

- [ ] **Step 2: Run it.**
  - Run: `uv run pytest tests/test_enricher.py::TestNotFoundCache -v`
  - Expected: PASS. It pins existing `upsert` behaviour that the deferral relies on, so it never failed first; say so in the commit.

- [ ] **Step 3: Install both skills from the branch.**
  - Check the resolved ref with `npx skills add "alperozaydin/my-skills#alozay/bar-84-daily-review" --list`.
  - Install `--skill omni-pilot-custom-foods -y`, then `--skill omni-pilot-daily-review -y`.
  - Check that `skills.json` lists both and `.agents/skills/omni-pilot-daily-review/SKILL.md` exists. `.claude/skills` is a symlink to `.agents/skills`.

- [ ] **Step 4: Run the full suite against the installed copy,** without `RECIPE_TOOL`.
  - Run: `make test`
  - Expected: all PASS.

- [ ] **Step 5: Update `CLAUDE.md`.**
  - **Key invariants**, in the `reviewed_fdc_id` paragraph: add `deferred_fdc_id` / `deferred_query` / `deferred_reason`. They are written by `recipe_tool.py defer` for the daily review, never read by the app, and kept by the same `upsert` rule. A refreshed not-found record keeps them too.
  - **Commands:** one line. The daily routine "Omni Pilot daily food review" runs the `omni-pilot-daily-review` skill at 9:00 Berlin time in the Default cloud environment.

- [ ] **Step 6: Commit (omni-pilot)** and open the omni-pilot PR.
  - `test: a refreshed not-found record keeps the daily review's mark (BAR-84)`
  - `chore: install the daily-review skill and document its marks (BAR-84)`

- [ ] **Step 7: Merge in order, with the user's go-ahead for each merge.**
  - Merge the my-skills PR.
  - Reinstall both skills from `alperozaydin/my-skills` without a ref, and commit `chore: install skills from main (BAR-84)`.
  - Run `make test`.
  - Merge the omni-pilot PR.

---

### Task 6: Backlog check-up, with the user (before Task 7)

Not code: a check-up with the custom-foods skill in this session, as spec §8.3 asks. It can run at any point before Task 7.

- [ ] `make pull`, then `review --exports '<scratchpad>/exports/*.xlsx'`. The full export converted from the user's CSV fills in the logged macros of the August–September gap.
- [ ] Go through the searched foods in batches, heaviest first. The user confirms each verdict:
  - right match → `approve`;
  - wrong match → a recipe that passes `check`;
  - Apfel and Ben's Original rice have no logged macros → the user says what they are.
- [ ] `check` exits 0, then `make push`. Report the counts.

---

### Task 7: Create the routine and test it

- [ ] **Step 1: Prerequisites with the user.** The Default environment's network access allows `api.nal.usda.gov` and `omni-pilot-data.s3.eu-central-1.amazonaws.com`. The user checks this in the claude.ai environment settings.
- [ ] **Step 2: Create the routine with the `schedule` skill.**
  - Name: "Omni Pilot daily food review"
  - Schedule: `CRON_TZ=Europe/Berlin 0 9 * * *`
  - Environment: Default (`env_012yKZUDBbA311mCUfG7tawS`)
  - Repository: `alperozaydin/omni-pilot`, `main`, a fresh session per run
  - Notifications: off
  - Prompt: `Run the omni-pilot-daily-review skill.`

  Relay the server's parsed run time and the routine's URL to the user.
- [ ] **Step 3: Run it once now** (`RemoteTrigger run`), then read `list_runs` / `get_run_log`. All of these must hold:
  - `pull` worked;
  - `pending` ran;
  - either "Nothing to review." with no push and no notification, or a push that S3 accepted, the first `push` from a cloud session;
  - the `PushNotification` call's result says it was delivered.

  If the notification did not reach the phone, stop and take the §7 fallback to the user.
- [ ] **Step 4: Run it a second time.** It must end with "Nothing to review." and send no notification.

---

### Task 8: Close the issues

- [ ] Set BAR-84 to Done, with both PR links and a short comment: routine URL, schedule, test-run results.
- [ ] Set BAR-81 to Done with a comment summarising the three parts (BAR-82, BAR-83, BAR-84) and the notification behaviour.
