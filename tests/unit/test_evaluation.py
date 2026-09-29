"""Scoring counts absent and extra lines as errors and flags regressions."""

import copy
from typing import Any

from contacompa.application.evaluation import gate, score


def _field(value: str) -> dict[str, Any]:
    return {"value": value, "confidence": 1.0, "source": None}


def _case() -> dict[str, Any]:
    expected = {
        "doc_type": "invoice",
        "supplier_ruc": _field("20600000005"),
        "supplier_name": _field("Distribuidora Andina S.A.C."),
        "doc_number": _field("F001-000001"),
        "issue_date": _field("2026-09-01"),
        "currency": _field("PEN"),
        "total_amount": _field("100.00"),
        "prices_include_igv": _field("true"),
        "buyer_ruc": _field("20543306771"),
        "lines": [
            {
                "description": _field("Cuadernos"),
                "quantity": _field("2"),
                "unit": _field("unit"),
                "unit_price": _field("50.00"),
                "line_total": _field("100.00"),
            }
        ],
    }
    return {
        "key": "case-1",
        "company_ruc": "20543306771",
        "expected": expected,
        "prediction": copy.deepcopy(expected),
        "expected_warnings": [],
    }


def test_exact_prediction_scores_full_accuracy() -> None:
    metrics = score([_case()])
    assert metrics["schema_validity"] == 1
    assert metrics["header_accuracy"] == 1
    assert metrics["line_accuracy"] == 1


def test_missing_and_extra_lines_count_as_errors() -> None:
    case = _case()
    case["prediction"]["lines"] = []
    assert score([case])["line_accuracy"] == 0
    case = _case()
    case["prediction"]["lines"].append(case["prediction"]["lines"][0])
    assert score([case])["line_accuracy"] == 0.5


def test_invalid_schema_fails_the_gate() -> None:
    baseline = score([_case()])
    case = _case()
    del case["prediction"]["supplier_ruc"]
    regressed = score([case])
    assert regressed["schema_validity"] == 0
    assert gate(regressed, baseline)
