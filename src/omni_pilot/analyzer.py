"""Analyze daily micronutrient intake against reference ranges."""
from __future__ import annotations

import logging
from collections import defaultdict
from typing import TypedDict

from omni_pilot.config import get_nutrient_target
from omni_pilot.enricher import RETINOL_KEY, EnrichmentResult
from omni_pilot.matcher import WeightEstimate
from omni_pilot.parser import LastDayCheck

logger = logging.getLogger(__name__)


class HighSource(TypedDict):
    name: str  # a logged food name, or "supplement"
    amount: float  # daily amount, in the nutrient's unit (retinol for a "retinol" UL)


class NutrientResult(TypedDict):
    name: str
    unit: str
    daily_avg: float
    target: float | None
    target_type: str
    ul: float | None
    # What the UL counts: None (the daily average), "supplements" or "retinol".
    ul_applies_to: str | None
    # The figure compared with the UL; None when there is no UL.
    ul_figure: float | None
    status: str
    pct_of_target: float | None
    coverage_pct: float | None
    is_floor: bool
    # Empty unless status is "high"; largest first, then by name.
    high_sources: list[HighSource]


class LowConfidenceFood(TypedDict):
    name: str
    usda_name: str
    macro_distance: float | None
    grams: float


class CustomIngredient(TypedDict):
    fdc_id: int
    usda_name: str
    share_pct: float


class CustomFoodUse(TypedDict):
    name: str
    grams: float
    macro_distance: float | None
    # macro_distance was taken at fitted grams (the logged weight is a placeholder)
    fitted: bool


class EstimatedWeightFood(TypedDict):
    name: str
    estimates: list[WeightEstimate]  # in log order


class CustomRecipeUse(TypedDict):
    recipe: str
    ingredients: list[CustomIngredient]  # in recipe order
    foods: list[CustomFoodUse]  # by grams, descending


class CoverageResult(TypedDict):
    total_food_entries: int
    mapped_entries: int
    skipped_entries: int
    not_in_usda_entries: int
    lookup_failed_entries: int
    untranslated_entries: int
    skipped_foods: list[str]
    not_in_usda_foods: list[str]
    lookup_failed_foods: list[str]
    untranslated_foods: list[str]
    not_in_usda_weight_pct: float
    lookup_failed_weight_pct: float
    untranslated_weight_pct: float
    low_confidence_foods: list[LowConfidenceFood]
    low_confidence_weight_pct: float
    # The same share, of analysed calories: placeholder weights can hide it by grams.
    low_confidence_kcal_pct: float
    # Foods with placeholder weights, outside skipped foods, by grams eaten.
    estimated_weights: list[EstimatedWeightFood]
    custom_recipes: list[CustomRecipeUse]
    # Share of the food fiber total taken from MacroFactor's logged figures
    # rather than USDA; None when no fiber was counted at all.
    logged_fiber_pct: float | None


class PeriodResult(TypedDict):
    start: str
    end: str
    days: int
    # The export's last day, left out of the analysis as only partly logged; None when all days count.
    left_out_day: LastDayCheck | None


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

# The one nutrient taken from MacroFactor's own figure when the entry has one.
LOGGED_NUTRIENT = "fiber_g"


def _food_parts(
    food_name: str, enrichment: EnrichmentResult,
) -> list[tuple[float, dict[str, float | None]]] | None:
    """A food's nutrient sources with their weight shares, or None if it isn't analysed.

    A USDA-matched food is one part; a custom food is one part per recipe
    ingredient.
    """
    profile = enrichment["profiles"].get(food_name)
    if profile is not None:
        return [(1.0, profile)]
    custom = enrichment["custom"].get(food_name)
    if custom is not None:
        return [(part["share"], part["per_100g"]) for part in custom["parts"]]
    return None


def _high_sources(
    food_totals: dict[str, float], num_days: int, supplement: float | None,
) -> list[HighSource]:
    """The sources of a UL figure as daily amounts, largest first; zero amounts left out."""
    sources = [
        HighSource(name=food_name, amount=round(total / num_days, 2))
        for food_name, total in food_totals.items()
    ]
    if supplement is not None:
        sources.append(HighSource(name="supplement", amount=round(supplement, 2)))
    return sorted(
        (source for source in sources if source["amount"] > 0),
        key=lambda source: (-source["amount"], source["name"]),
    )


