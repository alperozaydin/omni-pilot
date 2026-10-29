"""Find USDA ingredients and check omni-pilot custom food recipes before an analyze run.

Run from the omni-pilot repo root, where config/settings.yaml lives:

    PYTHONPATH=src uv run python <skill-dir>/scripts/recipe_tool.py <command> ...

Commands:
    review [--all]   every logged food and what the app matched it to, for a check-up;
                     approved matches are hidden unless --all is given
    pending [--limit N]
                     foods the daily review must still read, heaviest first
    flagged          foods weakly matched by USDA search and not yet in a recipe
    search QUERY     USDA search hits (SR Legacy / Foundation) with macros and coverage
    food ID [ID...]  one or more USDA foods by FDC ID, with the nutrients they lack
    check [FILE]     validate a recipe file with the app's own loader and run the
                     app's macro check against every logged export
    approve NAME...  mark each food's USDA match as read and right (the review mark)
    defer NAME... [--impossible | --no-macros]
                     mark each food as left for the user by the daily review

Every command is read-only except `approve` and `defer`, which write only their
mark (`reviewed_fdc_id`; `deferred_fdc_id` / `deferred_query` / `deferred_reason`)
and write nothing when any name is refused. Ingredients
fetched here are not cached, so the app still fetches and caches them itself.
"""
from __future__ import annotations

import argparse
import glob
import logging
import os
import sys

from tinydb import Query, TinyDB
from tinydb.operations import delete

from omni_pilot.cli import DEFAULT_CUSTOM_FOODS, DEFAULT_DB, DEFAULT_MAPPINGS, DEFAULT_SETTINGS
from omni_pilot.config import load_food_mappings, load_settings, resolve_path
from omni_pilot.custom_foods import CustomFoodsError, load_custom_foods, recipe_macros
from omni_pilot.enricher import (
    RETINOL_KEY,
    USDA_FOODS_TABLE,
    USDA_NUTRIENT_MAP,
    _nutrient_value,
    _usda_macros,
    extract_micros_from_usda,
    fetch_usda_food,
    search_usda,
)
from omni_pilot.matcher import (
    GOOD_DISTANCE,
    GOOD_FIT_DISTANCE,
    LoggedFoods,
    LoggedMacros,
    LoggedTotals,
    fit_grams,
    fitted_distance,
    logged_macros_for_matching,
    macro_distance,
)
from omni_pilot.parser import parse_food_log

# Retinol is stored only for vitamin A's upper limit: it is no reference nutrient and takes
# no part in coverage, so it is not counted or listed as missing.
COUNTED_NUTRIENTS = {key: number for key, number in USDA_NUTRIENT_MAP.items() if key != RETINOL_KEY}
N_NUTRIENTS = len(COUNTED_NUTRIENTS)
REVIEWED = "reviewed_fdc_id"  # the USDA ID a reviewer judged to be the logged food
DEFERRED_FDC = "deferred_fdc_id"  # the match the daily review left for the user
DEFERRED_QUERY = "deferred_query"  # the lookup text of a not-found record it left for the user
DEFERRED_REASON = "deferred_reason"  # "impossible" or "no_macros": why it was left, when that can go away


def _is_approved(entry: dict) -> bool:
    """Whether the entry's current match is the one that was approved, and still fits the logged macros."""
    return (
        entry.get(REVIEWED) is not None
        and entry.get(REVIEWED) == entry.get("usda_fdc_id")
        and entry.get("confidence") != "weak"
    )


def _is_deferred(entry: dict) -> bool:
    """Whether the daily review left the food for the user in its current state."""
    if entry.get("not_found"):
        return entry.get(DEFERRED_QUERY) == entry.get("usda_query")
    return entry.get(DEFERRED_FDC) is not None and entry.get(DEFERRED_FDC) == entry.get("usda_fdc_id")


def _settings() -> dict:
    return load_settings(DEFAULT_SETTINGS)


def _open_db(settings: dict) -> TinyDB | None:
    path = resolve_path("database_path", DEFAULT_DB, settings)
    return TinyDB(path, access_mode="r") if os.path.exists(path) else None


