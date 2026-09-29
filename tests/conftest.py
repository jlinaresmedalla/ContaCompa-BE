from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path

import pytest
from fpdf import FPDF

from contacompa.config import Settings, get_settings


def make_invoice_pdf(
    *,
    vendor: str = "Acme Tools S.A.C.",
    number: str = "F001-000123",
    issue_date: str = "15/03/2026",
    total: str = "1.234,56",
    currency: str = "PEN",
    pages: int = 1,
) -> bytes:
    pdf = FPDF()
    pdf.set_font("Helvetica", size=12)
    for page in range(pages):
        pdf.add_page()
        pdf.cell(0, 10, f"FACTURA ELECTRONICA {number}", new_x="LMARGIN", new_y="NEXT")
        pdf.cell(0, 10, f"Vendor: {vendor}", new_x="LMARGIN", new_y="NEXT")
        pdf.cell(0, 10, "RUC: 20123456789", new_x="LMARGIN", new_y="NEXT")
        pdf.cell(0, 10, "Cliente RUC: 20543306771", new_x="LMARGIN", new_y="NEXT")
        pdf.cell(0, 10, f"Fecha de emision: {issue_date}", new_x="LMARGIN", new_y="NEXT")
        pdf.cell(0, 10, "Item: Taladro industrial  x1  1.045,00", new_x="LMARGIN", new_y="NEXT")
        if page == pages - 1:
            pdf.cell(0, 10, f"Moneda: {currency}", new_x="LMARGIN", new_y="NEXT")
            pdf.cell(0, 10, "Subtotal: 1.045,00", new_x="LMARGIN", new_y="NEXT")
            pdf.cell(0, 10, "IGV 18%: 189,56", new_x="LMARGIN", new_y="NEXT")
            pdf.cell(0, 10, f"TOTAL: {total}", new_x="LMARGIN", new_y="NEXT")
    return bytes(pdf.output())


@pytest.fixture
def invoice_pdf() -> bytes:
    return make_invoice_pdf()


@pytest.fixture
def test_settings(tmp_path: Path) -> Iterator[Settings]:
    settings = Settings(
        _env_file=None,
        database_url="postgresql+asyncpg://app:app@localhost:5432/test",
        api_key="test-key",
        blob_dir=str(tmp_path / "blobs"),
        daily_cost_ceiling_usd=Decimal("1.00"),
    )
    yield settings
    get_settings.cache_clear()
