"""API key rules: random secret, sha256 at rest, 12-hour life, constant-time admin key check."""

import hashlib
import secrets
from datetime import datetime, timedelta

API_KEY_TTL = timedelta(hours=12)


def generate_api_key() -> str:
    """256 bits of randomness, URL-safe."""
    return secrets.token_urlsafe(32)


def hash_api_key(api_key: str) -> str:
    return hashlib.sha256(api_key.encode()).hexdigest()


def expiry_from(issued_at: datetime) -> datetime:
    return issued_at + API_KEY_TTL


def is_live(expires_at: datetime, now: datetime) -> bool:
    return expires_at > now


def admin_key_matches(presented: str | None, configured: str | None) -> bool:
    """False when either key is missing, so an unset ADMIN_API_KEY never authorizes anyone."""
    if not presented or not configured:
        return False
    return secrets.compare_digest(presented.encode(), configured.encode())