def _mappings(settings: dict, db: TinyDB) -> dict[str, str]:
    """Each food's lookup text: food_mappings.yaml, overridden by the database's translations, as in the app."""
    mappings = load_food_mappings(resolve_path("mappings_path", DEFAULT_MAPPINGS, settings))
    mappings.update({e["german"]: e["english"] for e in db.table("translations")})
    return mappings


def _export_paths(settings: dict, extra: list[str]) -> list[str]:
    """Every MacroFactor export: the repo's data/ and the data/ beside the database."""
    db_root = os.path.dirname(os.path.dirname(resolve_path("database_path", DEFAULT_DB, settings)))
    patterns = ["data/*.xlsx", os.path.join(db_root, "data", "*.xlsx"), *extra]
    paths = {os.path.realpath(p) for pattern in patterns for p in glob.glob(os.path.expanduser(pattern))}
    return sorted(p for p in paths if "example" not in os.path.basename(p).lower())


class Logged:
    """What was logged per food across all exports, split the way the app matches it.

    `per_100g` holds foods with real weights (their placeholder entries left out);
    `weight_unknown` the summed macros of foods logged only with MacroFactor's
    100 g placeholder (photo-logged meals), whose grams the app fits to the match.
    `latest` is the same split, from only the newest export that logs each food:
    what MacroFactor holds now, after any fix the user made to an older entry.
    """

    def __init__(self, foods: LoggedFoods, grams: dict[str, float], placeholders: dict[str, int],
                 latest: LoggedFoods):
        self.per_100g = foods["per_100g"]
        self.weight_unknown = foods["weight_unknown"]
        self.grams = grams
        self.placeholders = placeholders
        self.latest = latest

    def __contains__(self, name: str) -> bool:
        return name in self.per_100g or name in self.weight_unknown

    def names(self) -> set[str]:
        return set(self.per_100g) | set(self.weight_unknown)

    def describe(self, name: str) -> str | None:
        """The logged macros as text: per 100 g, or in all for a placeholder weight."""
        if name in self.per_100g:
            return _fmt_logged(self.per_100g[name])
        if name in self.weight_unknown:
            return f"{_fmt_logged(self.weight_unknown[name])} {self.servings(name)}"
        return None

    def servings(self, name: str) -> str:
        n = self.placeholders.get(name, 0)
        return f"over {n} serving{'s' if n != 1 else ''} with a placeholder weight"


def _logged(settings: dict, extra: list[str]) -> Logged:
    """Logged macros and total grams per food, across all exports.

    Exports overlap in time, so an entry already read from an earlier export is
    dropped; repeats within one export are real and kept.
    """
    entries = []
    seen: set[tuple] = set()
    newest: dict[str, tuple[str, list]] = {}  # food → (its newest export's last date, its entries there)
    logging.disable(logging.WARNING)  # the parser warns about every "Quick Add" row
    try:
        for path in _export_paths(settings, extra):
            file_entries = parse_food_log(path)
            entries += [e for e in file_entries if tuple(e.values()) not in seen]
            seen |= {tuple(e.values()) for e in file_entries}
            last_date = max((e["date"] for e in file_entries), default="")
            for name in {e["food_name"] for e in file_entries}:
                if last_date >= newest.get(name, ("", []))[0]:
                    newest[name] = (last_date, [e for e in file_entries if e["food_name"] == name])
    finally:
        logging.disable(logging.NOTSET)
    grams: dict[str, float] = {}
    placeholders: dict[str, int] = {}
    for entry in entries:
        grams[entry["food_name"]] = grams.get(entry["food_name"], 0.0) + max(entry["total_weight_g"], 0.0)
        if entry["weight_is_placeholder"]:
            placeholders[entry["food_name"]] = placeholders.get(entry["food_name"], 0) + 1
    latest = logged_macros_for_matching([e for _, food_entries in newest.values() for e in food_entries])
    return Logged(logged_macros_for_matching(entries), grams, placeholders, latest)


def _fmt_logged(m: LoggedMacros | LoggedTotals) -> str:
    return f"{m['kcal']:.0f} kcal, P {m['protein_g']:.1f}, F {m['fat_g']:.1f}, C {m['carbs_g']:.1f}"


