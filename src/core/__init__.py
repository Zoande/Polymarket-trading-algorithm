"""
Core Module - Trading Engine and Algorithms
============================================

Contains the main trading logic:
- AutoTradingBot: Main automated trading bot
- AllocationEngine: Capital rotation and allocation
- PaperTrader: Paper trading simulation
- Optimizer: Portfolio optimization algorithms
"""

from src.core.auto_trader import (
    AutoTradingBot,
    BotConfig,
    BotDecision,
    BotTrade,
    MarketOpportunity,
    MARKET_CATEGORY_KEYWORDS,
)

from src.core.engine import (
    AllocationEngine,
    CandidateOpportunity,
    EngineResult,
    compute_g,
    compute_fill_from_asks,
    compute_fill_from_bids,
    evaluate_market_candidate,
)

from src.core.paper_trader import (
    PaperTrader,
    PaperPortfolio,
    PaperPosition,
    PaperTrade,
    TradeAction,
)

from src.core.optimizer_core import (
    EXCHANGE_FEE,
    roi_from_price,
    VirtualMarketLevel,
    expand_virtual_markets,
    allocate_budget_greedy,
)

__all__ = [
    # Auto trader
    "AutoTradingBot",
    "BotConfig",
    "BotDecision",
    "BotTrade",
    "MarketOpportunity",
    "MARKET_CATEGORY_KEYWORDS",
    
    # Engine
    "AllocationEngine",
    "CandidateOpportunity",
    "EngineResult",
    "compute_g",
    "compute_fill_from_asks",
    "compute_fill_from_bids",
    "evaluate_market_candidate",
    
    # Paper trader
    "PaperTrader",
    "PaperPortfolio",
    "PaperPosition",
    "PaperTrade",
    "TradeAction",
    
    # Optimizer
    "EXCHANGE_FEE",
    "roi_from_price",
    "VirtualMarketLevel",
    "expand_virtual_markets",
    "allocate_budget_greedy",
]
