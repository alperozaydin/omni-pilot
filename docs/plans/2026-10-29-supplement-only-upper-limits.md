# Supplement-Only Upper Limits Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upper limits that cover only supplements (niacin, folate, vitamin E, magnesium) or only retinol (vitamin A) are checked against that intake alone, and every High in the reports says why (BAR-98).

**Architecture:** `reference_ranges.yaml` gains an optional `ul_applies_to` per nutrient, validated in `config.py`. `enricher.py` stores retinol (USDA 319) in `per_100g` and fills it into old cache entries by USDA ID, per run and from a one-time script. `analyzer.py` computes a separate UL figure and the sources behind a High; `reporter.py` marks High rows with `†` and prints one explanation line each.

**Tech Stack:** Python 3.14, TinyDB, PyYAML, requests, Rich, Jinja2, pytest + pytest-mock.

**Spec:** `docs/specs/2026-10-29-supplement-only-upper-limits-design.md`

## Global Constraints

- Branch: `alozay/bar-98-no-high-from-food-for-nutrients-whose-upper-limit-applies` from `main`. The first commit also adds the spec and this plan (untracked on `main`).
- Pure-Python only (a-Shell); no new dependencies.
- Tests never call USDA, Gemini or S3 for real: USDA through `mocker.patch("omni_pilot.enricher._SESSION.get")` or `omni_pilot.enricher.fetch_usda_food`, S3 through `tests.helpers.FakeS3Client` as in `tests/test_cli.py`.
- `ul_applies_to` values: absent, `supplements`, `retinol`. Nothing else.
- Retinol key: `retinol_mcg`, USDA nutrient number `"319"`. It is not a reference nutrient: no entry in `reference_ranges.yaml`, `NUTRIENT_CATEGORIES` or `COMBINED_NUTRIENTS`.
- The supplement source is named `supplement`. Marker: `†` (never `*`, which means partial USDA data).
- Status stays judged on values rounded to one decimal, the UL figure included.
- Backfill writes only `per_100g` (the whole extended dict, since `upsert` merges top-level fields only); every other field of the entry is untouched.
- Nothing is pushed to the real S3 bucket while executing the plan; running the backfill script for real is the user's step after merge.
- Each task updates its own paragraph of `CLAUDE.md` (spec section 7).
- Commit messages: one concise summary line, no body.

## Review Focus

- **A food name with `[...]` in a `†` line** (`Feta [Bio]`): printed literally in the terminal, not eaten as Rich markup. Test in Task 5.
- **A UL figure just above the limit** (sodium 2300.06 → shown 2300.1): the line shows `2,300.1 of 2,300.0`, never two equal numbers. Test in Task 5.
- **`ul_applies_to` on a nutrient whose `ul` is null:** loads fine, the nutrient is never High. Test in Task 1.
- **A supplement as the only source of an all-sources High** (zinc from a pill, no zinc in any food): one source, `supplement`, 100%. Test in Task 4.
- **Vitamin A from foods whose `per_100g` has no `retinol_mcg` key** (backfill failed this run): counts zero, no `KeyError`. Test in Task 4.

---

### Task 1: `ul_applies_to` in the configuration

**Files:**
- Modify: `src/omni_pilot/config.py` (`load_reference_ranges`)
- Modify: `src/omni_pilot/cli.py` (`cmd_analyze`, where it calls `load_reference_ranges`)
- Modify: `config/reference_ranges.yaml`, `config/supplements.example.yaml`, `CLAUDE.md`
- Commit also: `docs/specs/2026-10-29-supplement-only-upper-limits-design.md`, this plan
- Test: `tests/test_config.py`, `tests/test_cli.py`

**Interfaces:**
- Produces (in `omni_pilot.config`): `UL_APPLIES_TO: frozenset[str] = frozenset({"supplements", "retinol"})`; `class ReferenceRangesError(Exception)`; `load_reference_ranges(path)` raises it for an unknown value with the message `"{key}: unknown ul_applies_to '{value}' (use supplements or retinol)"`.

