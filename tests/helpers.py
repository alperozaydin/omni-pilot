"""Shared builders for tests."""
from __future__ import annotations

from collections.abc import Iterable

import openpyxl

from omni_pilot.enricher import CustomFoodMatch, EnrichmentResult, LowConfidenceMatch


def enrichment(
    profiles: dict[str, dict[str, float | None]] | None = None,
    *,
    skipped: Iterable[str] = (),
    not_in_usda: Iterable[str] = (),
    lookup_failed: Iterable[str] = (),
    untranslated: Iterable[str] = (),
    low_confidence: dict[str, LowConfidenceMatch] | None = None,
    custom: dict[str, CustomFoodMatch] | None = None,
) -> EnrichmentResult:
    """Build an EnrichmentResult, defaulting the parts a test doesn't care about."""
    return {
        "profiles": dict(profiles or {}),
        "skipped": set(skipped),
        "not_in_usda": set(not_in_usda),
        "lookup_failed": set(lookup_failed),
        "untranslated": set(untranslated),
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


def write_food_log(path, rows: list[list]) -> str:
    """Write a "Food Log" sheet in MacroFactor's column layout, one list per food row."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Food Log"
    ws.append(["Date", "Time", "Food Name"])
    for row in rows:
        ws.append(row)
    wb.save(path)
    return str(path)


def food_row(
    food_name: str, fiber: float | None = None, columns: int = 61, date: str = "2026-10-04"
) -> list:
    """A food row as wide as a real export, with the fiber cell set (None leaves it empty)."""
    row = [date, "12:00", food_name, "g", 100, 1, 50.0, 1.0, 10.0, 2.0] + [None] * (columns - 10)
    if fiber is not None:
        row[27] = fiber
    return row


class FakeS3Client:
    """In-memory stand-in for `omni_pilot.s3.S3Client`, mirroring its interface.

    `objects` maps key -> (body, etag). Every call is recorded in `calls` as
    `(operation, key)`; puts also land in `puts` with their conditions. Set
    `fail_get_on` to a key to make that `get` raise `S3Error("network")`.
    """

    def __init__(self, objects: dict[str, tuple[bytes, str]] | None = None, bucket: str = "test-bucket"):
        from types import SimpleNamespace

        self.config = SimpleNamespace(bucket=bucket)
        self.objects: dict[str, tuple[bytes, str]] = dict(objects or {})
        self.calls: list[tuple[str, str]] = []
        self.puts: list[dict] = []
        self.fail_get_on: str | None = None
        self._counter = 0

    def get(self, key: str) -> tuple[bytes, str] | None:
        from omni_pilot.s3 import S3Error

        self.calls.append(("get", key))
        if key == self.fail_get_on:
            raise S3Error("network", "S3 not reachable")
        return self.objects.get(key)

    def put(
        self, key: str, body: bytes, *, if_match: str | None = None, if_none_match: bool = False
    ) -> str:
        from omni_pilot.s3 import S3Error

        self.calls.append(("put", key))
        self.puts.append({"key": key, "body": body, "if_match": if_match, "if_none_match": if_none_match})
        stored = self.objects.get(key)
        if if_none_match and stored is not None:
            raise S3Error("conflict", f"{key} exists")
        if if_match is not None and (stored is None or stored[1] != if_match):
            raise S3Error("conflict", f"{key} changed")
        self._counter += 1
        etag = f'"put-{self._counter}"'
        self.objects[key] = (body, etag)
        return etag

    def list(self, prefix: str) -> list[str]:
        self.calls.append(("list", prefix))
        return sorted(k for k in self.objects if k.startswith(prefix))