def _custom_recipe_uses(
    custom: dict, weights_g: dict[str, float],
) -> list[CustomRecipeUse]:
    """Group the custom foods eaten in the period by recipe, heaviest first."""
    by_recipe: dict[str, CustomRecipeUse] = {}
    for food_name, grams in weights_g.items():
        match = custom[food_name]
        use = by_recipe.get(match["recipe"])
        if use is None:
            use = by_recipe[match["recipe"]] = CustomRecipeUse(
                recipe=match["recipe"],
                ingredients=[
                    CustomIngredient(fdc_id=part["fdc_id"], usda_name=part["usda_name"], share_pct=part["share"] * 100)
                    for part in match["parts"]
                ],
                foods=[],
            )
        use["foods"].append(CustomFoodUse(
            name=food_name, grams=grams, macro_distance=match["macro_distance"],
            fitted=match.get("weight_unknown", False),
        ))
    for use in by_recipe.values():
        use["foods"].sort(key=lambda food: (-food["grams"], food["name"]))
    return sorted(
        by_recipe.values(),
        key=lambda use: (-sum(food["grams"] for food in use["foods"]), use["recipe"]),
    )


def determine_status(
    value: float, target: float, ul: float | None, ul_figure: float | None = None,
) -> str:
    """Determine nutrient status based on value vs target and UL.

    The UL is compared with `ul_figure` when given (the intake the UL
    covers), else with `value`; the target always with `value`.

    Returns one of: "ok", "low", "deficient", "high".
    """
    if ul is not None and (value if ul_figure is None else ul_figure) > ul:
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
    *,
    left_out_day: LastDayCheck | None = None,
) -> AnalysisResult:
    """Run the full micronutrient analysis.

    1. For each food entry, scale USDA per-100g data by actual weight
    2. Sum per day
    3. Average across days
    4. Compare against reference ranges

    Coverage is tracked per reference nutrient key: a food with no USDA value
    for a nutrient — or, for a combined nutrient, missing any one component —
    contributes nothing to it, and its weight is booked as unmeasured rather
    than silently as 0.0. A custom food is counted part by part, each part
    weighing its share of the entry, so an ingredient missing a nutrient
    books only its own share as unmeasured. A not-in-USDA, failed or
    untranslated food is unmeasured for every nutrient, but only on a day that is counted: a day
    with no resolved food leaves the average entirely.

    Fiber is the exception to "USDA per 100 g": an analysed entry whose
    logged MacroFactor fiber is above zero counts that figure as it is, and
    its whole weight as measured. An empty or zero cell is not trusted (the
    export uses both for "unknown") and falls back to USDA.

    The UL is compared with the intake it covers (`ul_applies_to`): the daily
    average, the supplement alone, or retinol from food plus the supplement.
    A High lists its sources: each logged food name (a custom food as one)
    with its daily amount, and the supplement.
    """
    total_entries = len(entries)
    mapped_entries = 0
    skipped_entries = 0
    not_in_usda_entries = 0
    lookup_failed_entries = 0
    untranslated_entries = 0
    skipped_food_names: set[str] = set()
    not_in_usda_food_names: set[str] = set()
    lookup_failed_food_names: set[str] = set()
    untranslated_food_names: set[str] = set()

    # Collect all nutrient keys from reference_ranges
    nutrient_keys = list(ref_ranges["nutrients"].keys())
    # Nutrients whose UL counts food: their totals are also kept per logged food name
    food_ul_keys = {
        key for key, info in ref_ranges["nutrients"].items()
        if info.get("ul") is not None and info.get("ul_applies_to") is None
    }

    # Daily totals: date -> reference nutrient key -> total (components combined)
    daily_totals: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    # Reference nutrient key -> consumed grams that did / did not have a USDA value
    measured_weight_g: dict[str, float] = defaultdict(float)
    unmeasured_weight_g: dict[str, float] = defaultdict(float)
    # Consumed grams of all analysed entries, and of weakly matched foods
    analysed_weight_g = 0.0
    low_confidence_weight_g: dict[str, float] = defaultdict(float)
    custom_weight_g: dict[str, float] = defaultdict(float)
    # Calories of all analysed entries, and of weakly matched foods
    analysed_kcal = 0.0
    low_confidence_kcal = 0.0
    estimates: dict[str, list[WeightEstimate]] = defaultdict(list)
    logged_fiber_total_g = 0.0
    # Consumed grams of not-in-USDA, failed and untranslated foods, per date and per kind
    unresolved_weight_by_date_g: dict[str, float] = defaultdict(float)
    not_in_usda_weight_g = 0.0
    lookup_failed_weight_g = 0.0
    untranslated_weight_g = 0.0
    dates: set[str] = set()
    # Reference nutrient key -> logged food name -> total over the counted days
    food_totals: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    # Logged food name -> retinol total (mcg) over the counted days; no coverage
    retinol_totals: dict[str, float] = defaultdict(float)

    for entry in entries:
        food_name = entry["food_name"]
        parts = _food_parts(food_name, enrichment)
        if "weight_estimate" in entry and food_name not in enrichment["skipped"]:
            estimates[food_name].append(entry["weight_estimate"])

        if parts is None:
            # A skipped food is excluded from coverage on both sides. A
            # not-in-USDA, failed or untranslated food is a hole in its day's totals, booked
            # as unmeasured once the counted days are known.
            if food_name in enrichment["skipped"]:
                skipped_entries += 1
                skipped_food_names.add(food_name)
                continue
            if food_name in enrichment["not_in_usda"]:
                not_in_usda_entries += 1
                not_in_usda_food_names.add(food_name)
                not_in_usda_weight_g += entry["total_weight_g"]
            elif food_name in enrichment["untranslated"]:
                untranslated_entries += 1
                untranslated_food_names.add(food_name)
                untranslated_weight_g += entry["total_weight_g"]
            else:
                lookup_failed_entries += 1
                lookup_failed_food_names.add(food_name)
                lookup_failed_weight_g += entry["total_weight_g"]
            unresolved_weight_by_date_g[entry["date"]] += entry["total_weight_g"]
            continue

        mapped_entries += 1
        entry_date = entry["date"]
        dates.add(entry_date)
        total_weight_g = entry["total_weight_g"]
        analysed_weight_g += total_weight_g
        entry_kcal = entry.get("calories_kcal", 0.0)
        analysed_kcal += entry_kcal
        if food_name in enrichment["low_confidence"]:
            low_confidence_kcal += entry_kcal
        if total_weight_g > 0 and food_name in enrichment["low_confidence"]:
            low_confidence_weight_g[food_name] += total_weight_g
        if total_weight_g > 0 and food_name in enrichment["custom"]:
            custom_weight_g[food_name] += total_weight_g

        logged_fiber_g = entry.get("fiber_g") or 0.0
        uses_logged_fiber = logged_fiber_g > 0 and LOGGED_NUTRIENT in ref_ranges["nutrients"]
        if uses_logged_fiber:
            # Already the figure for the whole entry: not scaled by weight.
            measured_weight_g[LOGGED_NUTRIENT] += total_weight_g
            daily_totals[entry_date][LOGGED_NUTRIENT] += logged_fiber_g
            logged_fiber_total_g += logged_fiber_g
            if LOGGED_NUTRIENT in food_ul_keys:
                food_totals[LOGGED_NUTRIENT][food_name] += logged_fiber_g

        for share, food_micros in parts:
            part_weight_g = total_weight_g * share
            retinol_totals[food_name] += (food_micros.get(RETINOL_KEY) or 0.0) * (part_weight_g / 100.0)
            for nutrient_key in nutrient_keys:
                if uses_logged_fiber and nutrient_key == LOGGED_NUTRIENT:
                    continue
                component_keys = COMBINED_NUTRIENTS.get(nutrient_key, [nutrient_key])
                component_values = [food_micros.get(ck) for ck in component_keys]

                if any(value is None for value in component_values):
                    unmeasured_weight_g[nutrient_key] += part_weight_g
                    continue

                measured_weight_g[nutrient_key] += part_weight_g
                # USDA provides EPA and DHA in grams, but our reference target is in mg
                contribution = sum(
                    value * 1000.0 if ck in ("omega3_epa_mg", "omega3_dha_mg") else value
                    for ck, value in zip(component_keys, component_values)
                )
                amount = contribution * (part_weight_g / 100.0)
                daily_totals[entry_date][nutrient_key] += amount
                if nutrient_key in food_ul_keys:
                    food_totals[nutrient_key][food_name] += amount

    unresolved_on_counted_days_g = sum(
        grams for date, grams in unresolved_weight_by_date_g.items() if date in dates
    )
    for nutrient_key in nutrient_keys:
        unmeasured_weight_g[nutrient_key] += unresolved_on_counted_days_g

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
        supplement = supplements.get(nutrient_key)
        daily_avg += supplement or 0.0

        # The figure the UL is compared with: the intake the UL covers.
        ul_applies_to = nutrient_info.get("ul_applies_to")
        if ul is None:
            ul_figure = None
        elif ul_applies_to == "supplements":
            ul_figure = supplement or 0.0
        elif ul_applies_to == "retinol":
            ul_figure = sum(retinol_totals.values()) / num_days + (supplement or 0.0)
        else:
            ul_figure = daily_avg

        measured = measured_weight_g.get(nutrient_key, 0.0)
        unmeasured = unmeasured_weight_g.get(nutrient_key, 0.0)
        attributable = measured + unmeasured
        # Rounded once, here: the "*" rule and the displayed figure must agree.
        coverage_pct = (
            round(100.0 * measured / attributable, 1) if attributable > 0 else None
        )

        # Judged on the figures the reports show (one decimal), so 109.96 shown as
        # 110.0 is not "low" against a target of 110.
        if target is not None:
            status = determine_status(
                round(daily_avg, 1), target, ul, round(ul_figure, 1) if ul_figure is not None else None,
            )
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

        high_sources: list[HighSource] = []
        if status == "high":
            if ul_applies_to == "supplements":
                high_sources = _high_sources({}, num_days, supplement)
            elif ul_applies_to == "retinol":
                high_sources = _high_sources(retinol_totals, num_days, supplement)
            else:
                high_sources = _high_sources(food_totals[nutrient_key], num_days, supplement)

        nutrients_result[nutrient_key] = NutrientResult(
            name=nutrient_info["name"],
            unit=nutrient_info["unit"],
            daily_avg=round(daily_avg, 2),
            target=target,
            target_type=target_type,
            ul=ul,
            ul_applies_to=ul_applies_to,
            ul_figure=round(ul_figure, 2) if ul_figure is not None else None,
            status=status,
            pct_of_target=round(pct, 1) if pct is not None else None,
            coverage_pct=coverage_pct,
            is_floor=is_floor,
            high_sources=high_sources,
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
    low_confidence_kcal_pct = (
        round(100.0 * low_confidence_kcal / analysed_kcal, 1) if analysed_kcal > 0 else 0.0
    )
    estimated_weights = sorted(
        (
            EstimatedWeightFood(name=food_name, estimates=food_estimates)
            for food_name, food_estimates in estimates.items()
        ),
        key=lambda food: (
            -sum(e["estimated_g"] if e["estimated_g"] is not None else e["logged_g"] for e in food["estimates"]),
            food["name"],
        ),
    )
    food_weight_g = analysed_weight_g + not_in_usda_weight_g + lookup_failed_weight_g + untranslated_weight_g
    food_fiber_total_g = sum(daily_totals[d].get(LOGGED_NUTRIENT, 0.0) for d in sorted_dates)
    logged_fiber_pct = (
        round(100.0 * logged_fiber_total_g / food_fiber_total_g, 1) if food_fiber_total_g > 0 else None
    )

    def food_weight_pct(grams: float) -> float:
        return round(100.0 * grams / food_weight_g, 1) if food_weight_g > 0 else 0.0

    return AnalysisResult(
        period=PeriodResult(
            start=sorted_dates[0] if sorted_dates else "",
            end=sorted_dates[-1] if sorted_dates else "",
            days=num_days,
            left_out_day=left_out_day,
        ),
        nutrients=nutrients_result,
        coverage=CoverageResult(
            total_food_entries=total_entries,
            mapped_entries=mapped_entries,
            skipped_entries=skipped_entries,
            not_in_usda_entries=not_in_usda_entries,
            lookup_failed_entries=lookup_failed_entries,
            untranslated_entries=untranslated_entries,
            skipped_foods=sorted(skipped_food_names),
            not_in_usda_foods=sorted(not_in_usda_food_names),
            lookup_failed_foods=sorted(lookup_failed_food_names),
            untranslated_foods=sorted(untranslated_food_names),
            not_in_usda_weight_pct=food_weight_pct(not_in_usda_weight_g),
            lookup_failed_weight_pct=food_weight_pct(lookup_failed_weight_g),
            untranslated_weight_pct=food_weight_pct(untranslated_weight_g),
            low_confidence_foods=low_confidence_foods,
            low_confidence_weight_pct=low_confidence_weight_pct,
            low_confidence_kcal_pct=low_confidence_kcal_pct,
            estimated_weights=estimated_weights,
            custom_recipes=_custom_recipe_uses(enrichment["custom"], custom_weight_g),
            logged_fiber_pct=logged_fiber_pct,
        ),
    )
