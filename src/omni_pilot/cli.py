"""CLI entry point for Omni Pilot."""
from __future__ import annotations

import argparse
import logging
import os
import shutil
import sys
from datetime import date

from rich.console import Console
from rich.text import Text
from tinydb import TinyDB

from omni_pilot.analyzer import analyze
from omni_pilot.config import (
    S3ConfigError,
    load_reference_ranges,
    load_s3_config,
    load_settings,
    load_supplements,
    resolve_path,
)
from omni_pilot.custom_foods import CustomFoodsError, load_custom_foods
from omni_pilot.enricher import count_outdated_matches, enrich_all_foods
from omni_pilot.matcher import logged_macros_per_100g
from omni_pilot.net import prefer_ipv4_connections
from omni_pilot.parser import check_last_day, extract_unique_foods, parse_daily_expenditure, parse_food_log
from omni_pilot.reporter import generate_html_report, print_terminal_report
from omni_pilot.s3 import S3Client, S3Error
from omni_pilot.sync import (
    EXPORTS_PREFIX,
    DataFile,
    SyncError,
    data_files,
    pull,
    pull_exports,
    push,
    state_path,
    upload_export,
)
from omni_pilot.translator import resolve_and_sync_mappings

logger = logging.getLogger(__name__)

# Default paths relative to project root
DEFAULT_SETTINGS = "config/settings.yaml"
DEFAULT_REF_RANGES = "config/reference_ranges.yaml"
DEFAULT_MAPPINGS = "config/food_mappings.yaml"
DEFAULT_SUPPLEMENTS = "config/supplements.yaml"
DEFAULT_DB = "db/food_db.json"
DEFAULT_CUSTOM_FOODS = "config/custom_foods.yaml"


def _s3_context(settings: dict) -> tuple[S3Client, list[DataFile], str] | None:
    """The S3 client, the data files and the sync state file; None when S3 is not configured."""
    config = load_s3_config(settings)
    if config is None:
        return None
    db_path = resolve_path("database_path", DEFAULT_DB, settings)
    mappings_path = resolve_path("mappings_path", DEFAULT_MAPPINGS, settings)
    custom_foods_path = resolve_path("custom_foods_path", DEFAULT_CUSTOM_FOODS, settings)
    return S3Client(config), data_files(db_path, mappings_path, custom_foods_path), state_path(db_path)


def _require_s3_context(settings: dict) -> tuple[S3Client, list[DataFile], str]:
    """Like _s3_context, but a missing or broken S3 setup ends the command with exit code 1."""
    try:
        context = _s3_context(settings)
    except S3ConfigError as e:
        print(f"Error: {e}")
        sys.exit(1)
    if context is None:
        print("Error: S3 is not configured (s3 in settings.yaml or OMNI_PILOT_S3_* variables).")
        sys.exit(1)
    return context


def cmd_pull(args: argparse.Namespace) -> None:
    """Download the data files (and with --exports the MacroFactor exports) from S3."""
    settings = load_settings(args.settings or DEFAULT_SETTINGS)
    client, files, state_file = _require_s3_context(settings)
    try:
        labels = pull(client, files, state_file)
        pulled_exports = pull_exports(client) if args.exports else None
    except (S3Error, SyncError) as e:
        print(f"Error: {e}")
        sys.exit(1)
    found = ", ".join(labels) if labels else "nothing there yet"
    print(f"S3: pulled from {client.config.bucket} ({found})")
    if pulled_exports is not None:
        for name in pulled_exports:
            print(f"  export downloaded: {name}")
        if not pulled_exports:
            print("  no new exports")


