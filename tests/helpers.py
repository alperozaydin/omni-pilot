"""Shared builders for tests."""
from __future__ import annotations

from collections.abc import Iterable

from omni_pilot.enricher import EnrichmentResult


def enrichment(
    profiles: dict[str, dict[str, float | None]] | None = None,
    *,
    skipped: Iterable[str] = (),
    unresolved: Iterable[str] = (),
) -> EnrichmentResult:
    """Build an EnrichmentResult, defaulting the parts a test doesn't care about."""
    return {
        "profiles": dict(profiles or {}),
        "skipped": set(skipped),
        "unresolved": set(unresolved),
    }
