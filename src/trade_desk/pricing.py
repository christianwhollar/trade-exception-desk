"""Dated fixed-coupon cash flows under explicitly stated conventions.

Regular coupon dates are rolled from maturity with end-of-month preservation. Discount
times use ACT/365F with nominal periodic compounding. Coupon accrual is ACT/ACT within
the current coupon interval. This does not implement market calendars or irregular stubs.
"""

import calendar
from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class FixedBond(BaseModel):
    settlement: date
    maturity: date
    coupon_rate: float = Field(ge=0, le=1, allow_inf_nan=False)
    yield_rate: float = Field(gt=-0.5, le=2, allow_inf_nan=False)
    frequency: Literal[1, 2, 4] = 2
    face: float = Field(default=100, gt=0, le=1e9, allow_inf_nan=False)

    @model_validator(mode="after")
    def valid_dates(self):
        if not 0 < (self.maturity - self.settlement).days <= 36525:
            raise ValueError("Maturity must follow settlement by no more than 100 years")
        return self


def shift_months(value, months):
    target = value.year * 12 + value.month - 1 + months
    year, month = divmod(target, 12)
    last_day = calendar.monthrange(year, month + 1)[1]
    eom = value.day == calendar.monthrange(value.year, value.month)[1]
    return date(year, month + 1, last_day if eom else min(value.day, last_day))


def cashflows(bond):
    dates = []
    periods = 0
    while True:
        day = shift_months(bond.maturity, -periods * (12 // bond.frequency))
        if day <= bond.settlement:
            previous = day
            break
        dates.append(day)
        periods += 1
    dates.reverse()
    coupon = bond.face * bond.coupon_rate / bond.frequency
    accrued = coupon * (bond.settlement - previous).days / (dates[0] - previous).days
    return [
        {
            "date": day.isoformat(),
            "time_years": (day - bond.settlement).days / 365,
            "coupon": coupon,
            "principal": bond.face if day == bond.maturity else 0,
        }
        for day in dates
    ], accrued


def dirty_value(flows, yield_rate, frequency):
    return sum(
        (row["coupon"] + row["principal"])
        / (1 + yield_rate / frequency) ** (row["time_years"] * frequency)
        for row in flows
    )


def price(bond):
    flows, accrued = cashflows(bond)
    dirty = dirty_value(flows, bond.yield_rate, bond.frequency)
    down = dirty_value(flows, bond.yield_rate - 0.0001, bond.frequency)
    up = dirty_value(flows, bond.yield_rate + 0.0001, bond.frequency)
    for flow in flows:
        flow["present_value"] = (flow["coupon"] + flow["principal"]) / (
            1 + bond.yield_rate / bond.frequency
        ) ** (flow["time_years"] * bond.frequency)
    return {
        "dirty_price": dirty,
        "clean_price": dirty - accrued,
        "accrued_interest": accrued,
        "dv01": (down - up) / 2,
        "modified_duration": (down - up) / (2 * 0.0001 * dirty),
        "convexity": (up + down - 2 * dirty) / (dirty * 0.0001**2),
        "cashflows": flows,
        "shocks": [
            {
                "basis_points": bp,
                "clean_price": dirty_value(flows, bond.yield_rate + bp / 10000, bond.frequency)
                - accrued,
            }
            for bp in [-200, -100, -50, 0, 50, 100, 200]
        ],
        "conventions": "Regular coupons, maturity-anchored EOM schedule; ACT/365F discount; nominal periodic yield; ACT/ACT coupon accrual; no calendar adjustment or irregular stubs.",
    }


def implied_yield(bond, clean_price):
    flows, accrued = cashflows(bond)
    low, high = -0.49, 2.0
    target = clean_price + accrued
    if (
        not dirty_value(flows, high, bond.frequency)
        <= target
        <= dirty_value(flows, low, bond.frequency)
    ):
        raise ValueError("Price is outside supported yield bounds")
    for _ in range(100):
        mid = (low + high) / 2
        if dirty_value(flows, mid, bond.frequency) > target:
            low = mid
        else:
            high = mid
    return (low + high) / 2
