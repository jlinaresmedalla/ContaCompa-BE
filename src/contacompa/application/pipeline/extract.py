"""parse → prompt → provider → validate → normalize → verify → outcome.

Nothing here touches the database or the blob store; the worker and the eval runner own I/O."""

from typing import Any

from pydantic import BaseModel, ValidationError

from contacompa.application.pipeline.prompts import load_prompt
from contacompa.application.pipeline.verify import quote_on_page, verified_confidence
from contacompa.domain.config import RunConfig
from contacompa.domain.fields import ExtractedField, FieldKind
from contacompa.domain.normalize import normalize
from contacompa.domain.purchase_doc import parse_doc_type
from contacompa.domain.schemas import SchemaSpec, get_schema
from contacompa.infrastructure.observability.cost import Cost, Usage, cost_for
from contacompa.infrastructure.observability.logging import get_logger
from contacompa.infrastructure.observability.tracing import get_tracer
from contacompa.infrastructure.parsing import get_parser
from contacompa.infrastructure.parsing.base import PDF_MIME, ParsedDoc
from contacompa.infrastructure.providers.base import InvalidOutputError, Provider

_log = get_logger(__name__)
_tracer = get_tracer(__name__)


class MissingField(BaseModel):
    field: str
    reason: str


class ExtractionOutcome(BaseModel):
    schema_type: str
    schema_version: str
    doc_type: str
    fields: dict[str, Any]
    missing: list[MissingField]
    usage: Usage
    cost: Cost
    latency_ms: int
    page_count: int | None
    scanned: bool
    parser: str
    prompt_version: str


SCHEMA_NAME = "purchase_doc"


async def extract(
    data: bytes, cfg: RunConfig, provider: Provider, mime_type: str = PDF_MIME
) -> ExtractionOutcome:
    spec = get_schema(SCHEMA_NAME)
    prompt = load_prompt(spec.name, cfg.prompt_version)
    with _tracer.start_as_current_span("pipeline.extract") as span:
        span.set_attribute("doc.mime_type", mime_type)
        span.set_attribute("run.config", cfg.key())
        document = await get_parser(cfg.parser).parse(data, mime_type)
        if document.scanned and cfg.parser != "native":
            _log.info("pipeline.scanned_fallback", parser=cfg.parser)
        result = await provider.extract(document, spec.model, prompt, cfg)
        try:
            validated = spec.model.model_validate(result.model_output)
        except ValidationError as exc:
            raise InvalidOutputError(
                f"model output failed schema validation: {exc.error_count()} errors"
            ) from exc
        fields, missing = _render_fields(validated, spec, document)
        outcome = ExtractionOutcome(
            schema_type=spec.name,
            schema_version=spec.version,
            doc_type=parse_doc_type(getattr(validated, "doc_type", None)).value,
            fields=fields,
            missing=missing,
            usage=result.usage,
            cost=cost_for(result.usage, cfg.provider, cfg.model),
            latency_ms=result.latency_ms,
            page_count=document.page_count,
            scanned=document.scanned,
            parser=document.parser,
            prompt_version=prompt.version,
        )
        span.set_attribute("doc.type", outcome.doc_type)
        span.set_attribute("doc.missing_count", len(missing))
        return outcome


def _render_fields(
    validated: BaseModel, spec: SchemaSpec, document: ParsedDoc
) -> tuple[dict[str, Any], list[MissingField]]:
    fields: dict[str, Any] = {}
    missing: list[MissingField] = []
    for name, value in validated:
        if isinstance(value, ExtractedField):
            kind = spec.kinds.get(name, FieldKind.text)
            fields[name] = _render_one(name, value, kind, spec, document, missing)
        elif isinstance(value, list):
            rows = []
            for index, item in enumerate(value):
                if not isinstance(item, BaseModel):
                    continue
                row: dict[str, Any] = {}
                for sub_name, sub_value in item:
                    if isinstance(sub_value, ExtractedField):
                        kind = spec.kinds.get(f"{name}.{sub_name}", FieldKind.text)
                        path = f"{name}[{index}].{sub_name}"
                        row[sub_name] = _render_one(path, sub_value, kind, spec, document, missing)
                rows.append(row)
            fields[name] = rows
    return fields, missing


def _render_one(
    path: str,
    field: ExtractedField,
    kind: FieldKind,
    spec: SchemaSpec,
    document: ParsedDoc,
    missing: list[MissingField],
) -> dict[str, Any]:
    if field.value is None and kind is FieldKind.unit:
        # No printed unit means one unit (FR-017); that is a default, not a missing value.
        return {"value": "unit", "raw": None, "confidence": 1.0, "source": None, "verified": None}
    if field.value is None:
        missing.append(MissingField(field=path, reason="not_found"))
        return {"value": None, "raw": None, "confidence": 0.0, "source": None, "verified": None}
    normalized = normalize(kind, field.value, spec.decimal_separator)
    if normalized is None and kind is not FieldKind.text:
        missing.append(MissingField(field=path, reason=f"unparseable_{kind.value}"))
    verified: bool | None = None
    if field.source is not None and field.source.quote:
        verified = quote_on_page(document, field.source.page, field.source.quote)
    return {
        "value": normalized if normalized is not None else field.value,
        "raw": field.value,
        "confidence": verified_confidence(field.confidence, verified),
        "source": field.source.model_dump() if field.source else None,
        "verified": verified,
    }
