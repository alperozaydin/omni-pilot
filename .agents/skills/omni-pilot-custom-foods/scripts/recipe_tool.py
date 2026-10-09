"""Find USDA ingredients and check omni-pilot custom food recipes before an analyze run.

Run from the omni-pilot repo root, where config/settings.yaml lives:

    PYTHONPATH=src uv run python <skill-dir>/scripts/recipe_tool.py <command> ...

Commands:
    review           every logged food and what the app matched it to, for a check-up
    flagged          foods weakly matched by USDA search and not yet in a recipe
    search QUERY     USDA search hits (SR Legacy / Foundation) with macros and coverage
    food ID [ID...]  one or more USDA foods by FDC ID, with the nutrients they lack
    check [FILE]     validate a recipe file with the app's own loader and run the
                     app's macro check against every logged export

Everything is read-only: the food database is opened read-only and ingredients
fetched here are not cached, so the app still fetches and caches them itself.
"""
from __future__ import annotations

import argparse
import glob
import logging
import os
import sys

from tinydb import TinyDB

from omni_pilot.cli import DEFAULT_CUSTOM_FOODS, DEFAULT_DB, DEFAULT_MAPPINGS, DEFAULT_SETTINGS
from omni_pilot.config import load_food_mappings, load_settings, resolve_path
from omni_pilot.custom_foods import CustomFoodsError, load_custom_foods, recipe_macros
from omni_pilot.enricher import (
    USDA_FOODS_TABLE,
    USDA_NUTRIENT_MAP,
    _nutrient_value,
    _usda_macros,
    extract_micros_from_usda,
    fetch_usda_food,
    search_usda,
)
from omni_pilot.matcher import GOOD_DISTANCE, LoggedMacros, logged_macros_per_100g, macro_distance
from omni_pilot.parser import parse_food_log

N_NUTRIENTS = len(USDA_NUTRIENT_MAP)


def _settings() -> dict:
    return load_settings(DEFAULT_SETTINGS)


def _open_db(settings: dict) -> TinyDB | None:
    path = resolve_path("database_path", DEFAULT_DB, settings)
    return TinyDB(path, access_mode="r") if os.path.exists(path) else None


def _export_paths(settings: dict, extra: list[str]) -> list[str]:
    """Every MacroFactor export: the repo's data/ and the data/ beside the database."""
    db_root = os.path.dirname(os.path.dirname(resolve_path("database_path", DEFAULT_DB, settings)))
    patterns = ["data/*.xlsx", os.path.join(db_root, "data", "*.xlsx"), *extra]
    paths = {os.path.realpath(p) for pattern in patterns for p in glob.glob(os.path.expanduser(pattern))}
    return sorted(p for p in paths if "example" not in os.path.basename(p).lower())


def _logged(settings: dict, extra: list[str]) -> tuple[dict[str, LoggedMacros], dict[str, float]]:
    """Logged macros per 100 g and total grams per food, across all exports.

    Exports overlap in time, so an entry already read from an earlier export is
    dropped; repeats within one export are real and kept.
    """
    entries = []
    seen: set[tuple] = set()
    logging.disable(logging.WARNING)  # the parser warns about every "Quick Add" row
    try:
        for path in _export_paths(settings, extra):
            file_entries = parse_food_log(path)
            entries += [e for e in file_entries if tuple(e.values()) not in seen]
            seen |= {tuple(e.values()) for e in file_entries}
    finally:
        logging.disable(logging.NOTSET)
    grams: dict[str, float] = {}
    for entry in entries:
        grams[entry["food_name"]] = grams.get(entry["food_name"], 0.0) + max(entry["total_weight_g"], 0.0)
    return logged_macros_per_100g(entries), grams


def _fmt_logged(m: LoggedMacros) -> str:
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
    return [key for key, value in food["per_100g"].items() if value is None]


