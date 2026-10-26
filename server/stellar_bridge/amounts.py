"""Conversions between human amounts and Stellar's 7-decimal token units."""
from decimal import ROUND_DOWN, Decimal

DECIMALS = 7
UNIT = 10**DECIMALS


def to_units(amount) -> int:
    return int((Decimal(str(amount)) * UNIT).to_integral_value(rounding=ROUND_DOWN))


def from_units(units: int) -> Decimal:
    return (Decimal(int(units)) / UNIT).quantize(Decimal("0.0000001"))


def display(amount, places: int = 2) -> str:
    quantum = Decimal(1).scaleb(-places)
    return str(Decimal(str(amount)).quantize(quantum))
