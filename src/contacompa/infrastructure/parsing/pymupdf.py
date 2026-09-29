import asyncio

import pymupdf

from contacompa.infrastructure.parsing.base import PDF_MIME, Page, ParsedDoc


class PdfError(ValueError):
    """The bytes are not a readable PDF (malformed or password-protected)."""


class ImageError(ValueError):
    """The bytes are not a readable JPEG or PNG."""


def inspect_pdf(pdf_bytes: bytes) -> tuple[int, bool]:
    """Return (page_count, needs_password). Raises PdfError when the bytes are not a PDF."""
    try:
        with pymupdf.open(stream=pdf_bytes, filetype="pdf") as doc:
            return doc.page_count, bool(doc.needs_pass)
    except (pymupdf.FileDataError, RuntimeError, ValueError) as exc:
        raise PdfError("unreadable PDF") from exc


def has_text_layer(pdf_bytes: bytes) -> bool:
    """True when any page has extractable text (a digital PDF rather than a scan)."""
    try:
        with pymupdf.open(stream=pdf_bytes, filetype="pdf") as doc:
            return any(page.get_text("text").strip() for page in doc)
    except (pymupdf.FileDataError, RuntimeError, ValueError) as exc:
        raise PdfError("unreadable PDF") from exc


def image_size(image_bytes: bytes) -> tuple[int, int]:
    """(width, height) in pixels. Raises ImageError when the bytes are not a readable image."""
    try:
        pixmap = pymupdf.Pixmap(image_bytes)
    except (RuntimeError, ValueError) as exc:
        raise ImageError("unreadable image") from exc
    return pixmap.width, pixmap.height


def _extract_pages(pdf_bytes: bytes) -> tuple[Page, ...]:
    try:
        with pymupdf.open(stream=pdf_bytes, filetype="pdf") as doc:
            if doc.needs_pass:
                raise PdfError("password-protected PDF")
            pages = []
            for index, page in enumerate(doc, start=1):
                text = page.get_text("text").strip()
                pages.append(Page(number=index, text=text or None))
            return tuple(pages)
    except (pymupdf.FileDataError, RuntimeError) as exc:
        raise PdfError("unreadable PDF") from exc


class PyMuPDFParser:
    """Text-layer extraction. Pages without a text layer are marked; a document with no text at
    all is flagged as scanned so the pipeline can fall back to the native path."""

    name = "pymupdf"

    async def parse(self, data: bytes, mime_type: str = PDF_MIME) -> ParsedDoc:
        if mime_type != PDF_MIME:
            # A photo has no text layer: mark it scanned so the model reads the image itself.
            return ParsedDoc(
                data=data,
                mime_type=mime_type,
                pages=(Page(number=1, text=None),),
                scanned=True,
                parser=self.name,
            )
        pages = await asyncio.to_thread(_extract_pages, data)
        scanned = all(page.text is None for page in pages)
        return ParsedDoc(data=data, pages=pages, scanned=scanned, parser=self.name)