- [ ] **Step 1: Write the failing tests** in `tests/test_config.py` (write the YAML with `tmp_path`, as `test_config.py:43` does)

```python
def test_ul_applies_to_accepts_supplements_and_retinol(tmp_path): ...
    # niacin: ul 35, ul_applies_to supplements; vitamin_a: ul 3000, ul_applies_to retinol
    assert ranges["nutrients"]["b3_niacin_mg"]["ul_applies_to"] == "supplements"

def test_unknown_ul_applies_to_stops_loading_naming_the_nutrient(tmp_path):
    # b3_niacin_mg with ul_applies_to: food
    with pytest.raises(ReferenceRangesError, match=r"b3_niacin_mg: unknown ul_applies_to 'food'"):
        load_reference_ranges(path)

def test_ul_applies_to_with_null_ul_loads(tmp_path):  # Review Focus
    # magnesium_mg with ul: null, ul_applies_to: supplements loads without error

def test_shipped_reference_ranges_mark_the_partial_upper_limits():
    ranges = load_reference_ranges("config/reference_ranges.yaml")
    n = ranges["nutrients"]
    assert {k for k, v in n.items() if v.get("ul_applies_to") == "supplements"} == {
        "b3_niacin_mg", "folate_mcg", "vitamin_e_mg", "magnesium_mg"}
    assert n["vitamin_a_mcg"]["ul_applies_to"] == "retinol"
    assert n["magnesium_mg"]["ul"] == 350
```

In `tests/test_cli.py`: `test_analyze_invalid_reference_ranges_exits_1` — `cmd_analyze` with a ref-ranges file holding `ul_applies_to: food` prints `Error in <path>: b3_niacin_mg: unknown ul_applies_to 'food' …` and exits 1 (`pytest.raises(SystemExit)`, code 1).

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_config.py tests/test_cli.py -k "ul_applies_to or reference_ranges" -v`
Expected: FAIL (`ReferenceRangesError` cannot be imported).

- [ ] **Step 3: Implement**
  - `config.py`: the constant, the exception, the check after `yaml.safe_load` over `data["nutrients"]`.
  - `cli.py`: catch `ReferenceRangesError` around `load_reference_ranges` like the `CustomFoodsError` block: `print(f"Error in {ref_ranges_path}: {e}")`, `sys.exit(1)`.
  - `reference_ranges.yaml`: `ul_applies_to: supplements` on `b3_niacin_mg`, `folate_mcg`, `vitamin_e_mg`, each with a comment naming what the IOM UL covers (spec section 1 table); `magnesium_mg`: `ul: 350`, `ul_applies_to: supplements`, replacing the `null` comment; `vitamin_a_mcg`: `ul_applies_to: retinol` with "UL covers preformed vitamin A (retinol) only; a supplement counts as retinol". Document `ul_applies_to` in the terminology block at the top.
  - `supplements.example.yaml`: the notes on `vitamin_a_mcg`, `folate_mcg` (folic acid as on an EU label, not DFE) and `vitamin_e_mg` from spec section 2.
  - `CLAUDE.md`: mention `ul_applies_to` where `config.py` and `reference_ranges.yaml` are described.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_config.py tests/test_cli.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/omni_pilot/config.py src/omni_pilot/cli.py config/reference_ranges.yaml config/supplements.example.yaml CLAUDE.md tests/test_config.py tests/test_cli.py docs/specs/2026-10-29-supplement-only-upper-limits-design.md docs/plans/2026-10-29-supplement-only-upper-limits.md
git commit -m "feat: mark upper limits that cover only supplements or retinol"
```

---

### Task 2: Store retinol and fill it into old cache entries per run

**Files:**
- Modify: `src/omni_pilot/enricher.py` (`USDA_NUTRIENT_MAP`, `fetch_usda_food`, `get_food_match`, `get_usda_food`)
- Modify: `CLAUDE.md` (enricher paragraph; the `usda_foods` invariant)
- Test: `tests/test_enricher.py`

