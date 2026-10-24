"""tests/test_translator.py"""
from __future__ import annotations

import json

import pytest
import requests
import yaml
from tinydb import TinyDB

from omni_pilot.config import load_food_mappings
from omni_pilot.parser import generate_food_mappings
from omni_pilot.translator import (
    cached_food_names,
    normalise_query,
    resolve_and_sync_mappings,
    set_mapping,
    stored_mappings,
    translate_new_foods,
)


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

    mappings, untranslated = resolve_and_sync_mappings(foods, db_path, mappings_path, settings)

    assert mappings["Existing Food"] == "Oats"
    assert mappings["New Food"] == "egg, whole, raw, fresh"
    assert untranslated == set()

    # Verify TinyDB persistence
    db = TinyDB(db_path)
    translations = db.table("translations").all()
    assert any(t["german"] == "New Food" and t["english"] == "egg, whole, raw, fresh" for t in translations)

    # Verify YAML persistence
    with open(mappings_path) as f:
        data = yaml.safe_load(f)
    assert data["mappings"]["New Food"] == "egg, whole, raw, fresh"


def test_resolve_and_sync_mappings_missing_key_graceful(tmp_path, capsys):
    """Test graceful fallback when no Gemini key is provided."""
    db_path = str(tmp_path / "food_db.json")
    mappings_path = str(tmp_path / "food_mappings.yaml")

    settings = {}
    foods = ["Unknown Food"]

    mappings, untranslated = resolve_and_sync_mappings(foods, db_path, mappings_path, settings)

    # Does not crash, mappings dictionary contains known entries
    assert "Unknown Food" not in mappings
    # Gemini was never asked: the food is searched in USDA under its own name
    assert untranslated == set()
    assert "make update-mapping" in capsys.readouterr().out


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

    mappings, untranslated = resolve_and_sync_mappings(foods, db_path, mappings_path, settings)
    assert "Unknown Food" not in mappings
    assert untranslated == {"Unknown Food"}


def test_resolve_and_sync_mappings_reports_foods_gemini_left_untranslated(mocker, tmp_path):
    """An empty or "ERROR" answer leaves that food untranslated; the others are kept."""
    db_path = str(tmp_path / "food_db.json")
    mappings_path = str(tmp_path / "food_mappings.yaml")
    mocker.patch(
        "omni_pilot.translator.translate_new_foods",
        return_value=["lentils, raw", "", "ERROR"],
    )

    mappings, untranslated = resolve_and_sync_mappings(
        ["Braune Linsen", "Poké Bowl Reis", "Tee"], db_path, mappings_path, {"gemini_api_key": "valid_key"},
    )

    assert mappings["Braune Linsen"] == "lentils, raw"
    assert untranslated == {"Poké Bowl Reis", "Tee"}


def test_resolve_and_sync_mappings_wrong_answer_count_leaves_all_untranslated(mocker, tmp_path):
    db_path = str(tmp_path / "food_db.json")
    mappings_path = str(tmp_path / "food_mappings.yaml")
    mocker.patch("omni_pilot.translator.translate_new_foods", return_value=["lentils, raw"])

    mappings, untranslated = resolve_and_sync_mappings(
        ["Braune Linsen", "Tee"], db_path, mappings_path, {"gemini_api_key": "valid_key"},
    )

    assert "Braune Linsen" not in mappings
    assert untranslated == {"Braune Linsen", "Tee"}


def test_resolve_and_sync_mappings_known_foods_are_never_untranslated(mocker, tmp_path):
    db_path = str(tmp_path / "food_db.json")
    mappings_path = str(tmp_path / "food_mappings.yaml")
    with open(mappings_path, "w") as f:
        yaml.dump({"mappings": {"Haferflocken": "oats"}}, f)
    mocker.patch("omni_pilot.translator.translate_new_foods", side_effect=RuntimeError("timed out"))

    _, untranslated = resolve_and_sync_mappings(
        ["Haferflocken", "Braune Linsen"], db_path, mappings_path, {"gemini_api_key": "valid_key"},
    )

    assert untranslated == {"Braune Linsen"}


def _db(tmp_path, translations=(), cache=()) -> str:
    path = tmp_path / "db" / "food_db.json"
    path.parent.mkdir(exist_ok=True)
    with TinyDB(path) as db:
        db.table("translations").insert_multiple(list(translations))
        db.insert_multiple(list(cache))
    return str(path)