def _fmt_macros(m: dict) -> str:
    def v(key: str) -> str:
        return "?" if m.get(key) is None else f"{m[key]:.1f}"
    return f"P {v('protein_g')}, F {v('fat_g')}, C {v('carbs_g')}, fiber {v('fiber_g')}"


def _ingredient(fdc_id: int, db: TinyDB | None, api_key: str) -> dict | None:
    """A USDA food as the app would store it: from the usda_foods cache, else fetched."""
    if db is not None:
        for entry in db.table(USDA_FOODS_TABLE):
            if entry["fdc_id"] == fdc_id:
                return entry
    raw = fetch_usda_food(fdc_id, api_key)
    if raw is None:
        return None
    return {
        "fdc_id": fdc_id,
        "usda_name": raw["description"],
        "usda_dataset": raw["dataType"],
        "per_100g": extract_micros_from_usda(raw),
        "usda_macros": _usda_macros(raw),
    }


def _missing(food: dict) -> list[str]:
    return [key for key, value in food["per_100g"].items() if value is None and key in COUNTED_NUTRIENTS]


def cmd_flagged(args: argparse.Namespace) -> None:
    settings = _settings()
    db = _open_db(settings)
    if db is None:
        sys.exit("No food database found.")
    custom = load_custom_foods(resolve_path("custom_foods_path", DEFAULT_CUSTOM_FOODS, settings))
    logged = _logged(settings, args.exports)
    rows = [
        e for e in db.all()
        if e.get("confidence") == "weak" and e["original_name"] not in custom["by_food"]
    ]
    if not rows:
        print("No weak matches outside custom recipes.")
    for e in sorted(rows, key=lambda e: -logged.grams.get(e["original_name"], 0.0)):
        name = e["original_name"]
        distance = e.get("macro_distance")
        pick = f"    USDA pick: {e.get('usda_name')} (off {'?' if distance is None else f'{distance:.0%}'})"
        print(f"{name}")
        if name in logged.weight_unknown:
            print(f"    logged   : {logged.describe(name)}")
            grams = fit_grams(logged.weight_unknown[name], e["usda_macros"]) if e.get("usda_macros") else None
            print(pick + ("" if grams is None else f" → fitted {grams:.0f} g"))
            continue
        print(f"    logged   : {logged.describe(name) or 'no logged macros in any export'}"
              f" ({logged.grams.get(name, 0):.0f} g across exports)")
        print(pick)


def _implausible(m: LoggedMacros) -> bool:
    """Logged macros no real food can have: a logging error, usually the weight.

    Only per-100 g figures are checked. A photo-logged meal's 100 g placeholder
    is left out of them (its grams are fitted to the match instead), so what
    is still caught here is a weight typed in wrong.
    """
    return m["protein_g"] + m["fat_g"] + m["carbs_g"] > 100 or m["kcal"] > 900


def _food_header(name: str, logged: Logged, check_macros: bool = True) -> str:
    """A food's first line in `review` and `pending`: what was eaten and logged."""
    if name in logged.weight_unknown:
        servings = logged.servings(name).removeprefix("over ")
        return f"{name}  ({servings}; logged {_fmt_logged(logged.weight_unknown[name])})"
    m = logged.per_100g.get(name)
    grams = logged.grams.get(name)
    eaten = f"{grams:.0f} g" if grams else "not in any export"
    line = f"{name}  ({eaten}"
    if m:
        line += f"; logged {_fmt_logged(m)}"
    line += ")"
    if check_macros and m and _implausible(m):
        line += "  !! IMPOSSIBLE LOGGED MACROS"
    return line


def _print_searched(name: str, entry: dict, is_approved: bool) -> None:
    """The lines under a searched food's header: its lookup text, its match, and its review state."""
    distance = entry.get("macro_distance")
    off = "" if distance is None else f", off {distance:.0%}"
    print(f"    looked up as: {entry.get('usda_query')}")
    print(f"    matched to  : {entry.get('usda_name')}  [{entry.get('confidence', '?')}{off}]"
          + ("  (approved)" if is_approved else ""))
    if entry.get(REVIEWED) is not None and not is_approved:
        same = entry[REVIEWED] == entry.get("usda_fdc_id")
        reason = "the match is now weak" if same else "the match has changed since"
        print(f"    approved before as FDC {entry[REVIEWED]}; {reason}")


