# Technical Specification: Daily Routine That Reviews New Foods Unattended (BAR-84)

**Document Version:** 1.0
**Date:** 2026-10-21
**Status:** Approved (2026-10-21)
**Linear Issue:** [BAR-84](https://linear.app/knaak/issue/BAR-84/daily-routine-that-reviews-new-foods-unattended) (part 3 of [BAR-81](https://linear.app/knaak/issue/BAR-81/automate-the-custom-food-skill), the last one)
**Builds on:** BAR-82 (review mark, `approve`), BAR-83 (data in S3, `pull` / `push`)

---

## 1. Executive Summary & Objective

### The Problem

New foods often get a wrong USDA match even when their translation is good. Today a wrong match is fixed only when the user runs the custom-foods skill by hand. Until then, `analyze` counts the wrong food's nutrients.

### The Solution

A Claude cloud routine runs every morning, while the Mac can be off. It reads every food whose match nobody has judged yet, and settles each one in one of three ways: it approves the match, gives the food a new recipe, or leaves it for the user. It saves the changes to S3 and ends with a report. When it approved or fixed something, it sends a push notification to the user's phone. A day with nothing new is silent.

The routine is driven by a new, short skill that holds only the unattended procedure. It builds recipes the way the existing custom-foods skill describes, with that skill's tool.

### Decisions Made During Design

Settled with the user (on BAR-81 and BAR-84, and in this design session):

| Decision | Choice |
| --- | --- |
| Where it runs | A Claude cloud routine in the Default environment, on a fresh clone of `main` |
| When | Daily, in the morning: 9:00 Berlin time |
| Skill | A new skill, `omni-pilot-daily-review`, separate from `omni-pilot-custom-foods`, which it refers to for building recipes |
| User present | Never. The skill asks nothing |
| A wrong match | Gets a typical recipe, marked in the recipe file as an assumption |
| Existing recipes | Never changed: not edited, re-tuned or removed, and no food added to an existing recipe's food list. A ⚠ on an existing recipe is not the routine's business |
| Foods left for the user | Appear only in the report of the run that left them. The routine remembers them and does not read them again unless their match changes |
| Notification | When the run approved or fixed at least one food, or when the run failed. Nothing new, or only foods left for the user: no notification |
| Backlog | The 116 searched foods not yet approved (local copy, 2026-10-21) are checked by hand with the user before the routine is turned on (§8.3). The routine starts with only new foods |
| Per-run limit | At most 20 foods per run, heaviest first; the rest stay in `pending` for the next day. A safety limit for a run that finds an unexpected pile |
| S3 access | The app's own `pull` / `push` with the limited `omni-pilot-app` key from the environment's `OMNI_PILOT_S3_*` variables (BAR-83, checked 2026-10-20). Not the AWS connector, which settled the connector's root access and expiring sign-in |
| Out of scope | Running `analyze`, marking guessed recipes in the nutrient report, starting the routine from the app |

---

## 2. The Run

1. **Set up.** Copy `config/settings.example.yaml` to `config/settings.yaml` unchanged (a fresh clone has none). The S3 settings come from the environment's `OMNI_PILOT_S3_*` variables. The USDA key stays the example's placeholder: the environment's network secret on `api.nal.usda.gov` replaces the `X-Api-Key` header, and a request that already carries a placeholder key works (access check on BAR-81, 2026-10-10). Run `uv sync`, then `make pull` (database, mappings, recipes and every export).
2. **Anything new?** Run `recipe_tool.py pending` (§4.1). If it lists nothing, end with the one-line report "Nothing new to review." and stop. No push, no notification.
3. **Settle each listed food** (§3), at most the 20 heaviest. The rest stay listed for the next run, and the report says how many are left.
4. **Check.** `recipe_tool.py check` on the real recipe file must exit 0.
5. **Save.** `make push`. On a conflict, see §5.
6. **Report** as the session's last message (§6).
7. **Notify** when at least one food was approved or fixed (§7).

The routine never runs `analyze`, never edits `food_mappings.yaml`, and never commits or pushes anything to git: everything it changes lives in S3.

A food reaches the database only when the user runs `analyze` on the Mac or iPhone, so the routine reviews what the previous days' `analyze` runs added.

---

## 3. Settling a Food

`pending` lists two kinds of food: searched foods whose match is neither approved nor left for the user at its current USDA ID, and foods USDA found nothing for that were not left for the user at their current lookup text. For each, the routine reads the logged name beside the match, as the custom-foods skill's check-up describes, and picks one outcome:

| Verdict | Action | Counts as a change |
| --- | --- | --- |
| The match is clearly the same food, in the same form | `approve` | Yes |
| The match is wrong, or USDA had nothing, and a typical recipe fits | A **new** recipe that passes `check` (within 25%, or 15% for a placeholder weight) | Yes |
| Cannot be settled: impossible logged macros (`!!`), no plausible recipe within the limit, or the name does not say what the food is | `defer` (§4.2) | No |

Rules:

- **Approve** only when the name and the match are clearly the same food. When unsure, a recipe or `defer`, never an approval. Weak matches cannot be approved (the tool refuses them).
- **Recipes** are built with the custom-foods skill's workflow and its "Choosing ingredients", "Tuning amounts" and "Placeholder weights" sections. Every recipe the routine writes is a typical recipe, so each gets a comment saying so: `# Daily review YYYY-MM-DD: assumed typical recipe (<the assumption in a few words>)`.
- **New recipes only.** New recipes go at the end of `custom_foods.yaml` under a section comment `# --- Added by the daily review ---` (created once, then appended to). The routine only appends: it never edits, re-tunes, renames or removes an existing recipe, and never adds a name to an existing recipe's `foods:` list. This holds for every recipe already in the file, including those added by earlier daily-review runs. A new food that is the same dish as an existing recipe gets its own new recipe (it may use the same ingredients), and its comment names the recipe it copies.
- A food with "impossible logged macros" is deferred, not given a recipe: the user fixes the weight in MacroFactor. A photo-logged meal ("with a placeholder weight") is not impossible; its recipe is tuned to proportions as usual.
- **No logged macros** ("not in any export"): `check` cannot verify a recipe for such a food, so the routine only approves or defers it (`defer --no-macros`), never gives it a recipe. This only happens for foods from exports analysed before BAR-83 and never kept (the iPhone Shortcut overwrote `data/data.xlsx`; on 2026-10-21 no export covers 2026-08-18 to 2026-09-13). Since BAR-83 every analysed export is in S3, so a new food always has its logged macros. The 19 such foods in the backlog are settled in the hand check-up (§8.3).
- Foods mapped to `skip` and foods "not looked up yet" are not the routine's business (they are not in `pending`); the user's manual check-ups still show them.

---

## 4. Tool Changes (`recipe_tool.py`)

The tool stays in the custom-foods skill (`skills/omni-pilot-custom-foods/scripts/recipe_tool.py` in `~/Projects/my-skills`); the new skill calls it there. Two commands are added, and `review` gains one note line.

### 4.1 `pending`

```bash
PYTHONPATH=src uv run python $T pending
```

Lists, in `review`'s format and heaviest first (total logged grams across all exports, as `review` sorts), the foods the daily review must read:

- **Searched** foods that are not approved (`_is_approved` false) and whose `deferred_fdc_id` is not their current `usda_fdc_id`.
- **Not in USDA** foods (not-found records) whose `deferred_query` is not their current `usda_query`.
- Deferred foods whose reason is gone (§4.2): `"impossible"` with logged macros no longer impossible, `"no_macros"` with logged macros now in an export.

Foods in a recipe and foods mapped to `skip` are never listed. Two groups, each with a count in its header. With nothing to list it prints `Nothing to review.` and exits 0. Read-only.

### 4.2 `defer`

```bash
PYTHONPATH=src uv run python $T defer "Food A" "Food B"
```

Marks each food as left for the user at its current state:

| Entry | Field written |
| --- | --- |
| A match | `deferred_fdc_id` = its `usda_fdc_id` |
| A not-found record | `deferred_query` = its `usda_query` |

With `--impossible`, `defer` also writes `deferred_reason: "impossible"` (the food was left because its logged macros are impossible). `pending` lists such a food again once its logged macros are no longer impossible, so a weight the user fixed in MacroFactor brings it back, though its match did not change. *(Builder's call: fixing a weight never makes the app re-pick, so without this the food would never come back.)* Likewise `--no-macros` writes `deferred_reason: "no_macros"`, and `pending` lists the food again once an export has logged macros for it.

- Refused, as `approve` refuses: no entry with that name, in a recipe, mapped to `skip`. A food that is already approved is refused too (there is nothing to leave).
- All or nothing, like `approve`: any refusal prints every refusal, writes nothing, exits 1.
- Already deferred at the current state: reported as `already left`, not an error.
- Writes only that one field, one `db.update` per food.

**Why the marks stay correct without app changes** (same reasoning as BAR-82 §3): a re-pick with the same lookup text merges into the entry (`upsert`), so the mark survives and stops matching when the USDA ID changes. A changed lookup text removes the entry, and with it the mark. A not-found record re-searched after 30 days is rewritten with `upsert` and keeps `deferred_query`; when it turns into a match, the record is removed first, so the food is new again.

### 4.3 `review`

A deferred food (mark matching its current state) gets one more line: `    left for the user by the daily review`. It stays listed: a manual check-up still shows every unsettled food. Approving it, or giving it a recipe, settles it the usual way; the stale mark is harmless.

The module docstring's read-only sentence becomes: every command is read-only except `approve` and `defer`, which write only their mark.

---

## 5. Saving and Conflicts

`make push` uploads `custom_foods.yaml` and `food_db.json` with the conditional `PUT` from BAR-83. It is refused when S3 changed since the pull, which means the user ran `analyze` (or a manual skill run pushed) during the routine's run.

On a refusal the routine tries once more:

1. Keep the new recipes it wrote (copy them out of `custom_foods.yaml` to the scratchpad) and its list of decisions.
2. `make pull`, which replaces every local file.
3. Run `pending` again. Repeat each decision only for foods still listed with the same match; drop the rest (the user or the app settled or changed them meanwhile). Recipes for foods now in another recipe are dropped.
4. Append the kept recipes again, `check`, `make push`.

If the second push is refused too, or any step fails, nothing is saved: the report says so, with the error, and the next day's run reads the same foods again. This is a failed run, so it notifies (§7).

---

## 6. The Report

The session's last message, in this order, empty sections left out:

```
Daily food review 2026-10-22: 3 approved, 2 fixed, 1 left for you.

Fixed (new recipes, assumptions):
- Mango Iced Tea: was "Sweetener, baking" (off 4%) → recipe "Mango iced tea" (black tea 92%, sugar 7%, mango juice 1%), off 6%. Assumed: sweetened bottled iced tea.
- ...

Left for you:
- Protein Riegel XY: logged macros impossible (P+F+C 130 g per 100 g); fix the weight in MacroFactor.

Approved (3): Boiled Eggs, Watermelon, Fresh, Gouda
```

Fixed foods are listed heaviest first, each with the old match, the new recipe and its macro check, and the assumption. Approved foods are listed by name only.

---

## 7. Notification

Sent in two cases, and only these:

- **Changes saved:** at least one food was approved or fixed and the push succeeded, e.g. `Food review: 3 approved, 2 fixed, 1 left for you`.
- **Run failed:** any step stopped the run (setup, `pull`, a tool error, `check` not passing, a push refused twice), e.g. `Food review failed: push refused twice, nothing saved`. A run whose only outcome is foods left for the user is not a failure.

- The routine's own notification setting is off, so the service never decides on its own that a quiet day is "noteworthy".
- The skill sends the push itself with the `PushNotification` tool, one line under 200 characters, as its last step (or as soon as the run fails).
- **To verify in the first test run:** that `PushNotification` reaches the phone from a cloud routine session. If it does not, the fallback is the routine's own push setting on, with a report whose first line makes a quiet day plainly unremarkable; that cannot guarantee silence, so it goes back to the user before it is used.

---

## 8. The Routine and the Skill

### 8.1 The skill

`skills/omni-pilot-daily-review/SKILL.md` in `~/Projects/my-skills`, installed into omni-pilot like the existing one (`npx skills add`), so it is committed under `.agents/skills/` and present in every fresh clone. It contains §2–§7 as a procedure, in the same style as the custom-foods skill, and points to that skill (by its installed path) for recipe building and the tool. Its description says it is for the unattended daily run only, so it does not trigger in a normal conversation.

### 8.2 The routine

- Created with the `schedule` skill (`RemoteTrigger`), Default environment (`env_012yKZUDBbA311mCUfG7tawS`), repository `alperozaydin/omni-pilot` on `main`, a fresh session each day.
- Schedule: `CRON_TZ=Europe/Berlin 0 9 * * *`.
- Prompt: run the `omni-pilot-daily-review` skill.
- Notifications off (§7).

### 8.3 Before the routine is turned on

The backlog is cleared by hand: a check-up with the custom-foods skill in a session with the user, which approves the right matches, writes recipes for the wrong ones and pushes. The routine is created only after that, so its first run reads only what is new. Foods that check-up leaves unsettled stay in `pending`; the first runs read them, 20 at most per run.

### 8.4 Environment prerequisites (the user's, in the claude.ai environment settings)

- `OMNI_PILOT_S3_*` variables: in place (BAR-83).
- Network access: `omni-pilot-data.s3.eu-central-1.amazonaws.com` (in place) and `api.nal.usda.gov` (to check; the BAR-84 comment of 2026-10-20 says it is still needed).
- USDA network secret on `api.nal.usda.gov`: in place.
- `push` from a cloud session was never tested (BAR-83 comment); the first test run tests it.

---

## 9. Testing

**`tests/test_recipe_tool.py`** (against the installed tool, written first):

- `pending` lists a new searched food, a Changed one, a weak one and a not-found record; hides approved, deferred-at-current-state, recipe and skipped foods; prints `Nothing to review.` when empty.
- `pending` lists a deferred food again once its `usda_fdc_id` (or `usda_query`) differs from the mark.
- `defer` writes `deferred_fdc_id` for a match and `deferred_query` for a not-found record, and no other field or entry.
- Each `defer` refusal exits 1, names the food and writes nothing, including for valid names in the same call; an already-deferred food is not an error.
- `review` prints the "left for the user" line for a deferred food only while the mark matches.

**`tests/test_enricher.py`**: a not-found record re-searched after the TTL that still finds nothing keeps `deferred_query`.

**End to end:** `make test` passes. Then one manual run of the routine (`RemoteTrigger run`) against the real bucket, read from its log: pull, review, push and the notification all work in the cloud. Then one more run the same day: it must find nothing and stay silent.

---

## 10. Documentation

`CLAUDE.md`, Key invariants: the review-mark paragraph also names `deferred_fdc_id` and `deferred_query`, written by `recipe_tool.py defer` for the daily review, never read by the app, and kept by the same `upsert` rule. One line under Commands names the routine and its skill.
