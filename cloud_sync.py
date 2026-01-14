"""
Cloud Sync Module for Polymarket Trading Bot
=============================================
Enables real-time data synchronization between multiple users using Supabase.

Setup:
1. Create a free account at https://supabase.com
2. Create a new project
3. Run the SQL schema (see create_tables.sql)
4. Copy your project URL and anon key to config.yaml
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Any
from dataclasses import asdict
import threading
import time

# Try to import supabase
try:
    from supabase import create_client, Client
    SUPABASE_AVAILABLE = True
except ImportError:
    SUPABASE_AVAILABLE = False
    print("⚠️  Supabase not installed. Run: pip install supabase")

import yaml


class CloudSync:
    """Handles synchronization of bot state with Supabase cloud database."""
    
    def __init__(self, config_path: Optional[Path] = None):
        self.config_path = config_path or Path(__file__).parent / "config.yaml"
        self.client: Optional[Client] = None
        self.enabled = False
        self.bot_instance_id: str = "default"  # Can be used to run multiple bots
        self._last_sync: Optional[datetime] = None
        self._sync_lock = threading.Lock()
        
        self._load_config()
    
    def _load_config(self) -> None:
        """Load Supabase configuration from config.yaml."""
        if not SUPABASE_AVAILABLE:
            print("⚠️  Cloud sync disabled - supabase package not installed")
            return
        
        try:
            if self.config_path.exists():
                with open(self.config_path, 'r') as f:
                    config = yaml.safe_load(f) or {}
                
                cloud_config = config.get('cloud_sync', {})
                
                if not cloud_config.get('enabled', False):
                    print("ℹ️  Cloud sync disabled in config")
                    return
                
                url = cloud_config.get('supabase_url', '')
                key = cloud_config.get('supabase_key', '')
                self.bot_instance_id = cloud_config.get('bot_instance_id', 'default')
                
                if url and key and url != 'YOUR_SUPABASE_URL' and key != 'YOUR_SUPABASE_ANON_KEY':
                    self.client = create_client(url, key)
                    self.enabled = True
                    print(f"✅ Cloud sync enabled (instance: {self.bot_instance_id})")
                else:
                    print("⚠️  Cloud sync not configured - add Supabase credentials to config.yaml")
            else:
                print("⚠️  config.yaml not found - cloud sync disabled")
        except Exception as e:
            print(f"⚠️  Failed to initialize cloud sync: {e}")
            self.enabled = False
    
    def is_enabled(self) -> bool:
        """Check if cloud sync is enabled and working."""
        return self.enabled and self.client is not None
    
    # =========================================================================
    # Save Operations
    # =========================================================================
    
    def save_state(self, state: Dict[str, Any]) -> bool:
        """Save the complete bot state to the cloud."""
        if not self.is_enabled():
            return False
        
        with self._sync_lock:
            try:
                now = datetime.now(timezone.utc).isoformat()
                
                # 1. Save main bot state
                bot_state_data = {
                    'instance_id': self.bot_instance_id,
                    'cash_balance': state.get('cash_balance', 0),
                    'total_trades': state.get('total_trades', 0),
                    'winning_trades': state.get('winning_trades', 0),
                    'losing_trades': state.get('losing_trades', 0),
                    'total_pnl': state.get('total_pnl', 0),
                    'trade_counter': state.get('trade_counter', 0),
                    'updated_at': now,
                }
                
                # Upsert bot state (insert or update)
                self.client.table('bot_state').upsert(
                    bot_state_data, 
                    on_conflict='instance_id'
                ).execute()
                
                # 2. Sync open trades
                self._sync_open_trades(state.get('open_trades', {}))
                
                # 3. Sync closed trades (only new ones)
                self._sync_closed_trades(state.get('closed_trades', []))
                
                # 4. Save trade log (last 100 entries)
                self._sync_trade_log(state.get('trade_log', []))
                
                # 5. Save market categories
                self._sync_market_categories(state.get('market_categories', {}))
                
                # 6. Save blacklist
                self._sync_blacklist(state.get('blacklist', []))
                
                self._last_sync = datetime.now(timezone.utc)
                return True
                
            except Exception as e:
                print(f"⚠️  Cloud sync save failed: {e}")
                return False
    
    def _sync_open_trades(self, open_trades: Dict[str, Any]) -> None:
        """Sync open trades to cloud - handles additions and removals."""
        try:
            # Get current cloud open trades
            result = self.client.table('open_trades').select('trade_id').eq(
                'instance_id', self.bot_instance_id
            ).execute()
            
            cloud_trade_ids = {row['trade_id'] for row in result.data}
            local_trade_ids = set(open_trades.keys())
            
            # Delete trades that are no longer open
            trades_to_delete = cloud_trade_ids - local_trade_ids
            if trades_to_delete:
                self.client.table('open_trades').delete().eq(
                    'instance_id', self.bot_instance_id
                ).in_('trade_id', list(trades_to_delete)).execute()
            
            # Upsert all current open trades
            if open_trades:
                trades_data = []
                for trade_id, trade in open_trades.items():
                    trade_data = {
                        'instance_id': self.bot_instance_id,
                        'trade_id': trade_id,
                        'data': json.dumps(trade) if isinstance(trade, dict) else json.dumps(trade),
                        'updated_at': datetime.now(timezone.utc).isoformat(),
                    }
                    trades_data.append(trade_data)
                
                self.client.table('open_trades').upsert(
                    trades_data,
                    on_conflict='instance_id,trade_id'
                ).execute()
                
        except Exception as e:
            print(f"⚠️  Failed to sync open trades: {e}")
    
    def _sync_closed_trades(self, closed_trades: List[Any]) -> None:
        """Sync closed trades to cloud - append only, no duplicates."""
        try:
            if not closed_trades:
                return
            
            # Get existing closed trade IDs
            result = self.client.table('closed_trades').select('trade_id').eq(
                'instance_id', self.bot_instance_id
            ).execute()
            
            existing_ids = {row['trade_id'] for row in result.data}
            
            # Insert only new closed trades
            new_trades = []
            for trade in closed_trades:
                trade_dict = trade if isinstance(trade, dict) else trade
                trade_id = trade_dict.get('id', '')
                
                if trade_id and trade_id not in existing_ids:
                    new_trades.append({
                        'instance_id': self.bot_instance_id,
                        'trade_id': trade_id,
                        'data': json.dumps(trade_dict),
                        'closed_at': trade_dict.get('exit_timestamp', datetime.now(timezone.utc).isoformat()),
                    })
            
            if new_trades:
                self.client.table('closed_trades').insert(new_trades).execute()
                
        except Exception as e:
            print(f"⚠️  Failed to sync closed trades: {e}")
    
    def _sync_trade_log(self, trade_log: List[Dict]) -> None:
        """Sync trade log to cloud."""
        try:
            # Replace all trade log entries for this instance
            self.client.table('trade_log').delete().eq(
                'instance_id', self.bot_instance_id
            ).execute()
            
            if trade_log:
                log_entries = [{
                    'instance_id': self.bot_instance_id,
                    'data': json.dumps(entry),
                    'timestamp': entry.get('timestamp', datetime.now(timezone.utc).isoformat()),
                } for entry in trade_log[-100:]]  # Keep last 100
                
                self.client.table('trade_log').insert(log_entries).execute()
                
        except Exception as e:
            print(f"⚠️  Failed to sync trade log: {e}")
    
    def _sync_market_categories(self, categories: Dict[str, str]) -> None:
        """Sync market categories to cloud."""
        try:
            # Replace all categories for this instance
            self.client.table('market_categories').delete().eq(
                'instance_id', self.bot_instance_id
            ).execute()
            
            if categories:
                cat_entries = [{
                    'instance_id': self.bot_instance_id,
                    'market_id': market_id,
                    'category': category,
                } for market_id, category in categories.items()]
                
                # Insert in batches of 500
                batch_size = 500
                for i in range(0, len(cat_entries), batch_size):
                    batch = cat_entries[i:i + batch_size]
                    self.client.table('market_categories').insert(batch).execute()
                
        except Exception as e:
            print(f"⚠️  Failed to sync market categories: {e}")
    
    def _sync_blacklist(self, blacklist: List[str]) -> None:
        """Sync blacklist to cloud."""
        try:
            # Replace blacklist for this instance
            self.client.table('blacklist').delete().eq(
                'instance_id', self.bot_instance_id
            ).execute()
            
            if blacklist:
                entries = [{
                    'instance_id': self.bot_instance_id,
                    'market_id': market_id,
                } for market_id in blacklist]
                
                self.client.table('blacklist').insert(entries).execute()
                
        except Exception as e:
            print(f"⚠️  Failed to sync blacklist: {e}")
    
    # =========================================================================
    # Load Operations
    # =========================================================================
    
    def load_state(self) -> Optional[Dict[str, Any]]:
        """Load the complete bot state from the cloud."""
        if not self.is_enabled():
            return None
        
        with self._sync_lock:
            try:
                state = {}
                
                # 1. Load main bot state
                result = self.client.table('bot_state').select('*').eq(
                    'instance_id', self.bot_instance_id
                ).execute()
                
                if result.data:
                    row = result.data[0]
                    state['cash_balance'] = row.get('cash_balance', 10000)
                    state['total_trades'] = row.get('total_trades', 0)
                    state['winning_trades'] = row.get('winning_trades', 0)
                    state['losing_trades'] = row.get('losing_trades', 0)
                    state['total_pnl'] = row.get('total_pnl', 0)
                    state['trade_counter'] = row.get('trade_counter', 0)
                else:
                    # No state found, return None to use defaults
                    return None
                
                # 2. Load open trades
                state['open_trades'] = self._load_open_trades()
                
                # 3. Load closed trades
                state['closed_trades'] = self._load_closed_trades()
                
                # 4. Load trade log
                state['trade_log'] = self._load_trade_log()
                
                # 5. Load market categories
                state['market_categories'] = self._load_market_categories()
                
                # 6. Load blacklist
                state['blacklist'] = self._load_blacklist()
                
                self._last_sync = datetime.now(timezone.utc)
                return state
                
            except Exception as e:
                print(f"⚠️  Cloud sync load failed: {e}")
                return None
    
    def _load_open_trades(self) -> Dict[str, Any]:
        """Load open trades from cloud."""
        try:
            result = self.client.table('open_trades').select('trade_id, data').eq(
                'instance_id', self.bot_instance_id
            ).execute()
            
            trades = {}
            for row in result.data:
                trade_id = row['trade_id']
                data = json.loads(row['data']) if isinstance(row['data'], str) else row['data']
                trades[trade_id] = data
            
            return trades
            
        except Exception as e:
            print(f"⚠️  Failed to load open trades: {e}")
            return {}
    
    def _load_closed_trades(self) -> List[Any]:
        """Load closed trades from cloud."""
        try:
            result = self.client.table('closed_trades').select('data').eq(
                'instance_id', self.bot_instance_id
            ).order('closed_at', desc=True).execute()
            
            trades = []
            for row in result.data:
                data = json.loads(row['data']) if isinstance(row['data'], str) else row['data']
                trades.append(data)
            
            return list(reversed(trades))  # Oldest first
            
        except Exception as e:
            print(f"⚠️  Failed to load closed trades: {e}")
            return []
    
    def _load_trade_log(self) -> List[Dict]:
        """Load trade log from cloud."""
        try:
            result = self.client.table('trade_log').select('data').eq(
                'instance_id', self.bot_instance_id
            ).order('timestamp', desc=False).execute()
            
            log = []
            for row in result.data:
                data = json.loads(row['data']) if isinstance(row['data'], str) else row['data']
                log.append(data)
            
            return log
            
        except Exception as e:
            print(f"⚠️  Failed to load trade log: {e}")
            return []
    
    def _load_market_categories(self) -> Dict[str, str]:
        """Load market categories from cloud."""
        try:
            result = self.client.table('market_categories').select('market_id, category').eq(
                'instance_id', self.bot_instance_id
            ).execute()
            
            return {row['market_id']: row['category'] for row in result.data}
            
        except Exception as e:
            print(f"⚠️  Failed to load market categories: {e}")
            return {}
    
    def _load_blacklist(self) -> List[str]:
        """Load blacklist from cloud."""
        try:
            result = self.client.table('blacklist').select('market_id').eq(
                'instance_id', self.bot_instance_id
            ).execute()
            
            return [row['market_id'] for row in result.data]
            
        except Exception as e:
            print(f"⚠️  Failed to load blacklist: {e}")
            return []
    
    # =========================================================================
    # Utility Methods
    # =========================================================================
    
    def get_last_sync_time(self) -> Optional[datetime]:
        """Get the timestamp of the last successful sync."""
        return self._last_sync
    
    def test_connection(self) -> bool:
        """Test the connection to Supabase."""
        if not self.is_enabled():
            return False
        
        try:
            # Try a simple query
            self.client.table('bot_state').select('instance_id').limit(1).execute()
            return True
        except Exception as e:
            print(f"⚠️  Connection test failed: {e}")
            return False


# Singleton instance for easy access
_cloud_sync_instance: Optional[CloudSync] = None


def get_cloud_sync() -> CloudSync:
    """Get the singleton CloudSync instance."""
    global _cloud_sync_instance
    if _cloud_sync_instance is None:
        _cloud_sync_instance = CloudSync()
    return _cloud_sync_instance


def init_cloud_sync(config_path: Optional[Path] = None) -> CloudSync:
    """Initialize cloud sync with a custom config path."""
    global _cloud_sync_instance
    _cloud_sync_instance = CloudSync(config_path)
    return _cloud_sync_instance