def cmd_review(args: argparse.Namespace) -> None:
    """Every known food with how the app resolves it, heaviest first, for a read-through."""
    settings = _settings()
    db = _open_db(settings)
    if db is None:
        sys.exit("No food database found.")
    custom = load_custom_foods(resolve_path("custom_foods_path", DEFAULT_CUSTOM_FOODS, settings))
    mappings = _mappings(settings, db)
    matches = {e["original_name"]: e for e in db.all()}
    logged = _logged(settings, args.exports)
    grams = logged.grams

    names = sorted(logged.names() | set(matches) | set(mappings) | set(custom["by_food"]),
                   key=lambda n: (-grams.get(n, 0.0), n.lower()))
    groups: dict[str, list[str]] = {
        "custom": [], "skipped": [], "searched": [], "not in usda": [], "not looked up": [],
    }
    for name in names:
        if name in custom["by_food"]:
            groups["custom"].append(name)
        elif mappings.get(name, "").strip().lower() == "skip":
            groups["skipped"].append(name)
        elif name in matches:
            # A not-found record (BAR-79) has a query but no match
            groups["not in usda" if matches[name].get("not_found") else "searched"].append(name)
        else:
            groups["not looked up"].append(name)

    def header(name: str, check_macros: bool = True) -> str:
        return _food_header(name, logged, check_macros)

    approved = [name for name in groups["searched"] if _is_approved(matches[name])]
    print(f"== Searched in USDA ({len(groups['searched']) - len(approved)} to review, {len(approved)} approved):"
          " is each match the same food?")
    for name in groups["searched"]:
        is_approved = name in approved
        if is_approved and not args.all:
            continue
        print(header(name))
        _print_searched(name, matches[name], is_approved)
        if _is_deferred(matches[name]):
            print("    left for the user by the daily review")
    if groups["not in usda"]:
        print(f"\n== Not in USDA ({len(groups['not in usda'])}): give them a recipe")
        for name in groups["not in usda"]:
            print(header(name))
            print(f"    looked up as: {matches[name].get('usda_query')}")
            if _is_deferred(matches[name]):
                print("    left for the user by the daily review")
    print(f"\n== Skipped, not counted ({len(groups['skipped'])}): should any of these count?")
    for name in groups["skipped"]:
        print(header(name, check_macros=False))
    print(f"\n== Custom recipes ({len(groups['custom'])}): run `check` for their macro check")
    for name in groups["custom"]:
        print(f"{header(name)}  → {custom['by_food'][name]}")
    if groups["not looked up"]:
        print(f"\n== Not looked up yet ({len(groups['not looked up'])}): the next analyze run will")
        for name in groups["not looked up"]:
            print(header(name))


REASONS = {  # deferred_reason → (why it was left, why it is back)
    "impossible": ("impossible logged macros", "the logged macros are fine now"),
    "no_macros": ("no logged macros", "an export has them now"),
}


def _reason_gone(entry: dict, name: str, logged: Logged) -> bool:
    """Whether the reason a food was left for the user no longer holds."""
    reason = entry.get(DEFERRED_REASON)
    if reason == "impossible":
        # Older exports keep the wrong entries for good, so only the newest one counts.
        if name in logged.latest["per_100g"]:
            return not _implausible(logged.latest["per_100g"][name])
        return name in logged.latest["weight_unknown"]
    if reason == "no_macros":
        return name in logged
    return False


def _is_pending(name: str, entry: dict, custom: dict, mappings: dict[str, str], logged: Logged) -> bool:
    """Whether the daily review must still read the food: nobody has settled it in its current state."""
    if name in custom["by_food"] or mappings.get(name, "").strip().lower() == "skip":
        return False
    if not entry.get("not_found") and (entry.get("usda_fdc_id") is None or _is_approved(entry)):
        return False
    return not _is_deferred(entry) or _reason_gone(entry, name, logged)


