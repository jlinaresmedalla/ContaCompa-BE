"""Unit tests for blob storage."""

import asyncio
from pathlib import Path
from typing import Any, cast

import pytest

from contacompa.infrastructure.blob import (
    BlobStore,
    LocalBlobStore,
    sha256_hex,
)


class TestSha256Hex:
    """Test sha256_hex hash function."""

    def test_known_digest(self) -> None:
        """sha256_hex(b"hello") returns the known digest."""
        result = sha256_hex(b"hello")
        expected = "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
        assert result == expected


class TestLocalBlobStore:
    """Test LocalBlobStore implementation."""

    def test_put_and_get(self, tmp_path: Path) -> None:
        """Put then get returns identical bytes; file lands at correct path."""
        store = LocalBlobStore(tmp_path)
        data = b"test data"
        key = sha256_hex(data)

        # Put the blob
        asyncio.run(store.put(key, data))

        # Verify file is at root/<key[:2]>/<key>
        expected_path = tmp_path / key[:2] / key
        assert expected_path.exists(), f"Expected file at {expected_path}"

        # Verify no .tmp file left behind
        tmp_file = Path(str(expected_path) + ".tmp")
        assert not tmp_file.exists(), f"Found leftover .tmp file: {tmp_file}"

        # Get and verify identical bytes
        retrieved = asyncio.run(store.get(key))
        assert retrieved == data

    def test_exists_and_delete(self, tmp_path: Path) -> None:
        """Exists returns false before put, true after; delete removes; delete again is no-op."""
        store = LocalBlobStore(tmp_path)
        data = b"test data"
        key = sha256_hex(data)

        # exists should be false before put
        assert not asyncio.run(store.exists(key))

        # put the blob
        asyncio.run(store.put(key, data))

        # exists should be true after put
        assert asyncio.run(store.exists(key))

        # delete should remove it
        asyncio.run(store.delete(key))
        assert not asyncio.run(store.exists(key))

        # delete again should be a no-op (not raise)
        asyncio.run(store.delete(key))
        assert not asyncio.run(store.exists(key))

    def test_invalid_keys(self, tmp_path: Path) -> None:
        """Invalid keys raise ValueError."""
        store = LocalBlobStore(tmp_path)
        data = b"test"

        # Test path traversal attempt
        with pytest.raises(ValueError, match="invalid blob key"):
            asyncio.run(store.put("../x", data))

        # Test uppercase (invalid)
        with pytest.raises(ValueError, match="invalid blob key"):
            asyncio.run(store.put("ABC" + "a" * 61, data))

        # Test wrong length (63 instead of 64)
        with pytest.raises(ValueError, match="invalid blob key"):
            asyncio.run(store.put("a" * 63, data))

        # Test non-hex characters
        with pytest.raises(ValueError, match="invalid blob key"):
            asyncio.run(store.put("g" * 64, data))

    def test_blob_key_collision(self, tmp_path: Path) -> None:
        """Put same key with same bytes is no-op; different bytes raises collision error."""
        store = LocalBlobStore(tmp_path)
        key = "a" * 64  # Valid hex key
        data1 = b"first data"
        data2 = b"second data"

        # Put first data
        asyncio.run(store.put(key, data1))

        # Put same key, same bytes should be no-op
        asyncio.run(store.put(key, data1))

        # Verify data is still the first data
        retrieved = asyncio.run(store.get(key))
        assert retrieved == data1

        # Put same key with different bytes should raise
        with pytest.raises(ValueError, match="blob key collision"):
            asyncio.run(store.put(key, data2))

    def test_get_missing_key(self, tmp_path: Path) -> None:
        """Get on missing key raises FileNotFoundError with key in message."""
        store = LocalBlobStore(tmp_path)
        key = "b" * 64

        with pytest.raises(FileNotFoundError) as exc_info:
            asyncio.run(store.get(key))

        assert key in str(exc_info.value)

    def test_concurrent_put_different_bytes(self, tmp_path: Path) -> None:
        """Two concurrent puts with different bytes: one succeeds, one gets collision error."""
        store = LocalBlobStore(tmp_path)
        key = "c" * 64  # Valid hex key
        data1 = b"first data"
        data2 = b"second data"

        # Run two concurrent puts
        async def run_concurrent() -> list[Any]:
            return cast(
                list[Any],
                await asyncio.gather(
                    store.put(key, data1),
                    store.put(key, data2),
                    return_exceptions=True,
                ),
            )

        results: list[Any] = asyncio.run(run_concurrent())

        # Exactly one should succeed (None) and one should raise
        successes = [r for r in results if r is None]
        errors = [r for r in results if isinstance(r, Exception)]
        assert len(successes) == 1, f"Expected 1 success, got {len(successes)}"
        assert len(errors) == 1, f"Expected 1 error, got {len(errors)}"

        # The error should be a collision
        error = errors[0]
        assert isinstance(error, ValueError)
        assert "blob key collision" in str(error)

        # Verify one of the datasets was stored (the winner's)
        stored = asyncio.run(store.get(key))
        assert stored in (data1, data2), "Stored data is neither input"

        # Verify no .tmp files remain
        tmp_files = list(tmp_path.glob("**/*.tmp"))
        assert len(tmp_files) == 0, f"Found leftover .tmp files: {tmp_files}"

    def test_concurrent_put_identical_bytes(self, tmp_path: Path) -> None:
        """Two concurrent puts with identical bytes both succeed."""
        store = LocalBlobStore(tmp_path)
        key = "d" * 64  # Valid hex key
        data = b"shared data"

        # Run two concurrent puts with identical bytes
        async def run_concurrent() -> list[Any]:
            return cast(
                list[Any],
                await asyncio.gather(
                    store.put(key, data),
                    store.put(key, data),
                    return_exceptions=True,
                ),
            )

        results: list[Any] = asyncio.run(run_concurrent())

        # Both should succeed (no exceptions)
        assert all(r is None for r in results), f"Expected all successes, got {results}"

        # Verify data was stored
        stored = asyncio.run(store.get(key))
        assert stored == data

        # Verify no .tmp files remain
        tmp_files = list(tmp_path.glob("**/*.tmp"))
        assert len(tmp_files) == 0, f"Found leftover .tmp files: {tmp_files}"

    def test_protocol_conformance(self, tmp_path: Path) -> None:
        """LocalBlobStore is accepted where BlobStore is expected."""
        store = LocalBlobStore(tmp_path)

        # Mypy checks this at compile time; at runtime we can verify the protocol methods exist
        assert hasattr(store, "put")
        assert hasattr(store, "get")
        assert hasattr(store, "exists")
        assert hasattr(store, "delete")

        # Type check: LocalBlobStore should satisfy BlobStore protocol
        def _assert_protocol(s: LocalBlobStore) -> BlobStore:
            return s

        # If this passes type checking, the protocol is satisfied
        result = _assert_protocol(store)
        assert result is store
