import hashlib
import hmac

from nextix.github.webhooks import verify_signature

SECRET = "It's a Secret to Everybody"
BODY = b"Hello, World!"
# Worked example from GitHub's "Validating webhook deliveries" docs.
GITHUB_EXAMPLE = "sha256=757107ea0eb2509fc211221cce984b8a37570b6d7586c22c46f4379c8b043e17"


def sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_matches_githubs_documented_example() -> None:
    assert verify_signature(SECRET, BODY, GITHUB_EXAMPLE)


def test_valid_signature() -> None:
    assert verify_signature("s3cret", b'{"a":1}', sign("s3cret", b'{"a":1}'))


def test_wrong_secret() -> None:
    assert not verify_signature("s3cret", BODY, sign("other", BODY))


def test_tampered_body() -> None:
    assert not verify_signature("s3cret", BODY + b" ", sign("s3cret", BODY))


def test_missing_header() -> None:
    assert not verify_signature("s3cret", BODY, None)


def test_sha1_header_rejected() -> None:
    digest = hmac.new(b"s3cret", BODY, hashlib.sha1).hexdigest()
    assert not verify_signature("s3cret", BODY, f"sha1={digest}")


def test_fails_closed_without_configured_secret() -> None:
    # An empty secret must never validate, even against an "empty-key" signature.
    assert not verify_signature("", BODY, sign("", BODY))
