"""tests/test_translator.py"""
from __future__ import annotations

import json

import pytest
import requests
import yaml
from tinydb import TinyDB

from omni_pilot.translator import resolve_and_sync_mappings, translate_new_foods


def test_translate_new_foods_success(mocker):
    """Test successful Gemini API translation response."""
    mock_response = mocker.MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {"text": json.dumps(["milk, reduced fat, fluid, 2% milkfat", "potatoes, raw, skin"])}
                    ]
                }
            }
        ]
    }
    mocker.patch("requests.post", return_value=mock_response)

    results = translate_new_foods(["Milch 1.5%", "Kartoffeln"], api_key="fake_key")
    assert results == ["milk, reduced fat, fluid, 2% milkfat", "potatoes, raw, skin"]


def test_translate_new_foods_retry_limit(mocker):
    """Test that transient failures retry at most once (2 total attempts)."""
    mock_post = mocker.patch("requests.post", side_effect=requests.RequestException("Network Error"))

    with pytest.raises(requests.RequestException):
        translate_new_foods(["Milch"], api_key="fake_key")

    # 1 initial attempt + 1 retry = 2 total attempts
    assert mock_post.call_count == 2


def test_resolve_and_sync_mappings_gemini_success(mocker, tmp_path):
    """Test end-to-end resolution and persistence when new foods are translated."""
    db_path = str(tmp_path / "food_db.json")
    mappings_path = str(tmp_path / "food_mappings.yaml")

    # Initial mapping file with one existing item
    with open(mappings_path, "w") as f:
        yaml.dump({"mappings": {"Existing Food": "Oats"}}, f)

    mocker.patch(
        "omni_pilot.translator.translate_new_foods",
        return_value=["egg, whole, raw, fresh"],
    )

    settings = {"gemini_api_key": "valid_key", "gemini_model": "gemini-flash-latest"}
    foods = ["Existing Food", "New Food"]

    mappings = resolve_and_sync_mappings(foods, db_path, mappings_path, settings)

    assert mappings["Existing Food"] == "Oats"
    assert mappings["New Food"] == "egg, whole, raw, fresh"

    # Verify TinyDB persistence
    db = TinyDB(db_path)
    translations = db.table("translations").all()
    assert any(t["german"] == "New Food" and t["english"] == "egg, whole, raw, fresh" for t in translations)

    # Verify YAML persistence
    with open(mappings_path) as f:
        data = yaml.safe_load(f)
    assert data["mappings"]["New Food"] == "egg, whole, raw, fresh"


def test_resolve_and_sync_mappings_missing_key_graceful(tmp_path):
    """Test graceful fallback when no Gemini key is provided."""
    db_path = str(tmp_path / "food_db.json")
    mappings_path = str(tmp_path / "food_mappings.yaml")

    settings = {}
    foods = ["Unknown Food"]

    mappings = resolve_and_sync_mappings(foods, db_path, mappings_path, settings)

    # Does not crash, mappings dictionary contains known entries
    assert "Unknown Food" not in mappings


def test_resolve_and_sync_mappings_gemini_error_graceful(mocker, tmp_path):
    """Test graceful fallback when Gemini translation raises an exception."""
    db_path = str(tmp_path / "food_db.json")
    mappings_path = str(tmp_path / "food_mappings.yaml")

    mocker.patch(
        "omni_pilot.translator.translate_new_foods",
        side_effect=RuntimeError("API quota exceeded"),
    )

    settings = {"gemini_api_key": "valid_key"}
    foods = ["Unknown Food"]

    mappings = resolve_and_sync_mappings(foods, db_path, mappings_path, settings)
    assert "Unknown Food" not in mappings