def cmd_pending(args: argparse.Namespace) -> None:
    """The foods the daily review must still read, heaviest first, in `review`'s format."""
    settings = _settings()
    db = _open_db(settings)
    if db is None:
        sys.exit("No food database found.")
    custom = load_custom_foods(resolve_path("custom_foods_path", DEFAULT_CUSTOM_FOODS, settings))
    mappings = _mappings(settings, db)
    matches = {e["original_name"]: e for e in db.all()}
    logged = _logged(settings, args.exports)
    names = sorted(
        (n for n, e in matches.items() if _is_pending(n, e, custom, mappings, logged)),
        key=lambda n: (-logged.grams.get(n, 0.0), n.lower()),
    )
    if not names:
        print("Nothing to review.")
        return
    shown = names[: args.limit] if args.limit else names

    def returning(name: str) -> None:
        entry = matches[name]
        if _is_deferred(entry) and _reason_gone(entry, name, logged):
            why, back = REASONS[entry[DEFERRED_REASON]]
            print(f"    left for the user before ({why}); {back}")

    searched = [n for n in shown if not matches[n].get("not_found")]
    not_found = [n for n in shown if matches[n].get("not_found")]
    if searched:
        print(f"== Searched in USDA ({len(searched)}): is each match the same food?")
        for name in searched:
            print(_food_header(name, logged))
            _print_searched(name, matches[name], is_approved=False)
            returning(name)
    if not_found:
        print(f"\n== Not in USDA ({len(not_found)}): give them a recipe")
        for name in not_found:
            print(_food_header(name, logged))
            print(f"    looked up as: {matches[name].get('usda_query')}")
            returning(name)
    if len(shown) < len(names):
        print(f"\n{len(shown)} of {len(names)} shown; the rest wait for the next run")


def cmd_search(args: argparse.Namespace) -> None:
    hits = search_usda(" ".join(args.query), _settings()["usda_api_key"])
    if hits is None:
        sys.exit("USDA search failed.")
    for hit in hits[: args.limit]:
        m = {k: _nutrient_value(hit, n) for k, n in
             (("protein_g", "203"), ("fat_g", "204"), ("carbs_g", "205"), ("fiber_g", "291"))}
        have = sum(_nutrient_value(hit, n) is not None for n in COUNTED_NUTRIENTS.values())
        print(f"{hit['fdcId']:>8}  {hit.get('dataType', ''):<11} {have:>2}/{N_NUTRIENTS}  "
              f"{_fmt_macros(m):<34} {hit['description']}")


def cmd_food(args: argparse.Namespace) -> None:
    settings = _settings()
    db = _open_db(settings)
    for fdc_id in args.fdc_ids:
        food = _ingredient(fdc_id, db, settings["usda_api_key"])
        if food is None:
            print(f"{fdc_id}: not found")
            continue
        missing = _missing(food)
        print(f"{fdc_id}: {food['usda_name']} [{food['usda_dataset']}]")
        print(f"    {_fmt_macros(food['usda_macros'])}; {N_NUTRIENTS - len(missing)}/{N_NUTRIENTS} nutrients")
        if missing:
            print(f"    lacks: {', '.join(missing)}")


