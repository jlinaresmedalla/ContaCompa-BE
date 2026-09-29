"""Blob store protocol and local directory implementation."""

import asyncio
import hashlib
import os
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from contacompa.config import Settings

if TYPE_CHECKING:
    from mypy_boto3_s3.client import S3Client


def sha256_hex(data: bytes) -> str:
    """Compute sha256 digest of data as lowercase hex string."""
    return hashlib.sha256(data).hexdigest()


def _validate_key(key: str) -> None:
    """Validate that key is exactly 64 lowercase hex chars.

    Raises ValueError if invalid.
    """
    if len(key) != 64:
        raise ValueError("invalid blob key")
    if not all(c in "0123456789abcdef" for c in key):
        raise ValueError("invalid blob key")


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

    async def check(self) -> None:
        """Raise if the store is unreachable (used by /readyz)."""
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
        _validate_key(key)

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
        _validate_key(key)
        path = self._get_path(key)

        if not await asyncio.to_thread(path.exists):
            raise FileNotFoundError(f"Blob not found: {key}")

        return await asyncio.to_thread(path.read_bytes)

    async def exists(self, key: str) -> bool:
        """Check if key exists in store."""
        _validate_key(key)
        path = self._get_path(key)
        return await asyncio.to_thread(path.exists)

    async def delete(self, key: str) -> None:
        """Delete data for key. No-op if missing."""
        _validate_key(key)
        path = self._get_path(key)
        await asyncio.to_thread(path.unlink, missing_ok=True)

    async def check(self) -> None:
        """Raise OSError if the root directory cannot be created or is not writable."""
        await asyncio.to_thread(self.root.mkdir, parents=True, exist_ok=True)
        if not await asyncio.to_thread(os.access, self.root, os.W_OK):
            raise PermissionError("blob directory is not writable")


_MISSING_CODES = {"404", "NoSuchKey", "NotFound"}


def _is_missing(exc: ClientError) -> bool:
    return str(exc.response.get("Error", {}).get("Code")) in _MISSING_CODES


class S3BlobStore:
    """Stores blobs as objects in an S3-compatible bucket (Neon Object Storage).

    Keys are the same sha256 hex digests as the disk store. boto3 configures itself from the
    standard AWS variables (AWS_ENDPOINT_URL_S3, AWS_REGION, AWS_ACCESS_KEY_ID,
    AWS_SECRET_ACCESS_KEY); the client is synchronous, so calls run through asyncio.to_thread.
    Creating the client makes no network call.
    """

    def __init__(self, bucket: str) -> None:
        self.bucket = bucket
        self._client: S3Client = boto3.client(
            "s3",
            region_name=os.environ.get("AWS_REGION"),
            config=Config(
                s3={"addressing_style": "path"},
                connect_timeout=5,
                read_timeout=30,
                retries={"max_attempts": 2, "mode": "standard"},
                # Only send checksums when the API requires them: other S3 stores reject the
                # default trailing checksums newer botocore adds.
                request_checksum_calculation="when_required",
                response_checksum_validation="when_required",
            ),
        )

    def _get_object(self, key: str) -> bytes | None:
        try:
            response = self._client.get_object(Bucket=self.bucket, Key=key)
        except ClientError as exc:
            if _is_missing(exc):
                return None
            raise
        return response["Body"].read()

    def _head_size(self, key: str) -> int | None:
        """Size in bytes of the object, or None if it does not exist."""
        try:
            response = self._client.head_object(Bucket=self.bucket, Key=key)
        except ClientError as exc:
            if _is_missing(exc):
                return None
            raise
        return response["ContentLength"]

    async def put(self, key: str, data: bytes) -> None:
        """Store data under key.

        Raises ValueError if key is invalid or the key exists with a different size. Keys are the
        sha256 of the bytes, so one HEAD (size compare) stands in for downloading the object.
        """
        _validate_key(key)
        existing_size = await asyncio.to_thread(self._head_size, key)
        if existing_size is not None:
            if existing_size != len(data):
                raise ValueError("blob key collision")
            return
        await asyncio.to_thread(self._client.put_object, Bucket=self.bucket, Key=key, Body=data)

    async def get(self, key: str) -> bytes:
        """Retrieve data for key. Raises FileNotFoundError if missing."""
        _validate_key(key)
        data = await asyncio.to_thread(self._get_object, key)
        if data is None:
            raise FileNotFoundError(f"Blob not found: {key}")
        return data

    async def exists(self, key: str) -> bool:
        """Check if key exists in the bucket."""
        _validate_key(key)
        return await asyncio.to_thread(self._head_size, key) is not None

    async def delete(self, key: str) -> None:
        """Delete data for key. No-op if missing (S3 deletes are idempotent)."""
        _validate_key(key)
        await asyncio.to_thread(self._client.delete_object, Bucket=self.bucket, Key=key)

    async def check(self) -> None:
        """Raise if the bucket is unreachable or not accessible with these credentials."""
        await asyncio.to_thread(self._client.head_bucket, Bucket=self.bucket)


def make_blob_store(settings: Settings) -> BlobStore:
    """S3 store when S3_BUCKET is set, else the disk store under BLOB_DIR."""
    if settings.s3_bucket:
        return S3BlobStore(settings.s3_bucket)
    if not settings.blob_dir:
        raise ValueError("BLOB_DIR is required when S3_BUCKET is not set")
    return LocalBlobStore(Path(settings.blob_dir))
