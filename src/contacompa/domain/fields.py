from enum import StrEnum

from pydantic import BaseModel, Field


class FieldKind(StrEnum):
    text = "text"
    amount = "amount"
    number = "number"
    date = "date"
    datetime = "datetime"
    currency = "currency"
    integer = "integer"
    boolean = "boolean"
    ruc = "ruc"
    unit = "unit"


class Source(BaseModel):
    page: int = Field(ge=1, description="1-based page number where the value was read")
    quote: str | None = Field(default=None, description="Exact text as it appears on the page")


class ExtractedField(BaseModel):
    """One extracted value as the model reports it. Values are strings; normalization is applied
    afterwards according to the schema's FieldKind."""

    value: str | None = Field(default=None, description="Verbatim value, or null if not found")
    confidence: float = Field(ge=0, le=1, description="Model's confidence that the value is right")
    source: Source | None = None
