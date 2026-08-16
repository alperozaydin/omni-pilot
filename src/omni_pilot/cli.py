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
    """Translate and optimize food queries for USDA FoodData Central search using Gemini REST API."""
    prompt = f"""
You are an expert nutritionist translating food log entries (mostly German or branded) into optimal USDA FoodData Central (SR Legacy and Foundation datasets) search queries.

CRITICAL RULES FOR USDA SEARCH OPTIMIZATION:
1. USDA TAXONOMY: Format queries to match USDA staple naming conventions so whole staple foods rank #1 over processed byproducts (powders, baby food, crackers, flours, breads).
2. STATE & FORM: Always specify state ('raw', 'cooked', 'fluid', 'frozen', 'canned') when applicable:
   - VEGETABLES/FRUITS: 'tomatoes, red, ripe, raw' (never plain 'tomatoes'), 'potatoes, raw', 'vegetables, mixed, frozen, unprepared', 'olives, ripe, canned'.
   - LIQUID MILK: Must include 'fluid' and fat % (e.g. 'milk, reduced fat, fluid, 2% milkfat' or 'milk, whole, fluid'). Never plain 'milk' or 'low fat milk'.
   - GRAINS/CEREALS: Specify grain/cereal (e.g. 'rice, white, long-grain, regular, raw', 'rice, brown, long-grain, raw', 'cereals, oats, regular and quick, not fortified, dry'). Never plain 'rice' or 'oats'.
   - MEAT/POULTRY: Specify whole meat cut (e.g. 'chicken, broilers or fryers, breast, meat only, raw', 'chicken, liver, raw', 'beef, ground, 85% lean, raw'). Never plain 'chicken breast' (matches sliced lunchmeat).
   - OILS & BUTTER: 'oil, olive, salad or cooking', 'butter, without salt'.
   - CHEESES: 'cheese, swiss', 'cheese, feta', 'cheese, mozzarella, whole milk', 'cheese, gruyere'.
3. NOISE CLEANING: Remove prices, package weights, store names (e.g., Rewe, Edeka, Bio, XXL, 250g, 1.29€).
4. EXCLUSIONS & BASE FOOD MAPPING:
   - ALWAYS return 'skip' for 'Quick Add', water, and non-food entries (e.g., pill/capsule supplements).
   - For branded cereals/muesli/granola (e.g., 'Protein Müsli', 'Krunchy Chocolate Chunks'), map to base cereal: 'muesli' or 'cereals ready-to-eat, granola'.
   - For puddings/desserts (e.g., 'High-Protein-Pudding - Schoko'), map to base dessert: 'puddings, chocolate, ready-to-eat'.
   - For protein bars/bites, map to: 'protein bar' or 'snacks, granola bars, hard, almond'.
   - Return 'skip' for ultra-processed plant-based meat substitutes without whole-food equivalents (e.g., 'Like Tender Crunch').

EXAMPLES:
- "Frische Fettarme Bio Alpenmilch Laktosefrei" -> "milk, reduced fat, fluid, 2% milkfat"
- "Die Feinen Speisekartoffeln, Qualität I, Festkochend" -> "potatoes, raw, skin"
- "Haferflocken" -> "cereals, oats, regular and quick, not fortified, dry"
- "Tomaten" -> "tomatoes, red, ripe, raw, year round average"
- "Königsgemüse" / "Suppengemüse" -> "vegetables, mixed, frozen, unprepared"
- "Jasmin-Reis" -> "rice, white, long-grain, regular, raw, unenriched"
- "Hähnchen Brustfilet" -> "chicken, broilers or fryers, breast, meat only, raw"
- "Einfach Bio Hackfleisch Rind Zum Braten" -> "beef, ground, raw"
- "Rinder Rumpsteak" -> "beef, top sirloin, steak, raw"
- "Oliven Schwarze Oliven" -> "olives, ripe, canned (small-extra large)"
- "Protein Müsli 2" -> "muesli"
- "Krunchy Chocolate Chunks Mit Cornflakes" -> "cereals ready-to-eat, granola"
- "High-Protein-Pudding - Schoko" -> "puddings, chocolate, ready-to-eat"
- "Like Tender Crunch Original" -> "skip"
- "Quick Add" -> "skip"

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

