# Polymarket Trading Algorithm

An automated paper trading system for Polymarket prediction markets with real-time market analysis, insider detection, and portfolio management.

## Project Structure

```
Polymarket-trading-algorithm/
├── main.py                 # Main entry point
├── config.yaml             # Configuration file
│
├── Core Trading:
├── trading_bot_v2.py       # Main UI application (Tkinter GUI)
├── auto_trader.py          # Automated trading engine with G-score strategy
├── engine.py               # Core simulation/portfolio engine
├── polymarket_api.py       # API client for Polymarket
│
├── Analysis:
├── insider_detector.py     # Large trade detection system
├── news_analyzer.py        # News sentiment analysis
├── optimizer_core.py       # Kelly criterion optimizer
├── polymarket_optimizer.py # Portfolio optimization
│
├── State & Config:
├── config_manager.py       # YAML configuration loader
├── runtime_state.py        # Runtime state persistence
├── paper_trader.py         # Paper trading simulation
│
├── Integration:
├── cloud_sync.py           # Supabase cloud synchronization
├── log_manager.py          # CSV log export and cleanup
├── notification_manager.py # Notification system
│
├── UI Components:
├── ui_components.py        # Reusable UI widgets
│
├── data/                   # Runtime data files (JSON state)
│   ├── bot_state.json      # Auto-trader state
│   ├── insider_alerts.json # Detected insider activity
│   ├── notifications.json  # App notifications
│   ├── runtime_state.json  # UI runtime state
│   └── tracked_markets.json# Tracked market list
│
├── logs/                   # Log file output
│   ├── bot_activity/       # Bot activity CSV logs
│   ├── trade_log/          # Trade history logs
│   └── insider_alerts/     # Insider alert logs
│
├── scripts/                # Utility scripts
│   ├── headless_runner.py  # Run bot without UI
│   ├── setup_cloud_sync.py # Cloud sync setup wizard
│   └── test_api.py         # API connectivity tests
│
├── sql/                    # Database schemas
│   └── create_tables.sql   # Supabase table definitions
│
└── src/                    # Modular source (future refactor)
    ├── api/                # API modules
    ├── core/               # Core logic
    ├── config/             # Configuration
    ├── state/              # State management
    ├── analysis/           # Analysis modules
    ├── sync/               # Sync modules
    ├── notifications/      # Notification modules
    └── ui/                 # UI components
```

## Quick Start

### Prerequisites
- Python 3.11+
- Required packages: `requests`, `pyyaml`, `tkinter`
- Optional: `supabase` (for cloud sync)

### Installation

```bash
# Clone or download the project
cd Polymarket-trading-algorithm

# Install dependencies
pip install requests pyyaml

# Optional: Install cloud sync support
pip install supabase
```

### Running the Bot

```bash
# Start the GUI
python main.py

# Or run directly
python trading_bot_v2.py

# Headless mode (no UI, lower resource usage)
python scripts/headless_runner.py

# Run API tests
python main.py --test
```

## Configuration

Edit `config.yaml` to customize:

- **Trading parameters**: Position sizes, intervals, thresholds
- **Risk management**: Circuit breakers, exposure limits
- **Market policies**: Per-market trading rules
- **Cloud sync**: Supabase credentials for syncing with friends

## Trading Strategy

The bot uses a **G-Score** strategy to find high-value opportunities:

```
G = ln(1 + expected_return) / (days_to_resolution + λ)
```

Where:
- **expected_return**: Potential profit based on price deviation from fair value
- **days_to_resolution**: Time until market resolves
- **λ (lambda)**: Smoothing factor to prevent division by zero

### Trade Types
- **Swing trades**: Short-term opportunities (< 30 days)
- **Long trades**: Longer-term positions (30+ days)

## Features

### Market Analysis
- Real-time price monitoring via Polymarket API
- Volume and liquidity tracking
- Price change detection

### Insider Detection
- Monitors for unusually large trades
- Tracks significant price movements
- Alerts on suspicious activity patterns

### News Analysis
- Fetches relevant news for tracked markets
- Sentiment scoring using keyword analysis
- Integration with market decisions

### Portfolio Management
- Paper trading simulation
- P&L tracking with 2% fee calculation
- Position sizing with Kelly criterion

### Cloud Sync (Optional)
- Share portfolio state with friends via Supabase
- Real-time sync of trades and positions
- Multi-instance coordination

## API Endpoints

The bot uses two Polymarket APIs:
- **GAMMA API**: `https://gamma-api.polymarket.com` - Market data
- **CLOB API**: `https://clob.polymarket.com` - Order book data

## Development

### Adding New Features

1. Core logic goes in root-level `.py` files
2. Utility scripts go in `scripts/`
3. Data files are stored in `data/`
4. Logs are written to `logs/`

### Module Structure (src/)

The `src/` directory contains modular versions of components for future refactoring. Currently, the root-level files are the primary implementation.

## License

This project is for educational and personal use only. Paper trading simulation does not involve real money.

## Disclaimer

⚠️ **This is a paper trading simulation only.** No real trades are executed. This software is provided as-is for educational purposes. Always do your own research before making any investment decisions.
