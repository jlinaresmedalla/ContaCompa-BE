"""API key rules: random secret, sha256 at rest, a life chosen at minting (12 h by default, at
most a week), constant-time admin key check."""

import hashlib
import secrets
from datetime import datetime, timedelta

DEFAULT_TTL_HOURS = 12
MAX_TTL_HOURS = 168


def generate_api_key() -> str:
    """256 bits of randomness, URL-safe."""
    return secrets.token_urlsafe(32)


def hash_api_key(api_key: str) -> str:
    return hashlib.sha256(api_key.encode()).hexdigest()


def expiry_from(issued_at: datetime, hours: int = DEFAULT_TTL_HOURS) -> datetime:
    if not 1 <= hours <= MAX_TTL_HOURS:
        raise ValueError(f"hours must be between 1 and {MAX_TTL_HOURS}")
    return issued_at + timedelta(hours=hours)


def is_live(expires_at: datetime, now: datetime) -> bool:
    return expires_at > now


def admin_key_matches(presented: str | None, configured: str | None) -> bool:
    """False when either key is missing, so an unset ADMIN_API_KEY never authorizes anyone."""
    if not presented or not configured:
        return False
    return secrets.compare_digest(presented.encode(), configured.encode())