def cmd_push(args: argparse.Namespace) -> None:
    """Upload the named exports, then the data files that changed."""
    settings = load_settings(args.settings or DEFAULT_SETTINGS)
    client, files, state_file = _require_s3_context(settings)
    try:
        # Exports first: if one fails, no data file goes up.
        for path in args.export or []:
            result = upload_export(client, path)
            if result is None:
                print(f"  {path}: no food entries, not uploaded")
                continue
            key, uploaded = result
            print(f"  {key}: {'uploaded' if uploaded else 'already in S3'}")
        labels = push(client, files, state_file)
    except (S3Error, SyncError) as e:
        print(f"Error: {e}")
        sys.exit(1)
    for label in labels:
        print(f"  {label}: uploaded")
    if not labels:
        print(f"S3: data files in {client.config.bucket} are up to date")


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
    custom_foods_path = resolve_path("custom_foods_path", DEFAULT_CUSTOM_FOODS, settings)

    print(f"Database: {db_path}")
    print(f"Mappings: {mappings_path}")

    api_key = settings.get("usda_api_key", "")
    if not api_key:
        print("Error: No USDA API key in settings.yaml.")
        sys.exit(1)

    # Pull first: the run must start from S3's copy of the data files.
    try:
        s3 = _s3_context(settings)
    except S3ConfigError as e:
        print(f"Error: {e}")
        sys.exit(1)
    if s3 is None:
        print("S3: not configured — local files only")
    else:
        client, files, state_file = s3
        try:
            labels = pull(client, files, state_file)
        except (S3Error, SyncError) as e:
            print(f"Error: {e}")
            sys.exit(1)
        print(f"S3: pulled from {client.config.bucket} ({', '.join(labels)})")

    try:
        custom_foods = load_custom_foods(custom_foods_path)
    except CustomFoodsError as e:
        print(f"Error in {custom_foods_path}: {e}")
        sys.exit(1)

    if os.path.exists(custom_foods_path):
        n_recipes = len(custom_foods["recipes"])
        n_foods = len(custom_foods["by_food"])
        print(f"Custom foods: {custom_foods_path} ({n_recipes} recipes, {n_foods} foods)")
    else:
        print(f"Custom foods: {custom_foods_path} (not found — none used)")

    # 1. Parse Excel Food Log
    print(f"Parsing {xlsx_path}...")
    entries = parse_food_log(xlsx_path)
    print(f"  Found {len(entries)} food entries.")
    last_day = check_last_day(entries, parse_daily_expenditure(xlsx_path))
    if last_day and last_day["partial"]:
        # A mid-day export would pull every average down with a half-logged day (BAR-87).
        # The report names the day; see reporter._left_out_day_line.
        entries = [e for e in entries if e["date"] != last_day["date"]]
    elif last_day and last_day["expenditure_kcal"] is None:
        print(f"  No expenditure for {last_day['date']} in the export; counting it as a full day.")
    food_names = extract_unique_foods(entries)
    print(f"  Found {len(food_names)} unique foods.")

    # 2. Resolve & Sync Mappings (Auto-Translate with Gemini if new foods found).
    # Foods covered by a custom recipe need no translation, so Gemini never sees them.
    translatable = [f for f in food_names if f not in custom_foods["by_food"]]
    mappings = resolve_and_sync_mappings(translatable, db_path, mappings_path, settings)

    # 3. Enrich foods with USDA data
    print("Enriching foods with USDA data...")
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    db = TinyDB(db_path)
    outdated = count_outdated_matches(translatable, mappings, db)
    if outdated:
        print(f"  Re-matching {outdated} cached foods with updated USDA matching...")
    logged_macros = logged_macros_per_100g(entries)
    enrichment = enrich_all_foods(food_names, mappings, logged_macros, db, api_key, custom_foods=custom_foods)
    resolved = len(enrichment["profiles"]) + len(enrichment["custom"])
    print(f"  {resolved}/{len(food_names)} foods resolved.")
    if resolved == 0 and enrichment["lookup_failed"]:
        # Every nutrient would read as deficient, from no data at all.
        # The cause is not known here (key, quota, network, a wrong FDC ID in
        # a recipe); the logged request errors carry it.
        print(
            f"Error: no food resolved and {len(enrichment['lookup_failed'])} USDA lookups failed — "
            "the errors above say why. No report written."
        )
        sys.exit(1)

    # 4. Analyze Micronutrient Intake
    print("Analyzing micronutrient intake...")
    left_out_day = last_day if last_day and last_day["partial"] else None
    result = analyze(entries, enrichment, ref_ranges, supplements=supplements, left_out_day=left_out_day)

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

    if s3 is not None:
        _save_to_s3(*s3, xlsx_path)


def _save_to_s3(client: S3Client, files: list[DataFile], state_file: str, xlsx_path: str) -> None:
    """Upload the analysed export, then the data files that changed; a failure ends with exit code 1.

    The export goes first so the database in S3 never names foods whose logged
    macros are missing from S3. The reports are already written by now.
    """
    try:
        export = upload_export(client, xlsx_path)
        if export is None:
            print("S3: export has no Food Log entries — not uploaded")
        labels = push(client, files, state_file)
    except (S3Error, SyncError) as e:
        message = str(e) if str(e).endswith((".", "?")) else f"{e}."
        # A Text, so brackets in the message are not read as Rich markup.
        Console().print(Text(f"Results were not saved to S3: {message} Run analyze again.", style="red"))
        sys.exit(1)

    notes = []
    if export is not None:
        key, uploaded = export
        name = key.removeprefix(EXPORTS_PREFIX)
        notes.append(f"uploaded export {name}" if uploaded else f"export {name} already in S3")
    if labels:
        notes.append(", ".join(labels))
    if labels or (export is not None and export[1]):
        print(f"S3: {'; '.join(notes)}")
    else:
        print("S3: nothing changed")


def main() -> None:
    """Main CLI entry point."""
    prefer_ipv4_connections()
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

    pull_parser = subparsers.add_parser("pull", help="Download the data files from S3")
    pull_parser.add_argument(
        "--exports", action="store_true", help="Also download MacroFactor exports missing from data/"
    )
    pull_parser.add_argument(
        "--settings", default=None, help=f"Path to settings (default: {DEFAULT_SETTINGS})"
    )

    push_parser = subparsers.add_parser("push", help="Upload changed data files to S3")
    push_parser.add_argument(
        "--export", action="append", metavar="FILE", help="MacroFactor export to upload first (repeatable)"
    )
    push_parser.add_argument(
        "--settings", default=None, help=f"Path to settings (default: {DEFAULT_SETTINGS})"
    )

    args = parser.parse_args()

    if args.command == "analyze":
        cmd_analyze(args)
    elif args.command == "pull":
        cmd_pull(args)
    elif args.command == "push":
        cmd_push(args)


if __name__ == "__main__":
    main()
