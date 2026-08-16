"""CLI entry point for Omni Pilot."""
from __future__ import annotations

import argparse
import logging
import os
import sys
import json
import shutil
from datetime import date

import requests
from tinydb import TinyDB, Query
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from omni_pilot.config import (
    load_settings,
    load_reference_ranges,
    load_food_mappings,
    load_supplements,
    resolve_path,
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

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type((requests.RequestException, json.JSONDecodeError, KeyError, IndexError)),
    reraise=True,
)
def translate_new_foods(foods_list: list[str], api_key: str, model: str = "gemini-flash-latest") -> list[str]:
    """Translate and clean a list of foods using Gemini REST API."""
    prompt = f"""
Translate these messy German food entries to English and extract ONLY the most basic, generic ingredient.
CRITICAL RULES:
1. Drop ALL brand names, prices, and weights.
2. DROP meal-specific modifiers and brand-like adjectives (e.g. 'Burger', 'Frozen', 'Crunch'). For example, 'Burger Cheese' MUST become simply 'Cheese'. 'Caramel Crunch Protein Bar' MUST become simply 'Protein bar'.

Input foods:
{json.dumps(foods_list)}
"""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.0,
            "responseMimeType": "application/json",
            "responseSchema": {
                "type": "ARRAY",
                "items": {"type": "STRING"}
            }
        }
    }
    resp = requests.post(url, json=payload, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    raw_text = data["candidates"][0]["content"]["parts"][0]["text"]
    return json.loads(raw_text)


def cmd_import(args: argparse.Namespace) -> None:
    """Import MacroFactor xlsx and generate food_mappings.yaml."""
    xlsx_path = args.xlsx_file
    settings_path = args.settings or DEFAULT_SETTINGS
    settings = load_settings(settings_path) if os.path.exists(settings_path) else {}

    db_path = resolve_path("database_path", DEFAULT_DB, settings)
    mappings_output = resolve_path("mappings_path", DEFAULT_MAPPINGS, settings)

    print(f"Database: {db_path}")
    print(f"Mappings: {mappings_output}")
    print(f"Parsing {xlsx_path}...")
    entries = parse_food_log(xlsx_path)
    print(f"  Found {len(entries)} food entries.")

    foods = extract_unique_foods(entries)
    print(f"  Found {len(foods)} unique foods.")

    # Load existing mappings if present
    existing_mappings = load_food_mappings(mappings_output)

    # Load translations from TinyDB
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    db = TinyDB(db_path)
    translations_table = db.table("translations")
    db_mappings = {doc["german"]: doc["english"] for doc in translations_table.all() if doc.get("english", "").strip()}
    
    # Merge mappings: DB takes precedence, then existing YAML (only non-empty)
    known_mappings = {**existing_mappings, **db_mappings}
    known_mappings = {k: str(v).strip() for k, v in known_mappings.items() if v and str(v).strip()}
    new_foods = [f for f in foods if f not in known_mappings]


    if new_foods:
        print(f"  {len(new_foods)} new foods need English equivalents.")
        # Attempt Gemini Translation
        gemini_key = settings.get("gemini_api_key")
        gemini_model = settings.get("gemini_model", "gemini-flash-latest")
        
        if gemini_key and gemini_key != "YOUR_GEMINI_API_KEY_HERE":
            print(f"  Using Gemini API ({gemini_model}) to automatically translate and clean foods...")
            try:
                translations = translate_new_foods(new_foods, gemini_key, model=gemini_model)
                
                # Check if returned length matches
                if len(translations) == len(new_foods):
                    print("  Gemini translation successful! Saving to database...")
                    for german_food, english_food in zip(new_foods, translations):
                        if english_food and english_food.strip() and english_food != "ERROR":
                            known_mappings[german_food] = english_food.strip()
                            translations_table.upsert(
                                {"german": german_food, "english": english_food.strip()},
                                Query().german == german_food
                            )
                else:
                    print("Error: Gemini returned a different number of translations than expected.")
                    sys.exit(1)
            except Exception as e:
                print(f"Error: Gemini translation failed: {e}")
                sys.exit(1)
        else:
            print("  No valid gemini_api_key found in settings. Skipping automatic translation.")
            print("  Edit the food_mappings.yaml file and fill in USDA-searchable names.")


    # Generate/merge mappings (only writes if changes are present)
    written = generate_food_mappings(foods, known_mappings, mappings_output)
    if written:
        print(f"\nMappings written to: {mappings_output}")
    else:
        print(f"\nMappings are up to date: {mappings_output} (no changes).")

    if not new_foods:
        print("  All foods already mapped.")


def cmd_analyze(args: argparse.Namespace) -> None:
    """Run full analysis pipeline: parse → enrich → analyze → report."""
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

    mappings = load_food_mappings(mappings_path)
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
    
    # Save any new or updated non-empty mappings to database
    translations_table = db.table("translations")
    existing_db_translations = {
        doc["german"]: doc.get("english", "")
        for doc in translations_table.all()
    }
    TranslationQuery = Query()
    saved_count = 0
    for german, english in mappings.items():
        if english and str(english).strip():
            clean_english = str(english).strip()
            if existing_db_translations.get(german) != clean_english:
                translations_table.upsert(
                    {"german": german, "english": clean_english},
                    TranslationQuery.german == german
                )
                saved_count += 1
    if saved_count > 0:
        print(f"  Saved/Updated {saved_count} mappings in the database.")
    else:
        print("  Database translations up to date (0 updated).")

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

    # Import command
    import_parser = subparsers.add_parser(
        "import", help="Import MacroFactor xlsx and generate food mappings"
    )
    import_parser.add_argument("xlsx_file", help="Path to MacroFactor xlsx export")
    import_parser.add_argument("--settings", default=None, help=f"Path to settings (default: {DEFAULT_SETTINGS})")

    # Analyze command
    analyze_parser = subparsers.add_parser(
        "analyze", help="Analyze micronutrient intake"
    )
    analyze_parser.add_argument("xlsx_file", help="Path to MacroFactor xlsx export")
    analyze_parser.add_argument(
        "--html", action="store_true", help="Also generate HTML report"
    )
    analyze_parser.add_argument("--settings", default=None, help=f"Path to settings (default: {DEFAULT_SETTINGS})")
    analyze_parser.add_argument("--ref-ranges", default=None, help=f"Path to reference ranges (default: {DEFAULT_REF_RANGES})")
    analyze_parser.add_argument("--supplements", default=None, help=f"Path to supplements (default: {DEFAULT_SUPPLEMENTS})")

    args = parser.parse_args()

    if args.command == "import":
        cmd_import(args)
    elif args.command == "analyze":
        cmd_analyze(args)

if __name__ == "__main__":
    main()

