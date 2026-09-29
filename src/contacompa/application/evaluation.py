"""Score normalized headers and printed-order lines, including absent or extra rows."""

from collections import Counter
from decimal import Decimal
from math import ceil
from typing import Any

from pydantic import ValidationError

from contacompa.domain.checks import Severity, find_issues, severity_of
from contacompa.domain.normalize import normalize
from contacompa.domain.purchase_doc import PurchaseDocValues, values_from_fields
from contacompa.domain.schemas import get_schema
from contacompa.domain.schemas.purchase_doc import PurchaseDoc as SchemaPurchaseDoc

HEADER_NAMES = (
    "doc_type",
    "supplier_ruc",
    "supplier_name",
    "doc_number",
    "issue_date",
    "currency",
    "total_amount",
    "prices_include_igv",
    "buyer_ruc",
)
LINE_NAMES = ("description", "quantity", "unit", "unit_price", "line_total")


def _normalized(raw: dict[str, Any]) -> PurchaseDocValues:
    spec = get_schema("purchase_doc")
    model = SchemaPurchaseDoc.model_validate(raw)
    rendered: dict[str, Any] = {}
    for name in HEADER_NAMES[1:]:
        value = getattr(model, name).value
        kind = spec.kinds[name]
        rendered[name] = {
            "value": normalize(kind, value, spec.decimal_separator) if value is not None else None
        }
    rendered["lines"] = [
        {
            name: {
                "value": normalize(
                    spec.kinds[f"lines.{name}"], getattr(line, name).value, spec.decimal_separator
                )
                if getattr(line, name).value is not None
                else None
            }
            for name in LINE_NAMES
        }
        for line in model.lines
    ]
    return values_from_fields(model.doc_type, rendered)


def _value(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return str(value.normalize())
    return str(getattr(value, "value", value)).casefold().strip()


def score(cases: list[dict[str, Any]]) -> dict[str, Any]:
    header_correct = header_total = line_correct = line_total = valid = 0
    header_hits: Counter[str] = Counter()
    line_hits: Counter[str] = Counter()
    line_denominators: Counter[str] = Counter()
    warning_found = warning_total = 0
    cost = Decimal(0)
    latencies: list[int] = []
    for case in cases:
        expected = _normalized(case["expected"])
        prediction = case["prediction"]
        cost += Decimal(str(case.get("cost_usd", "0")))
        latencies.append(int(case.get("latency_ms", 0)))
        try:
            actual = _normalized(prediction)
            valid += 1
        except (ValidationError, ValueError, TypeError):
            actual = None
        for name in HEADER_NAMES:
            header_total += 1
            if actual is not None and _value(getattr(expected, name)) == _value(
                getattr(actual, name)
            ):
                header_correct += 1
                header_hits[name] += 1
        actual_lines = actual.lines if actual else ()
        for index in range(max(len(expected.lines), len(actual_lines))):
            for name in LINE_NAMES:
                line_total += 1
                line_denominators[name] += 1
                if index < len(expected.lines) and index < len(actual_lines):
                    if _value(getattr(expected.lines[index], name)) == _value(
                        getattr(actual_lines[index], name)
                    ):
                        line_correct += 1
                        line_hits[name] += 1
        labeled = Counter(tuple(item) for item in case.get("expected_warnings", []))
        warning_total += sum(labeled.values())
        predicted = (
            Counter(
                (issue.code.value, issue.line_number)
                for issue in find_issues(actual, case["company_ruc"])
                if severity_of(issue.code.value) is Severity.WARNING
            )
            if actual
            else Counter()
        )
        warning_found += sum((labeled & predicted).values())
    count = len(cases)
    if not count:
        raise ValueError("evaluation set is empty")
    ordered_latencies = sorted(latencies)
    return {
        "documents": count,
        "schema_validity": valid / count,
        "header_accuracy": header_correct / header_total if header_total else 0.0,
        "line_accuracy": line_correct / line_total if line_total else 0.0,
        "header_fields": {name: header_hits[name] / count for name in HEADER_NAMES},
        "line_fields": {
            name: line_hits[name] / line_denominators[name] if line_denominators[name] else 0.0
            for name in LINE_NAMES
        },
        "warning_recall": warning_found / warning_total if warning_total else 1.0,
        "warning_labels": warning_total,
        "cost_usd_per_document": float(cost / count),
        "latency_ms_per_document": sum(latencies) / count,
        "latency_ms_p50": ordered_latencies[ceil(0.50 * count) - 1],
        "latency_ms_p95": ordered_latencies[ceil(0.95 * count) - 1],
    }


def gate(metrics: dict[str, Any], baseline: dict[str, Any], *, private: bool = False) -> list[str]:
    failures: list[str] = []
    if metrics["schema_validity"] < baseline["schema_validity"]:
        failures.append("schema validity regressed")
    for name in ("header_accuracy", "line_accuracy"):
        if metrics[name] < baseline[name] - 0.02:
            failures.append(f"{name} fell more than two percentage points")
    if private:
        if metrics["documents"] < 9:
            failures.append("private release set needs nine distinct documents")
        if metrics["header_accuracy"] < 0.90:
            failures.append("private header accuracy below 90%")
        if metrics["line_accuracy"] < 0.85:
            failures.append("private line accuracy below 85%")
    return failures
