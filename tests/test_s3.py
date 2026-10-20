import hashlib
from types import SimpleNamespace

import pytest
import requests
from requests.structures import CaseInsensitiveDict

from omni_pilot.config import S3Config, load_s3_config
from omni_pilot.s3 import S3Client, S3Error, authorization_header

EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()
EXAMPLE_CONFIG = S3Config(
    bucket="examplebucket",
    region="us-east-1",
    access_key_id="AKIAIOSFODNN7EXAMPLE",
    secret_access_key="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
    key_source="settings.yaml",
)
SECRET = "SECRETKEYSECRETKEYSECRETKEY0123456789ab"
CONFIG = S3Config(
    bucket="omni-pilot-data",
    region="eu-central-1",
    access_key_id="AKIAREALKEYID123456",
    secret_access_key=SECRET,
    key_source="settings.yaml",
)
ENV_CONFIG = S3Config(
    bucket="omni-pilot-data",
    region="eu-central-1",
    access_key_id="AKIAREALKEYID123456",
    secret_access_key=SECRET,
    key_source="OMNI_PILOT_S3_ACCESS_KEY_ID",
)


def test_signature_matches_aws_example():
    headers = {
        "host": "examplebucket.s3.amazonaws.com",
        "range": "bytes=0-9",
        "x-amz-content-sha256": EMPTY_SHA256,
        "x-amz-date": "20130524T000000Z",
    }
    header = authorization_header(
        "GET", "/test.txt", {}, headers, EMPTY_SHA256, EXAMPLE_CONFIG, "20130524T000000Z"
    )
    assert header == (
        "AWS4-HMAC-SHA256 Credential=AKIAIOSFODNN7EXAMPLE/20130524/us-east-1/s3/aws4_request,"
        "SignedHeaders=host;range;x-amz-content-sha256;x-amz-date,"
        "Signature=f0e8bdb87c964420e857bd35b5d6ed310bd44f0170aba48dd91039c6036bdb41"
    )


def test_signature_matches_aws_list_objects_example():
    headers = {
        "host": "examplebucket.s3.amazonaws.com",
        "x-amz-content-sha256": EMPTY_SHA256,
        "x-amz-date": "20130524T000000Z",
    }
    header = authorization_header(
        "GET", "/", {"max-keys": "2", "prefix": "J"}, headers, EMPTY_SHA256,
        EXAMPLE_CONFIG, "20130524T000000Z",
    )
    assert header.endswith(
        "Signature=34b48302e7b5fa45bde8084f4b7868a86f0a534bc59db6670ed5711ef69dc6f7"
    )


def test_guard_blocks_real_request_with_s3_variables_set(monkeypatch):
    for var, value in [
        ("BUCKET", "x"),
        ("REGION", "eu-central-1"),
        ("ACCESS_KEY_ID", "x"),
        ("SECRET_ACCESS_KEY", "x"),
    ]:
        monkeypatch.setenv(f"OMNI_PILOT_S3_{var}", value)
    config = load_s3_config({})
    assert config is not None
    with pytest.raises(AssertionError, match="real S3 request in a test"):
        S3Client(config).get("food_db.json")


def fake(status=200, body=b"", headers=None):
    return SimpleNamespace(
        status_code=status, content=body, headers=CaseInsensitiveDict(headers or {})
    )


def error_xml(code):
    return f"<Error><Code>{code}</Code><Message>m</Message></Error>".encode()


@pytest.fixture
def request_mock(mocker):
    return mocker.patch("omni_pilot.s3._SESSION.request", return_value=fake())


def sent_headers(request_mock, index=-1):
    return request_mock.call_args_list[index].kwargs["headers"]


def test_get_returns_body_and_etag(request_mock):
    request_mock.return_value = fake(200, b"data", {"ETag": '"abc"'})
    assert S3Client(CONFIG).get("food_db.json") == (b"data", '"abc"')
    assert request_mock.call_args.kwargs["method"] == "GET"


def test_get_missing_returns_none(request_mock):
    request_mock.return_value = fake(404, error_xml("NoSuchKey"))
    assert S3Client(CONFIG).get("food_db.json") is None


def test_put_sends_if_match_exactly_as_received(request_mock):
    request_mock.return_value = fake(200, headers={"ETag": '"n"'})
    S3Client(CONFIG).put("food_db.json", b"x", if_match='"9b2cf535f27731c974343645a3985328"')
    assert sent_headers(request_mock)["If-Match"] == '"9b2cf535f27731c974343645a3985328"'
    assert "If-Match" not in sent_headers(request_mock)["Authorization"]


def test_put_if_none_match_sends_star(request_mock):
    request_mock.return_value = fake(200, headers={"ETag": '"n"'})
    S3Client(CONFIG).put("food_db.json", b"x", if_none_match=True)
    assert sent_headers(request_mock)["If-None-Match"] == "*"
    assert "If-Match" not in sent_headers(request_mock)


def test_put_returns_new_etag_and_sends_body(request_mock):
    request_mock.return_value = fake(200, headers={"ETag": '"new"'})
    assert S3Client(CONFIG).put("k", b"body") == '"new"'
    kwargs = request_mock.call_args.kwargs
    assert kwargs["method"] == "PUT"
    assert kwargs["data"] == b"body"
    assert kwargs["headers"]["x-amz-content-sha256"] == hashlib.sha256(b"body").hexdigest()


