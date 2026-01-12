"""Runtime state persistence for the Polymarket capital rotation simulator."""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from polymarket_api import MarketSnapshot
from math import log1p as math_log1p


def parse_volume(metadata: Dict) -> Optional[float]:
    volume = metadata.get("volumeNum") or metadata.get("volume")
    try:
        if volume is None:
            return None
        return float(volume)
    except (TypeError, ValueError):
        return None


def extract_parent_event(metadata: Dict) -> (str, str):
    events = metadata.get("events")
    if isinstance(events, list) and events:
        event = events[0]
        event_id = str(
            event.get("id") or event.get("slug") or metadata.get("conditionId") or metadata.get("marketId") or ""
        )
        event_label = event.get("title") or event.get("slug") or metadata.get("groupItemTitle") or ""
        return event_id, event_label
    condition = metadata.get("conditionId") or metadata.get("questionID") or metadata.get("marketId")
    return str(condition or ""), metadata.get("question") or metadata.get("title") or ""

RUNTIME_SCHEMA_VERSION = "1"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().isoformat(timespec="seconds").replace("+00:00", "Z")


def _floor_month(date: datetime) -> str:
    return date.strftime("%Y-%m")


def _parse_iso(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


@dataclass
class PriceSample:
    timestamp: str
    best_ask: Optional[float]
    best_bid: Optional[float]
    volume: Optional[float]


@dataclass
class TradeLogEntry:
    timestamp: str
    mode: str
    action: str  # BUY | SELL
    market_id: str
    question: str
    outcome: str
    shares: float
    price: float
    value: float
    g_before: Optional[float]
    g_after: Optional[float]
    slippage_bps: Optional[float]
    reasons: List[str] = field(default_factory=list)
    metadata: Dict = field(default_factory=dict)


@dataclass
class FreezeStatus:
    reason: str
    activated_at: str
    until: str
    details: Dict = field(default_factory=dict)

    def is_active(self, now: datetime) -> bool:
        return now < _parse_iso(self.until)


@dataclass
class MarketState:
    market_id: str
    outcome: str
    question: str
    parent_event_id: str
    parent_event_label: str
    resolution_datetime: str
    resolution_days: float
    metadata: Dict
    best_ask: Optional[float] = None
    best_bid: Optional[float] = None
    last_price: Optional[float] = None
    last_volume: Optional[float] = None
    order_book: Dict[str, List[List[float]]] = field(default_factory=lambda: {"asks": [], "bids": []})
    price_history: List[PriceSample] = field(default_factory=list)
    held_shares: float = 0.0
    average_price: Optional[float] = None
    realized_profit: float = 0.0
    last_fetch_ts: Optional[str] = None
    next_buy_time: Optional[str] = None

    def key(self) -> str:
        return f"{self.market_id}|{self.outcome}"

    def resolution_month(self) -> str:
        return _floor_month(_parse_iso(self.resolution_datetime))

    def market_value(self) -> float:
        if self.held_shares <= 0:
            return 0.0
        benchmark = self.best_bid or self.last_price or self.average_price or 0.0
        return self.held_shares * benchmark

    def invested_amount(self) -> float:
        if self.held_shares <= 0 or self.average_price is None:
            return 0.0
        return self.held_shares * self.average_price

    def g_for_price(self, price: float, lambda_days: float) -> Optional[float]:
        if price is None or price <= 0 or self.resolution_days <= 0:
            return None
        r = 0.98 * ((1.0 - price) / price)
        denom = self.resolution_days + lambda_days
        if denom <= 0:
            return None
        return float(math_log1p(r) / denom)

    def g_held(self, lambda_days: float) -> Optional[float]:
        if self.held_shares <= 0 or not self.average_price:
            return None
        return self.g_for_price(self.average_price, lambda_days)

    def update_from_snapshot(
        self,
        snapshot: MarketSnapshot,
        last_price: Optional[float],
        best_bid: Optional[float],
        best_ask: Optional[float],
        volume: Optional[float],
    ) -> None:
        self.question = snapshot.question
        self.resolution_datetime = snapshot.resolution_datetime.isoformat()
        self.resolution_days = snapshot.resolution_days
        self.metadata = snapshot.raw_metadata
        self.order_book = {
            "asks": [[price, size] for price, size in snapshot.order_book.get("asks", [])],
            "bids": [[price, size] for price, size in snapshot.order_book.get("bids", [])],
        }
        self.best_ask = best_ask
        self.best_bid = best_bid
        self.last_price = last_price
        self.last_volume = volume
        self.last_fetch_ts = _now_iso()
        self.price_history.append(
            PriceSample(timestamp=self.last_fetch_ts, best_ask=best_ask, best_bid=best_bid, volume=volume)
        )
        # keep limited history
        if len(self.price_history) > 240:
            self.price_history = self.price_history[-240:]

    def buy(self, shares: float, price: float) -> None:
        if shares <= 0:
            return
        total_cost = shares * price
        if self.held_shares <= 0 or not self.average_price:
            self.average_price = price
        else:
            current_cost = self.average_price * self.held_shares
            self.average_price = (current_cost + total_cost) / (self.held_shares + shares)
        self.held_shares += shares

    def sell(self, shares: float, price: float) -> float:
        shares = min(shares, self.held_shares)
        if shares <= 0:
            return 0.0
        proceeds = shares * price
        cost_basis = shares * (self.average_price or 0.0)
        self.held_shares -= shares
        if self.held_shares <= 1e-9:
            self.held_shares = 0.0
            self.average_price = None
        self.realized_profit += proceeds - cost_basis
        return proceeds


@dataclass
class DecisionRecord:
    timestamp: str
    buys: List[Dict]
    sells: List[Dict]
    rejections: List[Dict]
    opportunities: List[Dict]


@dataclass
class RuntimeState:
    schema_version: str = RUNTIME_SCHEMA_VERSION
    total_budget: float = 0.0
    cash_balance: float = 0.0
    markets: Dict[str, MarketState] = field(default_factory=dict)
    strategy_priority: List[str] = field(default_factory=list)
    trade_log: List[TradeLogEntry] = field(default_factory=list)
    active_freezes: Dict[str, FreezeStatus] = field(default_factory=dict)
    last_decision: Optional[DecisionRecord] = None
    mode: str = "live"
    filepath: Optional[Path] = None

    # --- Persistence -------------------------------------------------

    def to_dict(self) -> Dict:
        return {
            "schema_version": self.schema_version,
            "total_budget": self.total_budget,
            "cash_balance": self.cash_balance,
            "markets": {key: self._market_to_dict(mkt) for key, mkt in self.markets.items()},
            "strategy_priority": self.strategy_priority,
            "trade_log": [asdict(entry) for entry in self.trade_log],
            "active_freezes": {
                key: {"reason": freeze.reason, "activated_at": freeze.activated_at, "until": freeze.until, "details": freeze.details}
                for key, freeze in self.active_freezes.items()
            },
            "last_decision": asdict(self.last_decision) if self.last_decision else None,
            "mode": self.mode,
        }

    @staticmethod
    def from_dict(data: Dict, filepath: Optional[Path] = None) -> "RuntimeState":
        state = RuntimeState(
            schema_version=data.get("schema_version", RUNTIME_SCHEMA_VERSION),
            total_budget=data.get("total_budget", 0.0),
            cash_balance=data.get("cash_balance", 0.0),
            markets={key: RuntimeState._market_from_dict(value) for key, value in data.get("markets", {}).items()},
            strategy_priority=data.get("strategy_priority", []),
            trade_log=[TradeLogEntry(**entry) for entry in data.get("trade_log", [])],
            active_freezes={
                key: FreezeStatus(
                    reason=value["reason"],
                    activated_at=value["activated_at"],
                    until=value["until"],
                    details=value.get("details", {}),
                )
                for key, value in data.get("active_freezes", {}).items()
            },
            last_decision=DecisionRecord(**data["last_decision"]) if data.get("last_decision") else None,
            mode=data.get("mode", "live"),
            filepath=filepath,
        )
        if state.mode != "live":
            state.mode = "live"
        state._ensure_priority_consistency()
        return state

    def save(self, filepath: Optional[Path] = None) -> None:
        target = filepath or self.filepath
        if not target:
            raise ValueError("No filepath supplied for saving runtime state.")
        target = Path(target)
        target.write_text(json.dumps(self.to_dict(), indent=2))
        self.filepath = target

    @staticmethod
    def load(filepath: Path) -> "RuntimeState":
        if not filepath.exists():
            raise FileNotFoundError(filepath)
        data = json.loads(filepath.read_text())
        return RuntimeState.from_dict(data, filepath=filepath)

    # --- Market management -------------------------------------------

    def add_market(self, market: MarketState) -> None:
        key = market.key()
        if key in self.markets:
            raise ValueError(f"Market {key} is already tracked.")
        self.markets[key] = market
        if key not in self.strategy_priority:
            self.strategy_priority.append(key)

    def remove_market(self, key: str) -> None:
        market = self.markets.get(key)
        if not market:
            return
        if market.held_shares > 0:
            raise ValueError("Cannot remove market with active position; sell first.")
        self.markets.pop(key)
        if key in self.strategy_priority:
            self.strategy_priority.remove(key)
        if key in self.active_freezes:
            self.active_freezes.pop(key)

    def market(self, key: str) -> Optional[MarketState]:
        return self.markets.get(key)

    def list_markets(self) -> List[MarketState]:
        return list(self.markets.values())

    def engaged_markets(self) -> List[MarketState]:
        return [mkt for mkt in self.markets.values() if mkt.held_shares > 0]

    # --- Trade log ---------------------------------------------------

    def append_trade(self, entry: TradeLogEntry) -> None:
        self.trade_log.append(entry)
        if len(self.trade_log) > 5000:
            self.trade_log = self.trade_log[-5000:]

    # --- Freeze management ------------------------------------------

    def set_freeze(self, key: str, freeze: FreezeStatus) -> None:
        self.active_freezes[key] = freeze

    def clear_freeze(self, key: str) -> None:
        self.active_freezes.pop(key, None)

    def get_freeze(self, key: str) -> Optional[FreezeStatus]:
        freeze = self.active_freezes.get(key)
        if not freeze:
            return None
        if not freeze.is_active(_now()):
            self.clear_freeze(key)
            return None
        return freeze

    # --- Helpers -----------------------------------------------------

    def max_data_age_seconds(self) -> float:
        if not self.markets:
            return 0.0
        ages = []
        now = _now()
        for market in self.markets.values():
            if market.last_fetch_ts:
                ages.append((now - _parse_iso(market.last_fetch_ts)).total_seconds())
        return max(ages) if ages else float("inf")

    def exposures_by_event(self) -> Dict[str, float]:
        exposures: Dict[str, float] = {}
        for market in self.markets.values():
            exposures.setdefault(market.parent_event_id, 0.0)
            exposures[market.parent_event_id] += market.market_value()
        return exposures

    def exposures_by_month(self) -> Dict[str, float]:
        exposures: Dict[str, float] = {}
        for market in self.markets.values():
            exposures.setdefault(market.resolution_month(), 0.0)
            exposures[market.resolution_month()] += market.market_value()
        return exposures

    def ensure_cash(self) -> None:
        invested = sum(market.invested_amount() for market in self.markets.values())
        if self.total_budget <= 0:
            self.total_budget = invested + self.cash_balance
        if self.cash_balance <= 0 and self.total_budget > invested:
            self.cash_balance = self.total_budget - invested

    # --- Internal ----------------------------------------------------

    def _ensure_priority_consistency(self) -> None:
        keys = set(self.markets.keys())
        self.strategy_priority = [key for key in self.strategy_priority if key in keys]
        for key in self.markets.keys():
            if key not in self.strategy_priority:
                self.strategy_priority.append(key)

    @staticmethod
    def _market_to_dict(market: MarketState) -> Dict:
        return {
            "market_id": market.market_id,
            "outcome": market.outcome,
            "question": market.question,
            "parent_event_id": market.parent_event_id,
            "parent_event_label": market.parent_event_label,
            "resolution_datetime": market.resolution_datetime,
            "resolution_days": market.resolution_days,
            "metadata": market.metadata,
            "best_ask": market.best_ask,
            "best_bid": market.best_bid,
            "last_price": market.last_price,
            "last_volume": market.last_volume,
            "order_book": market.order_book,
            "price_history": [asdict(sample) for sample in market.price_history],
            "held_shares": market.held_shares,
            "average_price": market.average_price,
            "realized_profit": market.realized_profit,
            "last_fetch_ts": market.last_fetch_ts,
            "next_buy_time": market.next_buy_time,
        }

    @staticmethod
    def _market_from_dict(data: Dict) -> MarketState:
        market = MarketState(
            market_id=data["market_id"],
            outcome=data["outcome"],
            question=data.get("question", ""),
            parent_event_id=data.get("parent_event_id", data["market_id"]),
            parent_event_label=data.get("parent_event_label", ""),
            resolution_datetime=data.get("resolution_datetime", _now_iso()),
            resolution_days=data.get("resolution_days", 0.0),
            metadata=data.get("metadata", {}),
            best_ask=data.get("best_ask"),
            best_bid=data.get("best_bid"),
            last_price=data.get("last_price"),
            last_volume=data.get("last_volume"),
            order_book=data.get("order_book", {"asks": [], "bids": []}),
            held_shares=data.get("held_shares", 0.0),
            average_price=data.get("average_price"),
            realized_profit=data.get("realized_profit", 0.0),
            last_fetch_ts=data.get("last_fetch_ts"),
            next_buy_time=data.get("next_buy_time"),
        )
        market.price_history = [
            PriceSample(
                timestamp=sample["timestamp"],
                best_ask=sample.get("best_ask"),
                best_bid=sample.get("best_bid"),
                volume=sample.get("volume"),
            )
            for sample in data.get("price_history", [])
        ]
        return market


def ensure_runtime_state(path: Path, total_budget: float) -> RuntimeState:
    if path.exists():
        state = RuntimeState.load(path)
        state.ensure_cash()
        if state.mode != "live":
            state.mode = "live"
            state.save(path)
        return state
    state = RuntimeState(total_budget=total_budget, cash_balance=total_budget, mode="live")
    state.filepath = path
    state.save(path)
    return state
