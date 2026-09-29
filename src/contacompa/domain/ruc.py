"""Peru's RUC: 11 digits; the last one is a modulo-11 check digit over the first ten."""

_WEIGHTS = (5, 4, 3, 2, 7, 6, 5, 4, 3, 2)
_PREFIXES = ("10", "15", "16", "17", "20")


def check_digit(first_ten: str) -> int:
    total = sum(int(d) * w for d, w in zip(first_ten, _WEIGHTS, strict=True))
    digit = 11 - total % 11
    return {10: 0, 11: 1}.get(digit, digit)


def is_valid_ruc(ruc: str) -> bool:
    """True for 11 digits with a known prefix and a matching check digit."""
    if len(ruc) != 11 or not ruc.isdigit() or not ruc.startswith(_PREFIXES):
        return False
    return check_digit(ruc[:10]) == int(ruc[10])
