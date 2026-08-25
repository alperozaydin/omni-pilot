"""Gemini AI food translation and mapping synchronization."""
from __future__ import annotations

import json
import logging
import os

import requests
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential
from tinydb import Query, TinyDB

from omni_pilot.config import load_food_mappings
from omni_pilot.parser import generate_food_mappings

logger = logging.getLogger(__name__)

GEMINI_PROMPT_TEMPLATE = """
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
{foods_json}
"""


@retry(
    stop=stop_after_attempt(2),
    wait=wait_exponential(multiplier=1, min=2, max=5),
    retry=retry_if_exception_type((requests.RequestException, json.JSONDecodeError, KeyError, IndexError)),
    reraise=True,
)
def translate_new_foods(foods_list: list[str], api_key: str, model: str = "gemini-flash-latest") -> list[str]:
    """Translate and clean food log queries for USDA FoodData Central search using Gemini REST API."""
    prompt = GEMINI_PROMPT_TEMPLATE.format(foods_json=json.dumps(foods_list))
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


def resolve_and_sync_mappings(
    foods: list[str],
    db_path: str,
    mappings_path: str,
    settings: dict,
) -> dict[str, str]:
    """Resolve food mappings from DB & YAML, translate unknown foods via Gemini, and sync persistence."""
    # 1. Load existing mappings from YAML
    existing_mappings = load_food_mappings(mappings_path)

    # 2. Load translations from TinyDB
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    db = TinyDB(db_path)
    translations_table = db.table("translations")
    db_mappings = {
        doc["german"]: doc["english"]
        for doc in translations_table.all()
        if doc.get("english", "").strip()
    }

    # 3. Merge: DB takes precedence, then existing non-empty YAML
    known_mappings = {**existing_mappings, **db_mappings}
    known_mappings = {k: str(v).strip() for k, v in known_mappings.items() if v and str(v).strip()}
    new_foods = [f for f in foods if f not in known_mappings]

    # 4. If new foods exist, attempt Gemini translation
    if new_foods:
        print(f"  {len(new_foods)} new foods need English equivalents.")
        gemini_key = settings.get("gemini_api_key")
        gemini_model = settings.get("gemini_model", "gemini-flash-latest")

        if gemini_key and gemini_key != "YOUR_GEMINI_API_KEY_HERE":
            print(f"  Using Gemini API ({gemini_model}) to automatically translate and clean foods...")
            try:
                translations = translate_new_foods(new_foods, gemini_key, model=gemini_model)
                if len(translations) == len(new_foods):
                    print("  Gemini translation successful! Saving to database...")
                    for german_food, english_food in zip(new_foods, translations):
                        if english_food and english_food.strip() and english_food != "ERROR":
                            clean_english = english_food.strip()
                            known_mappings[german_food] = clean_english
                            translations_table.upsert(
                                {"german": german_food, "english": clean_english},
                                Query().german == german_food,
                            )
                else:
                    logger.warning("Gemini returned a different number of translations (%d) than expected (%d).", len(translations), len(new_foods))
            except Exception as e:
                logger.warning("Gemini translation failed: %s. Proceeding with unresolved items.", e)
        else:
            print("  No valid gemini_api_key found in settings. Skipping automatic translation.")
            print(f"  You can edit {mappings_path} and fill in USDA-searchable names.")

    # 5. Sync any manual YAML updates into TinyDB
    existing_db_translations = {
        doc["german"]: doc.get("english", "")
        for doc in translations_table.all()
    }
    TranslationQuery = Query()
    for german, english in known_mappings.items():
        if english and existing_db_translations.get(german) != english:
            translations_table.upsert(
                {"german": german, "english": english},
                TranslationQuery.german == german,
            )

    # 6. Update YAML file (only writes if changed)
    generate_food_mappings(foods, known_mappings, mappings_path)

    return known_mappings
