"""CLI entry point for Omni Pilot."""
from __future__ import annotations

import argparse
import logging
import os
import shutil
import sys
from datetime import date

from tinydb import TinyDB

from omni_pilot.analyzer import analyze
from omni_pilot.config import (
    load_reference_ranges,
    load_settings,
    load_supplements,
    resolve_path,
)
from omni_pilot.enricher import enrich_all_foods
from omni_pilot.parser import extract_unique_foods, parse_food_log
from omni_pilot.reporter import generate_html_report, print_terminal_report
from omni_pilot.translator import resolve_and_sync_mappings

logger = logging.getLogger(__name__)

# Default paths relative to project root
DEFAULT_SETTINGS = "config/settings.yaml"
DEFAULT_REF_RANGES = "config/reference_ranges.yaml"
DEFAULT_MAPPINGS = "config/food_mappings.yaml"
DEFAULT_SUPPLEMENTS = "config/supplements.yaml"
DEFAULT_DB = "db/food_db.json"


def cmd_analyze(args: argparse.Namespace) -> None:
    """Run full autonomous pipeline: parse → resolve/translate mappings → enrich → analyze → report."""
    xlsx_path = args.xlsx_file
    settings_path = args.settings or DEFAULT_SETTINGS
    ref_ranges_path = args.ref_ranges or DEFAULT_REF_RANGES
    supplements_path = args.supplements or DEFAULT_SUPPLEMENTS

    # Load config
    settings = load_settings(settings_path)
    ref_ranges = load_reference_ranges(ref_ranges_path)
    supplements = load_supplements(supplements_path)

    db_path = resolve_path("database_path", DEFAULT_DB, settings)
    mappings_path = resolve_path("mappings_path", DEFAULT_MAPPINGS, settings)

    print(f"Database: {db_path}")
    print(f"Mappings: {mappings_path}")

    api_key = settings.get("usda_api_key", "")
    if not api_key:
        print("Error: No USDA API key in settings.yaml.")
        sys.exit(1)

    # 1. Parse Excel Food Log
    print(f"Parsing {xlsx_path}...")
    entries = parse_food_log(xlsx_path)
    print(f"  Found {len(entries)} food entries.")
    food_names = extract_unique_foods(entries)
    print(f"  Found {len(food_names)} unique foods.")

    # 2. Resolve & Sync Mappings (Auto-Translate with Gemini if new foods found)
    mappings = resolve_and_sync_mappings(food_names, db_path, mappings_path, settings)

    # 3. Enrich foods with USDA data
    print("Enriching foods with USDA data...")
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    db = TinyDB(db_path)
    enriched = enrich_all_foods(food_names, mappings, db, api_key)
    resolved = sum(1 for v in enriched.values() if v is not None)
    print(f"  {resolved}/{len(food_names)} foods resolved.")

    # 4. Analyze Micronutrient Intake
    print("Analyzing micronutrient intake...")
    result = analyze(entries, enriched, ref_ranges, supplements=supplements)

    # 5. Report
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
        latest_path = os.path.join(reports_dir, "latest.html")
        if os.path.exists(html_path):
            shutil.copyfile(html_path, latest_path)
        print(f"\nHTML report saved to: {html_path}")
        print(f"  Latest report copy: {latest_path}")


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

    # Analyze command (single autonomous command)
    analyze_parser = subparsers.add_parser(
        "analyze", help="Analyze micronutrient intake"
    )
    analyze_parser.add_argument("xlsx_file", help="Path to MacroFactor xlsx export")
    analyze_parser.add_argument(
        "--html", action="store_true", help="Also generate HTML report"
    )
    analyze_parser.add_argument(
        "--settings", default=None, help=f"Path to settings (default: {DEFAULT_SETTINGS})"
    )
    analyze_parser.add_argument(
        "--ref-ranges",
        default=None,
        help=f"Path to reference ranges (default: {DEFAULT_REF_RANGES})",
    )
    analyze_parser.add_argument(
        "--supplements",
        default=None,
        help=f"Path to supplements (default: {DEFAULT_SUPPLEMENTS})",
    )

    args = parser.parse_args()

    if args.command == "analyze":
        cmd_analyze(args)


if __name__ == "__main__":
    main()
