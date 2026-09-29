"""Token usage and USD cost calculation from the price table."""

from decimal import ROUND_HALF_UP, Decimal

from pydantic import BaseModel

from contacompa.domain.prices import price_for

_QUANTUM = Decimal("0.000001")
# Approximation: providers that discount cached/reused input tokens are modeled as billing
# those tokens at 10% of the normal input price, regardless of the provider's actual discount.
_CACHE_DISCOUNT = Decimal("0.1")


class Usage(BaseModel):
    """Token counts for a single model call.

    Lives in observability.cost (rather than a provider module) because both the provider
    adapters (T010) and cost calculation need it; T010 imports Usage from this module.
    """

    input_tokens: int
    output_tokens: int
    cached_input_tokens: int = 0


class Cost(BaseModel):
    """Computed USD cost for a single model call."""

    billed_usd: Decimal
    list_usd: Decimal
    free_tier: bool


def _usd(
    usage: Usage,
    input_per_million: Decimal,
    output_per_million: Decimal,
) -> Decimal:
    uncached_input = usage.input_tokens - usage.cached_input_tokens
    raw = (
        uncached_input * input_per_million
        + usage.cached_input_tokens * input_per_million * _CACHE_DISCOUNT
        + usage.output_tokens * output_per_million
    ) / Decimal(1_000_000)
    return raw.quantize(_QUANTUM, rounding=ROUND_HALF_UP)


def cost_for(usage: Usage, provider: str, model: str) -> Cost:
    """Compute billed and list-price USD cost for usage against (provider, model)."""
    price = price_for(provider, model)
    return Cost(
        billed_usd=_usd(usage, price.input_per_million_usd, price.output_per_million_usd),
        list_usd=_usd(usage, price.list_input_per_million_usd, price.list_output_per_million_usd),
        free_tier=price.free_tier,
    )
