"""Unit tests for USD cost calculation from the price table."""

from decimal import Decimal

import pytest

from contacompa.infrastructure.observability.cost import Usage, cost_for


def test_free_tier_model_is_billed_zero_but_reports_list_price() -> None:
    """Gemini flash (free tier): billed is 0, list price reports what it would have cost."""
    usage = Usage(input_tokens=1_000_000, output_tokens=100_000)

    cost = cost_for(usage, "gemini", "gemini-2.5-flash")

    assert cost.billed_usd == Decimal("0.000000")
    assert cost.list_usd == Decimal("0.550000")
    assert cost.free_tier is True


def test_paid_model_billed_price() -> None:
    """Claude Opus 5: 1,000 input + 500 output tokens billed at list price (not free tier)."""
    usage = Usage(input_tokens=1_000, output_tokens=500)

    cost = cost_for(usage, "anthropic", "claude-opus-5")

    assert cost.billed_usd == Decimal("0.017500")
    assert cost.list_usd == Decimal("0.017500")
    assert cost.free_tier is False


def test_cached_input_tokens_billed_at_ten_percent() -> None:
    """cached_input_tokens are a subset of input_tokens, billed at 10% of input price."""
    # 800 tokens at full input price + 200 cached tokens at 10% of input price, no output.
    usage = Usage(input_tokens=1_000, output_tokens=0, cached_input_tokens=200)

    cost = cost_for(usage, "anthropic", "claude-opus-5")

    # (800 * 5.00 + 200 * 5.00 * 0.1) / 1_000_000 = (4000 + 100) / 1_000_000
    assert cost.billed_usd == Decimal("0.004100")


def test_unknown_model_raises_key_error_naming_provider_and_model() -> None:
    """An unknown (provider, model) pair raises KeyError mentioning both."""
    usage = Usage(input_tokens=1, output_tokens=1)

    with pytest.raises(KeyError) as exc_info:
        cost_for(usage, "acme", "made-up-model")

    message = str(exc_info.value)
    assert "acme" in message
    assert "made-up-model" in message


def test_billed_usd_is_quantized_to_six_decimal_places() -> None:
    """Cost values are always quantized to exactly 6 decimal places."""
    usage = Usage(input_tokens=1, output_tokens=1)

    cost = cost_for(usage, "openai", "gpt-5-mini")

    assert cost.billed_usd.as_tuple().exponent == -6
    assert cost.list_usd.as_tuple().exponent == -6
