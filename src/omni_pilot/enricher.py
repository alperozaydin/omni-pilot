"""USDA FoodData Central enrichment with TinyDB caching."""
from __future__ import annotations

import logging
import time
from datetime import date

import requests
from tinydb import Query, TinyDB

logger = logging.getLogger(__name__)

# Maps our internal nutrient keys to USDA nutrient numbers.
# Numbers verified against USDA FoodData Central SR Legacy dataset.
USDA_NUTRIENT_MAP: dict[str, str] = {
    # Vitamins
    "vitamin_a_mcg": "320",       # Vitamin A, RAE
    "vitamin_c_mg": "401",        # Vitamin C, total ascorbic acid
    "vitamin_d_mcg": "328",       # Vitamin D (D2 + D3)
    "vitamin_e_mg": "323",        # Vitamin E (alpha-tocopherol)
    "vitamin_k_mcg": "430",       # Vitamin K (phylloquinone)
    "b1_thiamine_mg": "404",      # Thiamin
    "b2_riboflavin_mg": "405",    # Riboflavin
    "b3_niacin_mg": "406",        # Niacin
    "b5_pantothenic_acid_mg": "410",  # Pantothenic acid
    "b6_pyridoxine_mg": "415",    # Vitamin B-6
    "b12_cobalamin_mcg": "418",   # Vitamin B-12
    "folate_mcg": "417",          # Folate, total
    # Minerals
    "calcium_mg": "301",          # Calcium, Ca
    "iron_mg": "303",             # Iron, Fe
    "zinc_mg": "309",             # Zinc, Zn
    "magnesium_mg": "304",        # Magnesium, Mg
    "manganese_mg": "315",        # Manganese, Mn
    "phosphorus_mg": "305",       # Phosphorus, P
    "potassium_mg": "306",        # Potassium, K
    "selenium_mcg": "317",        # Selenium, Se
    "copper_mg": "312",           # Copper, Cu
    "sodium_mg": "307",           # Sodium, Na
    # Other
    "fiber_g": "291",             # Fiber, total dietary
    "choline_mg": "421",          # Choline, total
    "omega3_ala_g": "619",        # PUFA 18:3 (ALA)
    "omega3_epa_mg": "629",       # PUFA 20:5 n-3 (EPA) — in g from USDA
    "omega3_dha_mg": "621",       # PUFA 22:6 n-3 (DHA) — in g from USDA
    # Amino acids (per 100g, in grams)
    "histidine_g": "512",         # Histidine
    "isoleucine_g": "503",        # Isoleucine
    "leucine_g": "504",           # Leucine
    "lysine_g": "505",            # Lysine
    "methionine_g": "506",        # Methionine
    "cysteine_g": "507",          # Cystine
    "phenylalanine_g": "508",     # Phenylalanine
    "tyrosine_g": "509",          # Tyrosine
    "threonine_g": "502",         # Threonine
    "tryptophan_g": "501",        # Tryptophan
    "valine_g": "510",            # Valine
}

# Reverse map: USDA number -> our key
_USDA_NUMBER_TO_KEY = {v: k for k, v in USDA_NUTRIENT_MAP.items()}

USDA_SEARCH_URL = "https://api.nal.usda.gov/fdc/v1/foods/search"


def search_usda(query: str, api_key: str) -> dict | None:
    """Search USDA FoodData Central for a food.

    Returns the best match food dict, or None if no results.
    Prefers SR Legacy and Foundation datasets.
    """
    params = {
        "api_key": api_key,
        "query": query,
        "dataType": "SR Legacy,Foundation",
        "pageSize": 5,
    }

    try:
        resp = requests.get(USDA_SEARCH_URL, params=params, timeout=30)
        if resp.status_code == 429:
            logger.warning("USDA API rate limit hit, waiting 5 seconds...")
            time.sleep(5)
            resp = requests.get(USDA_SEARCH_URL, params=params, timeout=30)
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.error("USDA API request failed for '%s': %s", query, e)
        return None

    data = resp.json()
    foods = data.get("foods", [])
    if not foods:
        logger.warning("No USDA results for query: '%s'", query)
        return None

    # Return the first (best) match
    return foods[0]


def extract_micros_from_usda(usda_food: dict) -> dict[str, float | None]:
    """Extract per-100g micro values from a USDA food response.

    Returns a dict mapping our nutrient keys to values.
    Missing nutrients are set to None.
    """
    # Build lookup: USDA number -> amount
    nutrient_lookup: dict[str, float] = {}
    for fn in usda_food.get("foodNutrients", []):
        number = str(fn.get("nutrientNumber", ""))
        value = fn.get("value")
        if number and value is not None:
            nutrient_lookup[number] = float(value)

    # Map to our keys
    result: dict[str, float | None] = {}
    for our_key, usda_number in USDA_NUTRIENT_MAP.items():
        result[our_key] = nutrient_lookup.get(usda_number)

    return result


def get_food_micros(
    food_name: str,
    usda_query: str,
    db: TinyDB,
    api_key: str,
) -> dict[str, float | None] | None:
    """Get per-100g micro profile for a food.

    Checks TinyDB cache first, then queries USDA API.
    Returns None if the food cannot be resolved.
    """
    Food = Query()
    cached = db.search(Food.original_name == food_name)
    if cached:
        if cached[0].get("usda_query") == usda_query:
            return cached[0]["per_100g"]
        else:
            # Mapping changed, invalidate cache
            logger.info("Mapping changed for '%s', refetching...", food_name)
            db.remove(Food.original_name == food_name)

    # Query USDA
    usda_food = search_usda(usda_query, api_key)
    if usda_food is None:
        return None

    micros = extract_micros_from_usda(usda_food)

    # Determine confidence
    confidence = "direct" if usda_query == food_name else "mapped"

    # Cache in TinyDB
    db.insert({
        "original_name": food_name,
        "usda_query": usda_query,
        "usda_name": usda_food.get("description", ""),
        "usda_fdc_id": usda_food.get("fdcId"),
        "usda_dataset": usda_food.get("dataType", ""),
        "per_100g": micros,
        "confidence": confidence,
        "last_updated": str(date.today()),
    })

    return micros


def enrich_all_foods(
    food_names: list[str],
    mappings: dict[str, str],
    db: TinyDB,
    api_key: str,
) -> dict[str, dict[str, float | None] | None]:
    """Enrich all foods with USDA micro data.

    Returns a dict mapping food names to their per-100g micro profiles.
    Foods mapped to "skip" get None.
    """
    result: dict[str, dict[str, float | None] | None] = {}

    for food_name in food_names:
        mapping = mappings.get(food_name, "")

        if mapping == "skip":
            result[food_name] = None
            logger.info("Skipping '%s' (mapped to 'skip')", food_name)
            continue

        # Use the mapping if provided, otherwise use the original name
        query = mapping if mapping else food_name
        micros = get_food_micros(food_name, query, db, api_key)

        if micros is None:
            logger.warning(
                "Could not resolve '%s' (query: '%s') — marking as unresolved",
                food_name, query,
            )

        result[food_name] = micros

    return result
