"""Shared builders for tests."""
from __future__ import annotations

from collections.abc import Iterable

from omni_pilot.enricher import CustomFoodMatch, EnrichmentResult, LowConfidenceMatch


def enrichment(
    profiles: dict[str, dict[str, float | None]] | None = None,
    *,
    skipped: Iterable[str] = (),
    unresolved: Iterable[str] = (),
    low_confidence: dict[str, LowConfidenceMatch] | None = None,
    custom: dict[str, CustomFoodMatch] | None = None,
) -> EnrichmentResult:
    """Build an EnrichmentResult, defaulting the parts a test doesn't care about."""
    return {
        "profiles": dict(profiles or {}),
        "skipped": set(skipped),
        "unresolved": set(unresolved),
        "low_confidence": dict(low_confidence or {}),
        "custom": dict(custom or {}),
    }


def food_entry(
    food_name: str,
    total_weight_g: float = 100.0,
    date: str = "2026-08-01",
    *,
    calories_kcal: float = 100.0,
    protein_g: float = 10.0,
    fat_g: float = 5.0,
    carbs_g: float = 15.0,
) -> dict:
    """Build a parsed food-log entry with the fields the pipeline reads."""
    return {
        "date": date,
        "food_name": food_name,
        "total_weight_g": total_weight_g,
        "calories_kcal": calories_kcal,
        "protein_g": protein_g,
        "fat_g": fat_g,
        "carbs_g": carbs_g,
    }
