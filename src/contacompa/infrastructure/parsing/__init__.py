from contacompa.infrastructure.parsing.base import PDF_MIME, Page, ParsedDoc, Parser
from contacompa.infrastructure.parsing.native import NativeParser
from contacompa.infrastructure.parsing.pymupdf import PyMuPDFParser

_PARSERS: dict[str, Parser] = {"native": NativeParser(), "pymupdf": PyMuPDFParser()}


def get_parser(name: str) -> Parser:
    try:
        return _PARSERS[name]
    except KeyError as exc:
        raise KeyError(f"unknown parser '{name}'; known: {sorted(_PARSERS)}") from exc


__all__ = ["PDF_MIME", "NativeParser", "Page", "ParsedDoc", "Parser", "PyMuPDFParser", "get_parser"]
