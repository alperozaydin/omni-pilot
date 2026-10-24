# Technical Specification: A Command to Change a Food's Lookup Text

**Document Version:** 1.0
**Date:** 2026-10-22
**Status:** Draft
**Fixes:** BAR-44 (manual corrections to `food_mappings.yaml` are silently reverted on the next run)
**Builds on:** BAR-83 (S3 storage), the custom-foods recipes

---

## 1. Executive Summary & Objective

### The Problem

Each logged food has a lookup text: the English, USDA-searchable query that Gemini wrote for it, or `skip`. It is stored twice: in the `translations` table of `db/food_db.json` and in `config/food_mappings.yaml`.

`translator.resolve_and_sync_mappings` merges the two with the database winning (`known_mappings = {**existing_mappings, **db_mappings}`, `translator.py:145`), then rewrites the YAML from the result (`translator.py:198`). So a hand edit of the YAML for any food the database already has is overwritten on the next `analyze` and never used. With S3 that is worse: the edit is pulled, reverted and pushed back, so it is gone from S3 too.

There is no other way to change a stored translation short of editing the TinyDB JSON by hand. The workarounds today:

- **Wrong food matched:** a one-ingredient custom recipe, which overrides any mapping. It works, but it is heavier than the problem when only the translation is wrong.
- **Food that should be excluded** (a supplement, a drink Gemini translated): none. A recipe cannot express `skip`.

The CLI also still tells the user to "edit `food_mappings.yaml` and fill in USDA-searchable names" (`translator.py:182`), which is untrue for every food the database already has.

### The Solution

A command that sets a food's lookup text in both places at once, going through S3 the way `analyze` does:

```bash
make update-mapping FOOD="Haferflocken" QUERY="cereals, oats, dry"
make update-mapping FOOD="Magnesium Kapsel" QUERY=skip
# without make (iPhone / a-Shell):
python -m omni_pilot.cli update-mapping "Haferflocken" "cereals, oats, dry"
```

The YAML becomes what the code already treats it as: a copy the app writes. Hand edits stay unsupported, and the CLI, the YAML header and the docs stop suggesting them.

### Decisions Made During Design

- **An explicit command, not detecting hand edits of the YAML.** Comparing the YAML against what the app last wrote would make edits "just work", but it has to guess whether a changed line is the user's edit or a stale copy from another device. A command states the intent, so nothing is guessed. (Agreed with the user.)
- **The database keeps precedence over the YAML in `resolve_and_sync_mappings`.** The command writes both, so the precedence never decides anything for a food it changed. The existing behaviour and its tests stay.
- **The command syncs with S3 itself** (pull before, push after), so the user never has to remember pull → edit → push. (Agreed with the user.)
- **`skip` is accepted** as a value. (Agreed with the user.)
- **`make` takes `FOOD=` and `QUERY=`** rather than positional arguments, like `make analyze FILE=`: `make` cannot pass arguments with spaces or umlauts reliably. (Agreed with the user.)

## 2. Behaviour

`update-mapping FOOD QUERY`, with `--settings` like the other subcommands.

1. **Normalise the input.** Strip both values. An empty `FOOD` or `QUERY` exits 1 with a usage message. A `QUERY` equal to `skip` in any case is stored as `skip`.
2. **Pull**, only when S3 is configured, like `make pull`: `_s3_context`, then `sync.pull` and `sync.pull_exports` (only the exports missing from `data/` are downloaded). An incomplete S3 setup (`S3ConfigError`) or a failed pull exits 1 with the error and writes nothing. Without S3 configured, it prints `S3: not configured — local files only` and works on the local files.
3. **Check the name.** The food must be known. The cheap sources come first: a row in the `translations` table, an entry in the main USDA cache table (`original_name`), a key in `food_mappings.yaml`, or a food listed in `custom_foods.yaml`. Only if none has it are the exports in `data/` read (`parse_food_log` / `extract_unique_foods` on each `.xlsx`). The exports are needed for foods stored nowhere else: a food Gemini gave no translation for (`untranslated` is kept out of both stores and never searched, and this command is the natural fix when Gemini keeps failing on it), and a food whose only lookup failed without a Gemini key (failures are never cached). *(My call, after the builder check.)* An unknown name exits 1 with `Unknown food "…"` plus up to three close names (`difflib.get_close_matches` over all the sources above), and writes nothing. Names are compared exactly, since mapping keys are exact.
4. **Compare.** If the stored lookup text (the database's, else the YAML's, as `resolve_and_sync_mappings` resolves it) already equals the new one, print `"…" is already "…"`, write nothing and push nothing; exit 0.
5. **Warn about a custom recipe.** If the food is listed in `custom_foods.yaml`, still write the mapping but print that the recipe takes precedence, so the new lookup text has no effect while the recipe lists the food. An invalid `custom_foods.yaml` (`CustomFoodsError`) only skips this check, with a note. It does not stop the command, whose job does not depend on that file.
6. **Write both places and drop the old match.** Upsert `{"german": FOOD, "english": QUERY}` in the `translations` table, and set the food in `food_mappings.yaml`, rewritten in the usual sorted form with the new header (section 3). Remove the food's entry from the main USDA cache table, whether a match or a not-found record. Print `"FOOD": "old" → "new"`, or `"FOOD": "new"` (set) when it had no lookup text.
7. **Push**, only when S3 is configured (`sync.push`, the data files that changed). On failure it prints and exits 1 like `cmd_push`, including the conflict hint ("Run `make pull` …, redo them, then push again."). The local files keep the change in that case. On success it prints what was uploaded.

