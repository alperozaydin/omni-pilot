"""Analyze daily micronutrient intake against reference ranges."""
from __future__ import annotations

import logging
from collections import defaultdict
from typing import TypedDict

from omni_pilot.config import get_nutrient_target

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


class CoverageResult(TypedDict):
    total_food_entries: int
    mapped_entries: int
    skipped_entries: int
    unresolved_entries: int
    skipped_foods: list[str]
    unresolved_foods: list[str]


class PeriodResult(TypedDict):
    start: str
    end: str
    days: int


class AnalysisResult(TypedDict):
    period: PeriodResult
    nutrients: dict[str, NutrientResult]
    coverage: CoverageResult


# Combined amino acid keys — WHO tracks these as pairs, but USDA/enricher
# stores them individually. The analyzer sums them for comparison.
COMBINED_AMINOS = {
    "methionine_cysteine_g": ["methionine_g", "cysteine_g"],
    "phenylalanine_tyrosine_g": ["phenylalanine_g", "tyrosine_g"],
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
    enriched: dict[str, dict[str, float | None] | None],
    ref_ranges: dict,
    supplements: dict | None = None,
) -> AnalysisResult:
    """Run the full micronutrient analysis.

    1. For each food entry, scale USDA per-100g data by actual weight
    2. Sum per day
    3. Average across days
    4. Compare against reference ranges
    """
    # Track coverage
    total_entries = len(entries)
    mapped_entries = 0
    skipped_entries = 0
    unresolved_entries = 0
    skipped_food_names: set[str] = set()
    unresolved_food_names: set[str] = set()

    # Collect all nutrient keys from reference_ranges
    nutrient_keys = list(ref_ranges["nutrients"].keys())

    # Daily totals: date -> nutrient_key -> total
    daily_totals: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    dates: set[str] = set()

    for entry in entries:
        food_name = entry["food_name"]
        food_micros = enriched.get(food_name)

        if food_micros is None:
            # If the key exists in enriched but is None, it was explicitly skipped
            if food_name in enriched:
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

        # Add scaled nutrients to daily totals
        for nutrient_key in food_micros:
            value = food_micros.get(nutrient_key)
            if value is not None:
                daily_totals[entry_date][nutrient_key] += value * scale_factor

    # Compute daily averages
    num_days = len(dates) if dates else 1
    sorted_dates = sorted(dates)

    # Build nutrient results
    nutrients_result: dict[str, NutrientResult] = {}

    for nutrient_key in nutrient_keys:
        nutrient_info = ref_ranges["nutrients"][nutrient_key]
        target, ul, target_type = get_nutrient_target(nutrient_key, ref_ranges)

        # Determine which enricher keys to sum for this reference key
        if nutrient_key in COMBINED_AMINOS:
            component_keys = COMBINED_AMINOS[nutrient_key]
        else:
            component_keys = [nutrient_key]

        # Sum across days, then average
        total_across_days = 0.0
        for d in sorted_dates:
            day_total = sum(
                daily_totals[d].get(ck, 0.0) for ck in component_keys
            )
            total_across_days += day_total

        daily_avg = total_across_days / num_days if num_days > 0 else 0.0

        if supplements is None:
            supplements = {}
            
        # Add supplement contribution
        daily_avg += supplements.get(nutrient_key, 0.0)

        # Determine status
        if target is not None:
            status = determine_status(daily_avg, target, ul)
            pct = (daily_avg / target * 100) if target > 0 else None
        else:
            status = "unknown"
            pct = None

        nutrients_result[nutrient_key] = NutrientResult(
            name=nutrient_info["name"],
            unit=nutrient_info["unit"],
            daily_avg=round(daily_avg, 2),
            target=target,
            target_type=target_type,
            ul=ul,
            status=status,
            pct_of_target=round(pct, 1) if pct is not None else None,
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
        ),
    )
