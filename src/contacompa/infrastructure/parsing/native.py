from contacompa.infrastructure.parsing.base import PDF_MIME, ParsedDoc


class NativeParser:
    """No parsing: the file (PDF or photo) is sent to a vision-capable model as-is."""

    name = "native"

    async def parse(self, data: bytes, mime_type: str = PDF_MIME) -> ParsedDoc:
        return ParsedDoc(data=data, mime_type=mime_type, parser=self.name)
