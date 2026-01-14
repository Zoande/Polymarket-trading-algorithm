"""Test P&L tracking with fixed API."""
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

import requests
from auto_trader import AutoTradingBot, BotConfig, BotTrade, GAMMA_API_BASE
from datetime import datetime, timezone
import json

# First, let's find a real market with a reasonable price
print("=== Finding a real market ===")
url = f"{GAMMA_API_BASE}/markets"
params = {"active": "true", "closed": "false", "limit": 20, "order": "volume24hr", "ascending": "false"}
response = requests.get(url, params=params, timeout=10)
markets = response.json()

# Find a market with a price between 0.1 and 0.9
real_market = None
for m in markets:
    prices = m.get("outcomePrices")
    if isinstance(prices, str):
        prices = json.loads(prices)
    if prices:
        p = float(prices[0])
        if 0.1 <= p <= 0.9:
            real_market = m
            break

if not real_market:
    real_market = markets[0]

market_slug = real_market.get("slug")
market_question = real_market.get("question", "Unknown")
outcomes = real_market.get("outcomes")
prices = real_market.get("outcomePrices")

print(f"Found market: {market_question[:50]}...")
print(f"  Slug: {market_slug}")

# Parse the outcome and price
if isinstance(outcomes, str):
    outcomes = json.loads(outcomes)
if isinstance(prices, str):
    prices = json.loads(prices)

outcome = outcomes[0] if outcomes else "Yes"
current_api_price = float(prices[0]) if prices else 0.5

print(f"  Outcome: {outcome}")
print(f"  Current API price: ${current_api_price:.3f}")

# Create bot
bot = AutoTradingBot()

# Create a test trade - pretend we bought at 5 cents lower
entry_price = max(0.05, current_api_price - 0.05)
t = BotTrade(
    id='test1',
    timestamp=datetime.now(timezone.utc).isoformat(),
    market_id=market_slug,
    question=market_question,
    outcome=outcome,
    action='buy',
    shares=100,
    entry_price=entry_price,
    current_price=entry_price,
)
bot.open_trades['test1'] = t

print("\n=== Testing P&L Calculation ===")
print(f"Trade: {t.question[:40]}...")
print(f"\nBefore update:")
print(f"  Entry: ${t.entry_price:.3f}")
print(f"  Current: ${t.current_price:.3f}")
print(f"  PnL: ${t.pnl:.2f}")

# Update positions
print("\nCalling update_positions()...")
bot.update_positions()

expected_pnl = (current_api_price - entry_price) * 100

print(f"\nAfter update:")
print(f"  Entry: ${t.entry_price:.3f}")
print(f"  Current: ${t.current_price:.3f}")
print(f"  PnL: ${t.pnl:.2f} ({t.pnl_pct:+.1%})")
print(f"  Expected PnL: ~${expected_pnl:.2f}")

if abs(t.pnl - expected_pnl) < 1:
    print("\n SUCCESS! P&L is being calculated correctly!")
else:
    print("\n WARNING: P&L doesn't match expected value")
