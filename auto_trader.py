"""Auto-trading bot that scans markets and makes trading decisions."""

from __future__ import annotations

import json
import random
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple
from enum import Enum
from math import log1p

import requests

from polymarket_api import (
    GAMMA_API_BASE,
    CLOB_API_BASE,
    fetch_order_book,
    compute_resolution_days,
    calculate_buy_execution,
    calculate_sell_execution,
    get_best_bid,
)

# Import cloud sync for multi-user support
try:
    from cloud_sync import get_cloud_sync, CloudSync
    CLOUD_SYNC_AVAILABLE = True
except ImportError:
    CLOUD_SYNC_AVAILABLE = False

# Import news analyzer for sentiment-based trading
try:
    from news_analyzer import NewsAnalyzer, MarketCategory, MarketSignal, get_market_category_display, CATEGORY_KEYWORDS
    NEWS_ANALYZER_AVAILABLE = True
except ImportError:
    NEWS_ANALYZER_AVAILABLE = False
    MarketCategory = None


class BotDecision(Enum):
    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"
    SKIP = "skip"


@dataclass
class MarketOpportunity:
    """A market opportunity identified by the bot."""
    market_id: str
    slug: str
    question: str
    outcome: str
    token_id: str
    price: float
    resolution_days: float
    end_date: str
    volume: float
    liquidity: float
    g_score: float  # Growth rate metric
    expected_roi: float
    confidence: float
    decision: BotDecision
    reasons: List[str] = field(default_factory=list)
    
    def to_dict(self) -> Dict:
        return {
            "market_id": self.market_id,
            "slug": self.slug,
            "question": self.question,
            "outcome": self.outcome,
            "token_id": self.token_id,
            "price": self.price,
            "resolution_days": self.resolution_days,
            "end_date": self.end_date,
            "volume": self.volume,
            "liquidity": self.liquidity,
            "g_score": self.g_score,
            "expected_roi": self.expected_roi,
            "confidence": self.confidence,
            "decision": self.decision.value,
            "reasons": self.reasons,
        }


@dataclass 
class BotTrade:
    """A trade executed by the bot."""
    id: str
    timestamp: str
    market_id: str
    question: str
    outcome: str
    action: str  # buy/sell
    shares: float
    entry_price: float
    current_price: float
    exit_price: Optional[float] = None
    exit_timestamp: Optional[str] = None
    pnl: float = 0.0
    pnl_pct: float = 0.0
    status: str = "open"  # open, closed, pending
    trade_type: str = "long"  # "swing" or "long"
    volume: float = 0.0  # Market volume when trade was made
    resolution_days: float = 0.0  # Days to resolution when traded
    category: str = "other"  # Market category (politics, crypto, sports, etc.)
    token_id: Optional[str] = None  # CLOB token ID for realistic exit pricing
    
    @property
    def value(self) -> float:
        return self.shares * (self.current_price if self.status == "open" else (self.exit_price or self.entry_price))
    
    @property
    def cost_basis(self) -> float:
        return self.shares * self.entry_price
    
    def to_dict(self) -> Dict:
        return {
            "id": self.id,
            "timestamp": self.timestamp,
            "market_id": self.market_id,
            "question": self.question,
            "outcome": self.outcome,
            "action": self.action,
            "shares": self.shares,
            "entry_price": self.entry_price,
            "current_price": self.current_price,
            "exit_price": self.exit_price,
            "exit_timestamp": self.exit_timestamp,
            "pnl": self.pnl,
            "pnl_pct": self.pnl_pct,
            "status": self.status,
            "trade_type": self.trade_type,
            "volume": self.volume,
            "resolution_days": self.resolution_days,
            "category": self.category,
            "token_id": self.token_id,
        }
    
    @staticmethod
    def from_dict(data: Dict) -> "BotTrade":
        # Handle missing fields for backwards compatibility
        data.setdefault('trade_type', 'long')
        data.setdefault('volume', 0.0)
        data.setdefault('resolution_days', 0.0)
        data.setdefault('category', 'other')
        data.setdefault('token_id', None)  # Old trades won't have this
        return BotTrade(**data)


@dataclass
class BotConfig:
    """Configuration for the auto-trading bot."""
    # Capital
    initial_capital: float = 10000.0
    max_position_size: float = 500.0  # Max per trade
    max_portfolio_pct: float = 0.10  # Max 10% in one market
    
    # Small test trades
    test_trade_size: float = 25.0  # $25 test trades for new opportunities
    test_trade_enabled: bool = True  # Enable small test trades
    
    # Swing trade settings
    swing_trade_enabled: bool = True
    swing_take_profit_pct: float = 0.15  # 15% quick profit for swing
    swing_stop_loss_pct: float = 0.10  # 10% stop loss for swing
    swing_min_volume: float = 50000.0  # Only swing trade on popular markets
    
    # Market filters - LOOSENED for more diversity
    min_price: float = 0.03  # Don't buy below 3 cents
    max_price: float = 0.85  # Don't buy above 85 cents
    min_days: float = 0.05   # Min 0.05 day to resolution (allow quick markets)
    max_days: float = 365.0  # Max 365 days (longer term)
    min_volume: float = 500.0   # Min $500 volume (lowered)
    min_liquidity: float = 200.0  # Min $200 liquidity (lowered)
    prefer_high_volume: bool = False  # DON'T just prioritize popular markets
    high_volume_threshold: float = 50000.0  # $50k+ is "popular" (lowered)
    
    # Strategy - ADJUSTED for better signals
    min_g_score: float = 0.0003  # Lower minimum growth rate
    min_expected_roi: float = 0.03  # Min 3% expected ROI (lowered)
    confidence_threshold: float = 0.45  # Lower for more opportunities
    high_confidence_threshold: float = 0.65  # For full-size trades
    
    # Risk management - Long term
    stop_loss_pct: float = 0.30  # 30% stop loss
    take_profit_pct: float = 0.50  # 50% take profit
    
    # Timing - FASTER scanning
    scan_interval_seconds: int = 10  # 10 second scanning (was 30)
    max_markets_per_scan: int = 200  # Scan more markets
    price_update_interval: int = 5  # Update prices every 5 seconds
    
    # Positions - DIVERSITY focused
    max_positions: int = 60  # Allow up to 60 positions
    max_long_term_positions: int = 40  # Long term (>7 days)
    max_swing_positions: int = 30  # Swing trades (<7 days)
    skip_recently_scanned: bool = False  # Allow re-scanning
    market_cooldown_minutes: int = 3  # Only 3 min cooldown (was 5)
    
    # Category diversity - ensure we don't only buy sports
    category_limits: Dict[str, int] = field(default_factory=lambda: {
        "sports": 10,      # Max 10 sports positions
        "politics": 10,    # Max 10 politics positions
        "crypto": 8,       # Max 8 crypto positions
        "entertainment": 5,
        "finance": 5,
        "technology": 5,
        "world_events": 5,
        "other": 7,
    })
    
    # News-based trading
    use_news_analysis: bool = True  # Enable news sentiment analysis
    news_confidence_boost: float = 0.15  # Boost confidence when news aligns
    
    # Realistic execution simulation
    realistic_execution: bool = True  # Enable realistic order book simulation
    max_slippage_pct: float = 5.0  # Reject trades with > 5% slippage
    min_book_depth_multiplier: float = 1.5  # Need 1.5x liquidity vs order size
    execution_delay_enabled: bool = True  # Simulate price movement during execution
    execution_delay_max_pct: float = 2.0  # Max random price movement (±2%)


