"""Financial checks are ordinary, deterministic code; language models never set prices."""

from datetime import date
from decimal import Decimal
from pydantic import BaseModel, ConfigDict, Field


class Trade(BaseModel):
    model_config = ConfigDict(extra="forbid")
    trade_id: str = Field(min_length=1, max_length=80)
    instrument: str = Field(min_length=1, max_length=80)
    quantity: Decimal = Field(gt=0, max_digits=16, decimal_places=4)
    price: Decimal = Field(gt=0, max_digits=12, decimal_places=6)
    settlement: date
    currency: str = Field(pattern=r"^[A-Z]{3}$")


class Investigation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    internal: Trade
    counterparty: Trade


def reconcile(case: Investigation) -> dict:
    left, right = case.internal, case.counterparty
    differences = []
    for field in ("trade_id", "instrument", "quantity", "price", "settlement", "currency"):
        if getattr(left, field) != getattr(right, field):
            differences.append(
                {
                    "field": field,
                    "internal": str(getattr(left, field)),
                    "counterparty": str(getattr(right, field)),
                }
            )
    comparable = left.currency == right.currency and left.instrument == right.instrument
    # Prices here are currency per unit, not bond prices per 100 of face value.
    exposure = (left.quantity * left.price - right.quantity * right.price) if comparable else None
    severity = "high" if not comparable or abs(exposure) >= Decimal("10000") else "normal"
    return {
        "differences": differences,
        "cash_difference": str(exposure.quantize(Decimal("0.01")))
        if exposure is not None
        else None,
        "currency": left.currency if comparable else None,
        "severity": severity,
        "recommendation": "review" if differences else "matched",
        "evidence": ["internal", "counterparty"],
    }


def bond_price(coupon_rate: float, yield_rate: float, years: int, face: float = 100.0) -> float:
    """Annual fixed coupons at integer years; no accrued interest or day-count conventions."""
    if not 1 <= years <= 100 or face <= 0 or coupon_rate < 0 or yield_rate <= -1:
        raise ValueError("Invalid annual bond parameters")
    return (
        sum(face * coupon_rate / (1 + yield_rate) ** t for t in range(1, years + 1))
        + face / (1 + yield_rate) ** years
    )


def yield_to_maturity(price: float, coupon_rate: float, years: int) -> float:
    if price <= 0:
        raise ValueError("Price must be positive")
    low, high = -0.9, 10.0
    if not bond_price(coupon_rate, high, years) <= price <= bond_price(coupon_rate, low, years):
        raise ValueError("Yield lies outside solver bounds")
    for _ in range(100):
        mid = (low + high) / 2
        if bond_price(coupon_rate, mid, years) > price:
            low = mid
        else:
            high = mid
    return (low + high) / 2
