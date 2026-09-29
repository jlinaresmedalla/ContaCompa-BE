"""API key rules (ADR 0017, 0022): random secret, sha256 hash, chosen life, admin key check."""

from datetime import UTC, datetime, timedelta

import pytest

from contacompa.domain.api_key import (
    DEFAULT_TTL_HOURS,
    MAX_TTL_HOURS,
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
    assert DEFAULT_TTL_HOURS == 12
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


def test_key_life_is_chosen_at_minting() -> None:
    assert expiry_from(NOW, 1) == NOW + timedelta(hours=1)
    assert expiry_from(NOW, MAX_TTL_HOURS) == NOW + timedelta(hours=168)


@pytest.mark.parametrize("hours", [0, -1, MAX_TTL_HOURS + 1])
def test_key_life_outside_bounds_is_rejected(hours: int) -> None:
    with pytest.raises(ValueError, match="between 1 and 168"):
        expiry_from(NOW, hours)