# Category detection keywords for market classification
MARKET_CATEGORY_KEYWORDS = {
    "sports": {
        "nba", "nfl", "mlb", "nhl", "soccer", "football", "basketball", "baseball",
        "hockey", "tennis", "golf", "ufc", "boxing", "mma", "championship", "playoffs",
        "finals", "superbowl", "world series", "team", "game", "match", "vs", "bulls",
        "lakers", "celtics", "warriors", "rockets", "thunder", "spurs", "heat", "nets",
        "knicks", "76ers", "bucks", "suns", "clippers", "mavs", "grizzlies", "pelicans",
        "blazers", "jazz", "kings", "pacers", "hawks", "hornets", "pistons", "magic",
        "wizards", "cavaliers", "raptors", "timberwolves", "nuggets", "chiefs", "eagles",
        "cowboys", "patriots", "49ers", "packers", "bills", "ravens", "bengals", "dolphins",
        "jets", "giants", "commanders", "bears", "lions", "vikings", "saints", "falcons",
        "panthers", "buccaneers", "cardinals", "rams", "seahawks", "chargers", "raiders",
        "broncos", "steelers", "browns", "titans", "colts", "texans", "jaguars",
    },
    "politics": {
        "election", "president", "congress", "senate", "governor", "vote", "democrat",
        "republican", "biden", "trump", "political", "politics", "legislation", "bill",
        "law", "policy", "campaign", "poll", "ballot", "primary", "caucus", "electoral",
        "candidate", "administration", "impeach", "veto", "supreme court", "scotus",
        "cabinet", "secretary", "ambassador", "diplomat", "treaty", "tariff",
    },
    "crypto": {
        "bitcoin", "btc", "ethereum", "eth", "crypto", "cryptocurrency", "blockchain",
        "defi", "nft", "token", "coin", "wallet", "exchange", "mining", "halving",
        "altcoin", "solana", "cardano", "dogecoin", "xrp", "ripple", "binance", "coinbase",
    },
    "entertainment": {
        "movie", "film", "oscar", "emmy", "grammy", "music", "album", "celebrity",
        "hollywood", "netflix", "streaming", "tv", "show", "concert", "tour", "box office",
        "premiere", "award", "actor", "actress", "singer", "artist", "youtube", "tiktok",
    },
    "finance": {
        "stock", "market", "nasdaq", "s&p", "dow", "fed", "interest rate", "inflation",
        "gdp", "economy", "economic", "bank", "jerome powell", "earnings", "revenue",
        "profit", "ipo", "merger", "acquisition", "recession", "bull market", "bear market",
    },
    "technology": {
        "tech", "technology", "ai", "artificial intelligence", "openai", "chatgpt",
        "google", "apple", "microsoft", "meta", "amazon", "tesla", "nvidia", "startup",
        "silicon valley", "software", "hardware", "chip", "semiconductor", "robot",
    },
    "world_events": {
        "war", "conflict", "military", "nato", "un", "united nations", "russia", "ukraine",
        "china", "iran", "israel", "middle east", "climate", "environment", "disaster",
        "earthquake", "hurricane", "pandemic", "covid", "virus", "health", "who",
    },
}


