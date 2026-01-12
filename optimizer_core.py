"""Optimization primitives used by the Polymarket simulator."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Tuple


EXCHANGE_FEE = 0.02  # 2% settlement fee.


def roi_from_price(price: float) -> float:
    """Return ROI per cycle (post-fee) for a given yes-price."""
    return (1.0 - EXCHANGE_FEE) * ((1.0 - price) / price)


@dataclass
class VirtualMarketLevel:
    market_id: str
    outcome: str
    ask_price: float
    available_shares: float
    resolution_days: float

    @property
    def max_investment(self) -> float:
        return self.ask_price * self.available_shares

    @property
    def roi_per_cycle(self) -> float:
        return roi_from_price(self.ask_price)

    @property
    def daily_profit_rate(self) -> float:
        if self.resolution_days <= 0:
            raise ValueError("resolution_days must be positive.")
        return self.roi_per_cycle / self.resolution_days


def expand_virtual_markets(
    markets: Iterable[Dict],
    sample_budget: float,
) -> List[VirtualMarketLevel]:
    virtual_levels: List[VirtualMarketLevel] = []

    for market in markets:
        market_id = market["market_id"]
        outcome = market.get("outcome", "Yes")
        resolution_days = float(market["resolution_days"])
        remaining_budget = float(sample_budget)

        ask_levels = sorted(
            ((float(price), float(size)) for price, size in market["order_book"]),
            key=lambda level: level[0],
        )

        for ask_price, available_shares in ask_levels:
            if remaining_budget <= 0:
                break

            level_cost = ask_price * available_shares
            if level_cost <= remaining_budget:
                sampled_shares = available_shares
            else:
                sampled_shares = remaining_budget / ask_price

            if sampled_shares <= 0:
                continue

            virtual_levels.append(
                VirtualMarketLevel(
                    market_id=market_id,
                    outcome=outcome,
                    ask_price=ask_price,
                    available_shares=sampled_shares,
                    resolution_days=resolution_days,
                )
            )

            remaining_budget -= ask_price * sampled_shares

    return virtual_levels


def allocate_budget_greedy(
    virtual_markets: Iterable[VirtualMarketLevel],
    total_budget: float,
) -> Tuple[List[Dict[str, float]], Dict[str, float]]:
    sorted_levels = sorted(
        virtual_markets,
        key=lambda level: level.daily_profit_rate,
        reverse=True,
    )

    remaining_budget = float(total_budget)
    allocations: List[Dict[str, float]] = []
    total_invested = 0.0
    total_expected_profit = 0.0
    total_daily_profit = 0.0

    for level in sorted_levels:
        if remaining_budget <= 0:
            break

        invest_amount = min(remaining_budget, level.max_investment)
        if invest_amount <= 0:
            continue

        expected_profit = invest_amount * level.roi_per_cycle
        daily_profit = invest_amount * level.daily_profit_rate

        allocations.append(
            {
                "market_id": level.market_id,
                "outcome": level.outcome,
                "ask_price": level.ask_price,
                "resolution_days": level.resolution_days,
                "invested_amount": invest_amount,
                "expected_profit_on_resolution": expected_profit,
                "roi_per_cycle": level.roi_per_cycle,
                "daily_profit_rate": level.daily_profit_rate,
                "daily_profit_usd": daily_profit,
            }
        )

        remaining_budget -= invest_amount
        total_invested += invest_amount
        total_expected_profit += expected_profit
        total_daily_profit += daily_profit

    if total_invested > 0:
        average_daily_profit_rate = total_daily_profit / total_invested
        annualized_equivalent_roi = (1.0 + average_daily_profit_rate) ** 365 - 1.0
    else:
        average_daily_profit_rate = 0.0
        annualized_equivalent_roi = 0.0

    summary = {
        "total_invested": total_invested,
        "expected_total_profit": total_expected_profit,
        "average_daily_profit_rate": average_daily_profit_rate,
        "annualized_equivalent_roi": annualized_equivalent_roi,
        "unallocated_budget": max(remaining_budget, 0.0),
        "total_daily_profit": total_daily_profit,
    }

    return allocations, summary
