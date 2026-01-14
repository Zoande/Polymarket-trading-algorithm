"""
Headless Trading Bot Runner
============================
Runs the trading bot without UI for minimal resource usage (overnight mode).

Usage:
    python headless_runner.py    # Interactive setup prompts

IMPORTANT: Do not run this while the UI (trading_bot_v2.py) is running!
           Both use the same bot_state.json file.
"""

import sys
import time
import signal
import os
from pathlib import Path
from datetime import datetime, timezone

# Ensure we're running from the correct directory
SCRIPT_DIR = Path(__file__).parent.resolve()
os.chdir(SCRIPT_DIR)

# Paths (relative to script directory)
LOCK_FILE = SCRIPT_DIR / ".bot_running.lock"
BOT_STATE_PATH = SCRIPT_DIR / "bot_state.json"

def check_lock():
    """Check if another instance is running."""
    if LOCK_FILE.exists():
        try:
            with open(LOCK_FILE, 'r') as f:
                info = f.read().strip()
            print(f"\n⚠️  ERROR: Another bot instance appears to be running!")
            print(f"   Lock info: {info}")
            print(f"\n   If this is incorrect, delete: {LOCK_FILE}")
            return False
        except:
            pass
    return True

def create_lock(mode: str):
    """Create lock file."""
    with open(LOCK_FILE, 'w') as f:
        f.write(f"{mode} started at {datetime.now().isoformat()}")

def remove_lock():
    """Remove lock file."""
    try:
        LOCK_FILE.unlink()
    except:
        pass

# Import trading components
try:
    from auto_trader import AutoTradingBot, BotConfig, BotTrade
except ImportError as e:
    print(f"❌ Failed to import auto_trader: {e}")
    print(f"   Current directory: {os.getcwd()}")
    print(f"   Script directory: {SCRIPT_DIR}")
    print("   Make sure you're running from the Polymarket-trading-algorithm folder.")
    input("\nPress Enter to exit...")
    sys.exit(1)

# Try to import news analyzer
try:
    from news_analyzer import NewsAnalyzer
    NEWS_AVAILABLE = True
except ImportError:
    NEWS_AVAILABLE = False


