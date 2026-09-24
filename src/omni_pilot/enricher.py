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
from omni_pilot.matcher import Candidate, LoggedMacros, UsdaMacros

logger = logging.getLogger(__name__)

# Bump whenever the candidate-picking logic changes in a way that should
# re-pick cached foods: entries stamped with an older version are re-matched.
MATCH_VERSION = 1
SEARCH_PAGE_SIZE = 25
# USDA's search endpoint rejects "/" and intermittently rejects brackets (HTTP 400).
_UNSAFE_QUERY_CHARS = re.compile(r"[()\[\]{}/\\]")


class LowConfidenceMatch(TypedDict):
    usda_name: str
    macro_distance: float | None


class EnrichmentResult(TypedDict):
    """Outcome of enriching a set of foods.

    "Deliberately skipped" and "USDA lookup failed" are different facts and are
    kept in separate sets, so profiles never carries None for either.
    """

    profiles: dict[str, dict[str, float | None]]
    skipped: set[str]
    unresolved: set[str]
    # Resolved foods whose USDA match fits the logged macros poorly. They stay
    # in profiles and are counted; the report names them.
    low_confidence: dict[str, LowConfidenceMatch]


class FoodMatch(TypedDict):
    per_100g: dict[str, float | None]
    usda_name: str
    confidence: str  # "good" | "weak" | "no_macros" | "unverified"
    macro_distance: float | None


class UsdaFood(TypedDict):
    fdc_id: int
    usda_name: str
    usda_dataset: str
    per_100g: dict[str, float | None]
    usda_macros: UsdaMacros


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
USDA_FOOD_URL = "https://api.nal.usda.gov/fdc/v1/food/{fdc_id}"
# Ingredients of custom foods, keyed by FDC ID. An ID always names the same
# food, so entries here are never invalidated.
USDA_FOODS_TABLE = "usda_foods"


def clean_query(query: str) -> str:
    """Strip characters USDA's search endpoint rejects, collapsing whitespace."""
    return " ".join(_UNSAFE_QUERY_CHARS.sub(" ", query).split())


def _usda_get(url: str, params: dict) -> requests.Response:
    """GET from the USDA API, waiting and retrying once on a rate limit (429)."""
    resp = requests.get(url, params=params, timeout=30)
    if resp.status_code == 429:
        logger.warning("USDA API rate limit hit, waiting 5 seconds...")
        time.sleep(5)
        resp = requests.get(url, params=params, timeout=30)
    return resp


