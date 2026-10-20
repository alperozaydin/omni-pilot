"""Fixtures that apply to every test."""
from unittest.mock import patch

import pytest

from omni_pilot import s3


@pytest.fixture(autouse=True)
def _no_s3_from_environment(monkeypatch):
    """Keep the shell's OMNI_PILOT_S3_* variables out: with them, analyze would talk to real S3."""
    for var in ("BUCKET", "REGION", "ACCESS_KEY_ID", "SECRET_ACCESS_KEY"):
        monkeypatch.delenv(f"OMNI_PILOT_S3_{var}", raising=False)


@pytest.fixture(autouse=True)
def _no_real_s3_requests():
    """Fail any test that would send a request to S3. A test's own patch of `request` overrides this."""

    def refuse(*args, **kwargs):
        raise AssertionError("real S3 request in a test")

    with patch.object(s3._SESSION, "request", refuse):
        yield
