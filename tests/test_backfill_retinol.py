"""Tests for scripts/backfill_retinol.py, the one-time retinol fill of the whole database."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
import yaml
from tinydb import TinyDB

from tests.helpers import FakeS3Client

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "backfill_retinol.py"
FULL_S3 = {"bucket": "omni-pilot-data", "region": "eu-central-1", "access_key_id": "AKIA", "secret_access_key": "x"}


@pytest.fixture
def script():
    spec = importlib.util.spec_from_file_location("backfill_retinol", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def _workdir(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config").mkdir()
    (tmp_path / "db").mkdir()


def write_settings(tmp_path, s3=None, key: str | None = "fake_key") -> None:
    body: dict = {}
    if key is not None:
        body["usda_api_key"] = key
    if s3 is not None:
        body["s3"] = s3
    (tmp_path / "config" / "settings.yaml").write_text(yaml.dump(body))


def db_with(path: Path, count: int = 2) -> bytes:
    db = TinyDB(str(path))
    for i in range(count):
        db.insert({"original_name": f"Food {i}", "usda_fdc_id": 100 + i, "per_100g": {"iron_mg": 1.0}})
    db.close()
    return path.read_bytes()


def hit(retinol: float = 7.0) -> dict:
    return {"fdcId": 1, "description": "x", "dataType": "SR Legacy",
            "foodNutrients": [{"nutrientNumber": "319", "value": retinol}]}


def fake_s3(mocker, db_bytes: bytes) -> FakeS3Client:
    fake = FakeS3Client({
        "food_db.json": (db_bytes, '"db-1"'),
        "food_mappings.yaml": (b"mappings:\n  Apple: apple\n", '"map-1"'),
    }, bucket="omni-pilot-data")
    mocker.patch("omni_pilot.cli.S3Client", return_value=fake)
    return fake


def run(script) -> int:
    try:
        script.main()
    except SystemExit as e:
        return e.code
    return 0


def test_without_s3_fills_local_database_and_prints_summary(script, mocker, tmp_path, capsys):
    write_settings(tmp_path)
    db_with(tmp_path / "db" / "food_db.json")
    mocker.patch("omni_pilot.enricher.fetch_usda_food_or_miss", return_value=hit())

    assert run(script) == 0

    out = capsys.readouterr().out
    assert "S3: not configured — local files only" in out
    assert "Filled retinol for 2 entries" in out
    stored = json.loads((tmp_path / "db" / "food_db.json").read_text())["_default"]
    assert all(e["per_100g"]["retinol_mcg"] == 7.0 for e in stored.values())


def test_with_s3_pulls_first_and_pushes_the_database(script, mocker, tmp_path, capsys):
    write_settings(tmp_path, s3=FULL_S3)
    original = db_with(tmp_path / "scratch.json")
    (tmp_path / "scratch.json").unlink()
    fake = fake_s3(mocker, original)
    mocker.patch("omni_pilot.enricher.fetch_usda_food_or_miss", return_value=hit())

    assert run(script) == 0

    first_put = next(i for i, call in enumerate(fake.calls) if call[0] == "put")
    assert ("get", "food_db.json") in fake.calls[:first_put]
    assert fake.calls[-1] == ("put", "food_db.json")
    assert b"retinol_mcg" in fake.objects["food_db.json"][0]
    assert "database: uploaded" in capsys.readouterr().out


def test_with_s3_nothing_to_fill_pushes_nothing(script, mocker, tmp_path, capsys):
    write_settings(tmp_path, s3=FULL_S3)
    db = TinyDB(str(tmp_path / "scratch.json"))
    db.insert({"original_name": "Eier", "usda_fdc_id": 1, "per_100g": {"retinol_mcg": 3.0}})
    db.close()
    original = (tmp_path / "scratch.json").read_bytes()
    (tmp_path / "scratch.json").unlink()
    fake = fake_s3(mocker, original)
    fetch = mocker.patch("omni_pilot.enricher.fetch_usda_food_or_miss")

    assert run(script) == 0

    fetch.assert_not_called()
    assert not any(call[0] == "put" for call in fake.calls)
    assert "Filled retinol for 0 entries" in capsys.readouterr().out


def test_failed_pull_exits_1_and_writes_nothing(script, mocker, tmp_path, capsys):
    write_settings(tmp_path, s3=FULL_S3)
    fake = fake_s3(mocker, b'{"_default": {}}')
    fake.fail_get_on = "food_db.json"
    backfill = mocker.patch.object(script, "backfill_retinol")

    assert run(script) == 1

    assert "S3 not reachable" in capsys.readouterr().out
    backfill.assert_not_called()
    assert not (tmp_path / "db" / "food_db.json").exists()
    assert fake.puts == []


def test_incomplete_s3_settings_exit_1(script, mocker, tmp_path, capsys):
    write_settings(tmp_path, s3={"bucket": "omni-pilot-data"})

    assert run(script) == 1
    assert "Error:" in capsys.readouterr().out


def test_missing_usda_key_exits_1(script, tmp_path, capsys):
    write_settings(tmp_path, key=None)

    assert run(script) == 1
    assert "No USDA API key in settings.yaml" in capsys.readouterr().out


def test_missing_database_exits_1(script, tmp_path, capsys):
    write_settings(tmp_path)

    assert run(script) == 1
    assert "No database at" in capsys.readouterr().out
    assert not (tmp_path / "db" / "food_db.json").exists()


def test_rate_limited_says_to_run_again_later_and_still_pushes_what_it_filled(script, mocker, tmp_path, capsys):
    write_settings(tmp_path, s3=FULL_S3)
    original = db_with(tmp_path / "scratch.json")
    (tmp_path / "scratch.json").unlink()
    fake = fake_s3(mocker, original)
    mocker.patch("omni_pilot.enricher.fetch_usda_food_or_miss", side_effect=[hit(), "rate_limited"])

    assert run(script) == 0

    out = capsys.readouterr().out
    assert "Filled retinol for 1 entries" in out
    assert "run it again later" in out
    assert ("put", "food_db.json") in fake.calls


def test_summary_names_entries_without_id_failed_fetches_and_gone_ids(script, mocker, tmp_path, capsys):
    write_settings(tmp_path)
    db = TinyDB(str(tmp_path / "db" / "food_db.json"))
    for name, fdc_id in [("Ohne ID", None), ("Kaputt", 1), ("Weg", 2)]:
        db.insert({"original_name": name, "usda_fdc_id": fdc_id, "per_100g": {}})
    db.close()
    mocker.patch("omni_pilot.enricher.fetch_usda_food_or_miss", side_effect=["failed", "gone"])

    assert run(script) == 0

    out = capsys.readouterr().out
    assert "Ohne ID" in out and "no USDA ID" in out
    assert "Kaputt" in out and "retried by the next analyze or another run of this script" in out
    assert "Weg" in out and "no longer" in out


def test_repeated_failures_say_to_run_it_again_later_and_still_push_what_it_filled(
    script, mocker, tmp_path, capsys
):
    write_settings(tmp_path, s3=FULL_S3)
    original = db_with(tmp_path / "scratch.json", count=8)
    (tmp_path / "scratch.json").unlink()
    fake = fake_s3(mocker, original)
    mocker.patch(
        "omni_pilot.enricher.fetch_usda_food_or_miss", side_effect=[hit()] + ["failed"] * 5 + [hit()] * 2
    )

    assert run(script) == 0

    out = capsys.readouterr().out
    assert "Filled retinol for 1 entries" in out
    assert "USDA kept failing" in out and "run it again later" in out
    assert ("put", "food_db.json") in fake.calls


def test_prints_progress_every_25_fetches(script, mocker, tmp_path, capsys):
    write_settings(tmp_path)
    db_with(tmp_path / "db" / "food_db.json", count=30)
    mocker.patch("omni_pilot.enricher.fetch_usda_food_or_miss", return_value=hit())

    assert run(script) == 0

    out = capsys.readouterr().out
    assert "fetched 25 of 30 USDA foods" in out
    assert "fetched 50" not in out


def test_refused_push_says_to_run_the_script_again_not_make_pull(script, mocker, tmp_path, capsys):
    write_settings(tmp_path, s3=FULL_S3)
    original = db_with(tmp_path / "scratch.json")
    (tmp_path / "scratch.json").unlink()
    fake = fake_s3(mocker, original)

    def other_device_pushes(*args, **kwargs):
        fake.objects["food_db.json"] = (b"{}", '"db-2"')
        return hit()

    mocker.patch("omni_pilot.enricher.fetch_usda_food_or_miss", side_effect=other_device_pushes)

    assert run(script) == 1

    out = capsys.readouterr().out
    assert "changed" in out
    assert "Run this script again" in out
    assert "make pull" not in out
