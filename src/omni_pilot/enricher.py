"""USDA FoodData Central enrichment with TinyDB caching."""
from __future__ import annotations

import logging
import re
import time
from datetime import date
from typing import TypedDict

import requests
from tinydb import Query, TinyDB

from omni_pilot import matcher
from omni_pilot.matcher import Candidate

logger = logging.getLogger(__name__)

SEARCH_PAGE_SIZE = 25
# USDA's search endpoint rejects "/" and intermittently rejects brackets (HTTP 400).
_UNSAFE_QUERY_CHARS = re.compile(r"[()\[\]{}/\\]")


class EnrichmentResult(TypedDict):
    """Outcome of enriching a set of foods.

    "Deliberately skipped" and "USDA lookup failed" are different facts and are
    kept in separate sets, so profiles never carries None for either.
    """

    profiles: dict[str, dict[str, float | None]]
    skipped: set[str]
    unresolved: set[str]


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


def clean_query(query: str) -> str:
    """Strip characters USDA's search endpoint rejects, collapsing whitespace."""
    return " ".join(_UNSAFE_QUERY_CHARS.sub(" ", query).split())


def search_usda(query: str, api_key: str) -> list[dict]:
    """Search USDA FoodData Central (SR Legacy and Foundation datasets).

    Returns up to SEARCH_PAGE_SIZE foods in USDA's ranking order, or an empty
    list when there are no results or the request fails.
    """
    cleaned = clean_query(query)
    if not cleaned:
        logger.warning("Empty USDA query after cleaning: '%s'", query)
        return []

    params = {
        "api_key": api_key,
        "query": cleaned,
        "dataType": "SR Legacy,Foundation",
        "pageSize": SEARCH_PAGE_SIZE,
    }

    try:
        resp = requests.get(USDA_SEARCH_URL, params=params, timeout=30)
        if resp.status_code == 429:
            logger.warning("USDA API rate limit hit, waiting 5 seconds...")
            time.sleep(5)
            resp = requests.get(USDA_SEARCH_URL, params=params, timeout=30)
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.error("USDA API request failed for '%s': %s", cleaned, e)
        return []

    foods = resp.json().get("foods", [])
    if not foods:
        logger.warning("No USDA results for query: '%s'", cleaned)
    return foods


def _nutrient_value(usda_food: dict, number: str) -> float | None:
    for fn in usda_food.get("foodNutrients", []):
        if str(fn.get("nutrientNumber", "")) == number and fn.get("value") is not None:
            return float(fn["value"])
    return None


def get_food_candidates(query: str, api_key: str) -> list[Candidate]:
    """Collect USDA candidates for a query, ranked, without duplicates.

    Hits for the query itself come first, then hits for its head words alone
    (e.g. "chickpea"), which surface forms the full query ranks out of view.
    """
    hits = search_usda(query, api_key)
    if not hits:
        return []
    head, _ = matcher.head_words(query)
    head_query = " ".join(head)
    if head_query and head_query != clean_query(query).lower():
        hits = hits + search_usda(head_query, api_key)

    candidates: list[Candidate] = []
    seen_ids: set = set()
    for hit in hits:
        fdc_id = hit.get("fdcId")
        if fdc_id in seen_ids:
            continue
        seen_ids.add(fdc_id)
        candidates.append(Candidate(
            fdc_id=fdc_id,
            description=hit.get("description", ""),
            data_type=hit.get("dataType", ""),
            rank=len(candidates),
            protein_g=_nutrient_value(hit, "203"),
            fat_g=_nutrient_value(hit, "204"),
            carbs_g=_nutrient_value(hit, "205"),
            fiber_g=_nutrient_value(hit, "291"),
            raw=hit,
        ))
    return candidates


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
    hits = search_usda(usda_query, api_key)
    if not hits:
        return None
    usda_food = hits[0]

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
) -> EnrichmentResult:
    """Enrich all foods with USDA micro data.

    Resolved foods go into profiles, foods mapped to "skip" into skipped, and
    foods whose USDA lookup failed into unresolved.
    """
    result: EnrichmentResult = {"profiles": {}, "skipped": set(), "unresolved": set()}

    for food_name in food_names:
        mapping = mappings.get(food_name, "")

        if mapping == "skip":
            result["skipped"].add(food_name)
            logger.info("Skipping '%s' (mapped to 'skip')", food_name)
            continue

        # Use the mapping if provided, otherwise use the original name
        query = mapping if mapping else food_name
        micros = get_food_micros(food_name, query, db, api_key)

        if micros is None:
            result["unresolved"].add(food_name)
            logger.warning(
                "Could not resolve '%s' (query: '%s') — marking as unresolved",
                food_name, query,
            )
            continue

        result["profiles"][food_name] = micros

    return result
