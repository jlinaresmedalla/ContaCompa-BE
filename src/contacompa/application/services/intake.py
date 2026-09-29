"""Intake: sniff and check an uploaded file, dedupe it per company, store it, enqueue a job."""

import asyncio
import re
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from contacompa.config import Settings
from contacompa.domain.config import RunConfig
from contacompa.domain.purchase_doc import SourceKind
from contacompa.infrastructure.blob import BlobStore, sha256_hex
from contacompa.infrastructure.db import queue
from contacompa.infrastructure.db.models import Company, Document
from contacompa.infrastructure.db.repos import (
    create_document,
    daily_spend,
    get_document_by_sha,
    latest_job_for_document,
)
from contacompa.infrastructure.parsing.base import PDF_MIME
from contacompa.infrastructure.parsing.pymupdf import (
    ImageError,
    PdfError,
    has_text_layer,
    image_size,
    inspect_pdf,
)

_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")
_SIGNATURES = (
    (b"%PDF", PDF_MIME),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
)


class IntakeError(Exception):
    """A rejected upload. `kind` maps to an HTTP status in the route."""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind


@dataclass(frozen=True)
class Submitted:
    document_id: UUID
    job_id: UUID | None
    duplicate: bool


@dataclass(frozen=True)
class FileFacts:
    mime_type: str
    source_kind: SourceKind
    page_count: int
    width_px: int | None = None
    height_px: int | None = None


def sniff_mime(data: bytes) -> str | None:
    """The file type from its first bytes; the client's Content-Type is not trusted."""
    return next((mime for magic, mime in _SIGNATURES if data.startswith(magic)), None)


def safe_filename(name: str | None) -> str:
    cleaned = _SAFE_NAME.sub("_", (name or "document").strip())[:255]
    return cleaned or "document"


def inspect_file(data: bytes, max_pages: int) -> FileFacts:
    mime = sniff_mime(data)
    if mime is None:
        raise IntakeError("unsupported", "file is not a PDF, JPEG or PNG")
    if mime != PDF_MIME:
        try:
            width, height = image_size(data)
        except ImageError as exc:
            raise IntakeError("unreadable", "unreadable image") from exc
        return FileFacts(mime, SourceKind.PHOTO, 1, width, height)
    try:
        page_count, needs_password = inspect_pdf(data)
        if needs_password:
            raise IntakeError("unreadable", "password-protected PDF")
        if page_count > max_pages:
            raise IntakeError("too_many_pages", f"document exceeds {max_pages} pages")
        kind = SourceKind.PDF_TEXT if has_text_layer(data) else SourceKind.PDF_SCANNED
    except PdfError as exc:
        raise IntakeError("unreadable", "unreadable PDF") from exc
    return FileFacts(mime, kind, page_count)


async def submit(
    session: AsyncSession,
    blobs: BlobStore,
    settings: Settings,
    company: Company,
    *,
    data: bytes,
    filename: str | None,
    run_config: RunConfig,
) -> Submitted:
    if await daily_spend(session) >= settings.daily_cost_ceiling_usd:
        raise IntakeError("paused", "intake paused: daily cost ceiling reached")
    if len(data) > settings.max_upload_bytes:
        raise IntakeError("too_large", "file exceeds the size limit")
    facts = await asyncio.to_thread(inspect_file, data, settings.max_pages)

    key = sha256_hex(data)
    existing = await get_document_by_sha(session, company.id, key)
    if existing is not None:
        job = await latest_job_for_document(session, existing.id)
        return Submitted(existing.id, job.id if job else None, duplicate=True)

    already_stored = await blobs.exists(key)
    await blobs.put(key, data)
    try:
        document = await create_document(
            session,
            company_id=company.id,
            sha256=key,
            filename=safe_filename(filename),
            mime_type=facts.mime_type,
            source_kind=facts.source_kind,
            size_bytes=len(data),
            page_count=facts.page_count,
            width_px=facts.width_px,
            height_px=facts.height_px,
            storage_key=key,
        )
        job = await queue.enqueue(session, document.id, run_config.model_dump())
        await session.commit()
        return Submitted(document.id, job.id, duplicate=False)
    except Exception as exc:
        await session.rollback()
        if not already_stored:
            reference = await session.scalar(
                select(Document.id).where(Document.storage_key == key).limit(1)
            )
            if reference is None:
                await blobs.delete(key)
        if isinstance(exc, IntegrityError):
            duplicate = await get_document_by_sha(session, company.id, key)
            if duplicate is not None:
                job = await latest_job_for_document(session, duplicate.id)
                return Submitted(duplicate.id, job.id if job else None, duplicate=True)
        raise