def search_usda(query: str, api_key: str) -> list[dict] | None:
    """Search USDA FoodData Central (SR Legacy and Foundation datasets).

    Returns up to SEARCH_PAGE_SIZE foods in USDA's ranking order, an empty
    list when there are no results (including an empty query after
    cleaning), or None when the request itself fails. None is kept distinct
    from [] so a caller can tell "nothing more to find" from "couldn't ask".
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
        resp = _usda_get(USDA_SEARCH_URL, params)
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.error("USDA API request failed for '%s': %s", cleaned, e)
        return None

    foods = resp.json().get("foods", [])
    if not foods:
        logger.warning("No USDA results for query: '%s'", cleaned)
    return foods


def _nutrient_value(usda_food: dict, number: str) -> float | None:
    for fn in usda_food.get("foodNutrients", []):
        if str(fn.get("nutrientNumber", "")) == number and fn.get("value") is not None:
            return float(fn["value"])
    return None


def get_food_candidates(query: str, api_key: str) -> tuple[list[Candidate], bool]:
    """Collect USDA candidates for a query, ranked, without duplicates.

    Hits for the query itself come first, then hits for its head words alone
    (e.g. "chickpea"), which surface forms the full query ranks out of view.

    Also returns whether the set is complete. It is incomplete only when the
    head-word search fails outright (as opposed to running and finding
    nothing) — a pick made from an incomplete set should not be trusted as
    final. Search 1 failing or finding nothing yields no candidates at all,
    which is unaffected by completeness.
    """
    hits = search_usda(query, api_key)
    if not hits:
        return [], True
    head, _ = matcher.head_words(query)
    head_query = " ".join(head)
    complete = True
    if head_query and head_query != clean_query(query).lower():
        head_hits = search_usda(head_query, api_key)
        if head_hits is None:
            complete = False
        else:
            hits = hits + head_hits

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
    return candidates, complete


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


def _usda_macros(usda_food: dict) -> UsdaMacros:
    return UsdaMacros(
        protein_g=_nutrient_value(usda_food, "203"),
        fat_g=_nutrient_value(usda_food, "204"),
        carbs_g=_nutrient_value(usda_food, "205"),
        fiber_g=_nutrient_value(usda_food, "291"),
    )


def fetch_usda_food(fdc_id: int, api_key: str) -> dict | None:
    """Fetch one USDA food by its FoodData Central ID.

    The abridged response names nutrients "number"/"amount"; they are
    converted to the search endpoint's "nutrientNumber"/"value" so the
    search-hit helpers read the result unchanged. Returns None on any
    failure; a 404 means the ID itself is wrong.
    """
    params = {"api_key": api_key, "format": "abridged"}
    try:
        resp = _usda_get(USDA_FOOD_URL.format(fdc_id=fdc_id), params)
        if resp.status_code == 404:
            logger.error("USDA has no food with FDC ID %s — check custom_foods.yaml", fdc_id)
            return None
        resp.raise_for_status()
        food = resp.json()
    except requests.RequestException as e:
        logger.error("USDA request failed for FDC ID %s: %s", fdc_id, e)
        return None
    return {
        "fdcId": food.get("fdcId", fdc_id),
        "description": food.get("description", ""),
        "dataType": food.get("dataType", ""),
        "foodNutrients": [
            {"nutrientNumber": str(n.get("number", "")), "value": n.get("amount")}
            for n in food.get("foodNutrients", [])
        ],
    }


def get_usda_food(fdc_id: int, db: TinyDB, api_key: str) -> UsdaFood | None:
    """Get a USDA food by ID from the usda_foods cache, fetching it on a miss.

    A cached entry is always reused. A failed fetch caches nothing, so it is
    retried on the next run.
    """
    table = db.table(USDA_FOODS_TABLE)
    cached = table.search(Query().fdc_id == fdc_id)
    if cached:
        entry = cached[0]
        return UsdaFood(
            fdc_id=entry["fdc_id"],
            usda_name=entry["usda_name"],
            usda_dataset=entry["usda_dataset"],
            per_100g=entry["per_100g"],
            usda_macros=entry["usda_macros"],
        )

    raw = fetch_usda_food(fdc_id, api_key)
    if raw is None:
        return None
    food = UsdaFood(
        fdc_id=fdc_id,
        usda_name=raw["description"],
        usda_dataset=raw["dataType"],
        per_100g=extract_micros_from_usda(raw),
        usda_macros=_usda_macros(raw),
    )
    table.insert({**food, "last_updated": str(date.today())})
    return food


def _is_current(entry: dict) -> bool:
    """Whether a cache entry's match_version is at least the current one.

    ">=", not "==": a device running older code must not re-pick an entry a
    newer device already stamped (and vice versa after the next version
    bump), or the two would keep rewriting each other's matches forever.
    """
    return entry.get("match_version", 0) >= MATCH_VERSION


def _match_from_entry(entry: dict, confidence: str | None = None) -> FoodMatch:
    return FoodMatch(
        per_100g=entry["per_100g"],
        usda_name=entry.get("usda_name", ""),
        confidence=confidence or entry.get("confidence", ""),
        macro_distance=entry.get("macro_distance"),
    )


def get_food_match(
    food_name: str,
    usda_query: str,
    logged: LoggedMacros | None,
    db: TinyDB,
    api_key: str,
) -> FoodMatch | None:
    """Get the USDA match and per-100g micro profile for a food.

    A cache entry is reused only when both its query is current and its
    match_version is at least the current one (see `_is_current`). An entry
    picked by older matching logic is re-picked, but kept (as "unverified")
    if the re-pick fails, so a network hiccup never turns a known food into
    an unresolved one. Returns None if the food cannot be resolved at all.

    A pick made from an incomplete candidate set (the head-word search
    failed) is cached but left without `match_version`, so it is treated as
    outdated and retried on the next run instead of being trusted as final.
    """
    Food = Query()
    stale_entry = None
    cached = db.search(Food.original_name == food_name)
    if cached:
        entry = cached[0]
        if entry.get("usda_query") == usda_query:
            if _is_current(entry):
                return _match_from_entry(entry)
            stale_entry = entry
        else:
            # Mapping changed: the old entry describes a different query
            logger.info("Mapping changed for '%s', refetching...", food_name)
            db.remove(Food.original_name == food_name)

    candidates, complete = get_food_candidates(usda_query, api_key)
    pick = matcher.pick_best(candidates, usda_query, logged)
    if pick is None:
        if stale_entry is not None:
            logger.warning("Re-matching '%s' failed; keeping its previous USDA match", food_name)
            return _match_from_entry(stale_entry, confidence="unverified")
        return None

    candidate = pick["candidate"]
    micros = extract_micros_from_usda(candidate["raw"])
    entry_to_cache = {
        "original_name": food_name,
        "usda_query": usda_query,
        "usda_name": candidate["description"],
        "usda_fdc_id": candidate["fdc_id"],
        "usda_dataset": candidate["data_type"],
        "per_100g": micros,
        "confidence": pick["confidence"],
        "macro_distance": pick["macro_distance"],
        "usda_macros": {
            "protein_g": candidate["protein_g"],
            "fat_g": candidate["fat_g"],
            "carbs_g": candidate["carbs_g"],
            "fiber_g": candidate["fiber_g"],
        },
        "last_updated": str(date.today()),
    }
    if complete:
        entry_to_cache["match_version"] = MATCH_VERSION
    else:
        logger.warning(
            "Caching an unstamped pick for '%s': the candidate set was incomplete", food_name
        )
    db.upsert(entry_to_cache, Food.original_name == food_name)

    return FoodMatch(
        per_100g=micros,
        usda_name=candidate["description"],
        confidence=pick["confidence"],
        macro_distance=pick["macro_distance"],
    )


def count_outdated_matches(food_names: list[str], mappings: dict[str, str], db: TinyDB) -> int:
    """Count cached foods whose query is current but whose match_version is not."""
    Food = Query()
    outdated = 0
    for food_name in food_names:
        mapping = mappings.get(food_name, "")
        if mapping == "skip":
            continue
        query = mapping if mapping else food_name
        cached = db.search(Food.original_name == food_name)
        if cached and cached[0].get("usda_query") == query and not _is_current(cached[0]):
            outdated += 1
    return outdated


def enrich_all_foods(
    food_names: list[str],
    mappings: dict[str, str],
    logged_macros: dict[str, LoggedMacros],
    db: TinyDB,
    api_key: str,
) -> EnrichmentResult:
    """Enrich all foods with USDA micro data.

    Resolved foods go into profiles, foods mapped to "skip" into skipped, and
    foods whose USDA lookup failed into unresolved. Resolved foods whose match
    is weak are also listed in low_confidence.
    """
    result: EnrichmentResult = {
        "profiles": {}, "skipped": set(), "unresolved": set(), "low_confidence": {},
    }

    for food_name in food_names:
        mapping = mappings.get(food_name, "")

        if mapping == "skip":
            result["skipped"].add(food_name)
            logger.info("Skipping '%s' (mapped to 'skip')", food_name)
            continue

        # Use the mapping if provided, otherwise use the original name
        query = mapping if mapping else food_name
        match = get_food_match(food_name, query, logged_macros.get(food_name), db, api_key)

        if match is None:
            result["unresolved"].add(food_name)
            logger.warning(
                "Could not resolve '%s' (query: '%s') — marking as unresolved",
                food_name, query,
            )
            continue

        result["profiles"][food_name] = match["per_100g"]
        if match["confidence"] == "weak":
            result["low_confidence"][food_name] = LowConfidenceMatch(
                usda_name=match["usda_name"],
                macro_distance=match["macro_distance"],
            )

    return result
