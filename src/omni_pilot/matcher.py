"""Choose the USDA search hit that matches what was actually eaten.

USDA search ranks by keywords, so its first hit is often the wrong food or the
wrong form of the right food. MacroFactor logs protein, fat and carbs for every
entry, and those logged macros are the only ground truth for what a food really
was. The matcher first keeps candidates that are the food the query names, then
lets the logged macros decide between its forms (raw vs cooked, fat level,
canned vs boiled) without letting them swap in a different food.
"""
from __future__ import annotations

import re
from collections import defaultdict
from typing import TypedDict

# Distances are divided by at least this many kcal, so tiny absolute
# differences in very low-calorie foods (tomatoes, diet drinks) stay small.
KCAL_FLOOR = 50.0
# Candidates scoring within this much of the best count as tied.
NEAR_TIE_BAND = 0.05
# A match whose macro distance is at or below this is "good"; above it, "weak".
GOOD_DISTANCE = 0.25
# The same for a food whose logged weight is a placeholder. Its grams are fitted
# to each candidate first, a free parameter that makes any distance look
# better, so the bar is stricter: at 0.25 raw rice alone would pass for rice
# with chickpeas (0.22).
GOOD_FIT_DISTANCE = 0.15
# kcal per gram of protein, fat and carbs, weighting each macro in the gram fit.
_FIT_WEIGHTS = (4.0, 9.0, 4.0)
# Score penalty for a candidate containing none of the query's words. Large
# enough that a different food with closer macros (camembert for feta) loses.
IDENTITY_WEIGHT = 0.5
# First segments too broad to identify a food on their own ("Snacks, ...",
# "Beverages, ..."); the second segment is read as part of the name.
GENERIC_HEADS = {
    "snack", "beverage", "cereal", "cereal ready-to-eat", "alcoholic beverage",
    "carbonated beverage", "fish", "crustacean", "nut", "seed", "spice",
    "sauce", "salad dressing",
}

_STOP_WORDS = {"and", "or", "with", "in", "of"}
_WORD_RE = re.compile(r"[a-z0-9-]+")
_PARENS_RE = re.compile(r"\([^)]*\)")


class LoggedMacros(TypedDict):
    """Macros per 100 g, as logged in MacroFactor."""

    kcal: float
    protein_g: float
    fat_g: float
    carbs_g: float


class LoggedTotals(TypedDict):
    """Macros summed over entries whose logged weight is a placeholder. Not per 100 g."""

    kcal: float
    protein_g: float
    fat_g: float
    carbs_g: float


class LoggedFoods(TypedDict):
    """Logged macros per food, split by whether the logged weight can be trusted.

    A food is in at most one of the two.
    """

    per_100g: dict[str, LoggedMacros]
    # Every entry's weight is a placeholder: only the proportions are known.
    weight_unknown: dict[str, LoggedTotals]


class Candidate(TypedDict):
    """One USDA search hit, normalised for matching."""

    fdc_id: int
    description: str
    data_type: str
    rank: int
    protein_g: float | None
    fat_g: float | None
    carbs_g: float | None
    fiber_g: float | None
    raw: dict


class UsdaMacros(TypedDict):
    """Per-100 g macros of a USDA food, or of a recipe built from USDA foods."""

    protein_g: float | None
    fat_g: float | None
    carbs_g: float | None
    fiber_g: float | None


class Pick(TypedDict):
    candidate: Candidate
    macro_distance: float | None
    confidence: str


def logged_macros_per_100g(entries: list[dict]) -> dict[str, LoggedMacros]:
    """Average each food's logged macros per 100 g, weighted by grams eaten.

    Entries without a positive weight are ignored, so a food with no weight at
    all gets no entry.
    """
    totals: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0, 0.0, 0.0, 0.0])
    for entry in entries:
        grams = entry["total_weight_g"]
        if grams <= 0:
            continue
        food_totals = totals[entry["food_name"]]
        food_totals[0] += grams
        food_totals[1] += entry["calories_kcal"]
        food_totals[2] += entry["protein_g"]
        food_totals[3] += entry["fat_g"]
        food_totals[4] += entry["carbs_g"]

    return {
        food_name: LoggedMacros(
            kcal=100.0 * kcal / grams,
            protein_g=100.0 * protein / grams,
            fat_g=100.0 * fat / grams,
            carbs_g=100.0 * carbs / grams,
        )
        for food_name, (grams, kcal, protein, fat, carbs) in totals.items()
    }