**Interfaces:**
- Produces (in `omni_pilot.enricher`):
  - `USDA_NUTRIENT_MAP["retinol_mcg"] = "319"`.
  - `FetchMiss = Literal["gone", "rate_limited", "failed"]`.
  - `fetch_usda_food_or_miss(fdc_id: int, api_key: str) -> dict | FetchMiss` — today's `fetch_usda_food` body; 404 → `"gone"`, 429 after `_usda_get`'s retry → `"rate_limited"`, any other failure → `"failed"`. `fetch_usda_food` becomes a wrapper returning `None` for any miss, behaviour unchanged; the "check custom_foods.yaml" error for a 404 moves into the wrapper, so `fetch_usda_food_or_miss` itself logs no advice about recipes.
  - `RETINOL_KEY = "retinol_mcg"`.
  - `fill_retinol(table: Table | TinyDB, cond: QueryLike, per_100g: dict, fdc_id: int | None, api_key: str, label: str) -> tuple[dict, FetchMiss | None]` — when `RETINOL_KEY` is in `per_100g` or `fdc_id` is None: returns `(per_100g, None)` without fetching. Otherwise fetches; on success or `"gone"` writes `table.update({"per_100g": extended}, cond)` (value `None` for gone) and returns `(extended, None | "gone")`; on `"rate_limited"`/`"failed"` writes nothing and returns `(per_100g, miss)`. Logs a warning naming `label` for every miss; for `"gone"` it says the food's USDA ID no longer exists.

- [ ] **Step 1: Write the failing tests** in `tests/test_enricher.py`

```python
def test_nutrient_map_stores_retinol():
    assert USDA_NUTRIENT_MAP["retinol_mcg"] == "319"

class TestFetchUsdaFoodOrMiss:
    def test_404_is_gone(self, mocker): ...           # == "gone"
    def test_429_after_retry_is_rate_limited(self, mocker): ...  # time.sleep patched; == "rate_limited"
    def test_500_and_connection_error_are_failed(self, mocker): ...

class TestRetinolBackfill:
    # cached main-table entry: per_100g without retinol_mcg, usda_fdc_id 171140,
    # reviewed_fdc_id 171140, deferred_reason "impossible", match_version MATCH_VERSION
    def test_reused_match_without_retinol_is_filled_by_usda_id(self, mocker, tmp_path):
        fetch = mocker.patch("omni_pilot.enricher.fetch_usda_food_or_miss",
                             return_value=_usda_hit(171140, nutrients={"319": 4968.0}))
        match = get_food_match("Rinderleber", "beef, liver, raw", logged, db, "k")
        fetch.assert_called_once_with(171140, "k")
        assert match["per_100g"]["retinol_mcg"] == 4968.0
        entry = db.all()[0]
        assert entry["per_100g"]["retinol_mcg"] == 4968.0
        assert {k: v for k, v in entry.items() if k != "per_100g"} == before_without_per_100g
        assert {k: v for k, v in entry["per_100g"].items() if k != "retinol_mcg"} == before["per_100g"]
    def test_failed_fill_writes_nothing_and_the_food_is_still_used(self, mocker, tmp_path): ...
        # returns "failed" -> match returned (not "lookup_failed"), entry unchanged
    def test_gone_id_stores_none_and_is_not_fetched_again(self, mocker, tmp_path): ...
        # second get_food_match makes no fetch
    def test_entry_with_retinol_key_even_none_is_not_fetched(self, mocker, tmp_path): ...
    def test_cached_ingredient_without_retinol_is_filled(self, mocker, tmp_path): ...
        # get_usda_food on a usda_foods entry lacking the key; fetched once, then reused
    def test_new_match_stores_retinol(self, mocker, tmp_path): ...
        # fresh pick from a hit with "319" -> cached per_100g has retinol_mcg
```

