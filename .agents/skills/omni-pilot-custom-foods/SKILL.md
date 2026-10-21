---
name: omni-pilot-custom-foods
description: >-
  Use when working in the omni-pilot repo and a food needs a custom recipe in
  custom_foods.yaml: the report lists it under "Low-confidence matches", a custom
  food shows "⚠ macros off N%", a mixed dish (salad, sandwich, pilav, muesli,
  sauce) matched a wrong USDA food, the user asks to add, fix or pin a food
  by FDC ID, or the user asks for a check-up of their foods ("check my foods",
  "look for odd matches").
---

# Omni Pilot Custom Foods

A custom food is a recipe of USDA ingredients, each fixed by its FoodData Central
ID (`fdc_id`) with a relative `amount`. It replaces translation and USDA search
for the logged foods it lists. The app only reads the file; you write it, so every
ID and amount you put there must be checked first. The format and rules are in
the repo: `config/custom_foods.example.yaml` and `src/omni_pilot/custom_foods.py`.

## Before any work

The app's data (database, mappings, recipes, exports) lives in S3; the repo holds
a local working copy. Before anything else, from the omni-pilot repo root, run
`make pull` (`pull --exports`). It replaces the local database and config files
with S3's and fetches missing exports into `data/`. If it fails, stop and show
the user the message; do not work on stale local files.

The same steps work in a Claude cloud session once its environment holds the
`OMNI_PILOT_S3_*` variables.

## The tool

`scripts/recipe_tool.py` (next to this file) uses the app's own loader, USDA
client and macro check. Only `approve` and `defer` write to the database, and
only their mark. Run it from the omni-pilot repo root:

```bash
T=<this skill's directory>/scripts/recipe_tool.py
PYTHONPATH=src uv run python $T review                  # matches not yet approved, every other food (check-up)
PYTHONPATH=src uv run python $T review --all            # same, including approved matches
PYTHONPATH=src uv run python $T approve "Boiled Eggs"   # mark matches you read and found right
PYTHONPATH=src uv run python $T pending                 # foods the daily review has not settled yet
PYTHONPATH=src uv run python $T defer "Boiled Eggs"     # used by the daily review: left for the user
PYTHONPATH=src uv run python $T flagged                 # weak matches not yet in a recipe
PYTHONPATH=src uv run python $T search feta cheese      # find ingredient IDs
PYTHONPATH=src uv run python $T food 169249 170457      # inspect IDs, list missing nutrients
PYTHONPATH=src uv run python $T check                   # validate + macro-check the real file
PYTHONPATH=src uv run python $T check draft.yaml        # same for a draft
```

Logged macros come from every MacroFactor export in `data/`, the local working
copy that `make pull` fills from S3. Add more with `--exports 'path/*.xlsx'`.

## Check-up

The app judges a match only by macros, never by name, so a wrong food with
similar macros is never flagged ("Milch" → buttermilk, "Mango Iced Tea" →
baking sweetener). When the user asks for a check-up, run `review` and `check`,
read every entry, and list for the user, heaviest first:

- **Wrong food:** the match is not the same food as the logged name, whatever
  its macro score (iced tea → sweetener, sauce → hamburger, berry mix → bar).
- **Wrong form:** right food, wrong kind (whole milk → buttermilk, Roma →
  orange tomatoes, raw → cooked).
- **Mixed dish matched to one ingredient** (pizza, udon with chicken, miso soup
  with rice): needs a recipe.
- **Impossible logged macros** (`!!` in the output): a logging error, usually
  a weight typed in wrong; the user fixes it in MacroFactor. A photo-logged
  meal is not one (see "Placeholder weights" below).
- **Skipped foods that are real food** (e.g. a vegan meat substitute): a recipe
  makes them count.
- **Not in USDA** (its own group in `review`): USDA found nothing for the
  food's lookup text; give it a recipe (see below why editing the mapping
  does not work).
- **US-fortified match:** a German rice, bread or milk matched to an
  "enriched" or "with added vitamin A and vitamin D" entry.
- **Custom foods marked ⚠** in `check`.

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
- Foods marked "left for the user by the daily review" are ones the daily
  routine (the `omni-pilot-daily-review` skill) could not settle. Settle them
  as usual: approve, a recipe, or tell the user.
- Report approvals as a count ("approved 97 matches") and mention that
  `review --all` shows them.
- After approving, run `make push` (the rules of Workflow step 6 apply), even
  when no recipe follows: the marks live in the database and a later pull or
  analyze would overwrite them.

Foods with "not in any export" were logged in older exports only; mention
them separately and lower. Give the list first, then fix the ones the user picks
with the workflow below. Fix a wrong single food with a one-ingredient recipe:
editing its lookup text in `food_mappings.yaml` does not work, because the
database's copy of the translation overrides the file for any food already seen.

## Workflow

1. **Collect the foods.** Take them from the user's report ("Low-confidence
   matches", or custom foods marked ⚠), from a check-up, or from `flagged`.
   Copy each name exactly as logged: the tool flags a name that no export or
   database entry contains.
