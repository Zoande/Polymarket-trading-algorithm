"""Capital rotation engine implementing Polymarket allocation logic."""

from __future__ import annotations

from dataclasses import dataclass, field
from math import log1p
from typing import Dict, List, Optional, Tuple

from config_manager import GlobalPolicy, MarketPolicy, SimulatorConfig
from runtime_state import MarketState, RuntimeState, TradeLogEntry, DecisionRecord, _now_iso


@dataclass
class CandidateOpportunity:
    market_key: str
    market_id: str
    outcome: str
    question: str
    best_ask: Optional[float]
    resolution_days: float
    g: Optional[float]
    capacity_value: float
    slippage_bps: Optional[float]
    status: str
    reasons: List[str] = field(default_factory=list)
    confidence: float = 1.0
    rank: Optional[int] = None


@dataclass
class EngineResult:
    timestamp: str
    buys: List[Dict] = field(default_factory=list)
    sells: List[Dict] = field(default_factory=list)
    rejections: List[Dict] = field(default_factory=list)
    opportunities: List[CandidateOpportunity] = field(default_factory=list)


def _ln1p(value: float) -> float:
    return log1p(value)


def compute_g(price: Optional[float], resolution_days: float, lambda_days: float) -> Optional[float]:
    if price is None or price <= 0 or resolution_days + lambda_days <= 0:
        return None
    r = 0.98 * ((1.0 - price) / price)
    return _ln1p(r) / (resolution_days + lambda_days)


def compute_fill_from_asks(
    asks: List[List[float]],
    max_value: float,
) -> Tuple[float, float, float]:
    """Return (shares, avg_price, slippage_bps)."""
    if not asks or max_value <= 0:
        return 0.0, 0.0, 0.0
    best_price = asks[0][0]
    total_cost = 0.0
    total_shares = 0.0
    remaining_value = max_value
    for price, size in asks:
        if price <= 0 or size <= 0:
            continue
        level_value = price * size
        take_value = min(level_value, remaining_value)
        if take_value <= 0:
            break
        shares = take_value / price
        total_cost += price * shares
        total_shares += shares
        remaining_value -= take_value
        if remaining_value <= 1e-9:
            break
    if total_shares <= 0:
        return 0.0, 0.0, 0.0
    avg_price = total_cost / total_shares
    slippage_bps = ((avg_price / best_price) - 1.0) * 1e4 if best_price else 0.0
    return total_shares, avg_price, slippage_bps


def compute_fill_from_bids(
    bids: List[List[float]],
    shares_to_sell: float,
) -> Tuple[float, float, float]:
    if not bids or shares_to_sell <= 0:
        return 0.0, 0.0, 0.0
    best_price = bids[0][0]
    total_proceeds = 0.0
    total_shares = 0.0
    remaining = shares_to_sell
    for price, size in bids:
        if price <= 0 or size <= 0:
            continue
        sell_shares = min(size, remaining)
        total_proceeds += price * sell_shares
        total_shares += sell_shares
        remaining -= sell_shares
        if remaining <= 1e-9:
            break
    if total_shares <= 0:
        return 0.0, 0.0, 0.0
    avg_price = total_proceeds / total_shares
    slippage_bps = ((best_price - avg_price) / best_price) * 1e4 if best_price else 0.0
    return total_shares, avg_price, slippage_bps


