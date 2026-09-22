"""Kalshi fee model.

Trading fee (per Kalshi's published schedule):

    fee = ceil_to_cent( 0.07 * C * P * (1 - P) )

where C = contract count and P = price in dollars. Maker fees are 25% of the
taker fee. The ceiling is applied per order, not per contract.

The useful identity for this research: expressed as a fraction of the capital
actually at risk (which is P per contract, since a binary contract costs P and
pays 1), the per-contract fee is

    fee / P = 0.07 * (1 - P)

i.e. ~6.9% of stake at 1c, ~5.6% at 20c, ~3.5% at 50c. Fee drag as a share of
stake is near its maximum exactly where the cheap-longshot edge is claimed to
live. Positions are held to resolution, so this is charged one way only --
settlement itself is free.
"""

from __future__ import annotations

import math

TAKER_RATE = 0.07
MAKER_MULTIPLIER = 0.25


def fee_per_contract(price: float, maker: bool = False) -> float:
    """Unrounded fee in dollars for one contract bought at `price` (dollars)."""
    fee = TAKER_RATE * price * (1.0 - price)
    return fee * MAKER_MULTIPLIER if maker else fee


def fee_fraction_of_stake(price: float, maker: bool = False) -> float:
    """Fee as a fraction of capital at risk. Closed form: 0.07 * (1 - price)."""
    if price <= 0:
        raise ValueError("price must be positive")
    return fee_per_contract(price, maker) / price


def order_fee(price: float, contracts: int, maker: bool = False) -> float:
    """Total fee in dollars for one order, including Kalshi's per-order ceiling."""
    raw = fee_per_contract(price, maker) * contracts
    return math.ceil(raw * 100.0) / 100.0


def breakeven_win_rate(price: float, maker: bool = False) -> float:
    """Resolution rate a position at `price` must achieve to break even net of fees."""
    return price + fee_per_contract(price, maker)


def net_ev_cents(price_cents: int, win_rate: float, maker: bool = False) -> float:
    """Net expected value in cents per contract, after the trading fee."""
    price = price_cents / 100.0
    gross = 100.0 * win_rate - price_cents
    return gross - fee_per_contract(price, maker) * 100.0


def net_return_pct(price_cents: int, win_rate: float, maker: bool = False) -> float:
    """Net EV as a percentage of capital at risk."""
    return 100.0 * net_ev_cents(price_cents, win_rate, maker) / price_cents