2. **Decide what each food is.** Ask the user for the product, or a label's
   ingredient percentages (EU labels list them, e.g. "Tomaten 55%"). If they
   have none, use a typical recipe, say it is an assumption, and mark it in a
   comment in the file.
3. **Find ingredient IDs** with `search`, then confirm each with `food`.
4. **Write a draft** in the scratchpad and run `check` on it until every food
   is ✓ (at most 25% off, the app's `GOOD_DISTANCE`; at most 15% for a
   placeholder weight, `GOOD_FIT_DISTANCE`).
5. **Merge into the real file** (`custom_foods_path` in `config/settings.yaml`,
   the local working copy kept in sync with S3). Keep existing recipes and
   comments; add under the matching section. Run `check` on the real file; it
   must exit 0.
6. **Push.** After your last change to `custom_foods.yaml` or the review marks,
   run `make push`, before suggesting or running `make analyze`: analyze pulls
   first, so an unpushed edit would be overwritten. If the push is refused
   ("changed in S3 since it was downloaded"), tell the user the message. Do not
   retry or pull by yourself: your edits were based on the old copy, and
   `make pull` would replace them. The user decides whether to pull and redo them.
7. **Report** each food's result (before → after), and every assumption made.

## Choosing ingredients

- **Prefer SR Legacy.** Foundation foods often have 9–15 of 38 nutrients,
  and any missing nutrient lowers the food's coverage in the report. Use
  Foundation only when SR Legacy has no such food.
- **Never a US-fortified entry.** Avoid "enriched" and "with added vitamin A
  and vitamin D": German rice, bread and milk are not fortified, so those
  entries overcount folate, iron, B vitamins and vitamin D. Use the
  "unenriched" / "without added vitamin A and vitamin D" entry.
- **Check coverage with `food`.** An ingredient lacking amino acids (water,
  arugula, dried fruit, sugar) is fine at a small share; avoid it as a large one.
- **No water as an ingredient.** USDA water has no amino acids, so it looks
  like missing data. For a dish denser than its cooked base, mix cooked and dry
  forms (e.g. cooked + dry bulgur) instead of dry + water.
- **Exception: hydrated soy products.** Plant-based meat (soy chunks, vegan
  nuggets) is dry soy protein plus water, and USDA has no hydrated entry: its
  "meatless" foods are far fattier and lack amino acids entirely. Use soy
  protein concentrate/isolate + water (`173647`) + the label's oil and salt.
  Amino acid coverage then reads low, but the amounts are right, because all
  the protein comes from the soy; say so in the report and in a file comment.
- **Match the form eaten:** raw vs cooked, canned drained, fresh Laugenbrezel =
  `Pretzels, soft`, not the hard snack.
- **One ingredient is fine.** A one-ingredient recipe pins a single food to one ID.
- **Share a recipe** only between foods that are the same dish; the macro check
  runs per food anyway.

## Tuning amounts

Fat drives the distance most (9 kcal/g), so adjust oil, butter, cheese, nuts
first; then protein sources; then starch. Keep amounts plausible for the dish.
Carbs are compared both with and without fiber, as EU labels exclude fiber.

## Placeholder weights

MacroFactor's AI photo logging gives a meal a serving weight of 100 g whatever
its size, with the macros of the whole meal. The app spots such an entry (100 g
serving, more protein + fat + carbs than the entry weighs) and fits its grams
to the match: the USDA food, or the recipe. The tool does the same. It shows
these foods as "over N servings with a placeholder weight" with their logged
macros in all, and `check` prints the fitted grams (`→ fitted 700 g, off 4%`).

For such a dish only the proportions of protein, fat and carbs count: tune
the recipe's shares until `check` is ✓, and leave the grams to the app. There
is nothing to fix in MacroFactor.

## Common mistakes

| Mistake | Fix |
|---|---|
| Stopping at "off > 25%" when the logged macros are impossible (`!!`, P+F+C well above 100 g per 100 g) | It's a weight typed in wrong. Write a sensible recipe, note in a comment that it stays flagged until the weight is fixed in MacroFactor, and tell the user. |
| Telling the user to fix the weight of a photo-logged meal ("with a placeholder weight") | The app fits its grams. Tune the recipe's shares to the logged proportions until `check` is ✓. |
| Trusting an ID from memory or a comment | IDs get mislabelled (169248 is iceberg, not green leaf). Always confirm with `food`. |
| A food name with a comma in a `foods: [...]` list | `[Rice, Cooked]` is read as two names; quote it: `["Rice, Cooked"]`. `check` flags the split halves as unknown names. |
| Listing a food in two recipes | The loader rejects it. Extend the existing recipe's `foods` instead. |
| Foods with no logged macros | `check` can't verify them; say so in the report. |
| Changing amounts to fit macros beyond what the dish could contain | Tell the user the recipe needs their real composition instead. |
