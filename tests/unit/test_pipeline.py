from typing import Any

import pytest
from pydantic import BaseModel

from contacompa.application.pipeline import extract
from contacompa.application.pipeline.verify import quote_on_page, verified_confidence
from contacompa.domain.config import RunConfig
from contacompa.domain.prompt import Prompt
from contacompa.infrastructure.observability.cost import Usage
from contacompa.infrastructure.parsing.base import Page, ParsedDoc
from contacompa.infrastructure.parsing.pymupdf import (
    PdfError,
    PyMuPDFParser,
    has_text_layer,
    inspect_pdf,
)
from contacompa.infrastructure.providers.base import InvalidOutputError, ProviderResult


def field(
    value: str | None, confidence: float = 0.9, page: int = 1, quote: str | None = None
) -> dict[str, Any]:
    return {
        "value": value,
        "confidence": confidence,
        "source": {"page": page, "quote": quote or value} if value else None,
    }


def purchase_doc_output(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "doc_type": "invoice",
        "supplier_ruc": field("20123456789"),
        "supplier_name": field("Acme Tools S.A.C."),
        "doc_number": field("F001-000123"),
        "issue_date": field("15/03/2026"),
        "currency": field("PEN"),
        "total_amount": field("1.234,56"),
        "prices_include_igv": field(None),
        "buyer_ruc": field("20543306771"),
        "lines": [
            {
                "description": field("Taladro industrial"),
                "quantity": field("1"),
                "unit": field(None),
                "unit_price": field("1.045,00"),
                "line_total": field("1.045,00"),
            }
        ],
    }
    base.update(overrides)
    return base


class StubProvider:
    name = "stub"

    def __init__(self, output: dict[str, Any]) -> None:
        self.output = output
        self.mime_type: str | None = None

    async def extract(
        self, document: ParsedDoc, schema: type[BaseModel], prompt: Prompt, cfg: RunConfig
    ) -> ProviderResult:
        self.mime_type = document.mime_type
        return ProviderResult(
            model_output=self.output,
            usage=Usage(input_tokens=1000, output_tokens=200),
            latency_ms=42,
        )


CFG = RunConfig(provider="gemini", model="gemini-2.5-flash", prompt_version="v1", parser="pymupdf")


async def test_pipeline_normalizes_verifies_and_reports_missing(invoice_pdf: bytes) -> None:
    outcome = await extract(invoice_pdf, CFG, StubProvider(purchase_doc_output()))
    assert outcome.schema_type == "purchase_doc" and outcome.schema_version == "1"
    assert outcome.doc_type == "invoice"
    assert outcome.fields["total_amount"]["value"] == "1234.56"
    assert outcome.fields["total_amount"]["raw"] == "1.234,56"
    assert outcome.fields["total_amount"]["verified"] is True
    assert outcome.fields["issue_date"]["value"] == "2026-03-15"
    assert outcome.fields["supplier_ruc"]["value"] == "20123456789"
    line = outcome.fields["lines"][0]
    assert line["line_total"]["value"] == "1045.00"
    assert line["unit"]["value"] == "unit"  # not printed → default, not missing
    assert [m.field for m in outcome.missing] == ["prices_include_igv"]
    assert outcome.cost.free_tier is True and outcome.cost.billed_usd == 0
    assert outcome.page_count == 1 and outcome.scanned is False and outcome.parser == "pymupdf"


async def test_pipeline_keeps_printed_precision_of_quantities_and_prices(
    invoice_pdf: bytes,
) -> None:
    line = {
        "description": field("PRIS 5/16 X 3/4"),
        "quantity": field("30.000"),
        "unit": field("NIU"),
        "unit_price": field("6355.2967"),
        "line_total": field("1.80"),
    }
    outcome = await extract(invoice_pdf, CFG, StubProvider(purchase_doc_output(lines=[line])))
    rendered = outcome.fields["lines"][0]
    assert rendered["quantity"]["value"] == "30.000"  # Peru: '.' is the decimal separator
    assert rendered["unit_price"]["value"] == "6355.2967"  # never rounded to cents
    assert rendered["unit"]["value"] == "unit"


async def test_pipeline_caps_confidence_when_quote_not_on_page(invoice_pdf: bytes) -> None:
    output = purchase_doc_output(
        supplier_name=field("Globex Corp", confidence=0.95, quote="Globex Corp")
    )
    outcome = await extract(invoice_pdf, CFG, StubProvider(output))
    assert outcome.fields["supplier_name"]["verified"] is False
    assert outcome.fields["supplier_name"]["confidence"] == 0.5


async def test_pipeline_unknown_doc_type_and_unparseable(invoice_pdf: bytes) -> None:
    output = purchase_doc_output(doc_type="guia de remision", total_amount=field("N/A"))
    outcome = await extract(invoice_pdf, CFG, StubProvider(output))
    assert outcome.doc_type == "other"
    assert outcome.fields["total_amount"]["value"] == "N/A"
    assert any(m.reason == "unparseable_amount" for m in outcome.missing)


async def test_pipeline_native_parser_cannot_verify(invoice_pdf: bytes) -> None:
    cfg = CFG.model_copy(update={"parser": "native"})
    outcome = await extract(invoice_pdf, cfg, StubProvider(purchase_doc_output()))
    assert outcome.fields["total_amount"]["verified"] is None
    assert outcome.page_count is None


async def test_pipeline_sends_photos_as_images() -> None:
    provider = StubProvider(purchase_doc_output())
    outcome = await extract(b"\xff\xd8\xff-jpeg", CFG, provider, "image/jpeg")
    assert provider.mime_type == "image/jpeg"
    assert outcome.scanned is True  # pymupdf has no text layer to read from a photo


async def test_pipeline_rejects_invalid_output(invoice_pdf: bytes) -> None:
    with pytest.raises(InvalidOutputError):
        await extract(invoice_pdf, CFG, StubProvider({"doc_type": "invoice"}))


def test_verify_helpers() -> None:
    doc = ParsedDoc(data=b"", pages=(Page(1, "Total:  1.234,56\nAcme"), Page(2, None)))
    assert quote_on_page(doc, 1, "total: 1.234,56") is True
    assert quote_on_page(doc, 1, "nope") is False
    assert quote_on_page(doc, 2, "x") is None
    assert quote_on_page(doc, 9, "x") is False
    assert quote_on_page(ParsedDoc(data=b""), 1, "x") is None
    assert verified_confidence(0.9, False) == 0.5
    assert verified_confidence(0.9, None) == 0.9


async def test_pymupdf_parser_and_inspection(invoice_pdf: bytes) -> None:
    parsed = await PyMuPDFParser().parse(invoice_pdf)
    assert parsed.pages[0].text and "FACTURA" in parsed.pages[0].text
    assert inspect_pdf(invoice_pdf) == (1, False)
    assert has_text_layer(invoice_pdf) is True
    with pytest.raises(PdfError):
        inspect_pdf(b"%PDF-not-really")
