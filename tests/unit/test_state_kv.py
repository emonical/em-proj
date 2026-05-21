"""Unit tests for em_proj.state.kv — pure KV ops against real Redis on db=15.

Uses the clean_db fixture from tests/conftest.py for per-test isolation.
Validates D-06..D-13, D-17, D-18 from Phase 2 CONTEXT.md.

Redis-touching tests depend on `clean_db` explicitly (function-scoped FLUSHDB on
db=15). Validation-only tests omit it — they must never reach Redis, and the
"validates key first" tests prove that by monkeypatching `get_client` to raise.
"""
from __future__ import annotations

import pytest

import em_proj.redis_client as rc
from em_proj.state.kv import (
    KEY_PREFIX,
    MAX_VALUE_BYTES,
    KvNotFound,
    ValidationError,
    kv_del,
    kv_get,
    kv_list,
    kv_set,
    validate_key,
)


@pytest.fixture(autouse=True)
def _reset_client_between_tests():
    """Reset the redis_client singleton so each test honors EM_PROJ_REDIS_DB=15.

    The clean_db fixture sets db=15 via its own client; kv.py calls get_client()
    which reads EM_PROJ_REDIS_DB. Resetting the singleton between tests keeps
    them isolated and ensures the test-DB env (set below) is picked up.
    """
    rc._reset_for_tests()
    yield
    rc._reset_for_tests()


@pytest.fixture(autouse=True)
def _point_kv_at_test_db(monkeypatch):
    """Force kv.py's get_client() onto db=15 so it shares the clean_db namespace."""
    monkeypatch.setenv("EM_PROJ_REDIS_DB", "15")


# --- validate_key (D-09) ----------------------------------------------------


def test_validate_key_accepts_alphanumeric():
    """All allowed character classes pass: letters, digits, _ . - / (D-09)."""
    for key in ("foo", "Foo123", "foo.bar", "foo_bar", "foo-bar", "foo/bar"):
        assert validate_key(key) is None


def test_validate_key_rejects_whitespace():
    """A key containing a space is rejected with code 'validation_error'."""
    with pytest.raises(ValidationError) as exc_info:
        validate_key("foo bar")
    assert exc_info.value.code == "validation_error"


def test_validate_key_rejects_colon():
    """A colon is rejected — it would collide with the state:kv: delimiter (D-09)."""
    with pytest.raises(ValidationError):
        validate_key("foo:bar")


@pytest.mark.parametrize(
    "metachar",
    [";", "|", "&", "$", "*", "?", "(", ")", "[", "]", "{", "}", "<", ">", "'", '"', "`"],
)
def test_validate_key_rejects_shell_metacharacters(metachar):
    """Every shell metacharacter is rejected by the regex (D-09)."""
    with pytest.raises(ValidationError):
        validate_key(f"foo{metachar}bar")


def test_validate_key_rejects_empty_string():
    """The empty string is rejected — the regex requires at least one char (D-09)."""
    with pytest.raises(ValidationError):
        validate_key("")


# --- kv_get (D-10) ----------------------------------------------------------


def test_kv_get_returns_string_value(clean_db):
    """kv_get reads back a value written under the state:kv: prefix."""
    clean_db.set("state:kv:foo", "bar")
    assert kv_get("foo") == "bar"


def test_kv_get_missing_raises_kv_not_found(clean_db):
    """kv_get on an absent key raises KvNotFound (D-10), not exit 0 / empty."""
    with pytest.raises(KvNotFound):
        kv_get("absent")


def test_kv_get_empty_string_value_is_distinct_from_missing(clean_db):
    """A key set to "" returns "" — distinct from a missing key (D-10 rationale)."""
    clean_db.set("state:kv:empty", "")
    assert kv_get("empty") == ""


def test_kv_get_validates_key_first(monkeypatch):
    """kv_get validates the key BEFORE touching Redis (D-09 gate ordering)."""

    def _fail(*args, **kwargs):
        raise AssertionError("get_client called despite invalid key")

    monkeypatch.setattr("em_proj.state.kv.get_client", _fail)
    with pytest.raises(ValidationError):
        kv_get("foo bar")


# --- kv_set (D-12) ----------------------------------------------------------


def test_kv_set_writes_with_prefix(clean_db):
    """kv_set stores the value under the state:kv: prefix (D-06)."""
    kv_set("foo", "bar")
    assert clean_db.get("state:kv:foo") == "bar"