def evaluate_market_candidate(
    market: MarketState,
    policy: MarketPolicy,
    global_policy: GlobalPolicy,
) -> CandidateOpportunity:
    best_ask = market.best_ask
    g = compute_g(best_ask, market.resolution_days, global_policy.settlement_lambda_days)
    reasons: List[str] = []
    status = "eligible"
    if not policy.enabled:
        status = "blocked"
        reasons.append("disabled")
    elif best_ask is None:
        status = "blocked"
        reasons.append("missing_best_ask")
    elif best_ask < policy.min_price or best_ask > policy.max_price:
        status = "blocked"
        reasons.append("price_bounds")
    elif market.resolution_days < policy.min_days or market.resolution_days > policy.max_days:
        status = "blocked"
        reasons.append("day_bounds")
    if status == "eligible":
        min_g_threshold = max(policy.min_g, global_policy.min_g)
        if g is None or g < min_g_threshold:
            status = "blocked"
            reasons.append("min_g")
    capacity_value = best_ask * market.order_book.get("asks", [[0, 0]])[0][1] if best_ask else 0.0
    return CandidateOpportunity(
        market_key=market.key(),
        market_id=market.market_id,
        outcome=market.outcome,
        question=market.question,
        best_ask=best_ask,
        resolution_days=market.resolution_days,
        g=g,
        capacity_value=capacity_value,
        slippage_bps=None,
        status=status,
        reasons=reasons,
    )


