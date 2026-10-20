"""Pull and push of the app's data files between this device and S3.

S3 is the source of truth: `pull` replaces the local copies, `push` uploads
only what changed and only if S3 still holds the version that was pulled.
"""

import hashlib
import json
import logging
import os
import tempfile
from typing import NamedTuple

from omni_pilot.parser import parse_food_log
from omni_pilot.s3 import S3Client, S3Error

EXPORTS_PREFIX = "exports/"


class DataFile(NamedTuple):
    key: str
    path: str
    label: str


class SyncError(Exception):
    """A pull or push cannot go on; the message says why."""


def data_files(db_path: str, mappings_path: str, custom_foods_path: str) -> list[DataFile]:
    """The three data files in push order (the database goes first)."""
    return [
        DataFile("food_db.json", db_path, "database"),
        DataFile("food_mappings.yaml", mappings_path, "mappings"),
        DataFile("custom_foods.yaml", custom_foods_path, "recipes"),
    ]


def state_path(db_path: str) -> str:
    return os.path.join(os.path.dirname(db_path), ".s3_state.json")


def _write_atomic(path: str, body: bytes) -> None:
    folder = os.path.dirname(path) or "."
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=".tmp-")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(body)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def _sha256(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _save_state(state_file: str, state: dict[str, dict]) -> None:
    _write_atomic(state_file, json.dumps({"files": state}, indent=2).encode("utf-8"))


def _load_state(state_file: str) -> dict[str, dict] | None:
    try:
        with open(state_file, encoding="utf-8") as f:
            files = json.load(f)["files"]
        if not isinstance(files, dict):
            return None
        return files
    except (OSError, ValueError, KeyError, TypeError):
        return None


def pull(client: S3Client, files: list[DataFile], state_file: str) -> list[str]:
    """Replace the local data files with S3's. Returns the labels found in S3."""
    fetched = [(f, client.get(f.key)) for f in files]
    if all(result is None for _, result in fetched):
        raise SyncError(
            f"S3 bucket {client.config.bucket} has none of the data files — "
            "wrong bucket, or the one-time copy was not done"
        )

    state: dict[str, dict] = {}
    found: list[str] = []
    for f, result in fetched:
        if result is None:
            if os.path.exists(f.path):
                os.remove(f.path)
            state[f.key] = {"etag": None, "sha256": None}
        else:
            body, etag = result
            _write_atomic(f.path, body)
            state[f.key] = {"etag": etag, "sha256": _sha256(body)}
            found.append(f.label)
    _save_state(state_file, state)
    return found


def push(client: S3Client, files: list[DataFile], state_file: str) -> list[str]:
    """Upload the data files that changed since the last pull. Returns their labels."""
    state = _load_state(state_file)
    if state is None:
        print("no sync state — uploading as new files")
        state = {}

    uploaded: list[str] = []
    for f in files:
        recorded = state.get(f.key) or {}
        etag = recorded.get("etag")
        recorded_sha = recorded.get("sha256")

        if not os.path.exists(f.path):
            if etag is not None:
                raise SyncError(
                    f"{f.path} was deleted locally after it was downloaded; "
                    "deleting files in S3 is not supported"
                )
            continue

        with open(f.path, "rb") as fh:
            body = fh.read()
        sha = _sha256(body)
        if sha == recorded_sha:
            continue

        try:
            if etag is not None:
                new_etag = client.put(f.key, body, if_match=etag)
            else:
                new_etag = client.put(f.key, body, if_none_match=True)
        except S3Error as e:
            if e.kind != "conflict":
                raise
            raise S3Error(
                "conflict",
                f"{f.key} changed in S3 since it was downloaded (another device or the "
                "daily routine wrote it). Nothing more was uploaded. Run again: the next "
                "run downloads the new version.",
            ) from e

        state[f.key] = {"etag": new_etag, "sha256": sha}
        _save_state(state_file, state)
        uploaded.append(f.label)
    return uploaded


def export_key(path: str) -> str | None:
    """S3 key for a MacroFactor export: its Food Log date range and a content hash.

    The same file always gets the same key, whatever it is called locally.
    None when the Food Log has no entries.
    """
    logging.disable(logging.WARNING)  # the parser warns about every "Quick Add" row
    try:
        entries = parse_food_log(path)
    finally:
        logging.disable(logging.NOTSET)
    if not entries:
        return None
    dates = [e["date"][:10] for e in entries]
    with open(path, "rb") as f:
        digest = _sha256(f.read())[:8]
    return f"{EXPORTS_PREFIX}{min(dates)}_{max(dates)}_{digest}.xlsx"


def upload_export(client: S3Client, path: str) -> tuple[str, bool] | None:
    """Upload an export unless S3 already holds it. Returns (key, uploaded), None if empty."""
    key = export_key(path)
    if key is None:
        return None
    with open(path, "rb") as f:
        body = f.read()
    try:
        client.put(key, body, if_none_match=True)
    except S3Error as e:
        if e.kind != "conflict":
            raise
        return key, False
    return key, True


def pull_exports(client: S3Client, exports_dir: str = "data") -> list[str]:
    """Download the exports S3 holds that exports_dir lacks. Returns the file names."""
    have = set(os.listdir(exports_dir)) if os.path.isdir(exports_dir) else set()
    pulled: list[str] = []
    for key in client.list(EXPORTS_PREFIX):
        name = key.removeprefix(EXPORTS_PREFIX)
        if not name or name in have:
            continue
        result = client.get(key)
        if result is None:
            continue
        _write_atomic(os.path.join(exports_dir, name), result[0])
        pulled.append(name)
    return pulled
