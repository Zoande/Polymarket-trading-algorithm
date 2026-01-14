"""Helpers for interacting with the Polymarket public APIs.

This module centralizes HTTP calls so both the CLI and GUI layers can
consume a single interface when resolving markets and events.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Literal, Tuple
from urllib.parse import urlparse

import requests
from requests import HTTPError


GAMMA_API_BASE = "https://gamma-api.polymarket.com"
CLOB_API_BASE = "https://clob.polymarket.com"


class PolymarketAPIError(RuntimeError):
    """Raised for recoverable Polymarket API failures."""


def _request_json(url: str, params: Dict | None = None) -> Dict:
    response = requests.get(url, params=params, timeout=10)
    response.raise_for_status()
    return response.json()


def extract_slug(value: str) -> str:
    """Extract the trailing slug from a URL or return the raw identifier."""
    parsed = urlparse(value)
    if parsed.scheme and parsed.netloc:
        segments = [segment for segment in parsed.path.split("/") if segment]
        if not segments:
            raise ValueError("Unable to derive identifier from URL.")
        return segments[-1]

    cleaned = value.strip()
    if not cleaned:
        raise ValueError("Identifier cannot be blank.")
    return cleaned


def _safe_json_list(value) -> List:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Unable to parse list from '{value}'") from exc
    return []


def fetch_market(slug: str) -> Dict:
    """Fetch raw metadata for a single market slug."""
    if slug.isdigit():
        return _request_json(f"{GAMMA_API_BASE}/markets/{slug}")

    candidates = _request_json(f"{GAMMA_API_BASE}/markets", params={"slug": slug})
    if isinstance(candidates, list) and candidates:
        return candidates[0]

    return _request_json(f"{GAMMA_API_BASE}/markets/{slug}")


def fetch_event(slug: str) -> Dict:
    """Fetch event metadata including any attached markets."""
    candidates = _request_json(f"{GAMMA_API_BASE}/events", params={"slug": slug})
    if isinstance(candidates, list) and candidates:
        return candidates[0]
    return _request_json(f"{GAMMA_API_BASE}/events/{slug}")


def resolve_reference(identifier: str) -> Tuple[Literal["market", "event"], Dict]:
    """Return the resolved type and metadata for the provided identifier."""
    try:
        metadata = fetch_market(identifier)
    except HTTPError:
        try:
            event = fetch_event(identifier)
        except HTTPError as error:
            raise PolymarketAPIError(f"Identifier '{identifier}' not found.") from error
        return "event", event
    return "market", metadata


def fetch_order_book(token_id: str) -> Dict[str, List[Tuple[float, float]]]:
    """Return the current order book (asks and bids) for the provided token id."""
    data = _request_json(f"{CLOB_API_BASE}/book", params={"token_id": token_id})
    asks = sorted(
        [(float(level["price"]), float(level["size"])) for level in data.get("asks", [])],
        key=lambda item: item[0],
    )
    bids = sorted(
        [(float(level["price"]), float(level["size"])) for level in data.get("bids", [])],
        key=lambda item: item[0],
        reverse=True,
    )
    return {"asks": asks, "bids": bids}


def calculate_buy_execution(order_book: Dict, dollar_amount: float) -> Dict:
    """
    Calculate realistic execution for a BUY order by walking the ask side.
    
    Returns:
        {
            'can_fill': bool,           # Whether the order can be filled
            'avg_price': float,         # Volume-weighted average fill price
            'total_shares': float,      # Total shares that would be bought
            'total_cost': float,        # Actual cost including slippage
            'slippage_pct': float,      # Percentage slippage from best ask
            'best_ask': float,          # Best available ask price
            'worst_price': float,       # Worst price level hit
            'levels_used': int,         # Number of price levels consumed
            'available_liquidity': float,  # Total $ available on ask side
        }
    """
    asks = order_book.get("asks", [])
    
    if not asks:
        return {
            'can_fill': False,
            'avg_price': 0,
            'total_shares': 0,
            'total_cost': 0,
            'slippage_pct': 0,
            'best_ask': 0,
            'worst_price': 0,
            'levels_used': 0,
            'available_liquidity': 0,
        }
    
    best_ask = asks[0][0]
    total_shares = 0
    total_cost = 0
    remaining_dollars = dollar_amount
    worst_price = best_ask
    levels_used = 0
    
    # Calculate total available liquidity
    available_liquidity = sum(price * size for price, size in asks)
    
    # Walk through ask levels
    for price, size in asks:
        if remaining_dollars <= 0:
            break
        
        # How many shares can we buy at this level?
        level_value = price * size  # Total $ available at this level
        
        if level_value >= remaining_dollars:
            # This level can fill the rest of our order
            shares_at_level = remaining_dollars / price
            total_shares += shares_at_level
            total_cost += remaining_dollars
            worst_price = price
            levels_used += 1
            remaining_dollars = 0
        else:
            # Take all shares at this level, continue to next
            total_shares += size
            total_cost += level_value
            remaining_dollars -= level_value
            worst_price = price
            levels_used += 1
    
    can_fill = remaining_dollars <= 0.01  # Allow tiny rounding errors
    avg_price = total_cost / total_shares if total_shares > 0 else 0
    slippage_pct = ((avg_price - best_ask) / best_ask * 100) if best_ask > 0 else 0
    
    return {
        'can_fill': can_fill,
        'avg_price': avg_price,
        'total_shares': total_shares,
        'total_cost': total_cost,
        'slippage_pct': slippage_pct,
        'best_ask': best_ask,
        'worst_price': worst_price,
        'levels_used': levels_used,
        'available_liquidity': available_liquidity,
    }


def calculate_sell_execution(order_book: Dict, shares_to_sell: float) -> Dict:
    """
    Calculate realistic execution for a SELL order by walking the bid side.
    
    Returns:
        {
            'can_fill': bool,           # Whether the order can be filled
            'avg_price': float,         # Volume-weighted average fill price
            'total_shares': float,      # Total shares that would be sold
            'total_proceeds': float,    # Actual proceeds from sale
            'slippage_pct': float,      # Percentage slippage from best bid (negative = worse)
            'best_bid': float,          # Best available bid price
            'worst_price': float,       # Worst price level hit
            'levels_used': int,         # Number of price levels consumed
            'available_liquidity': float,  # Total shares available on bid side
        }
    """
    bids = order_book.get("bids", [])
    
    if not bids:
        return {
            'can_fill': False,
            'avg_price': 0,
            'total_shares': 0,
            'total_proceeds': 0,
            'slippage_pct': 0,
            'best_bid': 0,
            'worst_price': 0,
            'levels_used': 0,
            'available_liquidity': 0,
        }
    
    best_bid = bids[0][0]
    total_shares = 0
    total_proceeds = 0
    remaining_shares = shares_to_sell
    worst_price = best_bid
    levels_used = 0
    
    # Calculate total available liquidity (shares)
    available_liquidity = sum(size for _, size in bids)
    
    # Walk through bid levels (highest to lowest)
    for price, size in bids:
        if remaining_shares <= 0:
            break
        
        if size >= remaining_shares:
            # This level can buy all our remaining shares
            total_shares += remaining_shares
            total_proceeds += remaining_shares * price
            worst_price = price
            levels_used += 1
            remaining_shares = 0
        else:
            # Sell all shares at this level, continue to next
            total_shares += size
            total_proceeds += size * price
            remaining_shares -= size
            worst_price = price
            levels_used += 1
    
    can_fill = remaining_shares <= 0.01  # Allow tiny rounding errors
    avg_price = total_proceeds / total_shares if total_shares > 0 else 0
    slippage_pct = ((best_bid - avg_price) / best_bid * 100) if best_bid > 0 else 0
    
    return {
        'can_fill': can_fill,
        'avg_price': avg_price,
        'total_shares': total_shares,
        'total_proceeds': total_proceeds,
        'slippage_pct': slippage_pct,
        'best_bid': best_bid,
        'worst_price': worst_price,
        'levels_used': levels_used,
        'available_liquidity': available_liquidity,
    }


def get_best_bid(token_id: str) -> float:
    """Get the best bid price for a token (for realistic exit pricing)."""
    try:
        book = fetch_order_book(token_id)
        bids = book.get("bids", [])
        if bids:
            return bids[0][0]  # Best bid (highest)
    except Exception:
        pass
    return 0.0


def compute_resolution_days(end_date_iso: str) -> float:
    if not end_date_iso:
        raise ValueError("Market metadata is missing endDate.")
    end_dt = datetime.fromisoformat(end_date_iso.replace("Z", "+00:00"))
    delta_days = (end_dt - datetime.now(timezone.utc)).total_seconds() / 86400.0
    if delta_days <= 0:
        raise ValueError("Market end date is not in the future.")
    return delta_days


@dataclass
class OutcomeDescriptor:
    name: str
    token_id: str
    last_price: float | None


def list_outcomes(metadata: Dict) -> List[OutcomeDescriptor]:
    """Return structured outcome descriptors for a market."""
    outcomes = _safe_json_list(metadata.get("outcomes"))
    token_ids = _safe_json_list(metadata.get("clobTokenIds"))
    prices = _safe_json_list(metadata.get("outcomePrices"))

    if not outcomes or not token_ids or len(outcomes) != len(token_ids):
        raise ValueError("Market metadata missing outcome/token information.")

    descriptors: List[OutcomeDescriptor] = []
    for index, name in enumerate(outcomes):
        try:
            price = float(prices[index]) if index < len(prices) else None
        except (TypeError, ValueError):
            price = None
        descriptors.append(
            OutcomeDescriptor(
                name=name,
                token_id=str(token_ids[index]),
                last_price=price,
            )
        )
    return descriptors


@dataclass
class MarketSnapshot:
    market_id: str
    question: str
    outcome: str
    outcome_token: str
    order_book: Dict[str, List[Tuple[float, float]]]
    resolution_days: float
    resolution_datetime: datetime
    raw_metadata: Dict


def build_market_snapshot(metadata: Dict, outcome: OutcomeDescriptor) -> MarketSnapshot:
    order_book = fetch_order_book(outcome.token_id)
    if not order_book:
        raise PolymarketAPIError(
            f"No ask-side liquidity for outcome '{outcome.name}' in market '{metadata.get('slug', '')}'."
        )

    end_date_raw = metadata.get("endDate")
    resolution_days = compute_resolution_days(end_date_raw)
    resolution_dt = datetime.fromisoformat(end_date_raw.replace("Z", "+00:00"))

    return MarketSnapshot(
        market_id=metadata.get("slug") or str(metadata.get("id")),
        question=metadata.get("question") or metadata.get("title") or metadata.get("slug"),
        outcome=outcome.name,
        outcome_token=outcome.token_id,
        order_book=order_book,
        resolution_days=resolution_days,
        resolution_datetime=resolution_dt,
        raw_metadata=metadata,
    )


def fetch_snapshot_for_outcome(market_slug: str, outcome_name: str) -> MarketSnapshot:
    metadata = fetch_market(market_slug)
    descriptor = get_outcome_descriptor(metadata, outcome_name)
    return build_market_snapshot(metadata, descriptor)


def get_outcome_descriptor(metadata: Dict, outcome_name: str) -> OutcomeDescriptor:
    descriptors = list_outcomes(metadata)
    for descriptor in descriptors:
        if descriptor.name.lower() == outcome_name.lower():
            return descriptor
    raise PolymarketAPIError(
        f"Outcome '{outcome_name}' not found for market '{metadata.get('slug', '')}'."
    )