Extend `_usda_hit` with a `nutrients: dict[str, float]` argument if it lacks one.

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_enricher.py -k "retinol or OrMiss" -v`
Expected: FAIL (`fetch_usda_food_or_miss` / `retinol_mcg` missing).

- [ ] **Step 3: Implement**
  - The map entry, `FetchMiss`, `fetch_usda_food_or_miss` and the `fetch_usda_food` wrapper.
  - `fill_retinol` as above, extending the dict with `extract_micros_from_usda(raw)[RETINOL_KEY]`.
  - `get_food_match`: on the reuse path (`_is_current` and `_made_the_same_way`), and on the "unverified" fallback, pass the entry through `fill_retinol(db, Food.original_name == food_name, entry["per_100g"], entry.get("usda_fdc_id"), api_key, food_name)` and build the `FoodMatch` from the returned `per_100g`.
  - `get_usda_food`: same on the cached path with `db.table(USDA_FOODS_TABLE)`, `Query().fdc_id == fdc_id`, label `f"FDC {fdc_id}"`.
  - Update the comment above `USDA_FOODS_TABLE` and `CLAUDE.md`: an ingredient entry can gain a missing value; the food it names never changes.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_enricher.py tests/test_custom_foods_integration.py tests/test_recipe_tool.py -v`
Expected: PASS. If `test_recipe_tool.py` counts nutrients (`x/N`), update the expected count for the extra key.

- [ ] **Step 5: Commit**

```bash
git add src/omni_pilot/enricher.py tests/test_enricher.py tests/test_recipe_tool.py CLAUDE.md
git commit -m "feat: store retinol per food and fill it into older cache entries"
```

---

### Task 3: One-time backfill script

**Files:**
- Modify: `src/omni_pilot/enricher.py` (new `backfill_retinol`)
- Create: `scripts/backfill_retinol.py`
- Modify: `CLAUDE.md` (Commands section)
- Test: `tests/test_enricher.py`, `tests/test_backfill_retinol.py`

**Interfaces:**
- Consumes: `fill_retinol`, `fetch_usda_food_or_miss`, `RETINOL_KEY`, `FetchMiss` (Task 2); `cli._s3_context`, `cli._push_or_exit`, `cli.DEFAULT_SETTINGS`, `cli.DEFAULT_DB`, `sync.pull`, `config.load_settings`, `config.resolve_path`.
- Produces (in `omni_pilot.enricher`):
  - `class BackfillReport(TypedDict)`: `filled: int`, `no_usda_id: list[str]`, `failed: list[str]`, `gone: list[str]`, `rate_limited: bool`.
  - `backfill_retinol(db: TinyDB, api_key: str) -> BackfillReport` — every main-table entry with `per_100g` (not-found records skipped) and every `usda_foods` entry lacking the key, main table first. Fetches each FDC ID at most once (keep the fetched result per ID and write it to every entry with that ID; route writes through `fill_retinol` or a shared helper so the write rule stays in one place). After the first `"rate_limited"` it fetches nothing more and returns with `rate_limited=True`. Labels are the food name (main table) or `FDC <id>` (ingredients).
- Produces (`scripts/backfill_retinol.py`): `main() -> None`, run as `PYTHONPATH=src uv run python scripts/backfill_retinol.py`.

- [ ] **Step 1: Write the failing tests**

`tests/test_enricher.py`, class `TestBackfillRetinol`:

```python
def test_fills_both_tables_and_fetches_a_shared_id_once(self, mocker, tmp_path):
    # main "Rinderleber" fdc 171140, usda_foods fdc 171140 and fdc 170457, all without the key
    assert fetch.call_count == 2
    assert report == {"filled": 3, "no_usda_id": [], "failed": [], "gone": [], "rate_limited": False}
def test_skips_not_found_records_and_names_entries_without_id(self, mocker, tmp_path): ...
def test_entries_with_the_key_are_left_alone(self, mocker, tmp_path):
    # db file bytes identical before and after; no fetch
def test_stops_fetching_after_rate_limit_but_keeps_what_it_filled(self, mocker, tmp_path):
    # side_effect=[hit, "rate_limited", hit] -> call_count == 2, filled == 1, rate_limited True
def test_failed_and_gone_are_reported(self, mocker, tmp_path): ...
```

