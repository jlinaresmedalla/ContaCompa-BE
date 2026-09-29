from pydantic import BaseModel

from contacompa.domain.fields import FieldKind
from contacompa.domain.schemas.purchase_doc import (
    PURCHASE_DOC_KINDS,
    PurchaseDoc,
    PurchaseDocLine,
)

SCHEMA_VERSION = "1"


class SchemaSpec(BaseModel):
    name: str
    version: str
    model: type[BaseModel]
    kinds: dict[str, FieldKind]
    decimal_separator: str | None = None


SCHEMAS: dict[str, SchemaSpec] = {
    "purchase_doc": SchemaSpec(
        name="purchase_doc",
        version=SCHEMA_VERSION,
        model=PurchaseDoc,
        kinds=PURCHASE_DOC_KINDS,
        decimal_separator=".",  # Peruvian documents print 1,234.56 and 30.000
    ),
}


def get_schema(name: str) -> SchemaSpec:
    try:
        return SCHEMAS[name]
    except KeyError as exc:
        raise KeyError(f"unknown schema '{name}'") from exc


__all__ = [
    "SCHEMAS",
    "SCHEMA_VERSION",
    "PurchaseDoc",
    "PurchaseDocLine",
    "SchemaSpec",
    "get_schema",
]