def test_412_is_conflict(request_mock):
    request_mock.return_value = fake(412, error_xml("PreconditionFailed"))
    with pytest.raises(S3Error) as exc:
        S3Client(CONFIG).put("food_db.json", b"x", if_match='"a"')
    assert exc.value.kind == "conflict"
    assert "food_db.json" in exc.value.message
    assert str(exc.value) == exc.value.message


def test_409_conditional_request_conflict_is_conflict(request_mock):
    request_mock.return_value = fake(409, error_xml("ConditionalRequestConflict"))
    with pytest.raises(S3Error) as exc:
        S3Client(CONFIG).put("food_db.json", b"x", if_match='"a"')
    assert exc.value.kind == "conflict"


@pytest.mark.parametrize(
    "config, expected",
    [(CONFIG, "s3 in settings.yaml"), (ENV_CONFIG, "OMNI_PILOT_S3_*")],
)
def test_403_access_denied_is_auth_and_names_key_source(request_mock, config, expected):
    request_mock.return_value = fake(403, error_xml("AccessDenied"))
    with pytest.raises(S3Error) as exc:
        S3Client(config).get("k")
    assert exc.value.kind == "auth"
    assert expected in exc.value.message


def test_403_clock_skew_says_device_clock(request_mock):
    request_mock.return_value = fake(403, error_xml("RequestTimeTooSkewed"))
    with pytest.raises(S3Error) as exc:
        S3Client(CONFIG).get("k")
    assert exc.value.kind == "other"
    assert "clock" in exc.value.message


def test_500_is_other_with_status_and_key(request_mock):
    request_mock.return_value = fake(500, b"boom")
    with pytest.raises(S3Error) as exc:
        S3Client(CONFIG).get("food_db.json")
    assert exc.value.kind == "other"
    assert "500" in exc.value.message and "food_db.json" in exc.value.message


@pytest.mark.parametrize("error", [requests.ConnectionError("x"), requests.Timeout("x")])
def test_connection_error_and_timeout_are_network(request_mock, error):
    request_mock.side_effect = error
    with pytest.raises(S3Error) as exc:
        S3Client(CONFIG).get("k")
    assert exc.value.kind == "network"


def test_url_never_contains_key_or_signature(request_mock):
    request_mock.return_value = fake(200, b"<ListBucketResult/>")
    S3Client(CONFIG).list("a/")
    url = request_mock.call_args.kwargs["url"]
    assert "AKIA" not in url and "Signature" not in url and SECRET not in url


def test_no_secret_in_any_error_message(request_mock):
    cases = [
        fake(403, error_xml("AccessDenied")),
        fake(403, error_xml("RequestTimeTooSkewed")),
        fake(412, error_xml("PreconditionFailed")),
        fake(500, b"x"),
        requests.ConnectionError(SECRET),
    ]
    for case in cases:
        if isinstance(case, Exception):
            request_mock.side_effect = case
        else:
            request_mock.side_effect = None
            request_mock.return_value = case
        with pytest.raises(S3Error) as exc:
            S3Client(CONFIG).get("k")
        assert SECRET not in exc.value.message
        assert CONFIG.access_key_id not in exc.value.message


def test_timeout_tuple_passed(request_mock):
    S3Client(CONFIG).get("k")
    assert request_mock.call_args.kwargs["timeout"] == (5, 30)


def test_url_is_virtual_hosted(request_mock):
    S3Client(CONFIG).get("food_db.json")
    assert (
        request_mock.call_args.kwargs["url"]
        == "https://omni-pilot-data.s3.eu-central-1.amazonaws.com/food_db.json"
    )
    assert sent_headers(request_mock)["Host"] == "omni-pilot-data.s3.eu-central-1.amazonaws.com"


def test_list_follows_continuation_tokens(request_mock):
    ns = "http://s3.amazonaws.com/doc/2006-03-01/"
    page1 = (
        f'<ListBucketResult xmlns="{ns}"><IsTruncated>true</IsTruncated>'
        "<NextContinuationToken>t1</NextContinuationToken>"
        "<Contents><Key>a/1</Key></Contents></ListBucketResult>"
    ).encode()
    page2 = (
        f'<ListBucketResult xmlns="{ns}"><IsTruncated>false</IsTruncated>'
        "<Contents><Key>a/2</Key></Contents></ListBucketResult>"
    ).encode()
    request_mock.side_effect = [fake(200, page1), fake(200, page2)]
    assert S3Client(CONFIG).list("a/") == ["a/1", "a/2"]
    first, second = (c.kwargs["url"] for c in request_mock.call_args_list)
    assert "continuation-token" not in first
    assert "continuation-token=t1" in second
    assert "list-type=2" in first and "prefix=a%2F" in first


def test_key_with_special_characters_is_encoded(request_mock):
    S3Client(CONFIG).get("dir/my file.json")
    assert request_mock.call_args.kwargs["url"].endswith("/dir/my%20file.json")