def cmd_check(args: argparse.Namespace) -> None:
    settings = _settings()
    path = args.file or resolve_path("custom_foods_path", DEFAULT_CUSTOM_FOODS, settings)
    try:
        custom = load_custom_foods(path)
    except CustomFoodsError as e:
        sys.exit(f"INVALID {path}: {e}")
    if not custom["recipes"]:
        sys.exit(f"No recipes in {path} (missing, empty or comments only).")
    db = _open_db(settings)
    logged = _logged(settings, args.exports)
    known = logged.names()
    if db is not None:
        known |= {e["original_name"] for e in db.all()}
        known |= {e.get("german") for e in db.table("translations")}

    problems = 0
    for recipe in custom["recipes"].values():
        print(f"\n== {recipe['name']}")
        parts = []
        for ingredient in recipe["ingredients"]:
            food = _ingredient(ingredient["fdc_id"], db, settings["usda_api_key"])
            if food is None:
                print(f"  ✗ {ingredient['fdc_id']}: USDA has no such food")
                problems += 1
                continue
            have = N_NUTRIENTS - len(_missing(food))
            print(f"  {ingredient['share']:6.1%}  {food['fdc_id']:>7}  {food['usda_dataset']:<11} "
                  f"{have:>2}/{N_NUTRIENTS}  {food['usda_name']}")
            parts.append((ingredient["share"], food["usda_macros"]))
        if len(parts) < len(recipe["ingredients"]):
            continue
        macros = recipe_macros(parts)
        print(f"  recipe per 100 g: {_fmt_macros(macros)}")
        for name in recipe["foods"]:
            if name not in known:
                print(f"  ✗ {name}: not a logged food name (check spelling against the export)")
                problems += 1
                continue
            if name not in logged:
                print(f"  - {name}: no logged macros in any export, not checked")
                continue
            if name in logged.weight_unknown:
                # As the app does: the grams are fitted to the recipe, so only proportions count.
                totals = logged.weight_unknown[name]
                distance = fitted_distance(totals, macros)
                limit = GOOD_FIT_DISTANCE
                result = None if distance is None else f"fitted {fit_grams(totals, macros):.0f} g, off {distance:.0%}"
            else:
                distance = macro_distance(logged.per_100g[name], macros)
                limit = GOOD_DISTANCE
                result = None if distance is None else f"off {distance:.0%}"
            if result is None:
                print(f"  - {name}: recipe lacks a macro, not checked")
                continue
            flag = "⚠" if distance > limit else "✓"
            print(f"  {flag} {name}: logged {logged.describe(name)} → {result}")
    print(f"\n{len(custom['recipes'])} recipes, {len(custom['by_food'])} foods, file valid"
          + (f", {problems} problem(s)" if problems else ""))
    if problems:
        sys.exit(1)


def _approval_problem(name: str, entry: dict | None, custom: dict, mappings: dict[str, str]) -> str | None:
    """Why a food's match cannot be approved, or None if it can."""
    if name in custom["by_food"]:
        return f"is in recipe {custom['by_food'][name]}; recipes need no approval"
    if mappings.get(name, "").strip().lower() == "skip":
        return "is mapped to skip and not counted"
    if entry is None:
        return "is not a searched food; check the spelling against `review`"
    if entry.get("not_found"):
        return "has no USDA match; it needs a recipe"
    if entry.get("confidence") == "weak":
        distance = entry.get("macro_distance")
        off = "?" if distance is None else f"{distance:.0%}"
        return f"is a weak match (macros off {off}); give it a recipe or leave it for the user"
    if entry.get("usda_fdc_id") is None:
        return "has no USDA ID yet; run analyze first"
    return None


def cmd_approve(args: argparse.Namespace) -> None:
    """Mark each named food's current USDA match as read and right. All names or none."""
    settings = _settings()
    path = resolve_path("database_path", DEFAULT_DB, settings)
    if not os.path.exists(path):
        sys.exit("No food database found.")
    custom = load_custom_foods(resolve_path("custom_foods_path", DEFAULT_CUSTOM_FOODS, settings))
    names = list(dict.fromkeys(args.foods))
    with TinyDB(path) as db:
        mappings = _mappings(settings, db)
        matches = {e["original_name"]: e for e in db.all()}
        problems = [
            f"✗ {name}: {problem}" for name in names
            if (problem := _approval_problem(name, matches.get(name), custom, mappings))
        ]
        if problems:
            print("\n".join(problems))
            print("Nothing approved.")
            sys.exit(1)
        approved = 0
        for name in names:
            entry = matches[name]
            match = f"{name} → {entry['usda_name']} ({entry['usda_fdc_id']})"
            if _is_approved(entry):
                print(f"already approved  {match}")
                continue
            db.update({REVIEWED: entry["usda_fdc_id"]}, Query().original_name == name)
            print(f"approved  {match}")
            approved += 1
    print(f"{approved} approved")


def _defer_problem(name: str, entry: dict | None, custom: dict, mappings: dict[str, str]) -> str | None:
    """Why a food cannot be left for the user, or None if it can."""
    if name in custom["by_food"]:
        return f"is in recipe {custom['by_food'][name]}; recipes need no approval"
    if mappings.get(name, "").strip().lower() == "skip":
        return "is mapped to skip and not counted"
    if entry is None:
        return "is not a searched food; check the spelling against `review`"
    if entry.get("not_found"):
        return None
    if _is_approved(entry):
        return "is approved; nothing to leave"
    if entry.get("usda_fdc_id") is None:
        return "has no USDA ID yet; run analyze first"
    return None


