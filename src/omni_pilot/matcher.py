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
