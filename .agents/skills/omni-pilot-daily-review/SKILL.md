---
name: omni-pilot-daily-review
description: >-
  Use only for the unattended daily food review in the omni-pilot repo, when a
  scheduled routine's prompt names this skill. It reviews new USDA matches with
  nobody present: approves, adds recipes, leaves the rest for the user, saves to
  S3 and reports. In a conversation with the user, use omni-pilot-custom-foods
  instead.
---

# Omni Pilot Daily Review

A scheduled routine runs this every morning in a fresh clone of omni-pilot,
while nobody is watching. It settles each food whose USDA match nobody has
judged yet, saves the result to S3 and ends with a report. **Never ask the
user anything**: decide, or leave the food for the user.

How to judge a match and build a recipe is in the custom-foods skill,
`.agents/skills/omni-pilot-custom-foods/SKILL.md`: read its "Check-up",
"Workflow" (steps 3–5), "Choosing ingredients", "Tuning amounts",
"Placeholder weights" and "Common mistakes" before settling anything. Where
the two skills differ, this one wins: it runs without the user. In particular,
ignore these custom-foods instructions here:

- "Extend the existing recipe's `foods` instead": never; write a new recipe.
- "add under the matching section": append at the end of the file (step 3).
- For impossible logged macros, "Write a sensible recipe": `defer --impossible`.
- Ask the user for the product or label: use a typical recipe, or `defer`.
- Do not retry the push: retry once, as step 5 says.

All commands run from the repo root. The tool:

```bash
T=.agents/skills/omni-pilot-custom-foods/scripts/recipe_tool.py
PYTHONPATH=src uv run python $T <command>
```

## Never

- Ask the user a question, or wait for an answer.
- Run `analyze` or `update-mapping`, or edit `config/food_mappings.yaml`.
- Commit, push or open anything in git. Everything this run changes lives in S3.
- Change an existing recipe in `config/custom_foods.yaml`: no edit, re-tune,
  rename or removal, and no name added to an existing `foods:` list. This
  holds for every recipe already in the file, including those earlier daily
  reviews added. The run only appends new recipes.
- Approve a match to shorten the list.

## 1. Set up

```bash
test -f config/settings.yaml || cp config/settings.example.yaml config/settings.yaml
uv sync
make pull
```

Keep `settings.yaml` as the example has it. The S3 settings come from the
environment's `OMNI_PILOT_S3_*` variables, and the environment's network
secret replaces the placeholder USDA key. If `make pull` fails, the run has
failed (step 6).

## 2. Anything new?

```bash
PYTHONPATH=src uv run python $T pending --limit 20
```

If it prints `Nothing to review.`, end the run with the one line
`Daily food review YYYY-MM-DD: nothing new to review.` as your last message.
No push, no notification.

Otherwise it lists at most the 20 heaviest unsettled foods. A last line
`N of M shown` means the rest wait for tomorrow; say so in the report.

## 3. Settle each listed food

Read the logged name beside the match, as the custom-foods check-up does,
and choose one outcome per food. Keep a list of each decision (food, outcome,
old match, new recipe, assumption) in a scratch file outside the repo; the
report and a retry (step 5) need it.

| Verdict | Do | Change? |
|---|---|---|
| The match is clearly the same food, in the same form | `approve` it | Yes |
| The match is wrong, or USDA found nothing, and a typical recipe fits | A **new** recipe that passes `check` (within 25%; 15% for a placeholder weight) | Yes |
| Impossible logged macros (`!! IMPOSSIBLE LOGGED MACROS`) | `defer --impossible` | No |
| The header says "not in any export" | `approve` if the match is clearly right, else `defer --no-macros`; never a recipe, as `check` cannot verify one | Approve: yes |
| Anything else that cannot be settled: no plausible recipe within the limit, or the name does not say what the food is | `defer` | No |

- **Approve** only when the name and the match are clearly the same food. When
  unsure, a recipe or `defer`. `approve` refuses weak matches; those need a
  recipe or `defer`.