`tests/test_backfill_retinol.py` loads the script with `importlib.util.spec_from_file_location` (as `tests/test_recipe_tool.py` does), runs `main()` from a `tmp_path` working directory with a settings file holding `usda_api_key`, and patches `backfill_retinol`/`fetch_usda_food_or_miss`:

```python
def test_without_s3_fills_local_database_and_prints_summary(...):
    assert "Filled retinol for 2 entries" in out
def test_with_s3_pulls_first_and_pushes_the_database(...):
    # FakeS3Client patched as omni_pilot.cli.S3Client; calls start with gets, end with a put of food_db.json
def test_with_s3_nothing_to_fill_pushes_nothing(...):
    # no ("put", ...) in client.calls
def test_failed_pull_exits_1_and_writes_nothing(...):
def test_missing_usda_key_exits_1(...):
def test_rate_limited_says_to_run_again_later(...):
    assert "run it again later" in out
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_enricher.py -k Backfill tests/test_backfill_retinol.py -v`
Expected: FAIL (`backfill_retinol` missing, script file missing).

- [ ] **Step 3: Implement `backfill_retinol` and the script**

The script's `main()`: load settings from `DEFAULT_SETTINGS`; no `usda_api_key` → `Error: No USDA API key in settings.yaml.`, exit 1; S3 setup like `cmd_analyze` (`S3ConfigError` → exit 1; `None` → `S3: not configured — local files only`; else `pull`, failure → `Error: …`, exit 1); `with TinyDB(db_path) as db: report = backfill_retinol(db, api_key)`; print the summary: `Filled retinol for N entries`, then one line each for entries without a USDA ID, failed fetches (`retried by the next analyze or another run of this script`) and IDs gone, then `USDA is refusing requests (rate limit); run it again later.` when `rate_limited`; with S3, `_push_or_exit(client, files, state_file)` and print what was uploaded. Call `logging.basicConfig(level=logging.WARNING)` and `prefer_ipv4_connections()` like `cli.main`.

Add the run command to the Commands section of `CLAUDE.md` as a one-time step.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_enricher.py tests/test_backfill_retinol.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/omni_pilot/enricher.py scripts/backfill_retinol.py tests/test_enricher.py tests/test_backfill_retinol.py CLAUDE.md
git commit -m "feat: add a one-time script that fills retinol for the whole database"
```

---

### Task 4: Status from the UL figure, and the sources of a High

**Files:**
- Modify: `src/omni_pilot/analyzer.py` (`NutrientResult`, `determine_status`, `analyze`)
- Modify: `CLAUDE.md` (analyzer paragraph; key invariant that `retinol_mcg` is a stored component, not a reference nutrient)
- Test: `tests/test_analyzer.py`, and `tests/test_integration.py` / `tests/test_custom_foods_integration.py` if they assert a status the new rules change

**Interfaces:**
- Consumes: `RETINOL_KEY` (Task 2) — import it, or use the literal `"retinol_mcg"`.
- Produces (in `omni_pilot.analyzer`):
  - `class HighSource(TypedDict)`: `name: str`, `amount: float` (daily amount, unit of the nutrient; retinol for `retinol`).
  - `NutrientResult` gains `ul_applies_to: str | None`, `ul_figure: float | None` (None when `ul` is None), `high_sources: list[HighSource]` (empty unless `status == "high"`; sorted by amount descending, then name; all sources, the reporter cuts to 3).
  - `determine_status(value: float, target: float, ul: float | None, ul_figure: float | None = None) -> str` — compares `ul` with `ul_figure` when given, else with `value`; existing calls and tests unchanged.

- [ ] **Step 1: Write the failing tests** in `tests/test_analyzer.py`, class `TestUpperLimitScope`, with a ref-ranges builder holding niacin (rda 15, ul 35, supplements), magnesium (ai 350, ul 350, supplements), vitamin A (rda 850, ul 3000, retinol), sodium (ai 1500, ul 2300), zinc (rda 14, ul 40):

```python
def test_niacin_from_food_above_the_limit_is_ok(self):       # the 2026-10-23 case: 36.4 mg from food
    assert result["nutrients"]["b3_niacin_mg"]["status"] == "ok"
    assert result["nutrients"]["b3_niacin_mg"]["daily_avg"] == pytest.approx(36.4)
