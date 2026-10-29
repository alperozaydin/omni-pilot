"""One-time fill of retinol (`retinol_mcg`) for every entry of the food database.

Run once from the repo root, on the Mac:

    PYTHONPATH=src uv run python scripts/backfill_retinol.py

It first replaces the local data files with S3's when S3 is configured (S3
wins, so unpushed local edits are lost), then fetches retinol by USDA
ID for each cached match and recipe ingredient that lacks it (each ID once),
and pushes the database when anything changed. Safe to run again: a second run
fetches only what the first could not. Needs the USDA key in settings.yaml.
"""
from __future__ import annotations

import logging
import os
import sys

from tinydb import TinyDB

from omni_pilot.cli import DEFAULT_DB, DEFAULT_SETTINGS, _push_or_exit, _s3_context
from omni_pilot.config import S3ConfigError, load_settings, resolve_path
from omni_pilot.enricher import backfill_retinol
from omni_pilot.net import prefer_ipv4_connections
from omni_pilot.s3 import S3Error
from omni_pilot.sync import SyncError, pull

CONFLICT_HINT = "Run this script again (it pulls first), then it fills and pushes on top of S3's copy."


def main() -> None:
    logging.basicConfig(level=logging.WARNING)
    prefer_ipv4_connections()

    settings = load_settings(DEFAULT_SETTINGS)
    db_path = resolve_path("database_path", DEFAULT_DB, settings)
    print(f"Database: {db_path}")

    api_key = settings.get("usda_api_key", "")
    if not api_key:
        print("Error: No USDA API key in settings.yaml.")
        sys.exit(1)

    # Pull first, like analyze: the fill must start from S3's copy of the database.
    try:
        s3 = _s3_context(settings)
    except S3ConfigError as e:
        print(f"Error: {e}")
        sys.exit(1)
    if s3 is None:
        print("S3: not configured — local files only")
    else:
        client, files, state_file = s3
        try:
            labels = pull(client, files, state_file)
        except (S3Error, SyncError) as e:
            print(f"Error: {e}")
            sys.exit(1)
        print(f"S3: pulled from {client.config.bucket} ({', '.join(labels)})")

    if not os.path.exists(db_path):
        print(f"Error: No database at {db_path}.")
        sys.exit(1)

    with TinyDB(db_path) as db:
        report = backfill_retinol(
            db, api_key, progress=lambda done, total: print(f"  fetched {done} of {total} USDA foods...")
        )

    print(f"Filled retinol for {report['filled']} entries")
    if report["no_usda_id"]:
        print(f"  Skipped, no USDA ID: {', '.join(report['no_usda_id'])}")
    if report["failed"]:
        print(
            "  Fetch failed (retried by the next analyze or another run of this script): "
            + ", ".join(report["failed"])
        )
    if report["gone"]:
        print(f"  USDA no longer has the ID (stored as no retinol): {', '.join(report['gone'])}")
    if report["rate_limited"]:
        print("USDA is refusing requests (rate limit); run it again later.")
    if report["stopped_after_failures"]:
        print("USDA kept failing (not reachable, or refusing the key); stopped fetching, run it again later.")

    if s3 is not None:
        if report["filled"] or report["gone"]:
            for label in _push_or_exit(client, files, state_file, conflict_hint=CONFLICT_HINT):
                print(f"  {label}: uploaded")
        else:
            print(f"S3: nothing changed, nothing uploaded to {client.config.bucket}")


if __name__ == "__main__":
    main()
