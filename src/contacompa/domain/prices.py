"""Price table for model providers, keyed by (provider, model).

Values below are placeholders seeded from public pricing pages at authoring time and must be
verified against each provider's current pricing page before being relied on for billing.
"""

from decimal import Decimal
from typing import NamedTuple


class Price(NamedTuple):
    """Per-provider, per-model USD pricing, per million tokens."""

    input_per_million_usd: Decimal  # billed price
    output_per_million_usd: Decimal
    list_input_per_million_usd: Decimal  # what it would cost on a paid tier (for reporting)
    list_output_per_million_usd: Decimal
    free_tier: bool


PRICES: dict[tuple[str, str], Price] = {
    ("gemini", "gemini-3.5-flash-lite"): Price(
        input_per_million_usd=Decimal("0"),
        output_per_million_usd=Decimal("0"),
        list_input_per_million_usd=Decimal("0.30"),
        list_output_per_million_usd=Decimal("2.50"),
        free_tier=True,
    ),
    ("gemini", "gemini-3.8-flash"): Price(
        input_per_million_usd=Decimal("0"),
        output_per_million_usd=Decimal("0"),
        list_input_per_million_usd=Decimal("0.75"),
        list_output_per_million_usd=Decimal("3.75"),
        free_tier=True,
    ),
    ("gemini", "gemini-2.5-flash"): Price(
        input_per_million_usd=Decimal("0"),
        output_per_million_usd=Decimal("0"),
        list_input_per_million_usd=Decimal("0.30"),
        list_output_per_million_usd=Decimal("2.50"),
        free_tier=True,
    ),
    ("openrouter", "deepseek/deepseek-chat-v3.1:free"): Price(
        input_per_million_usd=Decimal("0"),
        output_per_million_usd=Decimal("0"),
        list_input_per_million_usd=Decimal("0.20"),
        list_output_per_million_usd=Decimal("0.80"),
        free_tier=True,
    ),
    ("groq", "openai/gpt-oss-120b"): Price(
        input_per_million_usd=Decimal("0"),
        output_per_million_usd=Decimal("0"),
        list_input_per_million_usd=Decimal("0.15"),
        list_output_per_million_usd=Decimal("0.60"),
        free_tier=True,
    ),
    ("anthropic", "claude-opus-5"): Price(
        input_per_million_usd=Decimal("5.00"),
        output_per_million_usd=Decimal("25.00"),
        list_input_per_million_usd=Decimal("5.00"),
        list_output_per_million_usd=Decimal("25.00"),
        free_tier=False,
    ),
    ("anthropic", "claude-sonnet-5"): Price(
        input_per_million_usd=Decimal("2.00"),
        output_per_million_usd=Decimal("10.00"),
        list_input_per_million_usd=Decimal("2.00"),
        list_output_per_million_usd=Decimal("10.00"),
        free_tier=False,
    ),
    ("anthropic", "claude-haiku-4-5"): Price(
        input_per_million_usd=Decimal("1.00"),
        output_per_million_usd=Decimal("5.00"),
        list_input_per_million_usd=Decimal("1.00"),
        list_output_per_million_usd=Decimal("5.00"),
        free_tier=False,
    ),
    ("openai", "gpt-5-mini"): Price(
        input_per_million_usd=Decimal("0.25"),
        output_per_million_usd=Decimal("2.00"),
        list_input_per_million_usd=Decimal("0.25"),
        list_output_per_million_usd=Decimal("2.00"),
        free_tier=False,
    ),
}


def price_for(provider: str, model: str) -> Price:
    """Look up the price entry for (provider, model).

    Raises KeyError naming both provider and model when no entry exists.
    """
    try:
        return PRICES[(provider, model)]
    except KeyError as exc:
        raise KeyError(f"No price entry for provider={provider!r} model={model!r}") from exc