class AutoTradingBot:
    """
    Auto-trading bot that:
    1. Scans Polymarket for opportunities
    2. Evaluates markets using growth rate (g) metric
    3. Makes buy/sell decisions
    4. Tracks simulated P&L
    """
    
    EXCHANGE_FEE = 0.02  # 2% fee
    
    def __init__(
        self,
        config: Optional[BotConfig] = None,
        storage_path: Optional[Path] = None,
        on_trade: Optional[Callable[[BotTrade], None]] = None,
        on_opportunity: Optional[Callable[[MarketOpportunity], None]] = None,
        on_message: Optional[Callable[[str, str], None]] = None,
    ):
        self.config = config or BotConfig()
        self.storage_path = storage_path or Path("bot_state.json")
        
        # Callbacks
        self.on_trade = on_trade
        self.on_opportunity = on_opportunity
        self.on_message = on_message  # (message, type)
        
        # State
        self.cash_balance: float = self.config.initial_capital
        self.open_trades: Dict[str, BotTrade] = {}  # trade_id -> trade
        self.closed_trades: List[BotTrade] = []
        self.scanned_markets: Dict[str, MarketOpportunity] = {}  # market_key -> opportunity
        self.blacklist: set = set()  # Markets to skip
        
        # Stats
        self.total_trades: int = 0
        self.winning_trades: int = 0
        self.losing_trades: int = 0
        self.total_pnl: float = 0.0
        
        # Control
        self._running = False
        self._buys_paused = False  # When True, skip new buy trades but allow sells
        self._thread: Optional[threading.Thread] = None
        self._trade_counter = 0
        
        # Market tracking for diversity
        self._scanned_times: Dict[str, datetime] = {}  # market_id -> last scan time
        self._market_categories: Dict[str, str] = {}  # market_id -> category
        self._scan_offset: int = 0  # For pagination
        
        # Trade history log (for UI display)
        self.trade_log: List[Dict] = []  # Recent trades with outcomes
        
        # News analyzer (if available)
        self._news_analyzer: Optional[NewsAnalyzer] = None
        if NEWS_ANALYZER_AVAILABLE and self.config.use_news_analysis:
            self._news_analyzer = NewsAnalyzer(
                cache_duration_minutes=10,
                on_signal=self._on_news_signal,
            )
        
        # Cloud sync for multi-user support
        self._cloud_sync: Optional[CloudSync] = None
        if CLOUD_SYNC_AVAILABLE:
            self._cloud_sync = get_cloud_sync()
            if self._cloud_sync.is_enabled():
                self._log("☁️ Cloud sync enabled", "info")
        
        self._load()
    
    def _now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    
    def _log(self, message: str, msg_type: str = "info") -> None:
        """Log a message and notify listeners."""
        if self.on_message:
            try:
                self.on_message(message, msg_type)
            except Exception:
                pass
    
    def _generate_trade_id(self) -> str:
        self._trade_counter += 1
        return f"bot_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}_{self._trade_counter}"
    
    def _on_news_signal(self, signal: "MarketSignal") -> None:
        """Handle news signal from analyzer."""
        self._log(f"📰 News signal for '{signal.market_question[:40]}...' - {signal.recommendation}", "info")
    
    def _detect_category(self, question: str) -> str:
        """Detect market category from question text."""
        text_lower = question.lower()
        
        category_scores = {}
        for category, keywords in MARKET_CATEGORY_KEYWORDS.items():
            score = sum(1 for kw in keywords if kw in text_lower)
            if score > 0:
                category_scores[category] = score
        
        if category_scores:
            return max(category_scores, key=category_scores.get)
        return "other"
    
    def _get_category_count(self, category: str) -> int:
        """Get count of open positions in a category."""
        return sum(
            1 for t in self.open_trades.values()
            if self._market_categories.get(t.market_id, "other") == category
        )
    
    def _add_to_trade_log(self, action: str, question: str, amount: float, price: float, 
                          pnl: Optional[float] = None, result: Optional[str] = None) -> None:
        """Add entry to trade log for UI display."""
        entry = {
            "timestamp": self._now_iso(),
            "action": action,  # "BUY" or "SELL"
            "question": question[:50],
            "amount": amount,
            "price": price,
            "pnl": pnl,
            "result": result,  # "WIN", "LOSS", or None for buys
        }
        self.trade_log.append(entry)
        
        # Keep only last 100 entries
        if len(self.trade_log) > 100:
            self.trade_log = self.trade_log[-100:]
    
    def get_trade_log(self, limit: int = 20) -> List[Dict]:
        """Get recent trade log entries."""
        return list(reversed(self.trade_log[-limit:]))
    
    # -------------------------------------------------------------------------
    # Market Scanning
    # -------------------------------------------------------------------------
    
    def scan_markets(self) -> List[MarketOpportunity]:
        """Scan Polymarket for trading opportunities."""
        self._log("Scanning new markets for opportunities...", "info")
        
        opportunities = []
        skipped_owned = 0
        now = datetime.now(timezone.utc)
        
        try:
            # Fetch active markets from Polymarket API
            markets = self._fetch_active_markets()
            
            owned_market_ids = {t.market_id for t in self.open_trades.values()}
            
            for market in markets[:self.config.max_markets_per_scan]:
                try:
                    market_id = market.get("slug") or str(market.get("id"))
                    
                    # Skip if we already own this
                    if market_id in owned_market_ids:
                        skipped_owned += 1
                        continue
                    
                    opportunity = self._evaluate_market(market)
                    if opportunity:
                        # Mark as scanned
                        self._scanned_times[opportunity.market_id] = now
                        
                        opportunities.append(opportunity)
                        self.scanned_markets[f"{opportunity.market_id}|{opportunity.outcome}"] = opportunity
                        
                        if self.on_opportunity:
                            self.on_opportunity(opportunity)
                except Exception as e:
                    continue
            
            # Sort by g_score (best opportunities first)
            opportunities.sort(key=lambda x: x.g_score, reverse=True)
            
            # Log summary
            buy_count = sum(1 for o in opportunities if o.decision == BotDecision.BUY)
            self._log(
                f"Analyzed {len(opportunities)} markets | {buy_count} BUY signals | "
                f"Skipped {skipped_owned} owned | {len(self.open_trades)} positions open",
                "success"
            )
            
            # Clean up old scanned times (keep only last hour)
            cutoff = now - timedelta(hours=1)
            self._scanned_times = {k: v for k, v in self._scanned_times.items() if v > cutoff}
            
        except Exception as e:
            self._log(f"❌ Scan failed: {e}", "error")
        
        return opportunities
    
    def _fetch_active_markets(self) -> List[Dict]:
        """Fetch active markets from Polymarket with DIVERSITY focus."""
        all_markets = []
        
        try:
            url = f"{GAMMA_API_BASE}/markets"
            
            # Fetch from multiple orderings to get diverse markets
            orderings = [
                ("volume24hr", "false"),   # Active 24h trading
                ("volumeNum", "false"),    # Total volume
                ("liquidity", "false"),    # High liquidity
                ("startDate", "false"),    # Newest markets
            ]
            
            for order_by, ascending in orderings:
                try:
                    params = {
                        "active": "true",
                        "closed": "false",
                        "limit": 80,
                        "order": order_by,
                        "ascending": ascending,
                    }
                    response = requests.get(url, params=params, timeout=15)
                    if response.ok:
                        all_markets.extend(response.json())
                except Exception:
                    continue
            
            # Remove duplicates
            seen = set()
            unique_markets = []
            for m in all_markets:
                mid = m.get("slug") or str(m.get("id"))
                if mid not in seen:
                    seen.add(mid)
                    unique_markets.append(m)
            
            # Filter and categorize markets
            valid_markets = []
            owned_market_ids = {t.market_id for t in self.open_trades.values()}
            now = datetime.now(timezone.utc)
            
            # Track categories for diversity
            category_markets: Dict[str, List] = {cat: [] for cat in MARKET_CATEGORY_KEYWORDS.keys()}
            category_markets["other"] = []
            
            for market in unique_markets:
                end_date_str = market.get("endDate")
                if not end_date_str:
                    continue
                if market.get("closed"):
                    continue
                
                # Parse and validate end date
                try:
                    if end_date_str.endswith('Z'):
                        end_dt = datetime.fromisoformat(end_date_str.replace('Z', '+00:00'))
                    else:
                        end_dt = datetime.fromisoformat(end_date_str)
                    
                    if end_dt.tzinfo is None:
                        end_dt = end_dt.replace(tzinfo=timezone.utc)
                    
                    if end_dt <= now:
                        continue
                    
                    resolution_days = (end_dt - now).total_seconds() / 86400.0
                    if resolution_days < self.config.min_days or resolution_days > self.config.max_days:
                        continue
                except Exception:
                    continue
                
                market_id = market.get("slug") or str(market.get("id"))
                question = market.get("question") or market.get("title", "")
                
                # Skip owned markets
                if market_id in owned_market_ids:
                    continue
                
                # Skip recently scanned
                if self.config.skip_recently_scanned and market_id in self._scanned_times:
                    last_scan = self._scanned_times[market_id]
                    if (now - last_scan).total_seconds() < self.config.market_cooldown_minutes * 60:
                        continue
                
                # Get volumes
                volume = float(market.get("volumeNum") or market.get("volume") or 0)
                volume_24h = float(market.get("volume24hr") or 0)
                
                if volume < self.config.min_volume:
                    continue
                
                # Detect category
                category = self._detect_category(question)
                self._market_categories[market_id] = category
                
                # Check category limit
                current_cat_count = self._get_category_count(category)
                cat_limit = self.config.category_limits.get(category, 5)
                if current_cat_count >= cat_limit:
                    continue  # Skip - already have enough in this category
                
                # Add metadata
                market['_resolution_days'] = resolution_days
                market['_volume_24h'] = volume_24h
                market['_category'] = category
                
                # Sort into category bucket
                category_markets[category].append(market)
            
            # Build diverse final list - take from each category
            combined = []
            
            # Shuffle within categories for variety
            for cat, markets in category_markets.items():
                random.shuffle(markets)
                # Take top markets from each category proportionally
                limit = self.config.category_limits.get(cat, 5)
                combined.extend(markets[:limit * 2])  # 2x limit to have options
            
            # Final shuffle for unpredictability
            random.shuffle(combined)
            
            # Log category breakdown
            cat_counts = {cat: len([m for m in combined if m.get('_category') == cat]) 
                         for cat in MARKET_CATEGORY_KEYWORDS.keys()}
            cat_counts['other'] = len([m for m in combined if m.get('_category', 'other') == 'other'])
            
            cat_summary = ", ".join(f"{c[:3]}:{n}" for c, n in cat_counts.items() if n > 0)
            self._log(f"Fetched {len(combined)} markets ({cat_summary})", "info")
            
            return combined
            
        except Exception as e:
            self._log(f"Failed to fetch markets: {e}", "error")
            return []
    
    def _evaluate_market(self, market: Dict) -> Optional[MarketOpportunity]:
        """Evaluate a market for trading opportunity."""
        market_id = market.get("slug") or str(market.get("id"))
        question = market.get("question") or market.get("title", "Unknown")
        
        # Skip blacklisted
        if market_id in self.blacklist:
            return None
        
        # Get resolution time
        end_date = market.get("endDate")
        if not end_date:
            return None
        
        try:
            resolution_days = compute_resolution_days(end_date)
        except Exception:
            return None
        
        # Check day bounds
        if resolution_days < self.config.min_days or resolution_days > self.config.max_days:
            return None
        
        # Get token IDs and prices
        outcomes = market.get("outcomes")
        token_ids = market.get("clobTokenIds")
        prices = market.get("outcomePrices")
        
        if not outcomes or not token_ids:
            return None
        
        try:
            if isinstance(outcomes, str):
                outcomes = json.loads(outcomes)
            if isinstance(token_ids, str):
                token_ids = json.loads(token_ids)
            if isinstance(prices, str):
                prices = json.loads(prices)
        except Exception:
            return None
        
        # Find best outcome to trade (usually "Yes")
        best_opportunity = None
        best_g = -999
        
        for i, outcome in enumerate(outcomes):
            if i >= len(token_ids):
                break
            
            token_id = str(token_ids[i])
            
            try:
                price = float(prices[i]) if prices and i < len(prices) else None
            except (TypeError, ValueError, IndexError):
                price = None
            
            if price is None:
                # Fetch from order book
                try:
                    book = fetch_order_book(token_id)
                    if book.get("asks"):
                        price = book["asks"][0][0]
                except Exception:
                    continue
            
            if price is None:
                continue
            
            # Check price bounds
            if price < self.config.min_price or price > self.config.max_price:
                continue
            
            # Calculate g (growth rate)
            g_score = self._compute_g(price, resolution_days)
            if g_score is None or g_score < self.config.min_g_score:
                continue
            
            # Calculate expected ROI
            expected_roi = (1 - self.EXCHANGE_FEE) * ((1.0 - price) / price)
            if expected_roi < self.config.min_expected_roi:
                continue
            
            if g_score > best_g:
                best_g = g_score
                
                # Calculate confidence based on various factors
                volume = float(market.get("volumeNum") or market.get("volume") or 0)
                liquidity = float(market.get("liquidity") or volume * 0.1)
                
                confidence = self._calculate_confidence(
                    price=price,
                    volume=volume,
                    liquidity=liquidity,
                    resolution_days=resolution_days,
                    g_score=g_score,
                    market_id=market_id,
                    question=question,
                )
                
                # Determine decision
                decision, reasons = self._make_decision(
                    price=price,
                    g_score=g_score,
                    expected_roi=expected_roi,
                    confidence=confidence,
                    resolution_days=resolution_days,
                )
                
                best_opportunity = MarketOpportunity(
                    market_id=market_id,
                    slug=market.get("slug", market_id),
                    question=question,
                    outcome=outcome,
                    token_id=token_id,
                    price=price,
                    resolution_days=resolution_days,
                    end_date=end_date,
                    volume=volume,
                    liquidity=liquidity,
                    g_score=g_score,
                    expected_roi=expected_roi,
                    confidence=confidence,
                    decision=decision,
                    reasons=reasons,
                )
        
        return best_opportunity
    
    def _compute_g(self, price: float, resolution_days: float, lambda_days: float = 1.0) -> Optional[float]:
        """
        Compute growth rate (g) metric for a market opportunity.
        
        Formula: g = ln(1 + r) / (resolution_days + lambda)
        Where r = (1 - fee) * ((1 - price) / price)
        
        The 2% fee is included because g-score assumes holding to resolution,
        which is when Polymarket charges the fee. This makes the metric
        conservative/realistic for evaluating best-case returns.
        
        Args:
            price: Current ask price (0-1)
            resolution_days: Days until market resolves
            lambda_days: Smoothing factor (default 1.0)
        
        Returns:
            Growth rate per day, or None if invalid inputs
        """
        if price <= 0 or price >= 1 or resolution_days <= 0:
            return None
        
        # r = expected return if held to resolution (post-fee)
        r = (1 - self.EXCHANGE_FEE) * ((1.0 - price) / price)
        denom = resolution_days + lambda_days
        
        if denom <= 0:
            return None
        
        return log1p(r) / denom
    
    def _calculate_confidence(
        self,
        price: float,
        volume: float,
        liquidity: float,
        resolution_days: float,
        g_score: float,
        market_id: str = None,
        question: str = None,
    ) -> float:
        """Calculate confidence score (0-1)."""
        score = 0.5  # Base confidence
        
        # Volume factor (higher volume = more confidence)
        if volume > 100000:
            score += 0.12
        elif volume > 50000:
            score += 0.08
        elif volume > 10000:
            score += 0.05
        elif volume > 5000:
            score += 0.03  # Still give small boost for smaller markets
        
        # Liquidity factor
        if liquidity > 10000:
            score += 0.08
        elif liquidity > 5000:
            score += 0.05
        elif liquidity > 1000:
            score += 0.02
        
        # Price factor (mid-range prices are more reliable)
        if 0.20 <= price <= 0.70:
            score += 0.08
        elif 0.10 <= price <= 0.85:
            score += 0.04
        
        # Time factor (not too short, not too long)
        if 7 <= resolution_days <= 30:
            score += 0.08
        elif 3 <= resolution_days <= 60:
            score += 0.05
        elif 0.5 <= resolution_days <= 90:
            score += 0.02
        
        # G-score factor
        if g_score > 0.01:
            score += 0.05
        elif g_score > 0.005:
            score += 0.03
        
        # News sentiment boost (if available)
        if self._news_analyzer and market_id and question:
            try:
                signal = self._news_analyzer.get_cached_signal(market_id)
                if signal is None:
                    # Try to generate signal
                    signal = self._news_analyzer.generate_signal(market_id, question, price)
                
                if signal:
                    # Boost confidence if news aligns with our position
                    if signal.recommendation == "BUY" and signal.confidence > 0.5:
                        score += self.config.news_confidence_boost * signal.confidence
                        self._log(f"📰 News boost for {question[:30]}...: +{signal.confidence:.1%}", "info")
            except Exception:
                pass
        
        return min(score, 0.95)
    
    def _make_decision(
        self,
        price: float,
        g_score: float,
        expected_roi: float,
        confidence: float,
        resolution_days: float,
    ) -> Tuple[BotDecision, List[str]]:
        """Make a buy/sell/hold decision."""
        reasons = []
        
        # Check confidence threshold
        if confidence < self.config.confidence_threshold:
            reasons.append(f"Low confidence ({confidence:.1%})")
            return BotDecision.SKIP, reasons
        
        # Strong buy signals
        if g_score > 0.005 and expected_roi > 0.30 and confidence > 0.7:
            reasons.append(f"High g-score: {g_score:.4f}")
            reasons.append(f"Expected ROI: {expected_roi:.1%}")
            reasons.append(f"Good confidence: {confidence:.1%}")
            return BotDecision.BUY, reasons
        
        # Normal buy signals
        if g_score > self.config.min_g_score and expected_roi > self.config.min_expected_roi:
            reasons.append(f"G-score: {g_score:.4f}")
            reasons.append(f"Expected ROI: {expected_roi:.1%}")
            return BotDecision.BUY, reasons
        
        # Hold if we have position
        reasons.append("Doesn't meet buy criteria")
        return BotDecision.HOLD, reasons
    
    # -------------------------------------------------------------------------
    # Trading Execution
    # -------------------------------------------------------------------------
    
    def execute_trade(self, opportunity: MarketOpportunity, force_test: bool = False) -> Optional[BotTrade]:
        """Execute a paper trade based on opportunity with realistic execution simulation."""
        if opportunity.decision != BotDecision.BUY:
            return None
        
        # Determine if this is a swing trade (short-term, high-volume market)
        is_swing = (
            self.config.swing_trade_enabled and
            opportunity.volume >= self.config.swing_min_volume and
            opportunity.resolution_days <= 7
        )
        
        # Count current positions by type (handle missing trade_type for old trades)
        swing_count = sum(1 for t in self.open_trades.values() if getattr(t, 'trade_type', 'long') == "swing")
        long_count = sum(1 for t in self.open_trades.values() if getattr(t, 'trade_type', 'long') == "long")
        
        # Check position limits - LOG WHY WE SKIP
        if is_swing and swing_count >= self.config.max_swing_positions:
            self._log(f"Skip: Max swing positions ({swing_count}/{self.config.max_swing_positions})", "info")
            return None
        if not is_swing and long_count >= self.config.max_long_term_positions:
            self._log(f"Skip: Max long positions ({long_count}/{self.config.max_long_term_positions})", "info")
            return None
        if len(self.open_trades) >= self.config.max_positions:
            self._log(f"Skip: Max total positions ({len(self.open_trades)}/{self.config.max_positions})", "info")
            return None
        
        market_key = f"{opportunity.market_id}|{opportunity.outcome}"
        
        # Check if already have position
        for trade in self.open_trades.values():
            if trade.market_id == opportunity.market_id and trade.outcome == opportunity.outcome:
                return None  # Silent skip - already own this
        
        # Determine trade size
        is_test_trade = force_test or (
            self.config.test_trade_enabled and 
            opportunity.confidence < self.config.high_confidence_threshold and
            not is_swing  # Swing trades get full size if high volume
        )
        
        if is_test_trade:
            position_value = min(
                self.config.test_trade_size,
                self.cash_balance * 0.03,
            )
            trade_label = "TEST"
        elif is_swing:
            # Swing trades: medium size for quick profit
            position_value = min(
                self.config.max_position_size * 0.5,  # Half size for swing
                self.cash_balance * 0.08,
            )
            trade_label = "SWING"
        else:
            # Long-term: full size
            position_value = min(
                self.config.max_position_size,
                self.cash_balance * self.config.max_portfolio_pct,
                self.cash_balance * 0.2,
            )
            trade_label = "LONG"
        
        if position_value < 5:
            self._log(f"Skip: Position value too small (${position_value:.2f})", "info")
            return None
        
        # Check cash balance
        if position_value > self.cash_balance:
            self._log(f"Skip: Not enough cash (need ${position_value:.2f}, have ${self.cash_balance:.2f})", "info")
            return None
        
        # =====================================================================
        # REALISTIC EXECUTION SIMULATION
        # =====================================================================
        actual_entry_price = opportunity.price
        actual_shares = position_value / opportunity.price
        actual_cost = position_value
        slippage_info = ""
        
        if self.config.realistic_execution:
            try:
                # Fetch fresh order book for execution
                order_book = fetch_order_book(opportunity.token_id)
                
                # Calculate realistic buy execution
                execution = calculate_buy_execution(order_book, position_value)
                
                # Check 1: Is there enough liquidity?
                required_liquidity = position_value * self.config.min_book_depth_multiplier
                if execution['available_liquidity'] < required_liquidity:
                    self._log(
                        f"Skip: Insufficient liquidity for {opportunity.question[:25]}... "
                        f"(need ${required_liquidity:.0f}, have ${execution['available_liquidity']:.0f})",
                        "info"
                    )
                    return None
                
                # Check 2: Can the order be filled?
                if not execution['can_fill']:
                    self._log(
                        f"Skip: Order cannot be fully filled for {opportunity.question[:25]}... "
                        f"(only ${execution['total_cost']:.0f} of ${position_value:.0f} fillable)",
                        "info"
                    )
                    return None
                
                # Check 3: Is slippage acceptable?
                if execution['slippage_pct'] > self.config.max_slippage_pct:
                    self._log(
                        f"Skip: Slippage too high for {opportunity.question[:25]}... "
                        f"({execution['slippage_pct']:.1f}% > {self.config.max_slippage_pct}%)",
                        "info"
                    )
                    return None
                
                # Apply execution delay price movement (simulates time between decision and fill)
                if self.config.execution_delay_enabled:
                    # Random price movement during execution delay (can be positive or negative)
                    delay_movement = random.uniform(
                        -self.config.execution_delay_max_pct / 100,
                        self.config.execution_delay_max_pct / 100
                    )
                    execution['avg_price'] *= (1 + delay_movement)
                    if delay_movement != 0:
                        slippage_info += f" delay:{delay_movement*100:+.1f}%"
                
                # Use realistic execution values
                actual_entry_price = execution['avg_price']
                actual_shares = execution['total_shares']
                actual_cost = execution['total_cost']
                
                if execution['slippage_pct'] > 0.5:  # Log significant slippage
                    slippage_info = f" [slip:{execution['slippage_pct']:.1f}%{slippage_info}]"
                elif slippage_info:
                    slippage_info = f" [{slippage_info.strip()}]"
                    
            except Exception as e:
                # If order book fetch fails, fall back to displayed price
                self._log(f"Warning: Could not fetch order book, using displayed price: {e}", "info")
        
        # Detect category before creating trade
        category = self._detect_category(opportunity.question)
        
        trade = BotTrade(
            id=self._generate_trade_id(),
            timestamp=self._now_iso(),
            market_id=opportunity.market_id,
            question=opportunity.question,
            outcome=opportunity.outcome,
            action="buy",
            shares=actual_shares,
            entry_price=actual_entry_price,
            current_price=actual_entry_price,
            status="open",
            trade_type="swing" if is_swing else "long",
            volume=opportunity.volume,
            resolution_days=opportunity.resolution_days,
            category=category,
            token_id=opportunity.token_id,  # Persisted for realistic exit pricing
        )
        
        self.cash_balance -= actual_cost
        self.open_trades[trade.id] = trade
        self.total_trades += 1
        
        # Store category for diversity tracking
        self._market_categories[opportunity.market_id] = category
        
        # Add to trade log
        self._add_to_trade_log(
            action="BUY",
            question=opportunity.question,
            amount=actual_cost,
            price=actual_entry_price,
        )
        
        days_str = f"{opportunity.resolution_days:.1f}d" if opportunity.resolution_days < 30 else f"{opportunity.resolution_days/30:.1f}mo"
        cat_emoji = {"sports": "🏈", "politics": "🏛️", "crypto": "₿", "entertainment": "🎬", 
                     "finance": "📈", "technology": "💻", "world_events": "🌍"}.get(category, "📋")
        
        self._log(
            f"[{trade_label}] BOUGHT '{opportunity.question[:30]}...' "
            f"| ${actual_cost:.0f} @ ${actual_entry_price:.3f}{slippage_info} | Vol: ${opportunity.volume/1000:.0f}k | {days_str}",
            "trade"
        )
        
        if self.on_trade:
            self.on_trade(trade)
        
        self._save()
        return trade
    
    def update_positions(self) -> None:
        """Update prices and check stop-loss/take-profit for open positions.
        PRIORITY SYSTEM: Held positions update FAST, others update slower."""
        
        if not self.open_trades:
            return
        
        # ALL held positions get updated every call (priority)
        market_slugs = list(set(trade.market_id for trade in self.open_trades.values()))
        
        # Fetch ALL held position prices (no limit - these are critical)
        market_prices = {}  # market_id -> {outcome -> price}
        
        for slug in market_slugs:
            try:
                url = f"{GAMMA_API_BASE}/markets"
                response = requests.get(url, params={"slug": slug}, timeout=5)  # 5s timeout for held positions
                if response.ok:
                    data = response.json()
                    market_data = data[0] if isinstance(data, list) and data else data if isinstance(data, dict) else None
                    
                    if market_data:
                        prices = market_data.get("outcomePrices")
                        outcomes = market_data.get("outcomes")
                        
                        if prices and outcomes:
                            try:
                                if isinstance(prices, str):
                                    prices = json.loads(prices)
                                if isinstance(outcomes, str):
                                    outcomes = json.loads(outcomes)
                                
                                market_prices[slug] = {
                                    outcomes[i]: float(prices[i]) 
                                    for i in range(len(outcomes)) 
                                    if i < len(prices)
                                }
                            except Exception:
                                pass
            except requests.Timeout:
                self._log(f"⚠️ Timeout fetching {slug} - using cached price", "alert")
                continue
            except Exception:
                continue
        
        # Now update all positions with fetched prices
        for trade_id, trade in list(self.open_trades.items()):
            try:
                market_key = f"{trade.market_id}|{trade.outcome}"
                current_price = None
                
                # Use batch-fetched price
                if trade.market_id in market_prices:
                    current_price = market_prices[trade.market_id].get(trade.outcome)
                
                # Fallback to scanned markets if API fetch failed
                if current_price is None and market_key in self.scanned_markets:
                    opp = self.scanned_markets[market_key]
                    current_price = opp.price
                
                # Final fallback to current price if all else failed
                if current_price is None:
                    current_price = trade.current_price
                
                # Update trade
                trade.current_price = current_price
                trade.pnl = (current_price - trade.entry_price) * trade.shares
                trade.pnl_pct = (current_price - trade.entry_price) / trade.entry_price if trade.entry_price > 0 else 0
                
                # Different thresholds for swing vs long trades
                if trade.trade_type == "swing":
                    stop_loss = self.config.swing_stop_loss_pct
                    take_profit = self.config.swing_take_profit_pct
                else:
                    stop_loss = self.config.stop_loss_pct
                    take_profit = self.config.take_profit_pct
                
                # Check stop-loss
                if trade.pnl_pct <= -stop_loss:
                    self._close_trade(trade, current_price, "stop_loss")
                
                # Check take-profit
                elif trade.pnl_pct >= take_profit:
                    self._close_trade(trade, current_price, "take_profit")
                
            except Exception:
                continue
    
    def _close_trade(self, trade: BotTrade, exit_price: float, reason: str) -> None:
        """Close a trade with realistic execution simulation."""
        actual_exit_price = exit_price
        slippage_info = ""
        fee_applied = False
        
        # =====================================================================
        # DETECT MARKET RESOLUTION
        # If price is very close to $1.00 or $0.00, the market likely resolved
        # =====================================================================
        is_resolution_win = exit_price >= 0.98  # Price ~$1.00 means we won
        is_resolution_loss = exit_price <= 0.02  # Price ~$0.00 means we lost
        is_resolution = is_resolution_win or is_resolution_loss
        
        # Update reason if this looks like a resolution
        if is_resolution and reason in ("take_profit", "stop_loss"):
            reason = "resolution_win" if is_resolution_win else "resolution_loss"
        
        # =====================================================================
        # REALISTIC EXIT EXECUTION - Use BID price, not mid/last price
        # =====================================================================
        if self.config.realistic_execution and not is_resolution:
            # Only apply order book slippage for non-resolution exits
            # Resolution exits pay out at exactly $1.00 or $0.00
            try:
                # Get token_id - check both new field and old runtime attribute for backwards compatibility
                token_id = trade.token_id or getattr(trade, '_token_id', None)
                
                if token_id:
                    # Fetch order book for realistic sell execution
                    order_book = fetch_order_book(token_id)
                    
                    # Calculate realistic sell execution
                    execution = calculate_sell_execution(order_book, trade.shares)
                    
                    if execution['can_fill'] and execution['avg_price'] > 0:
                        # Apply execution delay price movement
                        if self.config.execution_delay_enabled:
                            delay_movement = random.uniform(
                                -self.config.execution_delay_max_pct / 100,
                                self.config.execution_delay_max_pct / 100
                            )
                            execution['avg_price'] *= (1 + delay_movement)
                        
                        actual_exit_price = execution['avg_price']
                        
                        if execution['slippage_pct'] > 0.5:
                            slippage_info = f" [exit slip:{execution['slippage_pct']:.1f}%]"
                    else:
                        # Can't fill on bid side - use best bid with penalty
                        if execution['best_bid'] > 0:
                            actual_exit_price = execution['best_bid'] * 0.98  # 2% penalty
                            slippage_info = " [low bid liquidity]"
                else:
                    # No token_id - estimate bid as ~2% below mid price
                    actual_exit_price = exit_price * 0.98
                    slippage_info = " [est. bid]"
                    
            except Exception as e:
                # Fallback: estimate bid as ~2% below displayed price
                actual_exit_price = exit_price * 0.98
                slippage_info = " [bid est.]"
        
        # =====================================================================
        # APPLY 2% FEE ON RESOLUTION WINS (Polymarket's actual fee structure)
        # Fee is only charged when you hold shares that resolve to $1.00
        # =====================================================================
        if is_resolution_win:
            # Resolution win: shares pay out at $1.00, minus 2% fee
            actual_exit_price = 1.00 * (1 - self.EXCHANGE_FEE)  # $0.98 per share
            fee_applied = True
            slippage_info = f" [2% resolution fee]"
        elif is_resolution_loss:
            # Resolution loss: shares pay out at $0.00, no fee
            actual_exit_price = 0.00
            slippage_info = " [resolved $0]"
        
        trade.exit_price = actual_exit_price
        trade.exit_timestamp = self._now_iso()
        trade.status = "closed"
        trade.pnl = (actual_exit_price - trade.entry_price) * trade.shares
        trade.pnl_pct = (actual_exit_price - trade.entry_price) / trade.entry_price if trade.entry_price > 0 else 0
        
        # Update balance
        proceeds = trade.shares * actual_exit_price
        self.cash_balance += proceeds
        
        # Update stats
        self.total_pnl += trade.pnl
        if trade.pnl >= 0:
            self.winning_trades += 1
        else:
            self.losing_trades += 1
        
        # Move to closed
        del self.open_trades[trade.id]
        self.closed_trades.append(trade)
        
        # Keep only last 100 closed trades
        if len(self.closed_trades) > 100:
            self.closed_trades = self.closed_trades[-100:]
        
        pnl_str = f"+${trade.pnl:.2f}" if trade.pnl >= 0 else f"-${abs(trade.pnl):.2f}"
        result = "WIN" if trade.pnl >= 0 else "LOSS"
        
        # Add to trade log
        self._add_to_trade_log(
            action="SELL",
            question=trade.question,
            amount=proceeds,
            price=actual_exit_price,
            pnl=trade.pnl,
            result=result,
        )
        
        self._log(
            f"[{result}] SOLD '{trade.question[:30]}...' - {reason.upper()}{slippage_info} - P&L: {pnl_str} ({trade.pnl_pct:+.1%})",
            "trade"
        )
        
        if self.on_trade:
            self.on_trade(trade)
        
        self._save()
    
    def sell_position(self, trade_id: str, price: Optional[float] = None) -> bool:
        """Manually sell a position."""
        if trade_id not in self.open_trades:
            return False
        
        trade = self.open_trades[trade_id]
        exit_price = price or trade.current_price
        self._close_trade(trade, exit_price, "manual")
        return True
    
    def _cleanup_stagnant_positions(self, min_positions_to_free: int = 5) -> int:
        """
        Sell stagnant positions (near $0 P&L) to free up slots and cash.
        VERY aggressive cleanup when at capacity.
        Returns number of positions closed.
        """
        closed_count = 0
        now = datetime.now(timezone.utc)
        
        # Check how full we are
        positions_used = len(self.open_trades)
        at_capacity = positions_used >= self.config.max_positions - 5
        very_full = positions_used >= self.config.max_positions - 2
        
        # Find stagnant positions - sorted by how "stuck" they are
        stagnant_candidates = []
        
        for trade_id, trade in list(self.open_trades.items()):
            # Calculate how long we've held this
            try:
                trade_time = datetime.fromisoformat(trade.timestamp.replace('Z', '+00:00'))
                hours_held = (now - trade_time).total_seconds() / 3600
            except:
                hours_held = 24  # Assume 24 hours if can't parse
            
            # VERY AGGRESSIVE when very full
            if very_full:
                # Sell almost anything - flat or small loss held > 15 min
                is_flat = -0.12 <= trade.pnl_pct <= 0.12
                held_long_enough = hours_held >= 0.25  # 15 minutes
                is_slight_loss = -0.20 <= trade.pnl_pct < -0.12 and hours_held >= 0.5
            elif at_capacity:
                # At capacity: sell anything flat held > 30 min
                is_flat = -0.10 <= trade.pnl_pct <= 0.10
                held_long_enough = hours_held >= 0.5  # 30 minutes
                is_slight_loss = -0.18 <= trade.pnl_pct < -0.10 and hours_held >= 1
            else:
                # Normal: sell flat positions held > 1 hour
                is_flat = -0.06 <= trade.pnl_pct <= 0.06
                held_long_enough = hours_held >= 1
                is_slight_loss = -0.15 <= trade.pnl_pct < -0.06 and hours_held >= 3
            
            if (is_flat and held_long_enough) or is_slight_loss:
                # Score: prefer to close positions that are most flat and oldest
                flatness_score = 1 - abs(trade.pnl_pct)  # Flatter = higher score
                age_score = min(hours_held / 12, 1.0)  # Older = higher score (faster scale)
                # Add small loss penalty - prefer selling break-even over losses
                loss_penalty = 0.05 if trade.pnl_pct < 0 else 0
                total_score = flatness_score * 0.5 + age_score * 0.4 - loss_penalty
                
                stagnant_candidates.append((trade_id, trade, total_score, hours_held))
        
        # Sort by score (highest first = most stagnant)
        stagnant_candidates.sort(key=lambda x: x[2], reverse=True)
        
        # How many to close - more aggressive when at capacity
        to_close = min_positions_to_free if not at_capacity else max(min_positions_to_free, 12)
        
        # Close positions
        for trade_id, trade, score, hours_held in stagnant_candidates[:to_close]:
            pnl_str = f"+${trade.pnl:.2f}" if trade.pnl >= 0 else f"-${abs(trade.pnl):.2f}"
            self._log(
                f"🧹 Closing stagnant: '{trade.question[:25]}...' | "
                f"{pnl_str} ({trade.pnl_pct:+.1%}) | {hours_held:.1f}h",
                "info"
            )
            self._close_trade(trade, trade.current_price, "stagnant")
            closed_count += 1
        
        if closed_count > 0:
            self._log(f"🧹 Freed {closed_count} slot(s) - Now {len(self.open_trades)}/{self.config.max_positions} positions", "success")
        
        return closed_count
    
    def _force_sell_worst_performers(self, count: int = 5) -> int:
        """
        Force sell the worst performing positions when at capacity.
        Used as last resort when no stagnant positions found.
        """
        if not self.open_trades:
            return 0
        
        # Sort all positions by P&L percentage (worst first)
        sorted_trades = sorted(
            self.open_trades.items(),
            key=lambda x: x[1].pnl_pct
        )
        
        closed = 0
        for trade_id, trade in sorted_trades[:count]:
            # Force sell anything not down more than 30%
            if trade.pnl_pct > -0.30:
                pnl_str = f"+${trade.pnl:.2f}" if trade.pnl >= 0 else f"-${abs(trade.pnl):.2f}"
                self._log(
                    f"⚡ Force selling: '{trade.question[:25]}...' | {pnl_str} ({trade.pnl_pct:+.1%})",
                    "alert"
                )
                self._close_trade(trade, trade.current_price, "forced")
                closed += 1
        
        if closed > 0:
            self._log(f"⚡ Force sold {closed} position(s) to make room", "alert")
        
        return closed
    
    # -------------------------------------------------------------------------
    # Auto-Trading Loop
    # -------------------------------------------------------------------------
    
    def start(self) -> None:
        """Start auto-trading."""
        if self._running:
            return
        
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        
        self._log("Auto-trading bot started!", "success")
    
    def stop(self) -> None:
        """Stop auto-trading."""
        self._running = False
        self._log("Auto-trading bot stopped", "info")
    
    def is_running(self) -> bool:
        return self._running
    
    def _run_loop(self) -> None:
        """Main trading loop."""
        while self._running:
            try:
                # First, ALWAYS check if we need to clean up stagnant positions
                positions_used = len(self.open_trades)
                positions_limit = self.config.max_positions
                long_count = sum(1 for t in self.open_trades.values() if getattr(t, 'trade_type', 'long') == "long")
                cash_low = self.cash_balance < 200  # Less than $200 cash
                at_long_limit = long_count >= self.config.max_long_term_positions
                near_capacity = positions_used >= (positions_limit - 3)
                
                # Clean up if at any limit
                if near_capacity or cash_low or at_long_limit:
                    self._log(f"📊 Capacity check: {positions_used}/{positions_limit} pos, {long_count}/{self.config.max_long_term_positions} long, ${self.cash_balance:.0f} cash", "info")
                    freed = self._cleanup_stagnant_positions(min_positions_to_free=8)
                    if freed == 0 and near_capacity:
                        # Force sell the worst performers if still at capacity
                        self._force_sell_worst_performers(count=5)
                
                # Scan for opportunities
                opportunities = self.scan_markets()
                
                # Execute trades on ALL buy opportunities (not just top 5)
                buy_opportunities = [o for o in opportunities if o.decision == BotDecision.BUY]
                executed = 0
                
                # Check if buys are paused by UI
                if self._buys_paused:
                    if buy_opportunities:
                        self._log(f"⏸️ Buys paused - skipping {len(buy_opportunities)} buy opportunities", "alert")
                else:
                    for opp in buy_opportunities[:20]:  # Try up to 20 opportunities
                        result = self.execute_trade(opp)
                        if result:
                            executed += 1
                            if executed >= 5:  # Max 5 trades per scan cycle
                                break
                
                if buy_opportunities and executed == 0 and len(self.open_trades) >= positions_limit - 2:
                    self._log(f"⚠️ At capacity ({positions_used}/{positions_limit}). Selling stagnant positions...", "alert")
                    self._cleanup_stagnant_positions(min_positions_to_free=10)
                
                # Update existing positions
                self.update_positions()
                
                # Wait before next scan
                for _ in range(self.config.scan_interval_seconds):
                    if not self._running:
                        break
                    time.sleep(1)
                    
            except Exception as e:
                self._log(f"Error in trading loop: {e}", "error")
                time.sleep(10)
    
    # -------------------------------------------------------------------------
    # Evaluate User-Added Market
    # -------------------------------------------------------------------------
    
    def evaluate_market_for_user(self, market_data: Dict, outcome: str, token_id: str) -> MarketOpportunity:
        """Evaluate a market that the user wants to add."""
        market_id = market_data.get("slug") or str(market_data.get("id"))
        question = market_data.get("question") or market_data.get("title", "Unknown")
        end_date = market_data.get("endDate")
        
        try:
            resolution_days = compute_resolution_days(end_date) if end_date else 30
        except Exception:
            resolution_days = 30
        
        # Get price from order book
        price = None
        try:
            book = fetch_order_book(token_id)
            if book.get("asks"):
                price = book["asks"][0][0]
        except Exception:
            pass
        
        if price is None:
            # Try from market data
            prices = market_data.get("outcomePrices")
            outcomes = market_data.get("outcomes")
            if prices and outcomes:
                try:
                    if isinstance(prices, str):
                        prices = json.loads(prices)
                    if isinstance(outcomes, str):
                        outcomes = json.loads(outcomes)
                    idx = outcomes.index(outcome) if outcome in outcomes else 0
                    price = float(prices[idx])
                except Exception:
                    price = 0.5
        
        price = price or 0.5
        
        # Calculate metrics
        g_score = self._compute_g(price, resolution_days) or 0
        expected_roi = (1 - self.EXCHANGE_FEE) * ((1.0 - price) / price) if price > 0 else 0
        
        volume = float(market_data.get("volumeNum") or market_data.get("volume") or 0)
        liquidity = float(market_data.get("liquidity") or volume * 0.1)
        
        confidence = self._calculate_confidence(
            price=price,
            volume=volume,
            liquidity=liquidity,
            resolution_days=resolution_days,
            g_score=g_score,
        )
        
        decision, reasons = self._make_decision(
            price=price,
            g_score=g_score,
            expected_roi=expected_roi,
            confidence=confidence,
            resolution_days=resolution_days,
        )
        
        return MarketOpportunity(
            market_id=market_id,
            slug=market_data.get("slug", market_id),
            question=question,
            outcome=outcome,
            token_id=token_id,
            price=price,
            resolution_days=resolution_days,
            end_date=end_date or "",
            volume=volume,
            liquidity=liquidity,
            g_score=g_score,
            expected_roi=expected_roi,
            confidence=confidence,
            decision=decision,
            reasons=reasons,
        )
    
    # -------------------------------------------------------------------------
    # Stats & Persistence
    # -------------------------------------------------------------------------
    
    def get_stats(self) -> Dict:
        """Get bot statistics."""
        portfolio_value = self.cash_balance + sum(t.shares * t.current_price for t in self.open_trades.values())
        unrealized_pnl = sum(t.pnl for t in self.open_trades.values())
        
        return {
            "cash_balance": self.cash_balance,
            "portfolio_value": portfolio_value,
            "open_positions": len(self.open_trades),
            "total_trades": self.total_trades,
            "winning_trades": self.winning_trades,
            "losing_trades": self.losing_trades,
            "win_rate": self.winning_trades / self.total_trades * 100 if self.total_trades > 0 else 0,
            "total_pnl": self.total_pnl,
            "unrealized_pnl": unrealized_pnl,
            "total_return_pct": (portfolio_value - self.config.initial_capital) / self.config.initial_capital * 100,
            "is_running": self._running,
        }
    
    def get_open_trades(self) -> List[BotTrade]:
        """Get all open trades."""
        return list(self.open_trades.values())
    
    def get_closed_trades(self, limit: int = 50) -> List[BotTrade]:
        """Get recent closed trades."""
        return list(reversed(self.closed_trades[-limit:]))
    
    def _save(self) -> None:
        """Save bot state to local file and cloud (if enabled)."""
        try:
            data = {
                "cash_balance": self.cash_balance,
                "open_trades": {k: v.to_dict() for k, v in self.open_trades.items()},
                "closed_trades": [t.to_dict() for t in self.closed_trades],
                "total_trades": self.total_trades,
                "winning_trades": self.winning_trades,
                "losing_trades": self.losing_trades,
                "total_pnl": self.total_pnl,
                "blacklist": list(self.blacklist),
                "trade_counter": self._trade_counter,
                "trade_log": self.trade_log[-100:],  # Save last 100 trade log entries
                "market_categories": self._market_categories,
            }
            
            # Save to local file
            self.storage_path.write_text(json.dumps(data, indent=2))
            
            # Save to cloud if enabled
            if self._cloud_sync and self._cloud_sync.is_enabled():
                self._cloud_sync.save_state(data)
                
        except Exception as e:
            pass
    
    def _load(self) -> None:
        """Load bot state from cloud (if enabled) or local file."""
        try:
            data = None
            
            # Try loading from cloud first if enabled
            if self._cloud_sync and self._cloud_sync.is_enabled():
                data = self._cloud_sync.load_state()
                if data:
                    self._log("☁️ Loaded state from cloud", "info")
            
            # Fall back to local file if cloud didn't return data
            if data is None and self.storage_path.exists():
                data = json.loads(self.storage_path.read_text())
                self._log("💾 Loaded state from local file", "info")
            
            # Apply the loaded data
            if data:
                self.cash_balance = data.get("cash_balance", self.config.initial_capital)
                self.open_trades = {k: BotTrade.from_dict(v) for k, v in data.get("open_trades", {}).items()}
                self.closed_trades = [BotTrade.from_dict(t) for t in data.get("closed_trades", [])]
                self.total_trades = data.get("total_trades", 0)
                self.winning_trades = data.get("winning_trades", 0)
                self.losing_trades = data.get("losing_trades", 0)
                self.total_pnl = data.get("total_pnl", 0.0)
                self.blacklist = set(data.get("blacklist", []))
                self._trade_counter = data.get("trade_counter", 0)
                self.trade_log = data.get("trade_log", [])
                self._market_categories = data.get("market_categories", {})
                
        except Exception as e:
            pass
    
    def reset(self) -> None:
        """Reset bot to initial state."""
        self.stop()
        self.cash_balance = self.config.initial_capital
        self.open_trades = {}
        self.closed_trades = []
        self.scanned_markets = {}
        self.total_trades = 0
        self.winning_trades = 0
        self.losing_trades = 0
        self.total_pnl = 0.0
        self._trade_counter = 0
        self.trade_log = []
        self._market_categories = {}
        self._save()
        self._log("🔄 Bot has been reset", "info")