def logged_macros_for_matching(entries: list[dict]) -> LoggedFoods:
    """Logged macros per food for matching, leaving placeholder weights out.

    A food's per-100 g figures come from its entries with a real weight only. A
    food whose every entry has a placeholder weight gets its summed macros
    instead, so it is matched by proportions (see `fitted_distance`).
    """
    per_100g = logged_macros_per_100g([e for e in entries if not e.get("weight_is_placeholder", False)])
    totals: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0, 0.0, 0.0])
    for entry in entries:
        if not entry.get("weight_is_placeholder", False) or entry["food_name"] in per_100g:
            continue
        food_totals = totals[entry["food_name"]]
        food_totals[0] += entry["calories_kcal"]
        food_totals[1] += entry["protein_g"]
        food_totals[2] += entry["fat_g"]
        food_totals[3] += entry["carbs_g"]
    return LoggedFoods(
        per_100g=per_100g,
        weight_unknown={
            food_name: LoggedTotals(kcal=kcal, protein_g=protein, fat_g=fat, carbs_g=carbs)
            for food_name, (kcal, protein, fat, carbs) in totals.items()
        },
    )


def fit_grams(logged: LoggedTotals, macros: UsdaMacros) -> float | None:
    """The grams of a food whose macros come closest to the logged ones.

    Least squares over protein, fat and carbs, each weighted by its kcal per
    gram. Never below the logged macros' own weight: no food weighs less than
    its protein, fat and carbs, though a wrong match can fit there. None when
    the food lacks a macro or has none at all.
    """
    food = (macros["protein_g"], macros["fat_g"], macros["carbs_g"])
    if any(value is None for value in food):
        return None
    eaten = (logged["protein_g"], logged["fat_g"], logged["carbs_g"])
    denominator = sum(w * w * u * u for w, u in zip(_FIT_WEIGHTS, food))
    if denominator == 0:
        return None
    numerator = sum(w * w * l * u for w, l, u in zip(_FIT_WEIGHTS, eaten, food))
    return max(100.0 * numerator / denominator, sum(eaten))


def fitted_distance(logged: LoggedTotals, macros: UsdaMacros) -> float | None:
    """`macro_distance` with the logged macros taken per 100 g at the fitted grams."""
    grams = fit_grams(logged, macros)
    if grams is None:
        return None
    scale = 100.0 / grams
    per_100g = LoggedMacros(
        kcal=logged["kcal"] * scale,
        protein_g=logged["protein_g"] * scale,
        fat_g=logged["fat_g"] * scale,
        carbs_g=logged["carbs_g"] * scale,
    )
    return macro_distance(per_100g, macros)


def _singular(word: str) -> str:
    if word.endswith("ies") and len(word) > 4:
        return word[:-3] + "y"
    if word.endswith("oes"):
        return word[:-2]
    if word.endswith("s") and not word.endswith("ss") and len(word) > 3:
        return word[:-1]
    return word


def _segments(text: str) -> list[list[str]]:
    """Split a USDA-style description into comma segments of normalised words.

    Parenthesised text is dropped, and segments left empty by that are removed.
    """
    text = _PARENS_RE.sub("", text.lower())
    segments = []
    for raw_segment in text.split(","):
        words = [_singular(w) for w in _WORD_RE.findall(raw_segment) if w not in _STOP_WORDS]
        if words:
            segments.append(words)
    return segments


def head_words(query: str) -> tuple[list[str], int]:
    """Return the words naming the food, and how many segments they span.

    That is the query's first segment, or its first two when the first is
    generic ("snacks, trail mix").
    """
    segments = _segments(query)
    if not segments:
        return [], 0
    if " ".join(segments[0]) in GENERIC_HEADS and len(segments) > 1:
        return segments[0] + segments[1], 2
    return list(segments[0]), 1


