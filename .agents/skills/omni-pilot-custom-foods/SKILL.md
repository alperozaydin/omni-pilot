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

## The tool

`scripts/recipe_tool.py` (next to this file) uses the app's own loader, USDA
client and macro check, and never writes to the database. Run it from the
omni-pilot repo root:

```bash
T=<this skill's directory>/scripts/recipe_tool.py
PYTHONPATH=src uv run python $T review                  # every food and its match (check-up)
PYTHONPATH=src uv run python $T flagged                 # weak matches not yet in a recipe
PYTHONPATH=src uv run python $T search feta cheese      # find ingredient IDs
PYTHONPATH=src uv run python $T food 169249 170457      # inspect IDs, list missing nutrients
PYTHONPATH=src uv run python $T check                   # validate + macro-check the real file
PYTHONPATH=src uv run python $T check draft.yaml        # same for a draft
```

Logged macros come from every MacroFactor export in `data/` and in the `data/`
beside the database (iCloud). Add more with `--exports 'path/*.xlsx'`.

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
  the weight; the user fixes it in MacroFactor.
- **Skipped foods that are real food** (e.g. a vegan meat substitute): a recipe
  makes them count.
- **Not in USDA** (its own group in `review`): USDA found nothing for the
  food's lookup text; give it a recipe (see below why editing the mapping
  does not work).
- **US-fortified match:** a German rice, bread or milk matched to an
  "enriched" or "with added vitamin A and vitamin D" entry.
- **Custom foods marked ⚠** in `check`.

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
   is ✓ (at most 25% off, the app's `GOOD_DISTANCE`).
5. **Merge into the real file** (`custom_foods_path` in `config/settings.yaml`,
   usually in iCloud). Keep existing recipes and comments; add under the
   matching section. Run `check` on the real file; it must exit 0.
6. **Report** each food's result (before → after), and every assumption made.

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

## Common mistakes

| Mistake | Fix |
|---|---|
| Stopping at "off > 25%" when the logged macros are impossible (P+F+C well above 100 g per 100 g) | It's a logging error. Write a sensible recipe, note in a comment that it stays flagged until the weight is fixed in MacroFactor, and tell the user. |
| Trusting an ID from memory or a comment | IDs get mislabelled (169248 is iceberg, not green leaf). Always confirm with `food`. |
| A food name with a comma in a `foods: [...]` list | `[Rice, Cooked]` is read as two names; quote it: `["Rice, Cooked"]`. `check` flags the split halves as unknown names. |
| Listing a food in two recipes | The loader rejects it. Extend the existing recipe's `foods` instead. |
| Foods with no logged macros | `check` can't verify them; say so in the report. |
| Changing amounts to fit macros beyond what the dish could contain | Tell the user the recipe needs their real composition instead. |
