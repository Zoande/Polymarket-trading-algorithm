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
