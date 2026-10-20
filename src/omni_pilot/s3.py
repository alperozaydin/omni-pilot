"""Minimal S3 client: SigV4 signing by hand on `requests`, no boto3.

The app also runs inside a-Shell on an iPhone, which only has pure-Python
packages, so this signs requests with hashlib/hmac instead of using an SDK.
"""

import hashlib
import hmac
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from typing import Literal
from urllib.parse import quote

import requests

from omni_pilot.config import S3Config

S3_TIMEOUT = (5, 30)
_SESSION = requests.Session()

_NS = "{http://s3.amazonaws.com/doc/2006-03-01/}"
_ALGORITHM = "AWS4-HMAC-SHA256"


class S3Error(Exception):
    """An S3 request failed. `kind` tells the caller how to react."""

    def __init__(self, kind: Literal["network", "auth", "conflict", "other"], message: str):
        super().__init__(message)
        self.kind = kind
        self.message = message


def _hmac(key: bytes, message: str) -> bytes:
    return hmac.new(key, message.encode("utf-8"), hashlib.sha256).digest()


def _canonical_query(query: dict[str, str]) -> str:
    pairs = sorted((quote(k, safe="-_.~"), quote(v, safe="-_.~")) for k, v in query.items())
    return "&".join(f"{k}={v}" for k, v in pairs)


def authorization_header(
    method: str,
    path: str,
    query: dict[str, str],
    headers: dict[str, str],
    payload_hash: str,
    config: S3Config,
    amz_date: str,
) -> str:
    """Return the Authorization header value, signing exactly the headers passed.

    `path` is the already URI-encoded path.
    """
    canonical = sorted((k.lower(), " ".join(v.split())) for k, v in headers.items())
    canonical_headers = "".join(f"{k}:{v}\n" for k, v in canonical)
    signed_headers = ";".join(k for k, _ in canonical)
    canonical_request = "\n".join(
        [method, path, _canonical_query(query), canonical_headers, signed_headers, payload_hash]
    )

    day = amz_date[:8]
    scope = f"{day}/{config.region}/s3/aws4_request"
    string_to_sign = "\n".join(
        [
            _ALGORITHM,
            amz_date,
            scope,
            hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
        ]
    )

    key = _hmac(("AWS4" + config.secret_access_key).encode("utf-8"), day)
    for part in (config.region, "s3", "aws4_request"):
        key = _hmac(key, part)
    signature = hmac.new(key, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()

    return (
        f"{_ALGORITHM} Credential={config.access_key_id}/{scope},"
        f"SignedHeaders={signed_headers},Signature={signature}"
    )


def _error_code(body: bytes) -> str | None:
    try:
        return ET.fromstring(body).findtext("Code")
    except ET.ParseError:
        return None


class S3Client:
    def __init__(self, config: S3Config):
        self.config = config
        self._host = f"{config.bucket}.s3.{config.region}.amazonaws.com"

    def _request(
        self,
        method: str,
        key: str,
        *,
        query: dict[str, str] | None = None,
        body: bytes = b"",
        extra_headers: dict[str, str] | None = None,
    ):
        query = query or {}
        path = "/" + quote(key, safe="/-_.~")
        payload_hash = hashlib.sha256(body).hexdigest()
        amz_date = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        signed = {
            "host": self._host,
            "x-amz-content-sha256": payload_hash,
            "x-amz-date": amz_date,
        }
        headers = {
            "Host": self._host,
            "x-amz-content-sha256": payload_hash,
            "x-amz-date": amz_date,
            "Authorization": authorization_header(
                method, path, query, signed, payload_hash, self.config, amz_date
            ),
            **(extra_headers or {}),
        }
        url = f"https://{self._host}{path}"
        if query:
            url += "?" + _canonical_query(query)
        try:
            return _SESSION.request(
                method=method,
                url=url,
                headers=headers,
                data=body if method == "PUT" else None,
                timeout=S3_TIMEOUT,
            )
        except (requests.ConnectionError, requests.Timeout):
            raise S3Error("network", "S3 not reachable — is the network down?") from None

    def _raise_for_status(self, resp, key: str) -> None:
        if 200 <= resp.status_code < 300:
            return
        code = _error_code(resp.content)
        if resp.status_code == 403:
            if code == "RequestTimeTooSkewed":
                raise S3Error(
                    "other",
                    "S3 refused the request: this device's clock is off — "
                    "set the time automatically and run again",
                )
            source = (
                "s3 in settings.yaml"
                if self.config.key_source == "settings.yaml"
                else "the OMNI_PILOT_S3_* variables"
            )
            raise S3Error("auth", f"S3 refused the key — check {source}")
        if resp.status_code == 412 or (
            resp.status_code == 409 and code == "ConditionalRequestConflict"
        ):
            raise S3Error("conflict", f"{key} changed in S3 since it was downloaded")
        raise S3Error("other", f"S3 answered {resp.status_code} for {key}")

    def get(self, key: str) -> tuple[bytes, str] | None:
        """Return (body, etag), or None when the object does not exist."""
        resp = self._request("GET", key)
        if resp.status_code == 404 and _error_code(resp.content) in (None, "NoSuchKey"):
            return None
        self._raise_for_status(resp, key)
        return resp.content, resp.headers.get("ETag", "")

    def put(
        self,
        key: str,
        body: bytes,
        *,
        if_match: str | None = None,
        if_none_match: bool = False,
    ) -> str:
        """Upload `body` and return the new ETag. Conditions are sent unsigned."""
        extra: dict[str, str] = {}
        if if_match is not None:
            extra["If-Match"] = if_match
        if if_none_match:
            extra["If-None-Match"] = "*"
        resp = self._request("PUT", key, body=body, extra_headers=extra)
        self._raise_for_status(resp, key)
        return resp.headers.get("ETag", "")

    def list(self, prefix: str) -> list[str]:
        """Return every key starting with `prefix`, following continuation tokens."""
        keys: list[str] = []
        token: str | None = None
        while True:
            query = {"list-type": "2", "prefix": prefix}
            if token:
                query["continuation-token"] = token
            resp = self._request("GET", "", query=query)
            self._raise_for_status(resp, prefix)
            try:
                root = ET.fromstring(resp.content)
            except ET.ParseError:
                raise S3Error("other", f"S3 sent an unreadable listing for {prefix}") from None
            keys += [k.text or "" for k in root.iter(f"{_NS}Key")]
            if root.findtext(f"{_NS}IsTruncated") != "true":
                return keys
            token = root.findtext(f"{_NS}NextContinuationToken")
