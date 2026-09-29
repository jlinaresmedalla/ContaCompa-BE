"""Blob store protocol and local directory implementation."""

import asyncio
import hashlib
import os
import uuid
from pathlib import Path
from typing import Protocol


def sha256_hex(data: bytes) -> str:
    """Compute sha256 digest of data as lowercase hex string."""
    return hashlib.sha256(data).hexdigest()


class BlobStore(Protocol):
    """Protocol for blob storage operations."""

    async def put(self, key: str, data: bytes) -> None:
        """Store data under key. Atomic: write to .tmp then rename."""
        ...

    async def get(self, key: str) -> bytes:
        """Retrieve data for key. Raises FileNotFoundError if missing."""
        ...

    async def exists(self, key: str) -> bool:
        """Check if key exists in store."""
        ...

    async def delete(self, key: str) -> None:
        """Delete data for key. No-op if missing."""
        ...


class LocalBlobStore:
    """Stores blobs as files under a root directory.

    Keys are sha256 hex digests; the file path is <root>/<first two hex chars>/<key>.

    Note: Serializes same-key writes in-process with asyncio.Lock to prevent TOCTOU races.
    Cross-process atomicity is not guaranteed and is out of scope.
    """

    def __init__(self, root: Path) -> None:
        """Initialize blob store with root directory."""
        self.root = root
        self._locks: dict[str, asyncio.Lock] = {}

    def _validate_key(self, key: str) -> None:
        """Validate that key is exactly 64 lowercase hex chars.

        Raises ValueError if invalid.
        """
        if len(key) != 64:
            raise ValueError("invalid blob key")
        if not all(c in "0123456789abcdef" for c in key):
            raise ValueError("invalid blob key")

    def _get_path(self, key: str) -> Path:
        """Get the file path for a key."""
        return self.root / key[:2] / key

    def _lock_for(self, key: str) -> asyncio.Lock:
        """Get or create a lock for a specific key."""
        if key not in self._locks:
            self._locks[key] = asyncio.Lock()
        return self._locks[key]

    async def put(self, key: str, data: bytes) -> None:
        """Store data under key. Atomic: write to .tmp then rename.

        Raises ValueError if key is invalid or key collision detected.
        """
        self._validate_key(key)

        lock = self._lock_for(key)
        async with lock:
            path = self._get_path(key)

            # Check if key already exists with different bytes
            if await asyncio.to_thread(path.exists):
                existing_data = await asyncio.to_thread(path.read_bytes)
                if existing_data != data:
                    raise ValueError("blob key collision")
                # Same data already exists, no-op
                return

            # Create parent directories
            await asyncio.to_thread(path.parent.mkdir, parents=True, exist_ok=True)

            # Write to unique .tmp file
            tmp_path = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
            await asyncio.to_thread(tmp_path.write_bytes, data)

            # Atomic rename
            await asyncio.to_thread(os.replace, str(tmp_path), str(path))

    async def get(self, key: str) -> bytes:
        """Retrieve data for key.

        Raises FileNotFoundError if key doesn't exist.
        """
        self._validate_key(key)
        path = self._get_path(key)

        if not await asyncio.to_thread(path.exists):
            raise FileNotFoundError(f"Blob not found: {key}")

        return await asyncio.to_thread(path.read_bytes)

    async def exists(self, key: str) -> bool:
        """Check if key exists in store."""
        self._validate_key(key)
        path = self._get_path(key)
        return await asyncio.to_thread(path.exists)

    async def delete(self, key: str) -> None:
        """Delete data for key. No-op if missing."""
        self._validate_key(key)
        path = self._get_path(key)
        await asyncio.to_thread(path.unlink, missing_ok=True)


def _assert_protocol(store: LocalBlobStore) -> BlobStore:
    """Assert that LocalBlobStore satisfies BlobStore protocol."""
    return store
