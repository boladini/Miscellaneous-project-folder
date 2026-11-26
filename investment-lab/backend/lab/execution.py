"""Execution model shared by the backtester and the forward paper simulator.

Daily-bar mode: signals use finalized bars through session T; orders fill at the recorded open of
the next eligible session T+1 with adverse slippage. Stops are evaluated on closes and therefore
fill at the following open — a gap can produce a much worse price than the stop threshold.
"""
from __future__ import annotations

from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal

CENT6 = Decimal("0.000001")
BPS = Decimal(10000)


def D(x) -> Decimal:
    return x if isinstance(x, Decimal) else Decimal(str(x))


def q6(x: Decimal) -> Decimal:
    return x.quantize(CENT6, rounding=ROUND_HALF_UP)


def fill_price(open_price: Decimal, side: str, slippage_bps: Decimal) -> Decimal:
    open_price, slippage_bps = D(open_price), D(slippage_bps)
    if side == "buy":
        return q6(open_price * (1 + slippage_bps / BPS))
    return q6(open_price * (1 - slippage_bps / BPS))


def whole_shares(notional: Decimal, est_price: Decimal, fractional: bool = False) -> Decimal:
    if est_price <= 0 or notional <= 0:
        return Decimal(0)
    raw = D(notional) / D(est_price)
    if fractional:
        return raw.quantize(Decimal("0.0001"), rounding=ROUND_DOWN)
    return raw.to_integral_value(rounding=ROUND_DOWN)


def reservation_for(qty: Decimal, ref_price: Decimal, slippage_bps: Decimal, buffer_bps: Decimal = Decimal(300),
                    commission: Decimal = Decimal(0)) -> Decimal:
    """Cash reserved for a pending buy: reference close plus slippage plus a gap buffer.

    If the next open gaps up beyond the buffer the fill is re-sized down (never into negative cash).
    """
    return q6(D(qty) * D(ref_price) * (1 + (D(slippage_bps) + buffer_bps) / BPS) + D(commission))
