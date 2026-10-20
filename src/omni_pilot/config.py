"""Configuration loading and validation."""
from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field

import yaml


def load_settings(path: str) -> dict:
    """Load settings.yaml and return as dict.

    Raises FileNotFoundError if file does not exist.
    """
    if not os.path.exists(path):
        raise FileNotFoundError(f"Settings file not found: {path}")
    with open(path) as f:
        return yaml.safe_load(f)


def load_reference_ranges(path: str) -> dict:
    """Load reference_ranges.yaml and return as dict.

    Raises FileNotFoundError if file does not exist.
    """
    if not os.path.exists(path):
        raise FileNotFoundError(f"Reference ranges file not found: {path}")
    with open(path) as f:
        return yaml.safe_load(f)


def load_food_mappings(path: str) -> dict[str, str]:
    """Load food_mappings.yaml and return the mappings dict.

    Returns empty dict if file does not exist (mappings not yet generated).
    """
    if not os.path.exists(path):
        return {}
    with open(path) as f:
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


def load_supplements(path: str) -> dict:
    """Load supplements.yaml and return the mapping dict.

    Returns empty dict if file does not exist.
    """
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        data = yaml.safe_load(f)
    return data if data is not None else {}


def resolve_path(
    setting_key: str,
    default_path: str,
    settings: dict | None = None,
) -> str:
    """Resolve file path from settings dict with ~ and env expansion, falling back to default_path."""
    raw_path = settings.get(setting_key, default_path) if settings else default_path
    resolved = os.path.expanduser(os.path.expandvars(str(raw_path)))
    parent = os.path.dirname(resolved)
    if parent:
        os.makedirs(parent, exist_ok=True)
    return resolved


S3_ENV_VARS: dict[str, str] = {
    "bucket": "OMNI_PILOT_S3_BUCKET",
    "region": "OMNI_PILOT_S3_REGION",
    "access_key_id": "OMNI_PILOT_S3_ACCESS_KEY_ID",
    "secret_access_key": "OMNI_PILOT_S3_SECRET_ACCESS_KEY",
}


class S3ConfigError(Exception):
    """The S3 connection is partly configured: a value is missing."""


@dataclass(frozen=True)
class S3Config:
    bucket: str
    region: str
    # "settings.yaml" or "OMNI_PILOT_S3_ACCESS_KEY_ID": where the access key id came from.
    access_key_id: str
    secret_access_key: str = field(repr=False)
    key_source: str


def _set_value(value: object) -> str | None:
    """Return the stripped string, or None when the value is not set."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def load_s3_config(
    settings: dict | None,
    environ: Mapping[str, str] = os.environ,
) -> S3Config | None:
    """Read the S3 connection from settings.yaml's `s3` section and OMNI_PILOT_S3_* variables.

    An environment variable wins over settings.yaml; an empty one counts as unset.
    Returns None when none of the four values is set (S3 not configured).
    Raises S3ConfigError when some are set and others are missing.
    """
    section = (settings or {}).get("s3") or {}
    values: dict[str, str | None] = {}
    for name, var in S3_ENV_VARS.items():
        values[name] = _set_value(environ.get(var)) or _set_value(section.get(name))

    if all(v is None for v in values.values()):
        return None

    for name, value in values.items():
        if value is None or value.startswith("YOUR_"):
            raise S3ConfigError(
                f"S3 setting '{name}' is missing: set s3.{name} in settings.yaml "
                f"or {S3_ENV_VARS[name]}"
            )

    key_var = S3_ENV_VARS["access_key_id"]
    key_source = key_var if _set_value(environ.get(key_var)) else "settings.yaml"
    return S3Config(key_source=key_source, **values)