def test_niacin_supplement_above_the_limit_is_high_with_supplement_as_only_source(self):
    # supplements={"b3_niacin_mg": 40.0}, food 5 mg
    assert n["status"] == "high" and n["ul_figure"] == pytest.approx(40.0)
    assert n["high_sources"] == [{"name": "supplement", "amount": 40.0}]
def test_niacin_supplement_below_the_limit_is_ok_even_with_food_above(self): ...  # 20 + 30 food
def test_magnesium_supplement_above_350_is_high(self): ...
def test_supplement_only_limit_still_reports_low(self): ...  # niacin 5 mg food, no supplement -> "deficient"
def test_vitamin_a_from_carrots_is_not_high(self):
    # carrots: vitamin_a_mcg 835, retinol_mcg 0.0, 500 g/day -> RAE 4175 > 3000, retinol 0
def test_vitamin_a_from_liver_is_high_with_retinol_sources(self):
    # liver 4968 retinol/100 g, 70 g/day; eggs 160/100 g, 100 g/day; supplements vitamin_a_mcg 800
    assert n["ul_figure"] == pytest.approx(3477.6 + 160.0 + 800.0)
    assert [s["name"] for s in n["high_sources"]] == ["Rinderleber", "supplement", "Eier"]
def test_vitamin_a_food_without_retinol_key_counts_zero(self):   # Review Focus
    # profile has vitamin_a_mcg but no retinol_mcg key -> ul_figure == 0.0, no KeyError
def test_sodium_from_food_stays_high_with_food_sources(self):
    # bread and feta over two days; amounts are daily averages (total / 2)
def test_supplement_as_only_source_of_an_all_sources_high(self):  # Review Focus
    # zinc: supplements {"zinc_mg": 45.0}, no zinc in food -> sources == [{"name": "supplement", "amount": 45.0}]
def test_custom_food_is_one_source_under_its_logged_name(self):
    # custom food with two parts carrying sodium -> one source named after the logged food