def test_set_mapping_fixes_a_wrong_translation_for_good(tmp_path):
    """BAR-44: a corrected lookup text survives the next run instead of being reverted."""
    db = _db(tmp_path, [{"german": "Haferflocken", "english": "oat milk"}])
    yml = tmp_path / "food_mappings.yaml"
    generate_food_mappings(["Haferflocken"], {"Haferflocken": "oat milk"}, str(yml))

    set_mapping("Haferflocken", "cereals, oats, dry", db, str(yml))
    written = yml.read_bytes()
    mappings, untranslated = resolve_and_sync_mappings(["Haferflocken"], db, str(yml), {})

    assert mappings == {"Haferflocken": "cereals, oats, dry"}
    assert untranslated == set()
    assert yml.read_bytes() == written
    assert load_food_mappings(str(yml)) == {"Haferflocken": "cereals, oats, dry"}


@pytest.mark.parametrize("entry", [
    {"original_name": "Haferflocken", "usda_query": "oat milk", "per_100g": {}, "reviewed_fdc_id": 1},
    {"original_name": "Haferflocken", "usda_query": "oat milk", "not_found": True, "deferred_query": "oat milk"},
])
def test_set_mapping_drops_the_food_cache_entry_only(tmp_path, entry):
    other = {"original_name": "Milch", "usda_query": "milk", "per_100g": {}}
    db = _db(tmp_path, cache=[entry, other])
    with TinyDB(db) as d:
        d.table("usda_foods").insert({"fdc_id": 170457})

    set_mapping("Haferflocken", "cereals, oats, dry", db, str(tmp_path / "m.yaml"))

    with TinyDB(db) as d:
        assert [e["original_name"] for e in d.all()] == ["Milch"]
        assert d.table("usda_foods").all() == [{"fdc_id": 170457}]


def test_set_mapping_keeps_umlauts_quotes_and_colons(tmp_path):
    name = 'Müsli "Bio": Nuss'
    yml = tmp_path / "m.yaml"

    set_mapping(name, "muesli", _db(tmp_path), str(yml))

    assert load_food_mappings(str(yml)) == {name: "muesli"}
    assert "Müsli" in yml.read_text()


def test_set_mapping_creates_missing_files(tmp_path):
    db, yml = tmp_path / "db" / "food_db.json", tmp_path / "config" / "m.yaml"

    set_mapping("Haferflocken", "skip", str(db), str(yml))

    assert stored_mappings(str(db), str(yml)) == {"Haferflocken": "skip"}


def test_mappings_file_header_points_to_update_mapping(tmp_path):
    yml = tmp_path / "m.yaml"

    set_mapping("Haferflocken", "skip", _db(tmp_path), str(yml))

    header = yml.read_text().split("mappings:")[0]
    assert "make update-mapping" in header
    assert "skip" in header
    assert "omni_pilot import" not in header


@pytest.mark.parametrize("raw, stored", [
    ("  Skip ", "skip"), ("SKIP", "skip"), (" cereals, oats ", "cereals, oats"), ("Skipjack tuna", "Skipjack tuna"),
])
def test_normalise_query(raw, stored):
    assert normalise_query(raw) == stored


def test_stored_mappings_database_wins_and_yaml_fills_gaps(tmp_path):
    db = _db(tmp_path, [{"german": "A", "english": "from db"}])
    yml = tmp_path / "m.yaml"
    generate_food_mappings([], {"A": "from yaml", "B": "only yaml"}, str(yml))

    assert stored_mappings(db, str(yml)) == {"A": "from db", "B": "only yaml"}


def test_stored_mappings_does_not_create_a_missing_database(tmp_path):
    db = tmp_path / "db" / "food_db.json"

    assert stored_mappings(str(db), str(tmp_path / "m.yaml")) == {}
    assert not db.exists()


def test_set_mapping_adds_the_database_row_for_a_yaml_only_food(tmp_path):
    db, yml = _db(tmp_path), tmp_path / "m.yaml"
    generate_food_mappings([], {"B": "only yaml"}, str(yml))

    set_mapping("B", "new", db, str(yml))

    with TinyDB(db) as d:
        assert d.table("translations").all() == [{"german": "B", "english": "new"}]


def test_cached_food_names_reads_the_main_table_only(tmp_path):
    db = _db(
        tmp_path,
        [{"german": "T", "english": "t"}],
        [{"original_name": "M", "per_100g": {}}, {"original_name": "N", "not_found": True}],
    )

    assert cached_food_names(db) == {"M", "N"}
    assert cached_food_names(str(tmp_path / "missing.json")) == set()
