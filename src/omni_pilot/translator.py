"""Gemini AI food translation and mapping synchronization."""
from __future__ import annotations

import json
import logging
import os

import requests
from tenacity import (
    retry,
    retry_if_exception_type,
    retry_if_not_exception_type,
    stop_after_attempt,
    wait_exponential,
)
from tinydb import Query, TinyDB

from omni_pilot.config import load_food_mappings
from omni_pilot.parser import generate_food_mappings

logger = logging.getLogger(__name__)

# (connect, read) seconds. The connect timeout applies to each address the host
# resolves to, tried in turn, and Gemini's host resolves to 16 of them: at 30s
# each, an unreachable network kept the CLI waiting for minutes. The read
# timeout leaves room for the model to think (~5-20s for a few foods at low
# thinking, see GEMINI_THINKING_LEVEL) but not for a stalled request: Gemini
# sometimes sends nothing for over a minute, while a fresh request answers in
# seconds, so a stall is cut off and asked again (GEMINI_ATTEMPTS). Two 60s
# attempts once both timed out on the iPhone.
GEMINI_TIMEOUT = (5, 30)
GEMINI_ATTEMPTS = 3

# gemini-flash-latest thinks for 30-60s at its default level before sending a
# byte, which ran into the read timeout on the iPhone; "low" answers in 5-20s
# with translations as good. Gemini 3 models take this setting, 2.5 models
# don't.
GEMINI_THINKING_LEVEL = "low"

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
    stop=stop_after_attempt(GEMINI_ATTEMPTS),
    wait=wait_exponential(multiplier=1, min=2, max=5),
    # A server that can't be reached now won't be two seconds later either.
    retry=(
        retry_if_exception_type((requests.RequestException, json.JSONDecodeError, KeyError, IndexError))
        & retry_if_not_exception_type(requests.ConnectionError)
    ),
    reraise=True,
)
def translate_new_foods(foods_list: list[str], api_key: str, model: str = "gemini-flash-latest") -> list[str]:
    """Translate and clean food log queries for USDA FoodData Central search using Gemini REST API."""
    prompt = GEMINI_PROMPT_TEMPLATE.format(foods_json=json.dumps(foods_list))
    # The key goes in a header, not the URL: requests includes the URL in its
    # error messages, which are logged.
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.0,
            "thinkingConfig": {"thinkingLevel": GEMINI_THINKING_LEVEL},
            "responseMimeType": "application/json",
            "responseSchema": {
                "type": "ARRAY",
                "items": {"type": "STRING"}
            }
        }
    }
    resp = requests.post(url, json=payload, headers={"x-goog-api-key": api_key}, timeout=GEMINI_TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    raw_text = data["candidates"][0]["content"]["parts"][0]["text"]
    return json.loads(raw_text)


def normalise_query(query: str) -> str:
    """A lookup text as stored: stripped, and "skip" in any case as "skip" (what the enricher compares)."""
    query = query.strip()
    return "skip" if query.lower() == "skip" else query


def stored_mappings(db_path: str, mappings_path: str) -> dict[str, str]:
    """Every stored lookup text: the YAML's, overridden by the database's; empty values dropped.

    A missing database reads as empty and is not created.
    """
    db_mappings: dict[str, str] = {}
    if os.path.exists(db_path):
        with TinyDB(db_path) as db:
            db_mappings = {
                doc["german"]: doc["english"]
                for doc in db.table("translations").all()
                if doc.get("english", "").strip()
            }
    merged = {**load_food_mappings(mappings_path), **db_mappings}
    return {k: str(v).strip() for k, v in merged.items() if v and str(v).strip()}


def cached_food_names(db_path: str) -> set[str]:
    """The foods with a USDA cache entry (a match or a not-found record); empty when there is no database."""
    if not os.path.exists(db_path):
        return set()
    with TinyDB(db_path) as db:
        return {entry["original_name"] for entry in db.all() if "original_name" in entry}


def set_mapping(food: str, query: str, db_path: str, mappings_path: str) -> None:
    """Store a food's lookup text in the database and the YAML, and drop its USDA cache entry.

    The cache entry goes now rather than on the next analyze, because the daily
    review reads the cache without analyzing and would keep reviewing the old
    match. Its review and defer marks go with it: they judged that match.
    """
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    with TinyDB(db_path) as db:
        db.table("translations").upsert({"german": food, "english": query}, Query().german == food)
        db.remove(Query().original_name == food)
    mappings = load_food_mappings(mappings_path)
    mappings[food] = query
    generate_food_mappings([], mappings, mappings_path)


def resolve_and_sync_mappings(
    foods: list[str],
    db_path: str,
    mappings_path: str,
    settings: dict,
) -> tuple[dict[str, str], set[str]]:
    """Resolve food mappings from DB & YAML, translate unknown foods via Gemini, and sync persistence.

    Returns the mappings and the foods Gemini was asked about but gave no
    translation for (an error, a wrong number of answers, an empty answer).
    Those are retried next run, so the caller must not search USDA under
    their own name. Without a Gemini key nothing is untranslated: an
    unmapped food's own name is its query.
    """
    # 1-3. Stored mappings: DB takes precedence, then existing non-empty YAML
    known_mappings = stored_mappings(db_path, mappings_path)

    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    db = TinyDB(db_path)
    translations_table = db.table("translations")
    new_foods = [f for f in foods if f not in known_mappings]
    untranslated: set[str] = set()

    # 4. If new foods exist, attempt Gemini translation
    if new_foods:
        print(f"  {len(new_foods)} new foods need English equivalents.")
        gemini_key = settings.get("gemini_api_key")
        gemini_model = settings.get("gemini_model", "gemini-flash-latest")

        if gemini_key and gemini_key != "YOUR_GEMINI_API_KEY_HERE":
            print(f"  Using Gemini API ({gemini_model}) to automatically translate and clean foods...")
            untranslated = set(new_foods)
            try:
                translations = translate_new_foods(new_foods, gemini_key, model=gemini_model)
                if len(translations) == len(new_foods):
                    print("  Gemini translation successful! Saving to database...")
                    for german_food, english_food in zip(new_foods, translations):
                        if english_food and english_food.strip() and english_food != "ERROR":
                            clean_english = english_food.strip()
                            known_mappings[german_food] = clean_english
                            untranslated.discard(german_food)
                            translations_table.upsert(
                                {"german": german_food, "english": clean_english},
                                Query().german == german_food,
                            )
                else:
                    logger.warning(
                        "Gemini returned a different number of translations (%d) than expected (%d).",
                        len(translations),
                        len(new_foods),
                    )
            except Exception as e:
                logger.warning("Gemini translation failed: %s. Proceeding with unresolved items.", e)
        else:
            print("  No valid gemini_api_key found in settings. Skipping automatic translation.")
            print('  To set a food\'s lookup text: make update-mapping FOOD="..." QUERY="..."')

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

    return known_mappings, untranslated
