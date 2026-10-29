"""USDA FoodData Central enrichment with TinyDB caching."""
from __future__ import annotations

import logging
import re
import time
from collections.abc import Collection
from datetime import date
from typing import Literal, TypedDict

import requests
from tinydb import Query, TinyDB
from tinydb.queries import QueryLike
from tinydb.table import Table

from omni_pilot import matcher
from omni_pilot.custom_foods import CustomFoods, Recipe, no_custom_foods, recipe_macros
from omni_pilot.matcher import Candidate, LoggedMacros, LoggedTotals, UsdaMacros

logger = logging.getLogger(__name__)

# Bump whenever the candidate-picking logic changes in a way that should
# re-pick cached foods: entries stamped with an older version are re-matched.
MATCH_VERSION = 1
# Days a "not in USDA" answer is reused before the food is searched again,
# in case USDA has added it since.
NOT_FOUND_TTL_DAYS = 30
SEARCH_PAGE_SIZE = 25
# USDA's search endpoint rejects "/" and intermittently rejects brackets (HTTP 400).
_UNSAFE_QUERY_CHARS = re.compile(r"[()\[\]{}/\\]")


class LowConfidenceMatch(TypedDict):
    usda_name: str
    macro_distance: float | None


class RecipePart(TypedDict):
    share: float
    fdc_id: int
    usda_name: str
    per_100g: dict[str, float | None]


class CustomFoodMatch(TypedDict):
    recipe: str
    parts: list[RecipePart]
    macro_distance: float | None
    # The food's logged weight is a placeholder, so macro_distance was taken at
    # the grams fitted to the recipe (matcher.fitted_distance).
    weight_unknown: bool


class EnrichmentResult(TypedDict):
    """Outcome of enriching a set of foods.

    "Deliberately skipped", "USDA has no such food" and "USDA could not be
    asked" are different facts and are kept in separate sets, so profiles
    never carries None for any of them.
    """

    profiles: dict[str, dict[str, float | None]]
    skipped: set[str]
    # The USDA search ran and found nothing: the food needs a custom recipe.
    not_in_usda: set[str]
    # USDA could not be asked, or a recipe ingredient could not be fetched:
    # retried next run.
    lookup_failed: set[str]
    # Gemini gave no translation this run: not searched in USDA under the
    # German name (that finds nothing, or a wrong food), retried next run.
    untranslated: set[str]
    # Resolved foods whose USDA match fits the logged macros poorly. They stay
    # in profiles and are counted; the report names them.
    low_confidence: dict[str, LowConfidenceMatch]
    # Foods covered by a custom recipe, built from its ingredients. They are
    # never in profiles: the analyzer counts them ingredient by ingredient, so
    # an ingredient missing a nutrient costs only its own share of coverage.
    custom: dict[str, CustomFoodMatch]
    # Per-100 g macros of each resolved food's match: the USDA food's, or the
    # recipe's combined ones. Placeholder weights are fitted against them
    # (matcher.estimate_weights). A USDA food without stored macros is absent.
    matched_macros: dict[str, UsdaMacros]


class FoodMatch(TypedDict):
    per_100g: dict[str, float | None]
    usda_name: str
    confidence: str  # "good" | "weak" | "no_macros" | "unverified"
    macro_distance: float | None
    usda_macros: UsdaMacros | None  # None for a legacy entry stored without them


# Why get_food_match has no match: USDA answered and has no such food
# ("not_in_usda", cached), or USDA could not be asked ("lookup_failed", never cached).
LookupMiss = Literal["not_in_usda", "lookup_failed"]


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
    # Not a reference nutrient: only the analyzer reads it, to tell retinol (the
    # part of vitamin A a vitamin A upper limit covers) from carotenoids.
    "retinol_mcg": "319",         # Retinol
}

# Reverse map: USDA number -> our key
_USDA_NUMBER_TO_KEY = {v: k for k, v in USDA_NUTRIENT_MAP.items()}

# (connect, read) seconds. The connect timeout applies to each address the host
# resolves to, tried in turn, so it is kept short: an unreachable network then
# fails in seconds instead of minutes.
USDA_TIMEOUT = (5, 30)
# One session for every USDA request, so a run reuses one connection instead of
# opening (and TLS-handshaking) a new one per request.
_SESSION = requests.Session()