- **`defer` is for foods, never for errors.** A failed `search` or `pending`,
  a `food` or `check` that says "not found" for an ID that `food` showed
  earlier in this run, a timeout or any other USDA or S3 error means the run
  has failed (step 6). Never defer a food because a tool call failed: the mark
  would keep it out of every later run until its match changes.
- **A food listed with "left for the user before (…)"** came back because its
  reason went away (the weight was fixed, an export now has its macros).
  Settle it like any other.
- Approve all right matches in one call, and defer in one call per flag:
  `approve "Food A" "Food B"`, `defer --impossible "Food C"`. If a call
  refuses a name, nothing is written: fix the list and run it again.

### New recipes

- Every recipe is a typical recipe, so its comment says so:
  `# Daily review YYYY-MM-DD: assumed typical recipe (<the assumption in a few words>)`.
- Draft them in a scratch file outside the repo and run `check` on the draft
  until every food is ✓.
- Then append them to the end of `config/custom_foods.yaml`, under the line
  `# --- Added by the daily review ---`. Add that line once, if the file does
  not have it yet; afterwards always append below everything already there.
- A food that is the same dish as an existing recipe gets its own new recipe,
  possibly with the same ingredients. Its comment names the recipe it copies.
  Never add it to the existing recipe's `foods:` list.
- Quote names with a comma: `foods: ["Rice, Cooked"]`.
- Give each new recipe a key (its name, e.g. `mango_iced_tea:`) that is not
  already in the file; if it is taken, add a suffix to your new key. Never
  rename an existing one.

## 4. Check

```bash
PYTHONPATH=src uv run python $T check
```

It must exit 0. If it fails because a recipe this run added is wrong (a food
off the limit, a duplicate key, a name in two recipes), fix that recipe, or
remove it and `defer` its food, then check again. If it fails for any other
reason (a USDA error included), the run has failed (step 6) and nothing is
pushed.

## 5. Save

```bash
make push
```

If S3 refuses the upload ("changed in S3 since it was downloaded"), the user's
`analyze` or a manual skill run wrote S3 during this run. Try once more:

1. Copy the recipes this run appended into the scratch file (they are lost on
   pull).
2. `make pull`.
3. Run `pending` again, without `--limit`. Repeat each decision only for foods
   it still lists with the same match. Drop the rest: the user or the app
   settled or changed them meanwhile. Drop a recipe whose food is now in
   another recipe.
4. Append the kept recipes again, `check`, `make push`.

If the second push is refused too, nothing is saved: the run has failed
(step 6), and tomorrow's run reads the same foods again.

## 6. Notify

Use the `PushNotification` tool (load it with ToolSearch first if only its
name is listed). Send exactly one push, and only in these two cases:

- **Changes saved:** at least one food was approved or fixed, and `make push`
  succeeded. Message: `Food review: 3 approved, 2 fixed, 1 left for you`.
- **Run failed:** any step stopped the run (setup, `make pull`, a tool error,
  `check` not passing, the push refused twice). Message:
  `Food review failed: <step>, nothing saved`. The report (step 7) then names
  the step and quotes the error.

No push for `Nothing to review.`, and none for a run whose only outcome is
foods left for the user. Send the push before writing the report, so the
report stays the session's last message.

## 7. Report

The session's last message, in this order, leaving out empty sections:

```
Daily food review YYYY-MM-DD: 3 approved, 2 fixed, 1 left for you.

Fixed (new recipes, assumptions):
- Mango Iced Tea: was "Sweetener, baking" (off 4%) → recipe mango_iced_tea (black tea 92%, sugar 7%, mango juice 1%), off 6%. Assumed: sweetened bottled iced tea.

Left for you:
- Protein Riegel XY: logged macros impossible (P+F+C 130 g per 100 g); fix the weight in MacroFactor.

Approved (3): Boiled Eggs, Watermelon, Fresh, Gouda

12 more foods wait for tomorrow's run.
```

Fixed foods come heaviest first, each with its old match, the new recipe with
its shares and macro check, and the assumption. Approved foods are listed by
name only. Each food left for the user gets the reason in a few words.
