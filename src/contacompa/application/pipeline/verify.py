"""Check that the quote the model cites really appears on the page it cites. Self-assessed
confidence is not trustworthy on its own; a quote that is not on the page is the cheapest signal
that a value was invented."""

import re

from contacompa.infrastructure.parsing.base import ParsedDoc

UNVERIFIED_CONFIDENCE_CAP = 0.5


def _fold(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().casefold()


def quote_on_page(document: ParsedDoc, page_number: int, quote: str) -> bool | None:
    """True/False when the page has a text layer; None when verification is impossible
    (native parser, scanned page or missing page)."""
    if not document.pages or document.scanned:
        return None
    for page in document.pages:
        if page.number == page_number:
            if page.text is None:
                return None
            return _fold(quote) in _fold(page.text)
    return False


def verified_confidence(confidence: float, verified: bool | None) -> float:
    if verified is False:
        return min(confidence, UNVERIFIED_CONFIDENCE_CAP)
    return confidence