def matches_head(description: str, head: list[str], head_segments: int) -> bool:
    """Whether a candidate is the food the query names.

    At least one head word must name the candidate itself (its first segment,
    widened by one when that segment is generic), and every head word must
    appear within its first head_segments + 1 segments. This rejects hits that
    only mention the food in passing, like "Lebanon bologna, beef" for "beef".
    """
    segments = _segments(description)
    if not segments:
        return False
    name = list(segments[0])
    if " ".join(segments[0]) in GENERIC_HEADS and len(segments) > 1:
        name += segments[1]
    prefix = {w for segment in segments[: head_segments + 1] for w in segment}
    return any(w in name for w in head) and all(w in prefix for w in head)


def _word_overlap(query_words: set[str], description: str) -> float:
    if not query_words:
        return 0.0
    description_words = {w for segment in _segments(description) for w in segment}
    return len(query_words & description_words) / len(query_words)


def macro_distance(logged: LoggedMacros, macros: UsdaMacros) -> float | None:
    """How far a food's macros are from the logged ones, relative to kcal.

    EU labels count carbs without fiber while USDA counts them with it, so the
    closer of the two readings is used. None when a macro is missing.
    """
    protein, fat, carbs = macros["protein_g"], macros["fat_g"], macros["carbs_g"]
    if protein is None or fat is None or carbs is None:
        return None
    carbs_delta = abs(logged["carbs_g"] - carbs)
    if macros["fiber_g"] is not None:
        carbs_delta = min(carbs_delta, abs(logged["carbs_g"] - (carbs - macros["fiber_g"])))
    weighted = (
        4 * abs(logged["protein_g"] - protein)
        + 9 * abs(logged["fat_g"] - fat)
        + 4 * carbs_delta
    )
    return weighted / max(logged["kcal"], KCAL_FLOOR)


def pick_best(
    candidates: list[Candidate],
    query: str,
    logged: LoggedMacros | None,
    totals: LoggedTotals | None = None,
) -> Pick | None:
    """Pick the candidate that is the named food and best fits the logged macros.

    With `totals` (the food's logged weight is a placeholder) each candidate is
    scored at the grams fitted to it, so only the proportions of protein, fat
    and carbs count, and the bar for "good" is GOOD_FIT_DISTANCE.
    """
    if not candidates:
        return None

    head, head_segments = head_words(query)
    pool = [c for c in candidates if matches_head(c["description"], head, head_segments)]
    no_head_match = not pool
    if no_head_match:
        pool = list(candidates)
    pool.sort(key=lambda c: c["rank"])

    if logged is None and totals is None:
        # No macros to fall back on, and no candidate is even the named food:
        # weaker than an ordinary no_macros pick, which at least has a head match.
        confidence = "weak" if no_head_match else "no_macros"
        return Pick(candidate=pool[0], macro_distance=None, confidence=confidence)

    query_words = {w for segment in _segments(query) for w in segment}
    scored = []
    for candidate in pool:
        distance = fitted_distance(totals, candidate) if totals is not None else macro_distance(logged, candidate)
        if distance is None:
            continue
        score = distance + IDENTITY_WEIGHT * (1.0 - _word_overlap(query_words, candidate["description"]))
        scored.append((candidate, distance, score))
    if not scored:
        return Pick(candidate=pool[0], macro_distance=None, confidence="weak")

    best_score = min(score for _, _, score in scored)
    tied = [s for s in scored if s[2] <= best_score + NEAR_TIE_BAND]
    # SR Legacy carries far more micronutrients than Foundation, so it wins ties.
    chosen, distance, _ = min(
        tied, key=lambda s: (s[0]["data_type"] != "SR Legacy", s[2], s[0]["rank"])
    )
    good_distance = GOOD_FIT_DISTANCE if totals is not None else GOOD_DISTANCE
    confidence = "good" if distance <= good_distance and not no_head_match else "weak"
    return Pick(candidate=chosen, macro_distance=distance, confidence=confidence)