def test_not_high_has_no_sources(self): ...
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_analyzer.py -k UpperLimitScope -v`
Expected: FAIL.

- [ ] **Step 3: Implement**
  - In the entry loop, for each nutrient with a `ul` (and for retinol), add each part's contribution to a per-nutrient, per-logged-food-name total (`food_name`, not the ingredient), alongside `daily_totals`. Retinol: `food_micros.get("retinol_mcg") or 0.0` per part, summed per food and in total; no coverage bookkeeping.
  - After the loop: per-food amount = total / `num_days`. UL figure per spec section 4; `determine_status(round(daily_avg, 1), target, ul, round(ul_figure, 1))`. When `high`: sources = food amounts (retinol amounts for `retinol`; none for `supplements`) plus `supplement` when listed, sorted.
  - `CLAUDE.md` updates.

- [ ] **Step 4: Run the whole suite**

Run: `uv run pytest -v`
Expected: PASS after updating any integration test that asserted a vitamin A, niacin, folate, vitamin E or magnesium status from the shipped config.

- [ ] **Step 5: Commit**

```bash
git add src/omni_pilot/analyzer.py tests/ CLAUDE.md
git commit -m "feat: judge upper limits on the intake they cover and list the sources of a high"
```

---

### Task 5: `†` and the explanation lines in both reports

**Files:**
- Modify: `src/omni_pilot/reporter.py` (terminal table, HTML table, `HTML_TEMPLATE`, `generate_html_report`)
- Modify: `CLAUDE.md` (reporter paragraph)
- Test: `tests/test_reporter.py`

**Interfaces:**
- Consumes: `NutrientResult.ul_applies_to`, `ul_figure`, `high_sources`, `ul`, `unit`, `name` (Task 4).
- Produces: `_high_lines(nutrients: dict) -> list[str]` — one line per `high` nutrient in `NUTRIENT_CATEGORIES` order, without the leading indentation:
  - absent: `† {name}: {fig} of {ul} {unit}/day — {src}, {src}, {src}`
  - `retinol`: `† {name}: retinol {fig} of {ul} {unit}/day — {src}, …`
  - `supplements`: `† {name}: supplement {fig} of {ul} {unit}/day — food isn't counted toward this limit`
  - `{src}` = `{source name} {amount} ({share}%)`, top 3; share = amount / UL figure as a whole percent.
  - `{fig}` and `{ul}` formatted `f"{x:,.1f}"` (like the table's one decimal, so a figure just above the limit never reads as equal); source amounts `f"{x:,.0f}"`, or `f"{x:,.1f}"` below 10.

- [ ] **Step 1: Write the failing tests** in `tests/test_reporter.py` (build results the way the existing floor-marker tests do)

```python
def test_high_lines_for_each_kind():
    assert _high_lines(nutrients) == [
        "† Vitamin A: retinol 4,437.6 of 3,000.0 mcg/day — Rinderleber 3,478 (78%), supplement 800 (18%), Eier 160 (4%)",
        "† Niacin: supplement 40.0 of 35.0 mg/day — food isn't counted toward this limit",
        "† Sodium: 3,100.0 of 2,300.0 mg/day — bread 900 (29%), feta 610 (20%), salami 480 (15%)",
    ]  # in NUTRIENT_CATEGORIES order; check the order there and arrange the expectation to match
def test_figure_just_above_the_limit_reads_above_it():   # Review Focus
    # sodium ul_figure 2300.06 -> line starts "† Sodium: 2,300.1 of 2,300.0 mg/day"
def test_fewer_than_three_sources(): ...
def test_terminal_marks_high_rows_and_prints_lines(capsys, monkeypatch):
    assert "High†" in out and "† Sodium: 3,100.0 of 2,300.0" in out
def test_terminal_prints_bracketed_food_names_literally(capsys, monkeypatch):   # Review Focus
    # source "Feta [Bio]" -> "Feta [Bio]" appears in captured output
def test_no_dagger_without_high(capsys, monkeypatch): ...
def test_html_marks_high_rows_and_prints_footnotes(tmp_path):
    assert "High†" in html and html.count('class="footnote">†') == 1
def test_high_lines_show_with_show_ok_false(capsys, monkeypatch): ...
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_reporter.py -k "high or dagger" -v`
Expected: FAIL.

- [ ] **Step 3: Implement**
  - Status label: append `†` for `high` in both tables (where `*` is appended for floors).
  - Terminal: after the `*` footnote and before the logged-fiber line, print each `_high_lines` line as `Text(f"  {line}", style="dim")` (Text, not markup: food names can contain `[...]`), then a blank line.
  - HTML: pass `high_lines` to the template; render each as `<p class="footnote">{{ line }}</p>` between the `*` footnote and the logged-fiber footnote.
  - `CLAUDE.md` reporter paragraph.

- [ ] **Step 4: Run the whole suite and lint**

Run: `uv run pytest && make lint`
Expected: all tests PASS, ruff reports no errors.

- [ ] **Step 5: Commit**

```bash
git add src/omni_pilot/reporter.py tests/test_reporter.py CLAUDE.md
git commit -m "feat: mark high nutrients and say why under the report tables"
```

---

## After merge (the user's steps)

1. `make sync-iphone` so the iPhone runs the new code.
2. `PYTHONPATH=src uv run python scripts/backfill_retinol.py` once on the Mac (pulls, fills, pushes to S3).
3. `make analyze FILE=…` and check the vitamin A, niacin and sodium rows.