class HeadlessRunner:
    """Minimal headless runner for the trading bot."""
    
    def __init__(self, settings: dict):
        self.settings = settings
        self.bot: AutoTradingBot = None
        self._running = False
        self._last_status = 0
        self._last_value_update = 0
        self._trade_count = 0
        
    def _log(self, message: str, level: str = "info"):
        """Simple console logging."""
        if self.settings['quiet'] and level == "info":
            return
            
        timestamp = datetime.now().strftime("%H:%M:%S")
        prefix = {
            "info": "ℹ️ ",
            "success": "✅",
            "alert": "⚠️ ",
            "error": "❌",
            "trade": "💰",
            "value": "📊",
        }.get(level, "  ")
        
        print(f"[{timestamp}] {prefix} {message}")
    
    def _on_message(self, message: str, msg_type: str):
        """Handle bot messages."""
        if self.settings['quiet']:
            # In quiet mode, only show errors
            if msg_type != "error":
                return
        
        self._log(message, msg_type)
    
    def _on_trade(self, trade: BotTrade):
        """Handle trade events."""
        if not self.settings['show_trades']:
            return
            
        self._trade_count += 1
        action = "OPENED" if trade.status == "open" else "CLOSED"
        pnl_str = ""
        if trade.status == "closed":
            pnl_str = f" | P&L: ${trade.pnl:+.2f} ({trade.pnl_pct:+.1%})"
        
        self._log(
            f"{action}: {trade.outcome} @ ${trade.entry_price:.3f} "
            f"(${trade.cost_basis:.2f}){pnl_str}",
            "trade"
        )
    
    def _get_portfolio_value(self) -> float:
        """Calculate total portfolio value."""
        if not self.bot:
            return 0
        total = self.bot.cash_balance
        for trade in self.bot.open_trades.values():
            total += trade.value
        return total
    
    def _print_value(self):
        """Print just the portfolio value."""
        value = self._get_portfolio_value()
        self._log(f"Portfolio Value: ${value:,.2f}", "value")
    
    def _print_status(self):
        """Print full bot status."""
        if not self.bot:
            return
            
        positions = len(self.bot.open_trades)
        total_value = self._get_portfolio_value()
        
        # Calculate stats
        total_pnl = 0
        profitable = 0
        losing = 0
        
        for trade in self.bot.open_trades.values():
            total_pnl += trade.pnl
            if trade.pnl >= 0:
                profitable += 1
            else:
                losing += 1
        
        win_rate = (self.bot.winning_trades / max(1, self.bot.winning_trades + self.bot.losing_trades)) * 100
        
        print("\n" + "=" * 60)
        print(f"📊 STATUS UPDATE - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("=" * 60)
        print(f"   Portfolio Value:  ${total_value:,.2f}")
        print(f"   Cash Balance:     ${self.bot.cash_balance:,.2f}")
        print(f"   Open Positions:   {positions}/{self.bot.config.max_positions}")
        print(f"   Unrealized P&L:   ${total_pnl:+,.2f}")
        print(f"   Profitable/Losing: {profitable}/{losing}")
        print(f"   Session Trades:   {self._trade_count}")
        print(f"   All-time Win Rate: {win_rate:.1f}%")
        print(f"   Total Realized:   ${self.bot.total_pnl:+,.2f}")
        print("=" * 60 + "\n")
    
    def start(self):
        """Start the headless bot."""
        if not check_lock():
            return False
        
        self._log("Starting headless trading bot...", "info")
        self._log(f"Working directory: {SCRIPT_DIR}", "info")
        self._log(f"State file: {BOT_STATE_PATH}", "info")
        
        # Create bot
        self.bot = AutoTradingBot(
            config=BotConfig(
                initial_capital=10000.0,
                max_position_size=500.0,
                min_volume=500.0,
                scan_interval_seconds=30,
                max_markets_per_scan=100,
                price_update_interval=5,
                max_positions=50,
                swing_trade_enabled=True,
                prefer_high_volume=False,
                use_news_analysis=NEWS_AVAILABLE,
            ),
            storage_path=BOT_STATE_PATH,
            on_trade=self._on_trade,
            on_message=self._on_message,
        )
        
        # Show initial status
        positions = len(self.bot.open_trades)
        self._log(f"Loaded {positions} existing positions", "success")
        self._log(f"Portfolio value: ${self._get_portfolio_value():,.2f}", "info")
        
        if self.settings['show_full_status']:
            self._print_status()
        
        # Create lock
        create_lock("headless_runner.py")
        
        # Start trading
        self.bot.start()
        self._running = True
        self._last_status = time.time()
        self._last_value_update = time.time()
        
        self._log("Bot is now running. Press Ctrl+C to stop.", "success")
        
        return True
    
    def run(self):
        """Main loop."""
        try:
            while self._running:
                time.sleep(1)
                
                now = time.time()
                
                # Portfolio value update (simple one-liner)
                if self.settings['value_interval'] > 0:
                    if now - self._last_value_update >= self.settings['value_interval']:
                        self._print_value()
                        self._last_value_update = now
                
                # Full status update
                if self.settings['show_full_status'] and self.settings['status_interval'] > 0:
                    if now - self._last_status >= self.settings['status_interval']:
                        self._print_status()
                        self._last_status = now
                    
        except KeyboardInterrupt:
            self._log("\nShutdown requested...", "alert")
        finally:
            self.stop()
    
    def stop(self):
        """Stop the bot gracefully."""
        self._running = False
        
        if self.bot:
            self._log("Stopping trading bot...", "info")
            self.bot.stop()
            
            # Final status
            if self.settings['show_full_status']:
                self._print_status()
            else:
                self._print_value()
            
            self._log("Bot stopped. State saved.", "success")
        
        remove_lock()


def get_input(prompt: str, default: str = "") -> str:
    """Get input with default value."""
    if default:
        result = input(f"{prompt} [{default}]: ").strip()
        return result if result else default
    return input(f"{prompt}: ").strip()


def get_yes_no(prompt: str, default: bool = True) -> bool:
    """Get yes/no input."""
    default_str = "Y/n" if default else "y/N"
    result = input(f"{prompt} ({default_str}): ").strip().lower()
    if not result:
        return default
    return result in ('y', 'yes', '1', 'true')


def get_int(prompt: str, default: int) -> int:
    """Get integer input."""
    result = input(f"{prompt} [{default}]: ").strip()
    if not result:
        return default
    try:
        return int(result)
    except ValueError:
        print(f"   Invalid number, using default: {default}")
        return default


def interactive_setup() -> dict:
    """Interactive setup prompts."""
    print("""
╔═══════════════════════════════════════════════════════════════╗
║         HEADLESS TRADING BOT - Setup                          ║
╚═══════════════════════════════════════════════════════════════╝
    """)
    
    print("Configure your headless session:\n")
    
    settings = {}
    
    # Quiet mode
    settings['quiet'] = get_yes_no(
        "1. Quiet mode? (less output, only important messages)", 
        default=False
    )
    
    # Show trades
    settings['show_trades'] = get_yes_no(
        "2. Show trade notifications? (when positions open/close)", 
        default=True
    )
    
    # Portfolio value updates
    print("\n3. Portfolio value updates (simple one-line value display)")
    value_interval = get_int(
        "   Interval in seconds (0 = disabled)", 
        default=60
    )
    settings['value_interval'] = max(0, value_interval)
    
    # Full status updates  
    settings['show_full_status'] = get_yes_no(
        "\n4. Show full status updates? (detailed stats table)", 
        default=True
    )
    
    if settings['show_full_status']:
        status_interval = get_int(
            "   Full status interval in seconds", 
            default=300
        )
        settings['status_interval'] = max(60, status_interval)
    else:
        settings['status_interval'] = 0
    
    # Summary
    print("\n" + "-" * 50)
    print("📋 Settings Summary:")
    print(f"   • Quiet mode: {'Yes' if settings['quiet'] else 'No'}")
    print(f"   • Trade notifications: {'Yes' if settings['show_trades'] else 'No'}")
    if settings['value_interval'] > 0:
        print(f"   • Portfolio value update: Every {settings['value_interval']}s")
    else:
        print(f"   • Portfolio value update: Disabled")
    if settings['show_full_status']:
        print(f"   • Full status update: Every {settings['status_interval']}s")
    else:
        print(f"   • Full status update: Disabled")
    print("-" * 50)
    
    if not get_yes_no("\nStart with these settings?", default=True):
        print("Cancelled.")
        sys.exit(0)
    
    return settings


def main():
    print("""
╔═══════════════════════════════════════════════════════════════╗
║         HEADLESS TRADING BOT - Low Resource Mode              ║
╠═══════════════════════════════════════════════════════════════╣
║  • No UI overhead                                             ║
║  • Same trading logic as GUI version                          ║
║  • State saved to bot_state.json                              ║
║  • Press Ctrl+C to stop gracefully                            ║
║                                                               ║
║  ⚠️  If you close the terminal without Ctrl+C, delete         ║
║     .bot_running.lock before restarting                       ║
╚═══════════════════════════════════════════════════════════════╝
    """)
    
    print(f"📂 Working directory: {SCRIPT_DIR}\n")
    
    # Interactive setup
    settings = interactive_setup()
    
    print("\n")
    
    runner = HeadlessRunner(settings)
    
    # Handle Ctrl+C gracefully
    def signal_handler(sig, frame):
        runner.stop()
        sys.exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    if runner.start():
        runner.run()


if __name__ == "__main__":
    main()

