import json
import os

import pytest

from omni_pilot.s3 import S3Error
from omni_pilot.sync import SyncError, data_files, pull, push, state_path
from tests.helpers import FakeS3Client


def setup(tmp_path):
    files = data_files(
        str(tmp_path / "db" / "food_db.json"),
        str(tmp_path / "config" / "food_mappings.yaml"),
        str(tmp_path / "config" / "custom_foods.yaml"),
    )
    return files, state_path(files[0].path)


def s3_with_all():
    return FakeS3Client(
        {
            "food_db.json": (b"db1", '"e1"'),
            "food_mappings.yaml": (b"map1", '"e2"'),
            "custom_foods.yaml": (b"rec1", '"e3"'),
        }
    )


def read(path):
    with open(path, "rb") as f:
        return f.read()


def state(state_file):
    with open(state_file) as f:
        return json.load(f)["files"]


def test_state_path_next_to_database():
    assert state_path("/a/db/food_db.json") == "/a/db/.s3_state.json"


def test_pull_writes_files_and_state(tmp_path):
    files, sf = setup(tmp_path)
    found = pull(s3_with_all(), files, sf)
    assert found == ["database", "mappings", "recipes"]
    assert read(files[0].path) == b"db1"
    assert read(files[2].path) == b"rec1"
    s = state(sf)
    assert s["food_db.json"]["etag"] == '"e1"'
    assert len(s["food_db.json"]["sha256"]) == 64


def test_pull_creates_missing_folders(tmp_path):
    files, sf = setup(tmp_path)
    assert not (tmp_path / "db").exists()
    pull(s3_with_all(), files, sf)
    assert (tmp_path / "db").is_dir() and (tmp_path / "config").is_dir()


def test_pull_deletes_local_file_absent_in_s3(tmp_path):
    files, sf = setup(tmp_path)
    client = s3_with_all()
    del client.objects["custom_foods.yaml"]
    os.makedirs(os.path.dirname(files[2].path))
    with open(files[2].path, "wb") as f:
        f.write(b"old")
    found = pull(client, files, sf)
    assert found == ["database", "mappings"]
    assert not os.path.exists(files[2].path)
    assert state(sf)["custom_foods.yaml"] == {"etag": None, "sha256": None}


def test_pull_all_absent_stops_without_touching_local(tmp_path):
    files, sf = setup(tmp_path)
    os.makedirs(os.path.dirname(files[0].path))
    with open(files[0].path, "wb") as f:
        f.write(b"local")
    with pytest.raises(SyncError) as e:
        pull(FakeS3Client(bucket="my-bucket"), files, sf)
    assert "my-bucket" in str(e.value) and "one-time copy" in str(e.value)
    assert read(files[0].path) == b"local"
    assert not os.path.exists(sf)


def test_failed_get_leaves_old_local_files(tmp_path):
    files, sf = setup(tmp_path)
    os.makedirs(os.path.dirname(files[0].path))
    with open(files[0].path, "wb") as f:
        f.write(b"local")
    client = s3_with_all()
    client.fail_get_on = "food_mappings.yaml"
    with pytest.raises(S3Error):
        pull(client, files, sf)
    assert read(files[0].path) == b"local"
    assert not os.path.exists(sf)


def test_pull_writes_atomically(tmp_path):
    files, sf = setup(tmp_path)
    pull(s3_with_all(), files, sf)
    leftovers = [n for d in ("db", "config") for n in os.listdir(tmp_path / d) if n.startswith(".tmp")]
    assert leftovers == []


def pulled(tmp_path):
    files, sf = setup(tmp_path)
    client = s3_with_all()
    pull(client, files, sf)
    client.calls.clear()
    return files, sf, client


def test_push_skips_unchanged(tmp_path):
    files, sf, client = pulled(tmp_path)
    assert push(client, files, sf) == []
    assert client.puts == []


def test_push_sends_recorded_etag(tmp_path):
    files, sf, client = pulled(tmp_path)
    with open(files[0].path, "wb") as f:
        f.write(b"db2")
    assert push(client, files, sf) == ["database"]
    assert client.puts[0]["if_match"] == '"e1"'
    assert client.puts[0]["if_none_match"] is False
    assert client.objects["food_db.json"][0] == b"db2"
    assert state(sf)["food_db.json"]["etag"] == client.objects["food_db.json"][1]


def test_push_absent_at_pull_uses_if_none_match(tmp_path):
    files, sf = setup(tmp_path)
    client = s3_with_all()
    del client.objects["custom_foods.yaml"]
    pull(client, files, sf)
    with open(files[2].path, "wb") as f:
        f.write(b"new recipes")
    assert push(client, files, sf) == ["recipes"]
    assert client.puts[0]["if_none_match"] is True
    assert client.puts[0]["if_match"] is None


def test_push_absent_both_sides_skipped(tmp_path):
    files, sf = setup(tmp_path)
    client = s3_with_all()
    del client.objects["custom_foods.yaml"]
    pull(client, files, sf)
    assert push(client, files, sf) == []
    assert client.puts == []


def test_conflict_on_database_stops_before_mappings(tmp_path):
    files, sf, client = pulled(tmp_path)
    for f in files[:2]:
        with open(f.path, "wb") as fh:
            fh.write(b"changed")
    client.objects["food_db.json"] = (b"other", '"e9"')
    with pytest.raises(S3Error) as e:
        push(client, files, sf)
    assert e.value.kind == "conflict"
    msg = str(e.value)
    assert "food_db.json" in msg and "Nothing more was uploaded" in msg and "Run again" in msg
    assert [p["key"] for p in client.puts] == ["food_db.json"]


def test_state_updated_after_each_success(tmp_path):
    files, sf, client = pulled(tmp_path)
    for f in files[:2]:
        with open(f.path, "wb") as fh:
            fh.write(b"changed")
    client.objects["food_mappings.yaml"] = (b"other", '"e9"')
    with pytest.raises(S3Error):
        push(client, files, sf)
    s = state(sf)
    assert s["food_db.json"]["etag"] == client.objects["food_db.json"][1]
    assert s["food_mappings.yaml"]["etag"] == '"e2"'


def test_no_state_treats_all_as_absent(tmp_path, capsys):
    files, sf = setup(tmp_path)
    for f in files:
        os.makedirs(os.path.dirname(f.path), exist_ok=True)
        with open(f.path, "wb") as fh:
            fh.write(b"local")
    client = FakeS3Client({"food_mappings.yaml": (b"s3 version", '"x"')})
    with pytest.raises(S3Error) as e:
        push(client, files, sf)
    assert e.value.kind == "conflict"
    assert all(p["if_none_match"] for p in client.puts)
    assert client.objects["food_mappings.yaml"][0] == b"s3 version"
    assert "no sync state — uploading as new files" in capsys.readouterr().out


def test_unreadable_state_treated_as_no_state(tmp_path, capsys):
    files, sf, client = pulled(tmp_path)
    with open(sf, "w") as f:
        f.write("{not json")
    with pytest.raises(S3Error) as e:
        push(client, files, sf)
    assert e.value.kind == "conflict"
    assert "no sync state" in capsys.readouterr().out


def test_deleted_locally_after_pull_stops(tmp_path):
    files, sf, client = pulled(tmp_path)
    os.remove(files[1].path)
    with pytest.raises(SyncError):
        push(client, files, sf)
