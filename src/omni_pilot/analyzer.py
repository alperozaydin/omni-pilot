"""Analyze daily micronutrient intake against reference ranges."""
from __future__ import annotations

import logging
from collections import defaultdict
from typing import TypedDict

from omni_pilot.config import get_nutrient_target
from omni_pilot.enricher import EnrichmentResult

logger = logging.getLogger(__name__)


class NutrientResult(TypedDict):
    name: str
    unit: str
    daily_avg: float
    target: float | None
    target_type: str
    ul: float | None
    status: str
    pct_of_target: float | None
    coverage_pct: float | None
    is_floor: bool


class LowConfidenceFood(TypedDict):
    name: str
    usda_name: str
    macro_distance: float | None
    grams: float


class CoverageResult(TypedDict):
    total_food_entries: int
    mapped_entries: int
    skipped_entries: int
    unresolved_entries: int
    skipped_foods: list[str]
    unresolved_foods: list[str]
    low_confidence_foods: list[LowConfidenceFood]
    low_confidence_weight_pct: float


class PeriodResult(TypedDict):
    start: str
    end: str
    days: int


class AnalysisResult(TypedDict):
    period: PeriodResult
    nutrients: dict[str, NutrientResult]
    coverage: CoverageResult


# Combined nutrient keys — some targets (like WHO aminos or EPA/DHA) are tracked
# as pairs or combinations, but USDA/enricher stores them individually. 
# The analyzer sums them for comparison.
COMBINED_NUTRIENTS = {
    "methionine_cysteine_g": ["methionine_g", "cysteine_g"],
    "phenylalanine_tyrosine_g": ["phenylalanine_g", "tyrosine_g"],
    "omega3_epa_dha_mg": ["omega3_epa_mg", "omega3_dha_mg"],
}


def determine_status(value: float, target: float, ul: float | None) -> str:
    """Determine nutrient status based on value vs target and UL.

    Returns one of: "ok", "low", "deficient", "high".
    """
    if ul is not None and value > ul:
        return "high"
    if value >= target:
        return "ok"
    if value >= target * 0.5:
        return "low"
    return "deficient"


