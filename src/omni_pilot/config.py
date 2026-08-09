"""Configuration loading and validation."""
from __future__ import annotations

import os
import yaml


def load_settings(path: str) -> dict:
    """Load settings.yaml and return as dict.

    Raises FileNotFoundError if file does not exist.
    """
    if not os.path.exists(path):
        raise FileNotFoundError(f"Settings file not found: {path}")
    with open(path, "r") as f:
        return yaml.safe_load(f)


def load_reference_ranges(path: str) -> dict:
    """Load reference_ranges.yaml and return as dict.

    Raises FileNotFoundError if file does not exist.
    """
    if not os.path.exists(path):
        raise FileNotFoundError(f"Reference ranges file not found: {path}")
    with open(path, "r") as f:
        return yaml.safe_load(f)


def load_food_mappings(path: str) -> dict[str, str]:
    """Load food_mappings.yaml and return the mappings dict.

    Returns empty dict if file does not exist (mappings not yet generated).
    """
    if not os.path.exists(path):
        return {}
    with open(path, "r") as f:
        data = yaml.safe_load(f)
    if data is None or "mappings" not in data:
        return {}
    return data["mappings"]


def get_nutrient_target(
    nutrient_key: str,
    ref_ranges: dict,
) -> tuple[float | None, float | None, str]:
    """Get the daily target and UL for a nutrient.

    For RDA/AI nutrients, returns the rda/ai value directly.
    For WHO per-kg amino acids, computes target from body weight.

    Returns:
        (target_value, ul_value, target_type)
        target_value is None if it cannot be computed (e.g. missing body weight).
    """
    nutrient = ref_ranges["nutrients"][nutrient_key]
    nutrient_type = nutrient["type"]
    ul = nutrient.get("ul")

    if nutrient_type == "rda":
        return nutrient["rda"], ul, "rda"
    elif nutrient_type == "ai":
        return nutrient["ai"], ul, "ai"
    elif nutrient_type == "who_per_kg":
        body_weight = ref_ranges["demographic"].get("body_weight_kg")
        if body_weight is None:
            return None, ul, "who_per_kg"
        # safe_mg_per_kg * body_weight_kg / 1000 to convert mg to g
        target_g = nutrient["safe_mg_per_kg"] * body_weight / 1000
        return target_g, ul, "who_per_kg"
    else:
        return None, ul, nutrient_type