USDA_SEARCH_URL = "https://api.nal.usda.gov/fdc/v1/foods/search"
USDA_FOOD_URL = "https://api.nal.usda.gov/fdc/v1/food/{fdc_id}"
# Ingredients of custom foods, keyed by FDC ID. An ID always names the same
# food, so entries here are never invalidated. The one change an entry can see is
# a missing value being filled in (retinol, `fill_retinol`); the food it names
# never changes.
USDA_FOODS_TABLE = "usda_foods"


def clean_query(query: str) -> str:
    """Strip characters USDA's search endpoint rejects, collapsing whitespace."""
    return " ".join(_UNSAFE_QUERY_CHARS.sub(" ", query).split())


def _usda_get(url: str, params: dict, api_key: str) -> requests.Response:
    """GET from the USDA API, waiting and retrying once on a rate limit (429).

    The key goes in a header, not the query string: requests includes the URL
    in its error messages, which are logged.
    """
    headers = {"X-Api-Key": api_key}
    resp = _SESSION.get(url, params=params, headers=headers, timeout=USDA_TIMEOUT)
    if resp.status_code == 429:
        logger.warning("USDA API rate limit hit, waiting 5 seconds...")
        time.sleep(5)
        resp = _SESSION.get(url, params=params, headers=headers, timeout=USDA_TIMEOUT)
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
        "query": cleaned,
        "dataType": "SR Legacy,Foundation",
        "pageSize": SEARCH_PAGE_SIZE,
    }

    try:
        resp = _usda_get(USDA_SEARCH_URL, params, api_key)
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

    Also returns whether the set is complete: False when any search failed
    outright (as opposed to running and finding nothing). A pick made from an
    incomplete set should not be trusted as final, and no candidates from an
    incomplete set mean USDA could not be asked, not that it has no such food.
    """
    hits = search_usda(query, api_key)
    if hits is None:
        return [], False
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


FetchMiss = Literal["gone", "rate_limited", "failed"]


def fetch_usda_food_or_miss(fdc_id: int, api_key: str) -> dict | FetchMiss:
    """Fetch one USDA food by its FoodData Central ID, or say why it could not be had.

    The abridged response names nutrients "number"/"amount"; they are
    converted to the search endpoint's "nutrientNumber"/"value" so the
    search-hit helpers read the result unchanged. A miss is "gone" (404: USDA
    has no food with this ID), "rate_limited" (429 after `_usda_get`'s one
    retry) or "failed" (any other error).
    """
    params = {"format": "abridged"}
    try:
        resp = _usda_get(USDA_FOOD_URL.format(fdc_id=fdc_id), params, api_key)
        if resp.status_code == 404:
            return "gone"
        if resp.status_code == 429:
            logger.error("USDA request for FDC ID %s hit the rate limit", fdc_id)
            return "rate_limited"
        resp.raise_for_status()
        food = resp.json()
    except requests.RequestException as e:
        logger.error("USDA request failed for FDC ID %s: %s", fdc_id, e)
        return "failed"
    return {
        "fdcId": food.get("fdcId", fdc_id),
        "description": food.get("description", ""),
        "dataType": food.get("dataType", ""),
        "foodNutrients": [
            {"nutrientNumber": str(n.get("number", "")), "value": n.get("amount")}
            for n in food.get("foodNutrients", [])
        ],
    }


def fetch_usda_food(fdc_id: int, api_key: str) -> dict | None:
    """Fetch one USDA food by ID; None on any failure (a 404 means the ID itself is wrong)."""
    food = fetch_usda_food_or_miss(fdc_id, api_key)
    if food == "gone":
        logger.error("USDA has no food with FDC ID %s — check custom_foods.yaml", fdc_id)
    return food if isinstance(food, dict) else None


RETINOL_KEY = "retinol_mcg"


def fill_retinol(
    table: Table | TinyDB,
    cond: QueryLike,
    per_100g: dict,
    fdc_id: int | None,
    api_key: str,
    label: str,
) -> tuple[dict, FetchMiss | None]:
    """Add retinol to a cached profile written before it was stored.

    A profile without the `retinol_mcg` key (not one with a None value, which
    says USDA has no figure) is extended from the food's USDA ID, without
    re-matching, and written back to the entries `cond` selects in `table`:
    only `per_100g`, since `upsert` merges top-level fields only. A 404 stores
    None, so a gone ID is not fetched again every run; any other miss writes
    nothing, leaving the key absent for the next run. Returns the profile to use
    and the miss, if any. An entry without a USDA ID is left as it is.
    """
    if RETINOL_KEY in per_100g or fdc_id is None:
        return per_100g, None
    raw = fetch_usda_food_or_miss(fdc_id, api_key)
    return _write_retinol(table, cond, per_100g, fdc_id, raw, label)


def _write_retinol(
    table: Table | TinyDB,
    cond: QueryLike,
    per_100g: dict,
    fdc_id: int,
    raw: dict | FetchMiss,
    label: str,
) -> tuple[dict, FetchMiss | None]:
    """Write the outcome of a retinol fetch back: the one place that rule lives.

    `raw` is what `fetch_usda_food_or_miss` returned for `fdc_id`. A food stores
    its retinol, a "gone" ID stores None, any other miss writes nothing.
    """
    if raw == "gone":
        logger.warning("Retinol for '%s' not filled: its USDA ID %s no longer exists", label, fdc_id)
        retinol = None
    elif isinstance(raw, dict):
        retinol = extract_micros_from_usda(raw)[RETINOL_KEY]
    else:
        logger.warning("Retinol for '%s' not filled (USDA lookup %s); retrying next run", label, raw)
        return per_100g, raw
    extended = {**per_100g, RETINOL_KEY: retinol}
    table.update({"per_100g": extended}, cond)
    return extended, ("gone" if raw == "gone" else None)


class BackfillReport(TypedDict):
    filled: int
    no_usda_id: list[str]
    failed: list[str]
    gone: list[str]
    rate_limited: bool


def backfill_retinol(db: TinyDB, api_key: str) -> BackfillReport:
    """Fill retinol into every cached profile that lacks it, fetching each USDA ID once.

    Covers the matches of the main table (not-found records have no profile and
    are skipped) and then the `usda_foods` entries. An ID shared by several
    entries is fetched once and written to each. After a rate-limit answer
    nothing more is fetched; what was filled stays written.
    """
    report = BackfillReport(filled=0, no_usda_id=[], failed=[], gone=[], rate_limited=False)
    fetched: dict[int, dict | FetchMiss] = {}
    sources: list[tuple[Table | TinyDB, list[dict]]] = [
        (db, db.all()),
        (db.table(USDA_FOODS_TABLE), db.table(USDA_FOODS_TABLE).all()),
    ]
    for table, entries in sources:
        for entry in entries:
            per_100g = entry.get("per_100g")
            if not isinstance(per_100g, dict) or RETINOL_KEY in per_100g:
                continue
            if table is db:
                label, fdc_id = entry["original_name"], entry.get("usda_fdc_id")
                cond = Query().original_name == label
            else:
                label, fdc_id = f"FDC {entry['fdc_id']}", entry["fdc_id"]
                cond = Query().fdc_id == fdc_id
            if fdc_id is None:
                report["no_usda_id"].append(label)
                continue
            if fdc_id not in fetched:
                if report["rate_limited"]:
                    continue
                fetched[fdc_id] = fetch_usda_food_or_miss(fdc_id, api_key)
            raw = fetched[fdc_id]
            if raw == "rate_limited":
                report["rate_limited"] = True
                continue
            _, miss = _write_retinol(table, cond, per_100g, fdc_id, raw, label)
            if miss == "gone":
                report["gone"].append(label)
            elif miss is not None:
                report["failed"].append(label)
            else:
                report["filled"] += 1
    return report


def get_usda_food(fdc_id: int, db: TinyDB, api_key: str) -> UsdaFood | None:
    """Get a USDA food by ID from the usda_foods cache, fetching it on a miss.

    A cached entry is always reused; one written before retinol was stored gets
    it filled in (`fill_retinol`). A failed fetch caches nothing, so it is
    retried on the next run.
    """
    table = db.table(USDA_FOODS_TABLE)
    cached = table.search(Query().fdc_id == fdc_id)
    if cached:
        entry = cached[0]
        per_100g, _ = fill_retinol(
            table, Query().fdc_id == fdc_id, entry["per_100g"], fdc_id, api_key, f"FDC {fdc_id}"
        )
        return UsdaFood(
            fdc_id=entry["fdc_id"],
            usda_name=entry["usda_name"],
            usda_dataset=entry["usda_dataset"],
            per_100g=per_100g,
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
    table.upsert({**food, "last_updated": str(date.today())}, Query().fdc_id == fdc_id)
    return food


def _is_current(entry: dict) -> bool:
    """Whether a cache entry's match_version is at least the current one.

    ">=", not "==": a device running older code must not re-pick an entry a
    newer device already stamped (and vice versa after the next version
    bump), or the two would keep rewriting each other's matches forever.
    """
    return entry.get("match_version", 0) >= MATCH_VERSION


def _is_fresh_not_found(entry: dict) -> bool:
    """Whether a not-found record can still be trusted without asking USDA again."""
    try:
        checked = date.fromisoformat(entry["last_updated"])
    except (KeyError, TypeError, ValueError):
        return False
    return _is_current(entry) and (date.today() - checked).days < NOT_FOUND_TTL_DAYS


def _match_from_entry(entry: dict, confidence: str | None = None) -> FoodMatch:
    return FoodMatch(
        per_100g=entry["per_100g"],
        usda_name=entry.get("usda_name", ""),
        confidence=confidence or entry.get("confidence", ""),
        macro_distance=entry.get("macro_distance"),
        usda_macros=entry.get("usda_macros"),
    )


def _with_retinol(
    entry: dict, db: TinyDB, food_name: str, api_key: str, confidence: str | None = None
) -> FoodMatch:
    """The cached match, with retinol filled into an entry written without it.

    A failed fill leaves the entry as it was: the food is still used, with its
    retinol counted as zero for the run.
    """
    per_100g, _ = fill_retinol(
        db, Query().original_name == food_name, entry["per_100g"],
        entry.get("usda_fdc_id"), api_key, food_name,
    )
    return _match_from_entry({**entry, "per_100g": per_100g}, confidence)


def _matched_by(totals: LoggedTotals | None) -> str:
    """How a pick is made: by proportions when the logged weight is a placeholder."""
    return "proportions" if totals is not None else "per_100g"


def _made_the_same_way(entry: dict, totals: LoggedTotals | None) -> bool:
    """Whether a cached pick was made the way this run would make it.

    An entry without the field predates it and was made by per-100 g figures.
    """
    return entry.get("matched_by", "per_100g") == _matched_by(totals)


def get_food_match(
    food_name: str,
    usda_query: str,
    logged: LoggedMacros | None,
    db: TinyDB,
    api_key: str,
    totals: LoggedTotals | None = None,
) -> FoodMatch | LookupMiss:
    """Get the USDA match and per-100g micro profile for a food.

    A cache entry is reused only when both its query is current and its
    match_version is at least the current one (see `_is_current`). An entry
    picked by older matching logic is re-picked, but kept (as "unverified")
    if the re-pick fails, so a network hiccup never turns a known food into
    an unresolved one.

    A search that ran and found nothing is cached as a not-found record
    (`not_found: True`, no profile) and answered from the cache for
    NOT_FOUND_TTL_DAYS. A failed request caches nothing, so it is retried
    next run. Without a match, returns "not_in_usda" or "lookup_failed".

    A pick made from an incomplete candidate set (the head-word search
    failed) is cached but left without `match_version`, so it is treated as
    outdated and retried on the next run instead of being trusted as final.

    With `totals` (the food's logged weight is a placeholder) candidates are
    scored by proportions. Each pick records how it was made (`matched_by`),
    and an entry made the other way is re-picked like an outdated one: a food
    whose weight was fixed in MacroFactor must not keep a proportions pick.
    """
    Food = Query()
    stale_entry = None
    cached = db.search(Food.original_name == food_name)
    if cached:
        entry = cached[0]
        if entry.get("usda_query") == usda_query:
            if entry.get("not_found"):
                if _is_fresh_not_found(entry):
                    return "not_in_usda"
            elif _is_current(entry) and _made_the_same_way(entry, totals):
                return _with_retinol(entry, db, food_name, api_key)
            stale_entry = entry
        else:
            # Mapping changed: the old entry describes a different query
            logger.info("Mapping changed for '%s', refetching...", food_name)
            db.remove(Food.original_name == food_name)

    candidates, complete = get_food_candidates(usda_query, api_key)
    pick = matcher.pick_best(candidates, usda_query, logged, totals)
    if pick is None:
        if stale_entry is not None and not stale_entry.get("not_found"):
            logger.warning("Re-matching '%s' failed; keeping its previous USDA match", food_name)
            return _with_retinol(stale_entry, db, food_name, api_key, confidence="unverified")
        if not complete:
            # USDA could not be asked; an expired not-found record is still its last answer
            return "not_in_usda" if stale_entry is not None else "lookup_failed"
        db.upsert({
            "original_name": food_name,
            "usda_query": usda_query,
            "not_found": True,
            "match_version": MATCH_VERSION,
            "last_updated": str(date.today()),
        }, Food.original_name == food_name)
        return "not_in_usda"

    if stale_entry is not None and stale_entry.get("not_found"):
        # upsert merges fields, so the not_found flag must go with the record
        db.remove(Food.original_name == food_name)

    candidate = pick["candidate"]
    micros = extract_micros_from_usda(candidate["raw"])
    usda_macros = UsdaMacros(
        protein_g=candidate["protein_g"],
        fat_g=candidate["fat_g"],
        carbs_g=candidate["carbs_g"],
        fiber_g=candidate["fiber_g"],
    )
    entry_to_cache = {
        "original_name": food_name,
        "usda_query": usda_query,
        "usda_name": candidate["description"],
        "usda_fdc_id": candidate["fdc_id"],
        "usda_dataset": candidate["data_type"],
        "per_100g": micros,
        "confidence": pick["confidence"],
        "macro_distance": pick["macro_distance"],
        "usda_macros": usda_macros,
        # Always written: upsert merges, so a stale value would otherwise survive.
        "matched_by": _matched_by(totals),
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
        usda_macros=usda_macros,
    )


def count_outdated_matches(
    food_names: list[str], mappings: dict[str, str], db: TinyDB, weight_unknown: Collection[str] = frozenset(),
) -> int:
    """Count cached foods whose query is current but which get_food_match will re-pick.

    That is an entry whose match_version is outdated, or whose pick was made
    the other way than its food in `weight_unknown` needs (see get_food_match).
    """
    Food = Query()
    outdated = 0
    for food_name in food_names:
        mapping = mappings.get(food_name, "")
        if mapping == "skip":
            continue
        query = mapping if mapping else food_name
        cached = db.search(Food.original_name == food_name)
        if not cached or cached[0].get("usda_query") != query:
            continue
        entry = cached[0]
        made_right = entry.get("not_found") or entry.get("matched_by", "per_100g") == (
            "proportions" if food_name in weight_unknown else "per_100g"
        )
        if not _is_current(entry) or not made_right:
            outdated += 1
    return outdated


def _recipe_ingredients(
    recipe: Recipe, db: TinyDB, api_key: str, fetched: dict[int, UsdaFood | None],
) -> list[tuple[float, UsdaFood]] | None:
    """Each ingredient's share and USDA food, or None if any is unavailable.

    `fetched` memoises lookups for the run, so an ID that failed isn't
    requested again for the next food sharing the recipe.
    """
    ingredients = []
    for ingredient in recipe["ingredients"]:
        fdc_id = ingredient["fdc_id"]
        if fdc_id not in fetched:
            fetched[fdc_id] = get_usda_food(fdc_id, db, api_key)
        food = fetched[fdc_id]
        if food is None:
            logger.error("Recipe '%s': USDA food %s is unavailable", recipe["name"], fdc_id)
            return None
        ingredients.append((ingredient["share"], food))
    return ingredients


def _custom_food_match(
    recipe_name: str, ingredients: list[tuple[float, UsdaFood]], logged: LoggedMacros | None,
    totals: LoggedTotals | None = None,
) -> CustomFoodMatch:
    macros = recipe_macros([(share, food["usda_macros"]) for share, food in ingredients])
    if totals is not None:
        distance = matcher.fitted_distance(totals, macros)
    else:
        distance = matcher.macro_distance(logged, macros) if logged is not None else None
    return CustomFoodMatch(
        recipe=recipe_name,
        parts=[
            RecipePart(share=share, fdc_id=food["fdc_id"], usda_name=food["usda_name"], per_100g=food["per_100g"])
            for share, food in ingredients
        ],
        macro_distance=distance,
        weight_unknown=totals is not None,
    )


def enrich_all_foods(
    food_names: list[str],
    mappings: dict[str, str],
    logged_macros: dict[str, LoggedMacros],
    db: TinyDB,
    api_key: str,
    custom_foods: CustomFoods | None = None,
    untranslated: Collection[str] = frozenset(),
    weight_unknown: dict[str, LoggedTotals] | None = None,
) -> EnrichmentResult:
    """Enrich all foods with USDA micro data.

    A food covered by a custom recipe is built from the recipe's ingredients
    and goes into custom, whatever its mapping says (even "skip"). Otherwise:
    resolved foods go into profiles, foods mapped to "skip" into skipped,
    foods USDA has no match for into not_in_usda, and foods whose lookup
    failed into lookup_failed. Foods in `untranslated` (Gemini gave no
    translation this run) go into untranslated without a USDA search or
    cache write. Resolved foods whose match is weak are also listed in
    low_confidence.

    A food in `weight_unknown` (every logged weight a placeholder) is matched
    by the proportions of its summed macros instead of `logged_macros`.
    """
    if custom_foods is None:
        custom_foods = no_custom_foods()
    result: EnrichmentResult = {
        "profiles": {}, "skipped": set(), "not_in_usda": set(), "lookup_failed": set(),
        "untranslated": set(), "low_confidence": {}, "custom": {}, "matched_macros": {},
    }
    if weight_unknown is None:
        weight_unknown = {}
    fetched: dict[int, UsdaFood | None] = {}

    for food_name in food_names:
        recipe_name = custom_foods["by_food"].get(food_name)
        if recipe_name is not None:
            ingredients = _recipe_ingredients(custom_foods["recipes"][recipe_name], db, api_key, fetched)
            if ingredients is None:
                result["lookup_failed"].add(food_name)
                logger.warning(
                    "Could not resolve '%s': an ingredient of recipe '%s' is unavailable",
                    food_name, recipe_name,
                )
            else:
                result["custom"][food_name] = _custom_food_match(
                    recipe_name, ingredients, logged_macros.get(food_name), weight_unknown.get(food_name),
                )
                result["matched_macros"][food_name] = recipe_macros(
                    [(share, food["usda_macros"]) for share, food in ingredients]
                )
            continue

        if food_name in untranslated:
            result["untranslated"].add(food_name)
            logger.warning("'%s' has no translation — not looked up, retried next run", food_name)
            continue

        mapping = mappings.get(food_name, "")

        if mapping == "skip":
            result["skipped"].add(food_name)
            logger.info("Skipping '%s' (mapped to 'skip')", food_name)
            continue

        # Use the mapping if provided, otherwise use the original name
        query = mapping if mapping else food_name
        match = get_food_match(
            food_name, query, logged_macros.get(food_name), db, api_key, weight_unknown.get(food_name),
        )

        if match == "not_in_usda":
            result["not_in_usda"].add(food_name)
            logger.warning("USDA has no match for '%s' (query: '%s')", food_name, query)
            continue
        if match == "lookup_failed":
            result["lookup_failed"].add(food_name)
            logger.warning("Could not look up '%s' (query: '%s') — retried next run", food_name, query)
            continue

        result["profiles"][food_name] = match["per_100g"]
        if match["usda_macros"] is not None:
            result["matched_macros"][food_name] = match["usda_macros"]
        if match["confidence"] == "weak":
            result["low_confidence"][food_name] = LowConfidenceMatch(
                usda_name=match["usda_name"],
                macro_distance=match["macro_distance"],
            )

    return result