class AllocationEngine:
    def __init__(self, config: SimulatorConfig) -> None:
        self.config = config

    # ------------------------------------------------------------------
    def evaluate(self, state: RuntimeState) -> EngineResult:
        now_iso = _now_iso()
        result = EngineResult(timestamp=now_iso)
        for market in state.list_markets():
            policy = self.config.get_market_policy(market.market_id)
            opportunity = evaluate_market_candidate(market, policy, self.config.global_policy)
            result.opportunities.append(opportunity)
            if opportunity.status != "eligible":
                result.rejections.append(
                    {
                        "type": "skip",
                        "market_id": opportunity.market_id,
                        "question": opportunity.question,
                        "outcome": opportunity.outcome,
                        "side": opportunity.outcome,
                        "reasons": opportunity.reasons,
                        "g": opportunity.g,
                        "details": {
                            "min_price": policy.min_price,
                            "max_price": policy.max_price,
                            "min_days": policy.min_days,
                            "max_days": policy.max_days,
                            "min_g": max(policy.min_g, self.config.global_policy.min_g),
                            "best_ask": opportunity.best_ask,
                            "resolution_days": market.resolution_days,
                        },
                    }
                )
        result.opportunities.sort(key=lambda item: (item.status != "eligible", -(item.g or -1)))
        rank = 1
        for opportunity in result.opportunities:
            if opportunity.status == "eligible":
                opportunity.rank = rank
                rank += 1
        return result

    # ------------------------------------------------------------------
    def execute(self, state: RuntimeState, mode: Optional[str] = None) -> EngineResult:
        evaluation = self.evaluate(state)
        exec_state = state
        global_policy = self.config.global_policy
        mode = mode or state.mode
        now_iso = _now_iso()

        cash = exec_state.cash_balance
        total_budget = exec_state.total_budget
        if total_budget <= 0:
            total_budget = cash + sum(market.invested_amount() for market in exec_state.list_markets())
            exec_state.total_budget = total_budget
        cash_reserve_value = total_budget * global_policy.cash_reserve_pct
        exposures_event = exec_state.exposures_by_event()
        exposures_month = exec_state.exposures_by_month()

        holdings: List[Dict] = []
        for held_market in exec_state.engaged_markets():
            policy = self.config.get_market_policy(held_market.market_id)
            if not policy.auto_sell:
                continue
            g_held = held_market.g_held(global_policy.settlement_lambda_days)
            holdings.append(
                {
                    "market": held_market,
                    "policy": policy,
                    "g": g_held if g_held is not None else -1e9,
                    "priority": policy.priority,
                }
            )
        holdings.sort(key=lambda item: (item["g"], -item["priority"]))

        result = EngineResult(timestamp=now_iso, opportunities=evaluation.opportunities)
        delta_threshold = global_policy.delta_threshold

        for opportunity in evaluation.opportunities:
            if opportunity.status != "eligible":
                continue
            if opportunity.g is None:
                continue

            market = exec_state.market(opportunity.market_key)
            if not market:
                continue
            policy = self.config.get_market_policy(market.market_id)

            def append_skip(reason_codes: List[str], details: Dict) -> None:
                result.rejections.append(
                    {
                        "type": "skip",
                        "market_id": market.market_id,
                        "question": market.question,
                        "outcome": market.outcome,
                        "side": market.outcome,
                        "rank": opportunity.rank,
                        "reasons": reason_codes,
                        "g": opportunity.g,
                        "details": details,
                    }
                )

            g_held = market.g_held(global_policy.settlement_lambda_days)
            required_g = max(global_policy.min_g, policy.min_g, (g_held or 0.0) + delta_threshold)
            if opportunity.g < required_g:
                append_skip(
                    ["delta_threshold"],
                    {
                        "g_required": required_g,
                        "g_current": g_held,
                        "delta_threshold": delta_threshold,
                    },
                )
                continue

            if not policy.auto_buy:
                append_skip(["auto_buy_disabled"], {})
                continue

            freeze = exec_state.get_freeze(market.key())
            if freeze and not policy.whitelist_autobuy:
                append_skip(
                    [f"freeze:{freeze.reason}"],
                    {
                        "freeze_until": freeze.until,
                        "freeze_reason": freeze.reason,
                    },
                )
                continue

            asks = market.order_book.get("asks")
            if not asks:
                append_skip(["no_liquidity"], {})
                continue

            max_value = policy.per_pass_buy_cap if policy.per_pass_buy_cap > 0 else float("inf")
            current_invested = market.invested_amount()
            max_notional = policy.max_notional
            max_allocation_pct = policy.max_allocation_pct if policy.max_allocation_pct is not None else 1.0
            max_market_value = total_budget * max_allocation_pct
            remaining_notional = max(0.0, min(max_notional, max_market_value) - current_invested)
            target_value = min(max_value, remaining_notional)
            if target_value <= 1e-6:
                append_skip(
                    ["allocation_cap"],
                    {
                        "max_notional": max_notional,
                        "max_allocation_pct": max_allocation_pct,
                        "already_invested": current_invested,
                    },
                )
                continue

            parent_cap_pct = policy.max_per_event_pct or global_policy.max_parent_allocation_pct
            month_cap_pct = policy.max_per_month_pct or global_policy.max_month_allocation_pct
            parent_total = exposures_event.get(market.parent_event_id, 0.0)
            month_total = exposures_month.get(market.resolution_month(), 0.0)
            max_parent_value = total_budget * parent_cap_pct
            max_month_value = total_budget * month_cap_pct
            remaining_parent = max(0.0, max_parent_value - parent_total)
            remaining_month = max(0.0, max_month_value - month_total)
            target_value = min(target_value, remaining_parent, remaining_month)
            if target_value <= 1e-6:
                append_skip(
                    ["portfolio_cap"],
                    {
                        "parent_cap_pct": parent_cap_pct,
                        "month_cap_pct": month_cap_pct,
                        "parent_usage": parent_total,
                        "month_usage": month_total,
                    },
                )
                continue

            shares, avg_price, slippage_bps = compute_fill_from_asks(asks, target_value)
            if shares <= 1e-9:
                continue

            slippage_cap = policy.effective_slippage_cap(global_policy)
            if slippage_bps > slippage_cap:
                append_skip(
                    ["slippage_cap"],
                    {
                        "slippage_bps": slippage_bps,
                        "slippage_cap_bps": slippage_cap,
                    },
                )
                continue

            cost = avg_price * shares
            needed_cash = cost - max(0.0, cash - cash_reserve_value) if cash - cost < cash_reserve_value else 0.0

            sells_performed: List[Dict] = []
            if needed_cash > 1e-6:
                needed_cash_remaining = needed_cash
                candidates = [item for item in holdings if item["market"].key() != market.key()]
                for record in candidates:
                    candidate_market: MarketState = record["market"]
                    candidate_policy: MarketPolicy = record["policy"]
                    g_candidate = candidate_market.g_held(global_policy.settlement_lambda_days)
                    if g_candidate is None:
                        continue
                    if opportunity.g < g_candidate + delta_threshold:
                        continue
                    bids = candidate_market.order_book.get("bids", [])
                    prior_shares = candidate_market.held_shares
                    prior_avg_price = candidate_market.average_price or 0.0
                    sell_shares, sell_price, sell_slippage_bps = self._simulate_sell(
                        bids, needed_cash_remaining, candidate_market, global_policy, candidate_policy
                    )
                    if sell_shares <= 0:
                        continue
                    proceeds = candidate_market.sell(sell_shares, sell_price)
                    cash += proceeds
                    needed_cash_remaining = max(0.0, cost - max(0.0, cash - cash_reserve_value))
                    exposures_event[candidate_market.parent_event_id] = max(
                        0.0,
                        exposures_event.get(candidate_market.parent_event_id, 0.0) - sell_price * sell_shares,
                    )
                    exposures_month[candidate_market.resolution_month()] = max(
                        0.0,
                        exposures_month.get(candidate_market.resolution_month(), 0.0) - sell_price * sell_shares,
                    )
                    cost_basis = sell_shares * prior_avg_price
                    profit_usd = proceeds - cost_basis
                    profit_pct = profit_usd / cost_basis if cost_basis > 1e-9 else 0.0
                    remaining_value = candidate_market.market_value()
                    sell_record = {
                        "type": "sell",
                        "market_id": candidate_market.market_id,
                        "question": candidate_market.question,
                        "outcome": candidate_market.outcome,
                        "side": candidate_market.outcome,
                        "shares": sell_shares,
                        "price": sell_price,
                        "value": proceeds,
                        "slippage_bps": sell_slippage_bps,
                        "exit_slippage_cap_bps": candidate_policy.effective_exit_slippage_cap(global_policy),
                        "g_before": g_candidate,
                        "target_market_id": opportunity.market_id,
                        "target_question": opportunity.question,
                        "target_outcome": opportunity.outcome,
                        "target_side": opportunity.outcome,
                        "target_g": opportunity.g,
                        "delta_threshold": delta_threshold,
                        "reason": "rotation",
                        "profit_usd": profit_usd,
                        "profit_pct": profit_pct,
                        "cost_basis": cost_basis,
                        "remaining_shares": candidate_market.held_shares,
                        "remaining_value": remaining_value,
                        "remaining_avg_price": candidate_market.average_price,
                        "rank": opportunity.rank,
                    }
                    sells_performed.append(sell_record)
                    result.sells.append(sell_record)
                    exec_state.append_trade(
                        TradeLogEntry(
                            timestamp=now_iso,
                            mode=mode,
                            action="SELL",
                            market_id=candidate_market.market_id,
                            question=candidate_market.question,
                            outcome=candidate_market.outcome,
                            shares=sell_shares,
                            price=sell_price,
                            value=proceeds,
                            g_before=g_candidate,
                            g_after=candidate_market.g_held(global_policy.settlement_lambda_days),
                            slippage_bps=sell_slippage_bps,
                            reasons=["rotation"],
                            metadata={
                                "target_market": opportunity.market_id,
                                "needed_cash_remaining": needed_cash_remaining,
                            },
                        )
                    )
                    updated_g = candidate_market.g_held(global_policy.settlement_lambda_days)
                    record["g"] = updated_g if updated_g is not None else -1e9
                    holdings.sort(key=lambda item: (item["g"], -item["priority"]))
                    if needed_cash_remaining <= 1e-6:
                        break
                if needed_cash_remaining > 1e-6:
                    append_skip(
                        ["insufficient_cash"],
                        {
                            "needed_cash": needed_cash_remaining,
                            "cash_available": cash,
                            "cash_reserve": cash_reserve_value,
                        },
                    )
                    continue

            opportunity.slippage_bps = slippage_bps
            cash -= cost
            prior_shares = market.held_shares
            prior_avg_price = market.average_price or 0.0
            market.buy(shares, avg_price)
            exposures_event[market.parent_event_id] = exposures_event.get(market.parent_event_id, 0.0) + cost
            exposures_month[market.resolution_month()] = exposures_month.get(market.resolution_month(), 0.0) + cost
            g_after = market.g_held(global_policy.settlement_lambda_days)
            roi = 0.98 * ((1.0 - avg_price) / avg_price) if avg_price > 0 else 0.0
            expected_profit = cost * roi
            delta_g = opportunity.g - (g_held or 0.0)
            buy_type = "top_up" if prior_shares > 1e-9 else "buy"
            buy_record = {
                "type": buy_type,
                "market_id": market.market_id,
                "question": market.question,
                "outcome": market.outcome,
                "side": market.outcome,
                "shares": shares,
                "price": avg_price,
                "cost": cost,
                "slippage_bps": slippage_bps,
                "slippage_cap_bps": slippage_cap,
                "rank": opportunity.rank,
                "g": opportunity.g,
                "g_before": g_held,
                "g_after": g_after,
                "previous_shares": prior_shares,
                "previous_avg_price": prior_avg_price,
                "new_shares": market.held_shares,
                "new_avg_price": market.average_price,
                "expected_profit": expected_profit,
                "roi": roi,
                "resolution_days": market.resolution_days,
                "delta_g": delta_g,
                "confidence": opportunity.confidence,
            }
            result.buys.append(buy_record)

            exec_state.append_trade(
                TradeLogEntry(
                    timestamp=now_iso,
                    mode=mode,
                    action="BUY",
                    market_id=market.market_id,
                    question=market.question,
                    outcome=market.outcome,
                    shares=shares,
                    price=avg_price,
                    value=cost,
                    g_before=g_held,
                    g_after=g_after,
                    slippage_bps=slippage_bps,
                    reasons=["rotation" if sells_performed else "entry"],
                    metadata={
                        "rank": opportunity.rank,
                        "expected_profit": expected_profit,
                    },
                )
            )

            if policy.auto_sell:
                updated_g = market.g_held(global_policy.settlement_lambda_days)
                holdings_entry = next((item for item in holdings if item["market"].key() == market.key()), None)
                if holdings_entry:
                    holdings_entry["g"] = updated_g if updated_g is not None else -1e9
                else:
                    holdings.append(
                        {
                            "market": market,
                            "policy": policy,
                            "g": updated_g if updated_g is not None else -1e9,
                            "priority": policy.priority,
                        }
                    )
                holdings.sort(key=lambda item: (item["g"], -item["priority"]))

        exec_state.cash_balance = cash
        exec_state.last_decision = DecisionRecord(
            timestamp=now_iso,
            buys=result.buys,
            sells=result.sells,
            rejections=result.rejections,
            opportunities=[asdict_opp(opportunity) for opportunity in result.opportunities],
        )
        return result

    # ------------------------------------------------------------------
    def _simulate_sell(
        self,
        bids: List[List[float]],
        needed_cash: float,
        market: MarketState,
        global_policy: GlobalPolicy,
        policy: MarketPolicy,
    ) -> Tuple[float, float, float]:
        if not bids or needed_cash <= 0:
            return 0.0, 0.0, 0.0
        best_price = bids[0][0] if bids and bids[0] else 0.0
        target_shares = min(market.held_shares, needed_cash / best_price if best_price else market.held_shares)
        shares, avg_price, slippage_bps = compute_fill_from_bids(bids, target_shares)
        if shares <= 0:
            return 0.0, 0.0, 0.0
        slippage_cap = policy.effective_exit_slippage_cap(global_policy)
        if slippage_bps > slippage_cap:
            return 0.0, 0.0, 0.0
        return shares, avg_price, slippage_bps


def asdict_opp(opportunity: CandidateOpportunity) -> Dict:
    return {
        "market_id": opportunity.market_id,
        "outcome": opportunity.outcome,
        "question": opportunity.question,
        "best_ask": opportunity.best_ask,
        "resolution_days": opportunity.resolution_days,
        "g": opportunity.g,
        "capacity_value": opportunity.capacity_value,
        "slippage_bps": opportunity.slippage_bps,
        "status": opportunity.status,
        "reasons": opportunity.reasons,
        "rank": opportunity.rank,
    }
