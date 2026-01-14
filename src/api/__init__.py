"""
API Module - Polymarket API Interface
=====================================

Provides HTTP client for interacting with Polymarket's public APIs.
"""

from src.api.polymarket_api import (
    # Constants
    GAMMA_API_BASE,
    CLOB_API_BASE,
    
    # Exceptions
    PolymarketAPIError,
    
    # Core functions
    fetch_market,
    fetch_event,
    fetch_order_book,
    resolve_reference,
    extract_slug,
    
    # Execution functions
    calculate_buy_execution,
    calculate_sell_execution,
    get_best_bid,
    
    # Market data
    compute_resolution_days,
    list_outcomes,
    build_market_snapshot,
    fetch_snapshot_for_outcome,
    get_outcome_descriptor,
    
    # Data classes
    OutcomeDescriptor,
    MarketSnapshot,
)

__all__ = [
    "GAMMA_API_BASE",
    "CLOB_API_BASE",
    "PolymarketAPIError",
    "fetch_market",
    "fetch_event",
    "fetch_order_book",
    "resolve_reference",
    "extract_slug",
    "calculate_buy_execution",
    "calculate_sell_execution",
    "get_best_bid",
    "compute_resolution_days",
    "list_outcomes",
    "build_market_snapshot",
    "fetch_snapshot_for_outcome",
    "get_outcome_descriptor",
    "OutcomeDescriptor",
    "MarketSnapshot",
]
