"""CLI entry point for Omni Pilot."""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import date

from tinydb import TinyDB, Query

from omni_pilot.config import (
    load_settings,
    load_reference_ranges,
    load_food_mappings,
    load_supplements,
)
from omni_pilot.parser import parse_food_log, extract_unique_foods, generate_food_mappings
from omni_pilot.enricher import enrich_all_foods
from omni_pilot.analyzer import analyze
from omni_pilot.reporter import print_terminal_report, generate_html_report

logger = logging.getLogger(__name__)

# Default paths relative to project root
DEFAULT_SETTINGS = "config/settings.yaml"
DEFAULT_REF_RANGES = "config/reference_ranges.yaml"
DEFAULT_MAPPINGS = "config/food_mappings.yaml"
DEFAULT_SUPPLEMENTS = "config/supplements.yaml"
DEFAULT_DB = "db/food_db.json"


def cmd_import(args: argparse.Namespace) -> None:
    """Import MacroFactor xlsx and generate food_mappings.yaml."""
    xlsx_path = args.xlsx_file
    mappings_output = args.mappings_output or DEFAULT_MAPPINGS

    print(f"Parsing {xlsx_path}...")
    entries = parse_food_log(xlsx_path)
    print(f"  Found {len(entries)} food entries.")

    foods = extract_unique_foods(entries)
    print(f"  Found {len(foods)} unique foods.")

    # Load existing mappings if present
    existing_mappings = load_food_mappings(mappings_output)

    # Load translations from TinyDB
    db_path = args.db or DEFAULT_DB
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    db = TinyDB(db_path)
    translations_table = db.table("translations")
    db_mappings = {doc["german"]: doc["english"] for doc in translations_table.all()}
    
    # Merge mappings: DB takes precedence, then existing YAML
    known_mappings = {**existing_mappings, **db_mappings}

    # Generate/merge mappings
    generate_food_mappings(foods, known_mappings, mappings_output)

    new_foods = [f for f in foods if f not in known_mappings]
    print(f"\nMappings written to: {mappings_output}")
    if new_foods:
        print(f"  {len(new_foods)} new foods need English equivalents.")
        print("  Edit the file and fill in USDA-searchable names.")
    else:
        print("  All foods already mapped.")


def cmd_analyze(args: argparse.Namespace) -> None:
    """Run full analysis pipeline: parse → enrich → analyze → report."""
    xlsx_path = args.xlsx_file
    settings_path = args.settings or DEFAULT_SETTINGS
    ref_ranges_path = args.ref_ranges or DEFAULT_REF_RANGES
    mappings_path = args.mappings or DEFAULT_MAPPINGS
    supplements_path = args.supplements or DEFAULT_SUPPLEMENTS
    db_path = args.db or DEFAULT_DB

    # Load config
    settings = load_settings(settings_path)
    ref_ranges = load_reference_ranges(ref_ranges_path)
    mappings = load_food_mappings(mappings_path)
    supplements = load_supplements(supplements_path)

    if not mappings:
        print("Error: No food mappings found. Run 'import' first.")
        sys.exit(1)

    api_key = settings.get("usda_api_key", "")
    if not api_key:
        print("Error: No USDA API key in settings.yaml.")
        sys.exit(1)

    # Parse
    print(f"Parsing {xlsx_path}...")
    entries = parse_food_log(xlsx_path)
    print(f"  Found {len(entries)} food entries.")

    # Enrich
    print("Enriching foods with USDA data...")
    food_names = extract_unique_foods(entries)
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    db = TinyDB(db_path)
    
    # Save all non-empty mappings to database
    translations_table = db.table("translations")
    TranslationQuery = Query()
    saved_count = 0
    for german, english in mappings.items():
        if english and str(english).strip():
            translations_table.upsert(
                {"german": german, "english": str(english).strip()},
                TranslationQuery.german == german
            )
            saved_count += 1
    print(f"  Saved/Updated {saved_count} mappings in the database.")

    enriched = enrich_all_foods(food_names, mappings, db, api_key)

    resolved = sum(1 for v in enriched.values() if v is not None)
    print(f"  {resolved}/{len(food_names)} foods resolved.")

    # Analyze
    print("Analyzing micronutrient intake...")
    result = analyze(entries, enriched, ref_ranges, supplements=supplements)

    # Report
    print()
    print_terminal_report(result, settings)

    # Optional HTML report
    if args.html:
        reports_dir = "reports"
        os.makedirs(reports_dir, exist_ok=True)
        html_path = os.path.join(
            reports_dir, f"micronutrient-report-{date.today()}.html"
        )
        generate_html_report(result, html_path)
        print(f"\nHTML report saved to: {html_path}")


def main() -> None:
    """Main CLI entry point."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    parser = argparse.ArgumentParser(
        prog="omni_pilot",
        description="Omni Pilot — Nutrition & Workout Analysis",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Import command
    import_parser = subparsers.add_parser(
        "import", help="Import MacroFactor xlsx and generate food mappings"
    )
    import_parser.add_argument("xlsx_file", help="Path to MacroFactor xlsx export")
    import_parser.add_argument(
        "--mappings-output", default=None,
        help=f"Output path for food_mappings.yaml (default: {DEFAULT_MAPPINGS})",
    )
    import_parser.add_argument("--db", default=None, help=f"Path to TinyDB database (default: {DEFAULT_DB})")

    # Analyze command
    analyze_parser = subparsers.add_parser(
        "analyze", help="Analyze micronutrient intake"
    )
    analyze_parser.add_argument("xlsx_file", help="Path to MacroFactor xlsx export")
    analyze_parser.add_argument(
        "--html", action="store_true", help="Also generate HTML report"
    )
    analyze_parser.add_argument("--settings", default=None)
    analyze_parser.add_argument("--ref-ranges", default=None)
    analyze_parser.add_argument("--mappings", default=None)
    analyze_parser.add_argument("--supplements", default=None)
    analyze_parser.add_argument("--db", default=None)

    args = parser.parse_args()

    if args.command == "import":
        cmd_import(args)
    elif args.command == "analyze":
        cmd_analyze(args)

if __name__ == "__main__":
    main()
