"""
Log Manager - Handles CSV export and memory cleanup for trading bot logs.
Exports logs hourly and cleans up old in-memory entries to save RAM.
"""

import os
import csv
import json
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Any
from dataclasses import dataclass
from pathlib import Path

_SCRIPT_DIR = Path(__file__).parent


@dataclass
class LogManagerConfig:
    """Configuration for log manager."""
    logs_directory: str = str(_SCRIPT_DIR / "logs")
    export_interval_minutes: int = 60  # Export every hour
    max_memory_entries: int = 100  # Keep max entries in memory
    max_log_files: int = 168  # Keep 7 days of hourly files


class LogManager:
    """Manages log export to CSV and memory cleanup."""
    
    def __init__(self, config: Optional[LogManagerConfig] = None):
        self.config = config or LogManagerConfig()
        self.logs_dir = Path(self.config.logs_directory)
        self.logs_dir.mkdir(exist_ok=True)
        
        # Track last export time
        self.last_export_time = datetime.now()
        
        # Create subdirectories
        (self.logs_dir / "bot_activity").mkdir(exist_ok=True)
        (self.logs_dir / "trade_log").mkdir(exist_ok=True)
        (self.logs_dir / "insider_alerts").mkdir(exist_ok=True)
        
    def _get_timestamp_filename(self, prefix: str) -> str:
        """Generate timestamped filename."""
        return f"{prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    
    def export_bot_activity(self, messages: List[Dict]) -> str:
        """Export bot activity messages to CSV."""
        if not messages:
            return ""
            
        filepath = self.logs_dir / "bot_activity" / self._get_timestamp_filename("activity")
        
        with open(filepath, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=['timestamp', 'type', 'title', 'message'])
            writer.writeheader()
            for msg in messages:
                writer.writerow({
                    'timestamp': msg.get('timestamp', ''),
                    'type': msg.get('type', 'info'),
                    'title': msg.get('title', ''),
                    'message': msg.get('message', ''),
                })
        
        print(f"[LogManager] Exported {len(messages)} activity entries to {filepath}")
        return str(filepath)
    
    def export_trade_log(self, trades: List[Dict]) -> str:
        """Export trade log to CSV."""
        if not trades:
            return ""
            
        filepath = self.logs_dir / "trade_log" / self._get_timestamp_filename("trades")
        
        with open(filepath, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=[
                'timestamp', 'action', 'question', 'amount', 'price', 'pnl', 'result'
            ])
            writer.writeheader()
            for trade in trades:
                writer.writerow({
                    'timestamp': trade.get('timestamp', ''),
                    'action': trade.get('action', ''),
                    'question': trade.get('question', ''),
                    'amount': trade.get('amount', 0),
                    'price': trade.get('price', 0),
                    'pnl': trade.get('pnl', ''),
                    'result': trade.get('result', ''),
                })
        
        print(f"[LogManager] Exported {len(trades)} trades to {filepath}")
        return str(filepath)
    
    def export_insider_alerts(self, alerts: List[Dict]) -> str:
        """Export insider alerts to CSV."""
        if not alerts:
            return ""
            
        filepath = self.logs_dir / "insider_alerts" / self._get_timestamp_filename("alerts")
        
        with open(filepath, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=[
                'timestamp', 'market_question', 'trade_size', 'trade_side', 
                'outcome', 'price', 'severity', 'reason'
            ])
            writer.writeheader()
            for alert in alerts:
                writer.writerow({
                    'timestamp': alert.get('timestamp', ''),
                    'market_question': alert.get('market_question', ''),
                    'trade_size': alert.get('trade_size', 0),
                    'trade_side': alert.get('trade_side', ''),
                    'outcome': alert.get('outcome', ''),
                    'price': alert.get('price', 0),
                    'severity': alert.get('severity', ''),
                    'reason': alert.get('reason', ''),
                })
        
        print(f"[LogManager] Exported {len(alerts)} alerts to {filepath}")
        return str(filepath)
    
    def should_export(self) -> bool:
        """Check if it's time for periodic export."""
        elapsed = datetime.now() - self.last_export_time
        return elapsed >= timedelta(minutes=self.config.export_interval_minutes)
    
    def cleanup_old_files(self) -> int:
        """Remove old log files beyond retention period."""
        removed_count = 0
        
        for subdir in ["bot_activity", "trade_log", "insider_alerts"]:
            folder = self.logs_dir / subdir
            if not folder.exists():
                continue
                
            files = sorted(folder.glob("*.csv"), key=lambda x: x.stat().st_mtime)
            
            # Keep only the most recent files
            while len(files) > self.config.max_log_files:
                old_file = files.pop(0)
                try:
                    old_file.unlink()
                    removed_count += 1
                except Exception as e:
                    print(f"[LogManager] Failed to delete {old_file}: {e}")
        
        if removed_count > 0:
            print(f"[LogManager] Cleaned up {removed_count} old log files")
        return removed_count
    
    def trim_list_to_max(self, data: List, max_entries: Optional[int] = None) -> List:
        """Trim a list to max entries, keeping the most recent."""
        max_size = max_entries or self.config.max_memory_entries
        if len(data) > max_size:
            return data[-max_size:]
        return data
    
    def perform_export_cycle(self, bot_activity: List[Dict], trade_log: List[Dict], 
                              insider_alerts: List[Dict]) -> Dict[str, str]:
        """
        Perform a full export cycle - export all logs to CSV and cleanup.
        Returns dict of exported file paths.
        """
        exports = {}
        
        if bot_activity:
            exports['bot_activity'] = self.export_bot_activity(bot_activity)
        
        if trade_log:
            exports['trade_log'] = self.export_trade_log(trade_log)
        
        if insider_alerts:
            exports['insider_alerts'] = self.export_insider_alerts(insider_alerts)
        
        # Update last export time
        self.last_export_time = datetime.now()
        
        # Cleanup old files
        self.cleanup_old_files()
        
        return exports
    
    def get_combined_trade_history(self, days: int = 7) -> List[Dict]:
        """Load and combine trade history from all CSV files."""
        trades = []
        folder = self.logs_dir / "trade_log"
        
        if not folder.exists():
            return trades
        
        cutoff = datetime.now() - timedelta(days=days)
        
        for csv_file in folder.glob("*.csv"):
            try:
                # Skip files older than cutoff
                if datetime.fromtimestamp(csv_file.stat().st_mtime) < cutoff:
                    continue
                    
                with open(csv_file, 'r', encoding='utf-8') as f:
                    reader = csv.DictReader(f)
                    trades.extend(list(reader))
            except Exception as e:
                print(f"[LogManager] Error reading {csv_file}: {e}")
        
        return trades
    
    def get_stats_summary(self) -> Dict[str, Any]:
        """Get summary stats from log files."""
        stats = {
            'total_trades': 0,
            'total_pnl': 0.0,
            'wins': 0,
            'losses': 0,
            'total_alerts': 0,
        }
        
        # Count trade log files
        trade_folder = self.logs_dir / "trade_log"
        if trade_folder.exists():
            trades = self.get_combined_trade_history(days=30)
            stats['total_trades'] = len(trades)
            
            for trade in trades:
                try:
                    pnl = float(trade.get('pnl', 0) or 0)
                    stats['total_pnl'] += pnl
                    if trade.get('result') == 'WIN':
                        stats['wins'] += 1
                    elif trade.get('result') == 'LOSS':
                        stats['losses'] += 1
                except:
                    pass
        
        # Count alert files
        alert_folder = self.logs_dir / "insider_alerts"
        if alert_folder.exists():
            stats['total_alerts'] = sum(1 for _ in alert_folder.glob("*.csv"))
        
        return stats


# Singleton instance
_log_manager: Optional[LogManager] = None


def get_log_manager() -> LogManager:
    """Get or create the global log manager instance."""
    global _log_manager
    if _log_manager is None:
        _log_manager = LogManager()
    return _log_manager