def analyze(
    entries: list[dict],
    enrichment: EnrichmentResult,
    ref_ranges: dict,
    supplements: dict | None = None,
) -> AnalysisResult:
    """Run the full micronutrient analysis.

    1. For each food entry, scale USDA per-100g data by actual weight
    2. Sum per day
    3. Average across days
    4. Compare against reference ranges

    Coverage is tracked per reference nutrient key: a food with no USDA value
    for a nutrient — or, for a combined nutrient, missing any one component —
    contributes nothing to it, and its weight is booked as unmeasured rather
    than silently as 0.0.
    """
    total_entries = len(entries)
    mapped_entries = 0
    skipped_entries = 0
    unresolved_entries = 0
    skipped_food_names: set[str] = set()
    unresolved_food_names: set[str] = set()

    # Collect all nutrient keys from reference_ranges
    nutrient_keys = list(ref_ranges["nutrients"].keys())

    # Daily totals: date -> reference nutrient key -> total (components combined)
    daily_totals: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    # Reference nutrient key -> consumed grams that did / did not have a USDA value
    measured_weight_g: dict[str, float] = defaultdict(float)
    unmeasured_weight_g: dict[str, float] = defaultdict(float)
    # Consumed grams of all analysed entries, and of weakly matched foods
    analysed_weight_g = 0.0
    low_confidence_weight_g: dict[str, float] = defaultdict(float)
    dates: set[str] = set()

    profiles = enrichment["profiles"]

    for entry in entries:
        food_name = entry["food_name"]
        food_micros = profiles.get(food_name)

        if food_micros is None:
            # Skipped and unresolved foods are both excluded from coverage on
            # both sides; they differ only in how the report labels them.
            if food_name in enrichment["skipped"]:
                skipped_entries += 1
                skipped_food_names.add(food_name)
            else:
                unresolved_entries += 1
                unresolved_food_names.add(food_name)
            continue

        mapped_entries += 1
        entry_date = entry["date"]
        dates.add(entry_date)
        total_weight_g = entry["total_weight_g"]
        scale_factor = total_weight_g / 100.0
        analysed_weight_g += total_weight_g
        if total_weight_g > 0 and food_name in enrichment["low_confidence"]:
            low_confidence_weight_g[food_name] += total_weight_g

        for nutrient_key in nutrient_keys:
            component_keys = COMBINED_NUTRIENTS.get(nutrient_key, [nutrient_key])
            component_values = [food_micros.get(ck) for ck in component_keys]

            if any(value is None for value in component_values):
                unmeasured_weight_g[nutrient_key] += total_weight_g
                continue

            measured_weight_g[nutrient_key] += total_weight_g
            # USDA provides EPA and DHA in grams, but our reference target is in mg
            contribution = sum(
                value * 1000.0 if ck in ("omega3_epa_mg", "omega3_dha_mg") else value
                for ck, value in zip(component_keys, component_values)
            )
            daily_totals[entry_date][nutrient_key] += contribution * scale_factor

    # Compute daily averages
    num_days = len(dates) if dates else 1
    sorted_dates = sorted(dates)

    if supplements is None:
        supplements = {}

    # Build nutrient results
    nutrients_result: dict[str, NutrientResult] = {}

    for nutrient_key in nutrient_keys:
        nutrient_info = ref_ranges["nutrients"][nutrient_key]
        target, ul, target_type = get_nutrient_target(nutrient_key, ref_ranges)

        total_across_days = sum(
            daily_totals[d].get(nutrient_key, 0.0) for d in sorted_dates
        )
        daily_avg = total_across_days / num_days if num_days > 0 else 0.0

        # Add supplement contribution. Supplements carry no weight, so they do
        # not participate in coverage.
        daily_avg += supplements.get(nutrient_key, 0.0)

        measured = measured_weight_g.get(nutrient_key, 0.0)
        unmeasured = unmeasured_weight_g.get(nutrient_key, 0.0)
        attributable = measured + unmeasured
        # Rounded once, here: the "*" rule and the displayed figure must agree.
        coverage_pct = (
            round(100.0 * measured / attributable, 1) if attributable > 0 else None
        )

        # Determine status
        if target is not None:
            status = determine_status(daily_avg, target, ul)
            pct = (daily_avg / target * 100) if target > 0 else None
        else:
            status = "unknown"
            pct = None

        # Absent data can only raise a value, so only low/deficient are unsafe.
        # Unknown coverage (None) is marked too: no attributable weight at all is
        # less supportable than 0% coverage, not more.
        is_floor = status in ("low", "deficient") and (
            coverage_pct is None or coverage_pct < 100.0
        )

        nutrients_result[nutrient_key] = NutrientResult(
            name=nutrient_info["name"],
            unit=nutrient_info["unit"],
            daily_avg=round(daily_avg, 2),
            target=target,
            target_type=target_type,
            ul=ul,
            status=status,
            pct_of_target=round(pct, 1) if pct is not None else None,
            coverage_pct=coverage_pct,
            is_floor=is_floor,
        )

    low_confidence_foods = sorted(
        (
            LowConfidenceFood(
                name=food_name,
                usda_name=enrichment["low_confidence"][food_name]["usda_name"],
                macro_distance=enrichment["low_confidence"][food_name]["macro_distance"],
                grams=grams,
            )
            for food_name, grams in low_confidence_weight_g.items()
        ),
        key=lambda food: (-food["grams"], food["name"]),
    )
    low_confidence_weight_pct = (
        round(100.0 * sum(low_confidence_weight_g.values()) / analysed_weight_g, 1)
        if analysed_weight_g > 0 else 0.0
    )

    return AnalysisResult(
        period=PeriodResult(
            start=sorted_dates[0] if sorted_dates else "",
            end=sorted_dates[-1] if sorted_dates else "",
            days=num_days,
        ),
        nutrients=nutrients_result,
        coverage=CoverageResult(
            total_food_entries=total_entries,
            mapped_entries=mapped_entries,
            skipped_entries=skipped_entries,
            unresolved_entries=unresolved_entries,
            skipped_foods=sorted(skipped_food_names),
            unresolved_foods=sorted(unresolved_food_names),
            low_confidence_foods=low_confidence_foods,
            low_confidence_weight_pct=low_confidence_weight_pct,
        ),
    )
