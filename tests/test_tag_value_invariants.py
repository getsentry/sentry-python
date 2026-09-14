import pytest

def sanitize_sentry_tag(key: str, value: str, max_len: int = 200) -> tuple[str, str] | None:
    if not key or not isinstance(key, str):
        return None
    k = key.strip()
    if not k:
        return None
    v = str(value) if value is not None else ""
    if len(v) > max_len:
        v = v[:max_len]
    return k, v

def test_valid_tag():
    assert sanitize_sentry_tag("environment", "production") == ("environment", "production")

def test_truncated_tag():
    long_str = "x" * 250
    k, v = sanitize_sentry_tag("payload_id", long_str)
    assert len(v) == 200

def test_invalid_key():
    assert sanitize_sentry_tag("", "production") is None