**Why the old match is removed now** *(my call, after the builder check)*: the next `analyze` would remove it anyway, since the entry's `usda_query` no longer matches the lookup text (`enricher.py:454`). But the daily review reads the cache every morning and never runs `analyze`. `recipe_tool.py`'s `_is_pending` does not compare an entry's `usda_query` with the current lookup text, so until the user's next `analyze`, the routine would keep reviewing the old match. It could even append a recipe for the food, which would override the new lookup text, and the routine never changes an existing recipe. Removing the entry takes the food out of `pending` until `analyze` has matched it under the new text. The review mark (`reviewed_fdc_id`) and the daily review's `deferred_*` marks go with the entry, which is right: they judged the old match. A food set to `skip` loses its entry too and is never looked up again. The `usda_foods` table (recipe ingredients) is not touched.

**Nothing is analysed.** The new lookup text takes effect on the next `analyze`. The closing line says so.

## 3. Messages, Header and Docs

- **`translator.py:182`**, shown when there is no Gemini key: it points to `make update-mapping FOOD=… QUERY=…` instead of editing the YAML.
- **The `food_mappings.yaml` header** written by `generate_food_mappings` (`parser.py:230`) still names a long-gone command (`python -m omni_pilot import`). It becomes: the file is written by the app, hand edits are overwritten, use `make update-mapping` to change a lookup text, and `skip` excludes a food. The header is only rewritten when the mappings change. That is fine, since the command changes them. `config/food_mappings.example.yaml` gets the same note.
- **`Makefile`**: an `update-mapping` target that fails with a usage line when `FOOD` or `QUERY` is unset (like `analyze`), and a line in `help`.
- **`README.md`**: line 153 lists `food_mappings.yaml` among the files to edit by hand around pull/push. It now lists only `custom_foods.yaml` there and names the command for lookup texts.
- **`CLAUDE.md`**: the command in Commands; in the `translator.py` description and the TinyDB invariant, the YAML is a copy the app writes and changes go through `update-mapping`. The line on hand edits ("a hand edit of `food_mappings.yaml` or `custom_foods.yaml` means pull, edit, push") keeps only `custom_foods.yaml`.
- **The custom-foods skill** (`SKILL.md` around line 107): "editing its lookup text does not work" becomes a choice. Use `update-mapping` when the translation itself is wrong or the food should be skipped. Use a one-ingredient recipe when a specific FDC ID must be pinned, since a lookup text still goes through USDA search and the matcher. Because the command pulls first, which replaces unpushed local changes, the skill runs it only after pushing its other edits, or before making them.
- **The daily-review skill** stays as it is: it never changes mappings. Its "don't" list gains `update-mapping` next to "edit `config/food_mappings.yaml`".
- **Where the skill edits go:** both skills come from the `alperozaydin/my-skills` repo through `npx skills`, and the installed copy in this repo is never edited by hand. The skill changes are a my-skills PR, installed here from its branch (`npx skills add "alperozaydin/my-skills#<branch>" --skill <name> -y`). Merge order: the my-skills PR, then `npx skills add alperozaydin/my-skills --skill <name> -y` again (drops the branch ref from `skills-lock.json`) and commit, then the omni-pilot PR.

## 4. Out of Scope

- Removing a mapping so Gemini translates the food again.
- Changing several foods in one call.
- Detecting or accepting hand edits of the YAML.
- Changing the DB-over-YAML precedence in `resolve_and_sync_mappings`.
- Running `analyze` after the change.

## 5. Verification

Tests, without live Gemini, USDA or S3 calls (S3 through the same fakes the existing `cli`/`sync` tests use):

- **BAR-44 regression:** the database holds a wrong translation and the YAML agrees. After `update-mapping`, both hold the new text, and a following `resolve_and_sync_mappings` returns the new text and leaves the YAML unchanged.
- `skip` in any case is stored as `skip`, and the enricher then treats the food as skipped.
- An unknown name exits 1, suggests close names and writes nothing (database and YAML byte-for-byte unchanged).
- A food known only from the USDA cache (no translation row, e.g. analysed without a Gemini key) is accepted. So are a food known only from `custom_foods.yaml` and a food known only from an export in `data/` (one Gemini left untranslated). Exports are not read when a cheap source already has the name.
- The food's main-table cache entry (match or not-found record) is gone after the change, `usda_foods` is untouched, and `recipe_tool.py pending` no longer lists the food.
- The same value again writes nothing and pushes nothing.
- A food with a custom recipe is written, with the warning. An invalid `custom_foods.yaml` does not stop the command.
- With S3: pull (data files and missing exports) happens before the name check, and push after the write. A failed pull exits 1 with nothing written. A push conflict exits 1 with the `make pull` hint and keeps the local change.
- Without S3: local files only, no S3 calls.
- The Makefile target refuses to run without `FOOD` or `QUERY`.

By hand: run `make update-mapping` on a real food against S3, then `make analyze`. The food is searched again under the new text, the YAML shows it, and `make pull` on a second checkout gets it.

## 6. Left to the Builder

- Where the core function lives (`translator.py` is the natural home, next to the code that owns the `translations` table), and its name and signature.
- Reusing `generate_food_mappings` to write the YAML, or a small dedicated writer, as long as the result is the same sorted file with the new header.
- The exact wording of the messages, in the style of the existing CLI output.
