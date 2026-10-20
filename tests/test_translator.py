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
    """Test that transient failures retry at most twice (3 total attempts)."""
    mock_post = mocker.patch("requests.post", side_effect=requests.RequestException("Network Error"))
    mocker.patch("time.sleep")

    with pytest.raises(requests.RequestException):
        translate_new_foods(["Milch"], api_key="fake_key")

    # 1 initial attempt + 2 retries = 3 total attempts
    assert mock_post.call_count == 3


@pytest.mark.parametrize("error", [requests.ConnectionError("unreachable"), requests.ConnectTimeout("timed out")])
def test_translate_new_foods_does_not_retry_when_gemini_is_unreachable(mocker, error):
    """A server that can't be reached now won't be in two seconds: fail at once instead of waiting twice."""
    mock_post = mocker.patch("requests.post", side_effect=error)
    mocker.patch("time.sleep")

    with pytest.raises(type(error)):
        translate_new_foods(["Milch"], api_key="fake_key")

    assert mock_post.call_count == 1


def _gemini_reply(mocker, translations: list[str]):
    reply = mocker.MagicMock(status_code=200)
    reply.json.return_value = {"candidates": [{"content": {"parts": [{"text": json.dumps(translations)}]}}]}
    return reply


def test_translate_new_foods_sends_api_key_in_a_header_never_the_url(mocker):
    """requests puts the URL into its error messages, which get logged: a key in the URL would be printed."""
    mock_post = mocker.patch("requests.post", return_value=_gemini_reply(mocker, ["milk"]))

    translate_new_foods(["Milch"], api_key="fake_key")

    assert "fake_key" not in mock_post.call_args.args[0]
    assert mock_post.call_args.kwargs["headers"] == {"x-goog-api-key": "fake_key"}


def test_translate_new_foods_gives_up_connecting_quickly(mocker):
    """The connect timeout applies to each of Gemini's (many) addresses in turn, so it must be short."""
    mock_post = mocker.patch("requests.post", return_value=_gemini_reply(mocker, ["milk"]))

    translate_new_foods(["Milch"], api_key="fake_key")

    connect_timeout, _ = mock_post.call_args.kwargs["timeout"]
    assert connect_timeout <= 5


def test_translate_new_foods_cuts_off_a_stalled_request_and_asks_again(mocker):
    """Gemini sometimes sends nothing for over a minute; a fresh request usually answers in seconds.

    On the iPhone two 60s attempts both timed out, leaving two foods untranslated
    after a two-minute wait.
    """
    mock_post = mocker.patch(
        "requests.post",
        side_effect=[requests.ReadTimeout("Read timed out."), _gemini_reply(mocker, ["milk"])],
    )
    mocker.patch("time.sleep")

    assert translate_new_foods(["Milch"], api_key="fake_key") == ["milk"]

    _, read_timeout = mock_post.call_args.kwargs["timeout"]
    # Gemini takes ~5-20s to answer for a few foods at low thinking; leave it room.
    assert 25 <= read_timeout <= 30


def test_translate_new_foods_gives_up_on_a_silent_gemini_sooner_than_before(mocker):
    """Every attempt timing out costs at most ~1.5 minutes, not the two minutes of 2 x 60s."""
    mock_post = mocker.patch("requests.post", side_effect=requests.ReadTimeout("Read timed out."))
    sleep = mocker.patch("time.sleep")

    with pytest.raises(requests.ReadTimeout):
        translate_new_foods(["Milch"], api_key="fake_key")

    _, read_timeout = mock_post.call_args.kwargs["timeout"]
    waited = read_timeout * mock_post.call_count + sum(call.args[0] for call in sleep.call_args_list)
    assert waited <= 100


def test_translate_new_foods_keeps_gemini_thinking_low(mocker):
    """At its default thinking level gemini-flash-latest took 30-60s for 7 foods and hit the read timeout."""
    mock_post = mocker.patch("requests.post", return_value=_gemini_reply(mocker, ["milk"]))

    translate_new_foods(["Milch"], api_key="fake_key")

    generation_config = mock_post.call_args.kwargs["json"]["generationConfig"]
    assert generation_config["thinkingConfig"] == {"thinkingLevel": "low"}


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