def test_kv_set_with_explicit_ttl_sets_expiry(clean_db):
    """kv_set with ttl= issues SET EX <ttl> (KV-02). Allow 2s CI jitter."""
    kv_set("foo", "bar", ttl=60)
    assert clean_db.ttl("state:kv:foo") in (58, 59, 60)


def test_kv_set_bare_update_preserves_ttl(clean_db):
    """A bare update preserves the existing TTL via SET KEEPTTL (D-12).

    Without KEEPTTL, Redis SET would reset the TTL to -1 (persistent).
    """
    kv_set("foo", "v1", ttl=60)
    kv_set("foo", "v2")  # no ttl — must KEEPTTL
    assert clean_db.ttl("state:kv:foo") >= 55


def test_kv_set_bare_on_new_key_sets_no_ttl(clean_db):
    """KEEPTTL on a brand-new key is a safe no-op — the key has no TTL (-1)."""
    kv_set("brand_new", "x")
    assert clean_db.ttl("state:kv:brand_new") == -1


def test_kv_set_explicit_ttl_overrides_existing(clean_db):
    """An explicit ttl= overrides the prior state (here: persistent → 30s)."""
    kv_set("foo", "v1")  # persistent, no ttl
    kv_set("foo", "v2", ttl=30)
    assert clean_db.ttl("state:kv:foo") in (28, 29, 30)


def test_kv_set_rejects_oversized_value(clean_db):
    """A value over MAX_VALUE_BYTES raises ValidationError code 'value_too_large'."""
    with pytest.raises(ValidationError) as exc_info:
        kv_set("foo", "x" * (MAX_VALUE_BYTES + 1))
    assert exc_info.value.code == "value_too_large"


def test_kv_set_validates_key_first(monkeypatch):
    """kv_set validates the key BEFORE touching Redis (D-09 gate ordering)."""

    def _fail(*args, **kwargs):
        raise AssertionError("get_client called despite invalid key")

    monkeypatch.setattr("em_proj.state.kv.get_client", _fail)
    with pytest.raises(ValidationError):
        kv_set("foo bar", "x")


# --- kv_del (D-11) ----------------------------------------------------------


def test_kv_del_existing_returns_true(clean_db):
    """kv_del removes an existing key and returns True (D-11)."""
    kv_set("foo", "bar")
    assert kv_del("foo") is True
    assert clean_db.get("state:kv:foo") is None


def test_kv_del_missing_returns_false_no_raise(clean_db):
    """kv_del on a missing key returns False, never raises (rm -f semantics, D-11)."""
    assert kv_del("absent") is False


def test_kv_del_validates_key_first(monkeypatch):
    """kv_del validates the key BEFORE touching Redis (D-09 gate ordering)."""

    def _fail(*args, **kwargs):
        raise AssertionError("get_client called despite invalid key")

    monkeypatch.setattr("em_proj.state.kv.get_client", _fail)
    with pytest.raises(ValidationError):
        kv_del("foo bar")


# --- kv_list (D-07, D-08, D-13) --------------------------------------------


def test_kv_list_empty_returns_empty_list(clean_db):
    """kv_list with zero kv keys returns [] — a valid result, not an error (D-13)."""
    assert kv_list() == []


def test_kv_list_strips_prefix(clean_db):
    """kv_list returns user-typed keys (prefix stripped), sorted (D-07)."""
    kv_set("alpha", "1")
    kv_set("beta", "2")
    assert kv_list() == ["alpha", "beta"]


def test_kv_list_scope_excludes_lock_and_claim(clean_db):
    """kv_list scopes to state:kv:* only — never lock/claim namespaces (D-08).

    Sets one state:lock:* and one state:claim:* key directly via the raw client
    (kv_set cannot reach those namespaces — validate_key rejects the colon) to
    prove the SCAN MATCH never picks them up.
    """
    kv_set("included", "yes")
    clean_db.set("state:lock:excluded", "lock-data")
    clean_db.set("state:claim:excluded", "claim-data")
    assert kv_list() == ["included"]


def test_kv_list_sorted_alphabetically(clean_db):
    """kv_list sorts alphabetically regardless of insertion order (Claude's discretion)."""
    kv_set("zulu", "z")
    kv_set("alpha", "a")
    kv_set("mike", "m")
    assert kv_list() == ["alpha", "mike", "zulu"]
