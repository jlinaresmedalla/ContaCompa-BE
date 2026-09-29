from dataclasses import dataclass
from typing import Protocol

PDF_MIME = "application/pdf"


@dataclass(frozen=True)
class Page:
    number: int
    text: str | None


@dataclass(frozen=True)
class ParsedDoc:
    data: bytes
    mime_type: str = PDF_MIME
    pages: tuple[Page, ...] = ()
    scanned: bool = False
    parser: str = "native"

    @property
    def text(self) -> str | None:
        if self.scanned or not self.pages:
            return None
        return "\f".join(page.text or "" for page in self.pages)

    @property
    def page_count(self) -> int | None:
        return len(self.pages) or None


class Parser(Protocol):
    name: str

    async def parse(self, data: bytes, mime_type: str = PDF_MIME) -> ParsedDoc: ...
