import json
import os
import re
import shutil

import pytest

from omni_pilot.s3 import S3Error
from omni_pilot.sync import (
    SyncError,
    data_files,
    export_key,
    pull,
    pull_exports,
    push,
    state_path,
    upload_export,
)
from tests.helpers import FakeS3Client, food_row, write_food_log


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


# --- exports ---

EXPORT_KEY = re.compile(r"exports/\d{4}-\d{2}-\d{2}_\d{4}-\d{2}-\d{2}_[0-9a-f]{8}\.xlsx")


def make_export(tmp_path, name="export.xlsx", dates=("2026-10-04", "2026-10-02", "2026-10-06")):
    rows = [food_row("Banana", date=d) for d in dates]
    return write_food_log(tmp_path / name, rows)


def test_export_key_uses_food_log_dates_and_hash(tmp_path):
    key = export_key(make_export(tmp_path))

    assert EXPORT_KEY.fullmatch(key)
    assert key.startswith("exports/2026-10-02_2026-10-06_")


def test_same_file_same_key(tmp_path):
    path = make_export(tmp_path)
    copy = tmp_path / "other name.xlsx"
    shutil.copy(path, copy)

    assert export_key(str(copy)) == export_key(path)


def test_empty_export_has_no_key_and_is_not_uploaded(tmp_path):
    path = write_food_log(tmp_path / "empty.xlsx", [])
    client = FakeS3Client()

    assert export_key(path) is None
    assert upload_export(client, path) is None
    assert client.calls == []


def test_upload_export_uses_if_none_match(tmp_path):
    path = make_export(tmp_path)
    client = FakeS3Client()

    key, uploaded = upload_export(client, path)

    assert uploaded is True
    assert client.puts[0]["key"] == key
    assert client.puts[0]["if_none_match"] is True
    assert client.puts[0]["body"] == read(path)


def test_upload_existing_export_is_success(tmp_path):
    path = make_export(tmp_path)
    client = FakeS3Client()
    upload_export(client, path)

    key, uploaded = upload_export(client, path)

    assert EXPORT_KEY.fullmatch(key)
    assert uploaded is False
    assert len(client.objects) == 1


def test_upload_export_other_errors_propagate(tmp_path):
    class Failing(FakeS3Client):
        def put(self, key, body, **kwargs):
            raise S3Error("network", "S3 not reachable")

    with pytest.raises(S3Error):
        upload_export(Failing(), make_export(tmp_path))


def test_pull_exports_fetches_only_missing(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (data / "2026-09-01_2026-09-30_aaaaaaaa.xlsx").write_bytes(b"have")
    client = FakeS3Client(
        {
            "exports/2026-09-01_2026-09-30_aaaaaaaa.xlsx": (b"have", '"a"'),
            "exports/2026-10-01_2026-10-20_bbbbbbbb.xlsx": (b"new", '"b"'),
            "food_db.json": (b"db", '"c"'),
        }
    )

    pulled = pull_exports(client, str(data))

    assert pulled == ["2026-10-01_2026-10-20_bbbbbbbb.xlsx"]
    assert [c for c in client.calls if c[0] == "get"] == [
        ("get", "exports/2026-10-01_2026-10-20_bbbbbbbb.xlsx")
    ]
    assert read(data / "2026-10-01_2026-10-20_bbbbbbbb.xlsx") == b"new"
    assert read(data / "2026-09-01_2026-09-30_aaaaaaaa.xlsx") == b"have"


def test_pull_exports_creates_data_folder(tmp_path):
    data = tmp_path / "fresh" / "data"
    client = FakeS3Client({"exports/2026-10-01_2026-10-20_bbbbbbbb.xlsx": (b"new", '"b"')})

    assert pull_exports(client, str(data)) == ["2026-10-01_2026-10-20_bbbbbbbb.xlsx"]
    assert os.path.isdir(data)


def test_pulled_export_name_drops_prefix(tmp_path):
    path = make_export(tmp_path)
    client = FakeS3Client()
    key, _ = upload_export(client, path)
    data = tmp_path / "data"

    pull_exports(client, str(data))

    assert os.listdir(data) == [key.removeprefix("exports/")]
    assert read(data / key.removeprefix("exports/")) == read(path)


def test_pull_exports_with_nothing_in_s3_does_nothing(tmp_path):
    assert pull_exports(FakeS3Client(), str(tmp_path / "data")) == []