def cmd_flagged(args: argparse.Namespace) -> None:
    settings = _settings()
    db = _open_db(settings)
    if db is None:
        sys.exit("No food database found.")
    custom = load_custom_foods(resolve_path("custom_foods_path", DEFAULT_CUSTOM_FOODS, settings))
    logged, grams = _logged(settings, args.exports)
    rows = [
        e for e in db.all()
        if e.get("confidence") == "weak" and e["original_name"] not in custom["by_food"]
    ]
    if not rows:
        print("No weak matches outside custom recipes.")
    for e in sorted(rows, key=lambda e: -grams.get(e["original_name"], 0.0)):
        name = e["original_name"]
        m = logged.get(name)
        distance = e.get("macro_distance")
        print(f"{name}")
        print(f"    logged   : {_fmt_logged(m) if m else 'no logged macros in any export'}"
              f" ({grams.get(name, 0):.0f} g across exports)")
        print(f"    USDA pick: {e.get('usda_name')} (off {'?' if distance is None else f'{distance:.0%}'})")


def _implausible(m: LoggedMacros) -> bool:
    """Logged macros no real food can have: a logging error, usually the weight."""
    return m["protein_g"] + m["fat_g"] + m["carbs_g"] > 100 or m["kcal"] > 900


def cmd_review(args: argparse.Namespace) -> None:
    """Every known food with how the app resolves it, heaviest first, for a read-through."""
    settings = _settings()
    db = _open_db(settings)
    if db is None:
        sys.exit("No food database found.")
    custom = load_custom_foods(resolve_path("custom_foods_path", DEFAULT_CUSTOM_FOODS, settings))
    mappings = load_food_mappings(resolve_path("mappings_path", DEFAULT_MAPPINGS, settings))
    mappings.update({e["german"]: e["english"] for e in db.table("translations")})  # the DB wins, as in the app
    matches = {e["original_name"]: e for e in db.all()}
    logged, grams = _logged(settings, args.exports)

    names = sorted(set(logged) | set(matches) | set(mappings) | set(custom["by_food"]),
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
        m = logged.get(name)
        eaten = f"{grams[name]:.0f} g" if grams.get(name) else "not in any export"
        line = f"{name}  ({eaten}"
        if m:
            line += f"; logged {_fmt_logged(m)}"
        line += ")"
        if check_macros and m and _implausible(m):
            line += "  !! IMPOSSIBLE LOGGED MACROS"
        return line

    print(f"== Searched in USDA ({len(groups['searched'])}): is each match the same food?")
    for name in groups["searched"]:
        e = matches[name]
        distance = e.get("macro_distance")
        off = "" if distance is None else f", off {distance:.0%}"
        print(header(name))
        print(f"    looked up as: {e.get('usda_query')}")
        print(f"    matched to  : {e.get('usda_name')}  [{e.get('confidence', '?')}{off}]")
    if groups["not in usda"]:
        print(f"\n== Not in USDA ({len(groups['not in usda'])}): fix their mapping")
        for name in groups["not in usda"]:
            print(header(name))
            print(f"    looked up as: {matches[name].get('usda_query')}")
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


def cmd_search(args: argparse.Namespace) -> None:
    hits = search_usda(" ".join(args.query), _settings()["usda_api_key"])
    if hits is None:
        sys.exit("USDA search failed.")
    for hit in hits[: args.limit]:
        m = {k: _nutrient_value(hit, n) for k, n in
             (("protein_g", "203"), ("fat_g", "204"), ("carbs_g", "205"), ("fiber_g", "291"))}
        have = sum(_nutrient_value(hit, n) is not None for n in USDA_NUTRIENT_MAP.values())
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
    logged, _ = _logged(settings, args.exports)
    known = set(logged)
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
            m = logged.get(name)
            if m is None:
                print(f"  - {name}: no logged macros in any export, not checked")
                continue
            distance = macro_distance(m, macros)
            if distance is None:
                print(f"  - {name}: recipe lacks a macro, not checked")
                continue
            flag = "⚠" if distance > GOOD_DISTANCE else "✓"
            print(f"  {flag} {name}: logged {_fmt_logged(m)} → off {distance:.0%}")
    print(f"\n{len(custom['recipes'])} recipes, {len(custom['by_food'])} foods, file valid"
          + (f", {problems} problem(s)" if problems else ""))
    if problems:
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    exports_help = "extra MacroFactor export globs for logged macros"

    p = sub.add_parser("review", help="every food and its match, for a check-up")
    p.add_argument("--exports", nargs="*", default=[], help=exports_help)
    p.set_defaults(func=cmd_review)

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

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
