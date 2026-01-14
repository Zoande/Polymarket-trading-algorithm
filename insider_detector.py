"""Insider trading detector for Polymarket."""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Callable, Dict, List, Optional, Set
from enum import Enum

import requests


# Polymarket API endpoints
GAMMA_API_BASE = "https://gamma-api.polymarket.com"
CLOB_API_BASE = "https://clob.polymarket.com"


class AlertSeverity(Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class TraderProfile:
    """Profile of a trader's activity."""
    address: str
    first_seen: str
    total_trades: int = 0
    total_volume: float = 0.0
    markets_traded: Set[str] = field(default_factory=set)
    large_trades: int = 0  # Trades over $1000
    
    def days_active(self) -> float:
        try:
            first = datetime.fromisoformat(self.first_seen.replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)
            return (now - first).total_seconds() / 86400
        except Exception:
            return 0.0
    
    def is_new_account(self, threshold_days: float = 7.0) -> bool:
        return self.days_active() < threshold_days
    
    def is_low_activity(self, threshold_trades: int = 5) -> bool:
        return self.total_trades < threshold_trades
    
    def to_dict(self) -> Dict:
        return {
            "address": self.address,
            "first_seen": self.first_seen,
            "total_trades": self.total_trades,
            "total_volume": self.total_volume,
            "markets_traded": list(self.markets_traded),
            "large_trades": self.large_trades,
        }
    
    @staticmethod
    def from_dict(data: Dict) -> "TraderProfile":
        profile = TraderProfile(
            address=data["address"],
            first_seen=data["first_seen"],
            total_trades=data.get("total_trades", 0),
            total_volume=data.get("total_volume", 0.0),
            large_trades=data.get("large_trades", 0),
        )
        profile.markets_traded = set(data.get("markets_traded", []))
        return profile


@dataclass
class InsiderAlert:
    """An alert for suspicious trading activity."""
    id: str
    timestamp: str
    severity: AlertSeverity
    market_id: str
    market_question: str
    trader_address: str
    trade_size: float
    trade_side: str  # "buy" or "sell"
    outcome: str
    price: float
    reason: str
    trader_profile: Optional[TraderProfile] = None
    acknowledged: bool = False
    
    def to_dict(self) -> Dict:
        return {
            "id": self.id,
            "timestamp": self.timestamp,
            "severity": self.severity.value,
            "market_id": self.market_id,
            "market_question": self.market_question,
            "trader_address": self.trader_address,
            "trade_size": self.trade_size,
            "trade_side": self.trade_side,
            "outcome": self.outcome,
            "price": self.price,
            "reason": self.reason,
            "trader_profile": self.trader_profile.to_dict() if self.trader_profile else None,
            "acknowledged": self.acknowledged,
        }
    
    @staticmethod
    def from_dict(data: Dict) -> "InsiderAlert":
        return InsiderAlert(
            id=data["id"],
            timestamp=data["timestamp"],
            severity=AlertSeverity(data["severity"]),
            market_id=data["market_id"],
            market_question=data["market_question"],
            trader_address=data["trader_address"],
            trade_size=data["trade_size"],
            trade_side=data["trade_side"],
            outcome=data["outcome"],
            price=data["price"],
            reason=data["reason"],
            trader_profile=TraderProfile.from_dict(data["trader_profile"]) if data.get("trader_profile") else None,
            acknowledged=data.get("acknowledged", False),
        )


@dataclass
class InsiderDetectorConfig:
    """Configuration for the insider detector.
    
    SIMPLIFIED: Just detect large trades over $10,000 in any market.
    """
    # Simple threshold - alert on any trade over this amount
    large_trade_threshold: float = 10000.0  # $10,000 threshold
    
    # Monitoring settings
    poll_interval_seconds: int = 10  # Fast polling (10 sec)
    max_alerts_stored: int = 200  # Don't store too many
    
    # What to monitor
    monitor_large_trades: bool = True


class InsiderDetector:
    """
    Detects potential insider trading activity on Polymarket.
    
    Monitors for:
    - New accounts placing large bets (>$10k)
    - Sudden volume spikes
    - Unusual trading patterns before major events
    """
    
    # Default data directory
    _DEFAULT_DATA_DIR = Path(__file__).parent / "data"
    
    def __init__(
        self,
        config: Optional[InsiderDetectorConfig] = None,
        storage_path: Optional[Path] = None,
    ):
        self.config = config or InsiderDetectorConfig()
        
        # Use data directory for storage
        if storage_path is None:
            self._DEFAULT_DATA_DIR.mkdir(exist_ok=True)
            self.storage_path = self._DEFAULT_DATA_DIR / "insider_alerts.json"
        else:
            self.storage_path = storage_path
        
        self.alerts: List[InsiderAlert] = []
        self.trader_profiles: Dict[str, TraderProfile] = {}
        self.monitored_markets: Dict[str, Dict] = {}  # market_id -> market info
        self.market_volume_history: Dict[str, List[float]] = {}  # market_id -> recent volumes
        
        self.listeners: List[Callable[[InsiderAlert], None]] = []
        self._alert_counter = 0
        self._lock = threading.Lock()
        self._running = False
        self._monitor_thread: Optional[threading.Thread] = None
        
        self._load()
    
    def _now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    
    def _generate_alert_id(self) -> str:
        self._alert_counter += 1
        return f"insider_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}_{self._alert_counter}"
    
    def add_listener(self, callback: Callable[[InsiderAlert], None]) -> None:
        """Register a callback for new alerts."""
        self.listeners.append(callback)
    
    def remove_listener(self, callback: Callable[[InsiderAlert], None]) -> None:
        """Remove an alert listener."""
        if callback in self.listeners:
            self.listeners.remove(callback)
    
    def add_market(self, market_id: str, question: str, token_id: str) -> None:
        """Add a market to monitor."""
        self.monitored_markets[market_id] = {
            "market_id": market_id,
            "question": question,
            "token_id": token_id,
            "added_at": self._now_iso(),
        }
        self._save()
    
    def remove_market(self, market_id: str) -> None:
        """Remove a market from monitoring."""
        self.monitored_markets.pop(market_id, None)
        self._save()
    
    def get_monitored_markets(self) -> List[Dict]:
        """Get list of monitored markets."""
        return list(self.monitored_markets.values())
    
    def analyze_trade(
        self,
        market_id: str,
        market_question: str,
        trader_address: str,
        trade_size: float,
        trade_side: str,
        outcome: str,
        price: float,
        trader_first_seen: Optional[str] = None,
        trader_trade_count: Optional[int] = None,
        market_volume: Optional[float] = None,  # Total market volume
    ) -> Optional[InsiderAlert]:
        """
        SIMPLIFIED: Just alert on any trade over $10,000.
        Returns an InsiderAlert if trade is large, None otherwise.
        """
        # Simple check: is this trade over our threshold?
        if trade_size < self.config.large_trade_threshold:
            return None
        
        # Determine severity based on size
        if trade_size >= 100000:
            severity = AlertSeverity.CRITICAL
        elif trade_size >= 50000:
            severity = AlertSeverity.HIGH
        elif trade_size >= 25000:
            severity = AlertSeverity.MEDIUM
        else:
            severity = AlertSeverity.LOW
        
        # Create simple alert
        alert = self._create_alert(
            severity=severity,
            market_id=market_id,
            market_question=market_question,
            trader_address=trader_address,
            trade_size=trade_size,
            trade_side=trade_side,
            outcome=outcome,
            price=price,
            reason=f"Large trade: ${trade_size:,.0f} {trade_side.upper()}",
            trader_profile=None,
        )
        
        return alert
    
    def check_volume_spike(
        self,
        market_id: str,
        market_question: str,
        current_volume: float,
        outcome: str = "Yes",
    ) -> Optional[InsiderAlert]:
        """Check if there's a volume spike in a market."""
        if not self.config.monitor_sudden_volume:
            return None
        
        if market_id not in self.market_volume_history:
            self.market_volume_history[market_id] = []
        
        history = self.market_volume_history[market_id]
        history.append(current_volume)
        
        # Keep last 24 data points
        if len(history) > 24:
            history = history[-24:]
            self.market_volume_history[market_id] = history
        
        # Need at least 5 data points for comparison
        if len(history) < 5:
            return None
        
        # Calculate average of previous volumes (excluding current)
        avg_volume = sum(history[:-1]) / len(history[:-1])
        
        if avg_volume > 0 and current_volume > avg_volume * self.config.volume_spike_multiplier:
            spike_ratio = current_volume / avg_volume
            alert = self._create_alert(
                severity=AlertSeverity.MEDIUM,
                market_id=market_id,
                market_question=market_question,
                trader_address="N/A",
                trade_size=current_volume,
                trade_side="volume",
                outcome=outcome,
                price=0.0,
                reason=f"Volume spike detected: {spike_ratio:.1f}x normal ({avg_volume:,.0f} → {current_volume:,.0f})",
            )
            return alert
        
        return None
    
    def _create_alert(
        self,
        severity: AlertSeverity,
        market_id: str,
        market_question: str,
        trader_address: str,
        trade_size: float,
        trade_side: str,
        outcome: str,
        price: float,
        reason: str,
        trader_profile: Optional[TraderProfile] = None,
    ) -> InsiderAlert:
        """Create and store a new alert."""
        alert = InsiderAlert(
            id=self._generate_alert_id(),
            timestamp=self._now_iso(),
            severity=severity,
            market_id=market_id,
            market_question=market_question,
            trader_address=trader_address,
            trade_size=trade_size,
            trade_side=trade_side,
            outcome=outcome,
            price=price,
            reason=reason,
            trader_profile=trader_profile,
        )
        
        with self._lock:
            self.alerts.append(alert)
            if len(self.alerts) > self.config.max_alerts_stored:
                self.alerts = self.alerts[-self.config.max_alerts_stored:]
        
        # Notify listeners
        for listener in self.listeners:
            try:
                listener(alert)
            except Exception:
                pass
        
        self._save()
        return alert
    
    def get_alerts(self, limit: int = 50, unacknowledged_only: bool = False) -> List[InsiderAlert]:
        """Get recent alerts."""
        alerts = self.alerts
        if unacknowledged_only:
            alerts = [a for a in alerts if not a.acknowledged]
        return list(reversed(alerts[-limit:]))
    
    def get_alerts_by_severity(self, severity: AlertSeverity, limit: int = 50) -> List[InsiderAlert]:
        """Get alerts filtered by severity."""
        filtered = [a for a in self.alerts if a.severity == severity]
        return list(reversed(filtered[-limit:]))
    
    def acknowledge_alert(self, alert_id: str) -> None:
        """Mark an alert as acknowledged."""
        for alert in self.alerts:
            if alert.id == alert_id:
                alert.acknowledged = True
                break
        self._save()
    
    def acknowledge_all(self) -> None:
        """Acknowledge all alerts."""
        for alert in self.alerts:
            alert.acknowledged = True
        self._save()
    
    def get_unacknowledged_count(self) -> int:
        """Get count of unacknowledged alerts."""
        return sum(1 for a in self.alerts if not a.acknowledged)
    
    def get_trader_profile(self, address: str) -> Optional[TraderProfile]:
        """Get a trader's profile."""
        return self.trader_profiles.get(address)
    
    def get_suspicious_traders(self, min_large_trades: int = 3) -> List[TraderProfile]:
        """Get traders with multiple large trades."""
        return [
            p for p in self.trader_profiles.values()
            if p.large_trades >= min_large_trades
        ]
    
    def _save(self) -> None:
        """Persist state to disk."""
        try:
            data = {
                "alerts": [a.to_dict() for a in self.alerts],
                "trader_profiles": {k: v.to_dict() for k, v in self.trader_profiles.items()},
                "monitored_markets": self.monitored_markets,
                "market_volume_history": self.market_volume_history,
            }
            self.storage_path.write_text(json.dumps(data, indent=2))
        except Exception:
            pass
    
    def _load(self) -> None:
        """Load state from disk."""
        try:
            if self.storage_path.exists():
                data = json.loads(self.storage_path.read_text())
                self.alerts = [InsiderAlert.from_dict(a) for a in data.get("alerts", [])]
                self.trader_profiles = {
                    k: TraderProfile.from_dict(v) 
                    for k, v in data.get("trader_profiles", {}).items()
                }
                self.monitored_markets = data.get("monitored_markets", {})
                self.market_volume_history = data.get("market_volume_history", {})
                self._alert_counter = len(self.alerts)
        except Exception:
            pass
    
    def clear_all(self) -> None:
        """Clear all alerts and data."""
        self.alerts = []
        self.trader_profiles = {}
        self.market_volume_history = {}
        self._alert_counter = 0
        self._save()
    
    def start_monitoring(self) -> None:
        """Start background monitoring thread."""
        if self._running:
            return
        self._running = True
        self._monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._monitor_thread.start()
    
    def stop_monitoring(self) -> None:
        """Stop background monitoring."""
        self._running = False
    
    def _monitor_loop(self) -> None:
        """Background loop to fetch and analyze trades."""
        print(f"[InsiderDetector] Starting monitoring loop...")
        while self._running:
            try:
                # First, auto-fetch active markets if we don't have enough
                self._auto_fetch_markets()
                print(f"[InsiderDetector] Monitoring {len(self.monitored_markets)} markets")
                
                # Then scan for suspicious activity
                self._scan_all_markets()
            except Exception as e:
                print(f"[InsiderDetector] Error in monitor loop: {e}")
            
            # Wait for next poll
            for _ in range(self.config.poll_interval_seconds):
                if not self._running:
                    break
                time.sleep(1)
    
    def _auto_fetch_markets(self) -> None:
        """Auto-fetch active markets to monitor - includes both popular and small markets."""
        try:
            from polymarket_api import GAMMA_API_BASE
            from datetime import datetime, timezone
            import json as json_module
            
            now = datetime.now(timezone.utc)
            
            # Fetch THREE sets of markets for comprehensive coverage:
            # 1. Popular markets by volume (for general monitoring)
            # 2. Recent markets (newer markets may be targets)
            # 3. Low liquidity markets (where insider trading is more likely)
            
            all_markets = []
            url = f"{GAMMA_API_BASE}/markets"
            
            # Fetch popular markets first
            params_popular = {
                "active": "true",
                "closed": "false",
                "limit": 50,
                "order": "volume24hr",
                "ascending": "false",
            }
            
            response = requests.get(url, params=params_popular, timeout=15)
            if response.ok:
                all_markets.extend(response.json())
            
            # Also fetch smaller/newer markets (sorted by creation date)
            params_recent = {
                "active": "true",
                "closed": "false",
                "limit": 50,
                "order": "startDate",
                "ascending": "false",
            }
            
            response = requests.get(url, params=params_recent, timeout=15)
            if response.ok:
                all_markets.extend(response.json())
            
            # Also fetch by liquidity ascending (smaller markets have less liquidity)
            params_liquidity = {
                "active": "true",
                "closed": "false",
                "limit": 50,
                "order": "liquidity",
                "ascending": "true",
            }
            
            response = requests.get(url, params=params_liquidity, timeout=15)
            if response.ok:
                all_markets.extend(response.json())
            
            for market in all_markets:
                market_id = market.get("slug") or str(market.get("id"))
                
                # Skip if already monitored
                if market_id in self.monitored_markets:
                    continue
                
                # Check end date is in future
                end_date_str = market.get("endDate")
                if end_date_str:
                    try:
                        if end_date_str.endswith('Z'):
                            end_dt = datetime.fromisoformat(end_date_str.replace('Z', '+00:00'))
                        else:
                            end_dt = datetime.fromisoformat(end_date_str)
                        if end_dt.tzinfo is None:
                            end_dt = end_dt.replace(tzinfo=timezone.utc)
                        if end_dt <= now:
                            continue
                    except:
                        continue
                
                # Get token ID
                token_ids = market.get("clobTokenIds")
                if not token_ids:
                    continue
                
                try:
                    if isinstance(token_ids, str):
                        token_ids = json_module.loads(token_ids)
                    token_id = str(token_ids[0]) if token_ids else None
                except:
                    continue
                
                if not token_id:
                    continue
                
                # Add to monitored markets
                question = market.get("question") or market.get("title", "Unknown")
                volume = float(market.get("volumeNum") or 0)
                
                self.monitored_markets[market_id] = {
                    "market_id": market_id,
                    "question": question,
                    "token_id": token_id,
                    "volume": volume,
                    "added_at": self._now_iso(),
                }
            
            self._save()
            
        except Exception:
            pass
    
    def _scan_all_markets(self) -> None:
        """SIMPLIFIED: Scan markets for trades over $10,000."""
        trades_analyzed = 0
        large_trades_found = 0
        
        for market_id, market_info in list(self.monitored_markets.items()):
            try:
                token_id = market_info.get("token_id")
                question = market_info.get("question", "Unknown")
                
                if not token_id:
                    continue
                
                # Fetch recent trades
                trades = fetch_recent_trades(token_id, limit=50)
                
                for trade in trades:
                    trade_size = float(trade.get("size", 0)) * float(trade.get("price", 0))
                    trade_price = float(trade.get("price", 0))
                    trades_analyzed += 1
                    
                    # Simple: only alert on trades >= $10,000
                    if trade_size >= self.config.large_trade_threshold:
                        large_trades_found += 1
                        self.analyze_trade(
                            market_id=market_id,
                            market_question=question,
                            trader_address=trade.get("maker", trade.get("taker", "unknown")),
                            trade_size=trade_size,
                            trade_side=trade.get("side", "buy"),
                            outcome=trade.get("outcome", "Yes"),
                            price=trade_price,
                        )
                        
            except Exception:
                continue
        
        if large_trades_found > 0:
            print(f"[InsiderDetector] Found {large_trades_found} large trades (>$10k) from {trades_analyzed} analyzed")


def fetch_recent_trades(token_id: str, limit: int = 100) -> List[Dict]:
    """
    Fetch recent trades for a token from Polymarket API.
    
    Tries multiple API endpoints to get trade data.
    """
    trades = []
    
    # Try the CLOB API trades endpoint first
    try:
        url = f"{CLOB_API_BASE}/trades"
        params = {"token_id": token_id, "limit": limit}
        response = requests.get(url, params=params, timeout=15)
        
        if response.status_code == 200:
            data = response.json()
            if isinstance(data, list):
                trades = data
            elif isinstance(data, dict) and "trades" in data:
                trades = data["trades"]
    except Exception:
        pass
    
    # Try alternative endpoint if first one fails
    if not trades:
        try:
            url = f"{CLOB_API_BASE}/order-book/trades"
            params = {"token_id": token_id, "limit": limit}
            response = requests.get(url, params=params, timeout=15)
            
            if response.status_code == 200:
                data = response.json()
                if isinstance(data, list):
                    trades = data
                elif isinstance(data, dict) and "trades" in data:
                    trades = data["trades"]
        except Exception:
            pass
    
    # Try the gamma API as another fallback
    if not trades:
        try:
            url = f"{GAMMA_API_BASE}/trades"
            params = {"asset_id": token_id, "limit": limit}
            response = requests.get(url, params=params, timeout=15)
            
            if response.status_code == 200:
                data = response.json()
                if isinstance(data, list):
                    trades = data
        except Exception:
            pass
    
    return trades


def analyze_order_book_for_large_orders(
    order_book: Dict,
    threshold: float = 10000.0,
) -> List[Dict]:
    """
    Analyze order book for large orders that might indicate insider activity.
    
    Returns list of suspicious orders.
    """
    suspicious = []
    
    for side in ["asks", "bids"]:
        orders = order_book.get(side, [])
        for price, size in orders:
            value = price * size
            if value >= threshold:
                suspicious.append({
                    "side": side,
                    "price": price,
                    "size": size,
                    "value": value,
                })
    
    return suspicious