def cmd_defer(args: argparse.Namespace) -> None:
    """Mark each named food as left for the user at its current match. All names or none."""
    settings = _settings()
    path = resolve_path("database_path", DEFAULT_DB, settings)
    if not os.path.exists(path):
        sys.exit("No food database found.")
    custom = load_custom_foods(resolve_path("custom_foods_path", DEFAULT_CUSTOM_FOODS, settings))
    reason = "impossible" if args.impossible else "no_macros" if args.no_macros else None
    names = list(dict.fromkeys(args.foods))
    with TinyDB(path) as db:
        mappings = _mappings(settings, db)
        matches = {e["original_name"]: e for e in db.all()}
        problems = [
            f"✗ {name}: {problem}" for name in names
            if (problem := _defer_problem(name, matches.get(name), custom, mappings))
        ]
        if problems:
            print("\n".join(problems))
            print("Nothing written.")
            sys.exit(1)
        left = 0
        for name in names:
            entry = matches[name]
            if entry.get("not_found"):
                mark = {DEFERRED_QUERY: entry["usda_query"]}
                what = f"{name} (not in USDA: {entry['usda_query']})"
            else:
                mark = {DEFERRED_FDC: entry["usda_fdc_id"]}
                what = f"{name} → {entry['usda_name']} ({entry['usda_fdc_id']})"
            if _is_deferred(entry) and entry.get(DEFERRED_REASON) == reason:
                print(f"already left  {what}")
                continue
            food = Query().original_name == name
            db.update({**mark, **({DEFERRED_REASON: reason} if reason else {})}, food)
            if reason is None and DEFERRED_REASON in entry:
                db.update(delete(DEFERRED_REASON), food)
            print(f"left  {what}")
            left += 1
    print(f"{left} left")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    exports_help = "extra MacroFactor export globs for logged macros"

    p = sub.add_parser("review", help="foods and their matches not yet approved, for a check-up")
    p.add_argument("--exports", nargs="*", default=[], help=exports_help)
    p.add_argument("--all", action="store_true", help="also list matches already approved")
    p.set_defaults(func=cmd_review)

    p = sub.add_parser("pending", help="foods the daily review must still read, heaviest first")
    p.add_argument("--exports", nargs="*", default=[], help=exports_help)
    p.add_argument("--limit", type=int, help="show at most N foods")
    p.set_defaults(func=cmd_pending)

    p = sub.add_parser("flagged", help="weak USDA matches not yet in a recipe")
    p.add_argument("--exports", nargs="*", default=[], help=exports_help)
    p.set_defaults(func=cmd_flagged)

    p = sub.add_parser("search", help="search USDA SR Legacy / Foundation")
    p.add_argument("query", nargs="+")
    p.add_argument("--limit", type=int, default=10)
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("food", help="show USDA foods by FDC ID")
    p.add_argument("fdc_ids", nargs="+", type=int)
    p.set_defaults(func=cmd_food)

    p = sub.add_parser("check", help="validate recipes and run the macro check")
    p.add_argument("file", nargs="?", help="recipe file (default: custom_foods_path)")
    p.add_argument("--exports", nargs="*", default=[], help=exports_help)
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("approve", help="mark foods whose USDA match was read and is the right food")
    p.add_argument("foods", nargs="+", help="food names exactly as logged")
    p.set_defaults(func=cmd_approve)

    p = sub.add_parser("defer", help="mark foods the daily review leaves for the user")
    p.add_argument("foods", nargs="+", help="food names exactly as logged")
    reasons = p.add_mutually_exclusive_group()
    reasons.add_argument("--impossible", action="store_true",
                         help="its logged macros are impossible; it comes back once they are fixed")
    reasons.add_argument("--no-macros", action="store_true",
                         help="no export has its logged macros; it comes back once one has")
    p.set_defaults(func=cmd_defer)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
