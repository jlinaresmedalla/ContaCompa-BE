"""API key rules (ADR 0017): random secret, sha256 hash, 12-hour expiry, admin key check."""

from datetime import UTC, datetime, timedelta

from contacompa.domain.api_key import (
    API_KEY_TTL,
    admin_key_matches,
    expiry_from,
    generate_api_key,
    hash_api_key,
    is_live,
)

NOW = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)


def test_generated_keys_are_long_and_unique() -> None:
    keys = {generate_api_key() for _ in range(50)}
    assert len(keys) == 50
    assert all(len(key) >= 43 for key in keys)  # token_urlsafe(32) = 256 bits


def test_hash_is_sha256_hex_and_not_the_key() -> None:
    digest = hash_api_key("abc")
    assert digest == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    assert hash_api_key("abc") == digest
    assert hash_api_key("abd") != digest


def test_key_lives_twelve_hours() -> None:
    expires_at = expiry_from(NOW)
    assert API_KEY_TTL == timedelta(hours=12)
    assert expires_at == NOW + timedelta(hours=12)
    assert is_live(expires_at, NOW + timedelta(hours=11, minutes=59))
    assert not is_live(expires_at, expires_at)
    assert not is_live(expires_at, NOW + timedelta(hours=12, seconds=1))


def test_admin_key_check() -> None:
    assert admin_key_matches("s3cret", "s3cret")
    assert not admin_key_matches("wrong", "s3cret")
    assert not admin_key_matches(None, "s3cret")
    assert not admin_key_matches("", "s3cret")
    assert not admin_key_matches("s3cret", None)  # ADMIN_API_KEY unset: nobody gets in
    assert not admin_key_matches("", "")
    assert not admin_key_matches(None, None)
    assert not admin_key_matches("s3crét", "s3cret")  # non-ASCII must not raise
