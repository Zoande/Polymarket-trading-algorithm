"""Improved Modern Polymarket Trading Bot UI v2.

Features:
- Smoother UI with better responsiveness
- Central chat feed for bot activity
- Auto-trading mode with market scanning
- Bot evaluates markets and decides whether to trade
- Real-time P&L tracking
- Insider detection focused on small markets
"""

from __future__ import annotations

import json
import threading
import time
import queue
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from config_manager import SimulatorConfig, ensure_config
from notification_manager import NotificationManager, NotificationType
from auto_trader import AutoTradingBot, BotConfig, BotDecision, MarketOpportunity, BotTrade
from insider_detector import InsiderDetector, InsiderAlert, AlertSeverity, InsiderDetectorConfig
from polymarket_api import (
    PolymarketAPIError,
    build_market_snapshot,
    extract_slug,
    fetch_market,
    fetch_order_book,
    get_outcome_descriptor,
    list_outcomes,
    resolve_reference,
    compute_resolution_days,
)
from runtime_state import parse_volume, extract_parent_event, _now_iso
from log_manager import LogManager, get_log_manager

# Try to import news analyzer
try:
    from news_analyzer import NewsAnalyzer, MarketCategory, get_market_category_display
    NEWS_ANALYZER_AVAILABLE = True
except ImportError:
    NEWS_ANALYZER_AVAILABLE = False


# Paths - Use absolute paths based on script location to avoid working directory issues
_SCRIPT_DIR = Path(__file__).parent.resolve()
CONFIG_PATH = _SCRIPT_DIR / "config.yaml"
BOT_STATE_PATH = _SCRIPT_DIR / "bot_state.json"
NOTIFICATIONS_PATH = _SCRIPT_DIR / "notifications.json"
INSIDER_PATH = _SCRIPT_DIR / "insider_alerts.json"
MARKETS_PATH = _SCRIPT_DIR / "tracked_markets.json"
LOCK_FILE = _SCRIPT_DIR / ".bot_running.lock"


# ============================================================================
# Color Theme
# ============================================================================

class Theme:
    # Backgrounds
    BG_PRIMARY = "#0d1117"
    BG_SECONDARY = "#161b22"
    BG_TERTIARY = "#21262d"
    BG_CARD = "#1c2128"
    BG_INPUT = "#0d1117"
    BG_HOVER = "#30363d"
    
    # Accents
    ACCENT_BLUE = "#58a6ff"
    ACCENT_GREEN = "#3fb950"
    ACCENT_RED = "#f85149"
    ACCENT_YELLOW = "#d29922"
    ACCENT_PURPLE = "#a371f7"
    ACCENT_ORANGE = "#db6d28"
    
    # Text
    TEXT_PRIMARY = "#e6edf3"
    TEXT_SECONDARY = "#8b949e"
    TEXT_MUTED = "#6e7681"
    
    # Borders
    BORDER = "#30363d"
    BORDER_LIGHT = "#3d444d"
    
    # Status
    PROFIT = "#3fb950"
    LOSS = "#f85149"
    NEUTRAL = "#8b949e"


# ============================================================================
# UI Components
# ============================================================================

class SmoothScrollText(tk.Frame):
    """A text widget with smooth scrolling for the chat feed - OPTIMIZED."""
    
    def __init__(self, parent, **kwargs):
        super().__init__(parent, bg=Theme.BG_SECONDARY)
        
        # Create text widget
        self.text = tk.Text(
            self,
            bg=Theme.BG_SECONDARY,
            fg=Theme.TEXT_PRIMARY,
            font=("Consolas", 10),
            wrap=tk.WORD,
            relief=tk.FLAT,
            padx=10,
            pady=10,
            cursor="arrow",
            state=tk.DISABLED,
            highlightthickness=0,
            borderwidth=0,
        )
        
        # Scrollbar
        self.scrollbar = ttk.Scrollbar(self, command=self.text.yview)
        self.text.configure(yscrollcommand=self.scrollbar.set)
        
        # Layout
        self.scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        
        # Tags for message types
        self.text.tag_configure("timestamp", foreground=Theme.TEXT_MUTED, font=("Consolas", 9))
        self.text.tag_configure("bot", foreground=Theme.ACCENT_BLUE)
        self.text.tag_configure("trade", foreground=Theme.ACCENT_GREEN)
        self.text.tag_configure("alert", foreground=Theme.ACCENT_YELLOW)
        self.text.tag_configure("error", foreground=Theme.ACCENT_RED)
        self.text.tag_configure("success", foreground=Theme.ACCENT_GREEN)
        self.text.tag_configure("info", foreground=Theme.TEXT_SECONDARY)
        self.text.tag_configure("title", foreground=Theme.TEXT_PRIMARY, font=("Consolas", 10, "bold"))
        
        # Store messages for export - REDUCED for memory
        self.message_log: List[Dict] = []
        self.max_messages = 100  # REDUCED: Keep max 100 messages (was 200)
        self._message_count = 0
        self._pending_scroll = False
    
    def add_message(self, message: str, msg_type: str = "info", title: str = "") -> None:
        """Add a message to the feed - OPTIMIZED for performance."""
        self._message_count += 1
        
        # Batch trim: only check every 20 messages
        if self._message_count % 20 == 0:
            self._trim_old_messages()
        
        self.text.configure(state=tk.NORMAL)
        
        timestamp = datetime.now().strftime("%H:%M:%S")
        
        # Store for potential export (lightweight)
        self.message_log.append({
            'timestamp': datetime.now().isoformat(),
            'type': msg_type,
            'title': title,
            'message': message,
        })
        
        # Trim message log
        if len(self.message_log) > self.max_messages:
            self.message_log = self.message_log[-self.max_messages:]
        
        # Add timestamp
        self.text.insert(tk.END, f"[{timestamp}] ", "timestamp")
        
        # Add title if present
        if title:
            self.text.insert(tk.END, f"{title}: ", "title")
        
        # Add message
        self.text.insert(tk.END, f"{message}\n", msg_type)
        
        self.text.configure(state=tk.DISABLED)
        
        # Debounced scroll - only schedule once
        if not self._pending_scroll:
            self._pending_scroll = True
            self.after(50, self._scroll_to_bottom)
    
    def _trim_old_messages(self) -> None:
        """Remove oldest messages from text widget to save memory - AGGRESSIVE."""
        try:
            line_count = int(self.text.index('end-1c').split('.')[0])
            if line_count > 150:  # REDUCED threshold (was 300)
                self.text.configure(state=tk.NORMAL)
                self.text.delete('1.0', f'{line_count - 100}.0')
                self.text.configure(state=tk.DISABLED)
        except Exception:
            pass
    
    def get_messages_for_export(self) -> List[Dict]:
        """Get messages and clear log."""
        messages = self.message_log.copy()
        self.message_log = []
        return messages
    
    def _scroll_to_bottom(self) -> None:
        """Scroll to the bottom of the text widget."""
        self._pending_scroll = False
        self.text.see(tk.END)
    
    def clear(self) -> None:
        """Clear all messages."""
        self.text.configure(state=tk.NORMAL)
        self.text.delete(1.0, tk.END)
        self.text.configure(state=tk.DISABLED)
        self._message_count = 0


class ToolTip:
    """Creates a tooltip for a given widget with hover delay."""
    
    def __init__(self, widget, text: str, delay: int = 500):
        self.widget = widget
        self.text = text
        self.delay = delay
        self.tooltip_window = None
        self.scheduled_id = None
        
        widget.bind("<Enter>", self._schedule_tooltip)
        widget.bind("<Leave>", self._hide_tooltip)
        widget.bind("<ButtonPress>", self._hide_tooltip)
    
    def _schedule_tooltip(self, event=None):
        self._hide_tooltip()
        self.scheduled_id = self.widget.after(self.delay, self._show_tooltip)
    
    def _show_tooltip(self, event=None):
        if self.tooltip_window:
            return
        
        x = self.widget.winfo_rootx() + 20
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 5
        
        self.tooltip_window = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        tw.configure(bg=Theme.BG_TERTIARY)
        
        # Create tooltip frame with border effect
        frame = tk.Frame(tw, bg=Theme.BORDER, padx=1, pady=1)
        frame.pack()
        
        inner = tk.Frame(frame, bg=Theme.BG_TERTIARY, padx=8, pady=6)
        inner.pack()
        
        label = tk.Label(
            inner,
            text=self.text,
            font=("Segoe UI", 9),
            bg=Theme.BG_TERTIARY,
            fg=Theme.TEXT_PRIMARY,
            justify=tk.LEFT,
            wraplength=300,
        )
        label.pack()
    
    def _hide_tooltip(self, event=None):
        if self.scheduled_id:
            self.widget.after_cancel(self.scheduled_id)
            self.scheduled_id = None
        if self.tooltip_window:
            self.tooltip_window.destroy()
            self.tooltip_window = None


class CollapsibleSection(tk.Frame):
    """A collapsible section with header and content."""
    
    def __init__(self, parent, title: str, initially_open: bool = True, **kwargs):
        super().__init__(parent, bg=Theme.BG_PRIMARY, **kwargs)
        
        self.is_open = initially_open
        
        # Header frame (clickable)
        self.header = tk.Frame(self, bg=Theme.BG_TERTIARY, cursor="hand2")
        self.header.pack(fill=tk.X, pady=(0, 1))
        
        # Arrow indicator
        self.arrow_var = tk.StringVar(value="▼" if initially_open else "▶")
        self.arrow = tk.Label(
            self.header,
            textvariable=self.arrow_var,
            font=("Segoe UI", 10),
            bg=Theme.BG_TERTIARY,
            fg=Theme.ACCENT_BLUE,
            padx=10,
            pady=8,
        )
        self.arrow.pack(side=tk.LEFT)
        
        # Title
        self.title_label = tk.Label(
            self.header,
            text=title,
            font=("Segoe UI", 10, "bold"),
            bg=Theme.BG_TERTIARY,
            fg=Theme.TEXT_PRIMARY,
            pady=8,
        )
        self.title_label.pack(side=tk.LEFT)
        
        # Content frame
        self.content = tk.Frame(self, bg=Theme.BG_SECONDARY, padx=15, pady=10)
        if initially_open:
            self.content.pack(fill=tk.X)
        
        # Bind click events
        self.header.bind("<Button-1>", self._toggle)
        self.arrow.bind("<Button-1>", self._toggle)
        self.title_label.bind("<Button-1>", self._toggle)
        
        # Hover effects
        self.header.bind("<Enter>", lambda e: self.header.configure(bg=Theme.BG_HOVER))
        self.header.bind("<Leave>", lambda e: self.header.configure(bg=Theme.BG_TERTIARY))
        self.arrow.bind("<Enter>", lambda e: self._on_hover(True))
        self.arrow.bind("<Leave>", lambda e: self._on_hover(False))
        self.title_label.bind("<Enter>", lambda e: self._on_hover(True))
        self.title_label.bind("<Leave>", lambda e: self._on_hover(False))
    
    def _on_hover(self, entering: bool):
        bg = Theme.BG_HOVER if entering else Theme.BG_TERTIARY
        self.header.configure(bg=bg)
        self.arrow.configure(bg=bg)
        self.title_label.configure(bg=bg)
    
    def _toggle(self, event=None):
        self.is_open = not self.is_open
        if self.is_open:
            self.content.pack(fill=tk.X)
            self.arrow_var.set("▼")
        else:
            self.content.pack_forget()
            self.arrow_var.set("▶")
    
    def get_content_frame(self) -> tk.Frame:
        return self.content


class ConfigEntry(tk.Frame):
    """A single configuration entry with label, input, and tooltip."""
    
    def __init__(
        self, 
        parent, 
        label: str, 
        tooltip: str,
        var_type: str = "float",  # "float", "int", "bool", "percent"
        default_value = None,
        min_value = None,
        max_value = None,
        **kwargs
    ):
        super().__init__(parent, bg=Theme.BG_SECONDARY, **kwargs)
        
        self.var_type = var_type
        self.min_value = min_value
        self.max_value = max_value
        
        # Create variable based on type
        if var_type == "bool":
            self.var = tk.BooleanVar(value=default_value if default_value is not None else False)
        else:
            self.var = tk.StringVar(value=str(default_value) if default_value is not None else "")
        
        # Main row
        row = tk.Frame(self, bg=Theme.BG_SECONDARY)
        row.pack(fill=tk.X, pady=3)
        
        # Label with tooltip
        self.label = tk.Label(
            row,
            text=label,
            font=("Segoe UI", 9),
            bg=Theme.BG_SECONDARY,
            fg=Theme.TEXT_PRIMARY,
            width=25,
            anchor="w",
        )
        self.label.pack(side=tk.LEFT)
        
        # Add tooltip to label
        ToolTip(self.label, tooltip)
        
        # Input based on type
        if var_type == "bool":
            self.input = tk.Checkbutton(
                row,
                variable=self.var,
                bg=Theme.BG_SECONDARY,
                fg=Theme.TEXT_PRIMARY,
                activebackground=Theme.BG_SECONDARY,
                selectcolor=Theme.BG_TERTIARY,
                highlightthickness=0,
            )
            self.input.pack(side=tk.RIGHT)
        else:
            self.input = tk.Entry(
                row,
                textvariable=self.var,
                font=("Segoe UI", 9),
                bg=Theme.BG_INPUT,
                fg=Theme.TEXT_PRIMARY,
                insertbackground=Theme.TEXT_PRIMARY,
                relief=tk.FLAT,
                width=12,
                highlightthickness=1,
                highlightbackground=Theme.BORDER,
                highlightcolor=Theme.ACCENT_BLUE,
            )
            self.input.pack(side=tk.RIGHT)
            
            # Add unit label for percent type
            if var_type == "percent":
                tk.Label(
                    row,
                    text="%",
                    font=("Segoe UI", 9),
                    bg=Theme.BG_SECONDARY,
                    fg=Theme.TEXT_MUTED,
                ).pack(side=tk.RIGHT, padx=(0, 5))
            elif var_type == "dollar":
                tk.Label(
                    row,
                    text="$",
                    font=("Segoe UI", 9),
                    bg=Theme.BG_SECONDARY,
                    fg=Theme.TEXT_MUTED,
                ).pack(side=tk.RIGHT, padx=(0, 5))
            elif var_type == "seconds":
                tk.Label(
                    row,
                    text="sec",
                    font=("Segoe UI", 9),
                    bg=Theme.BG_SECONDARY,
                    fg=Theme.TEXT_MUTED,
                ).pack(side=tk.RIGHT, padx=(0, 5))
            elif var_type == "minutes":
                tk.Label(
                    row,
                    text="min",
                    font=("Segoe UI", 9),
                    bg=Theme.BG_SECONDARY,
                    fg=Theme.TEXT_MUTED,
                ).pack(side=tk.RIGHT, padx=(0, 5))
    
    def get_value(self):
        """Get the current value with proper type conversion."""
        try:
            if self.var_type == "bool":
                return self.var.get()
            elif self.var_type == "int":
                return int(self.var.get())
            elif self.var_type in ("float", "dollar"):
                return float(self.var.get())
            elif self.var_type in ("percent",):
                return float(self.var.get()) / 100.0  # Convert percentage to decimal
            elif self.var_type in ("seconds", "minutes"):
                return int(self.var.get())
            else:
                return self.var.get()
        except (ValueError, TypeError):
            return None
    
    def set_value(self, value):
        """Set the value with proper formatting."""
        if self.var_type == "bool":
            self.var.set(bool(value))
        elif self.var_type == "percent":
            self.var.set(str(round(value * 100, 1)))  # Convert decimal to percentage
        elif self.var_type == "dollar":
            self.var.set(str(round(value, 2)))
        elif self.var_type == "float":
            self.var.set(str(round(value, 4)))
        else:
            self.var.set(str(value))


class StatDisplay(tk.Frame):
    """A stat display widget."""
    
    def __init__(self, parent, label: str, initial_value: str = "$0.00", **kwargs):
        super().__init__(parent, bg=Theme.BG_CARD, **kwargs)
        
        self.configure(padx=15, pady=10)
        
        self.label_widget = tk.Label(
            self,
            text=label,
            font=("Segoe UI", 9),
            bg=Theme.BG_CARD,
            fg=Theme.TEXT_SECONDARY,
        )
        self.label_widget.pack(anchor="w")
        
        self.value_var = tk.StringVar(value=initial_value)
        self.value_widget = tk.Label(
            self,
            textvariable=self.value_var,
            font=("Segoe UI", 18, "bold"),
            bg=Theme.BG_CARD,
            fg=Theme.TEXT_PRIMARY,
        )
        self.value_widget.pack(anchor="w")
        
        self.subtitle_var = tk.StringVar(value="")
        self.subtitle_widget = tk.Label(
            self,
            textvariable=self.subtitle_var,
            font=("Segoe UI", 9),
            bg=Theme.BG_CARD,
            fg=Theme.TEXT_MUTED,
        )
        self.subtitle_widget.pack(anchor="w")
    
    def set_value(self, value: str, subtitle: str = "", color: str = None) -> None:
        self.value_var.set(value)
        self.subtitle_var.set(subtitle)
        if color:
            self.value_widget.configure(fg=color)


class PositionRow(tk.Frame):
    """A compact position card for grid layout."""
    
    def __init__(
        self,
        parent,
        trade: BotTrade,
        on_sell: callable = None,
        on_click: callable = None,
        **kwargs
    ):
        super().__init__(parent, bg=Theme.BG_CARD, **kwargs)
        
        self.trade = trade
        self.on_sell = on_sell
        self.on_click = on_click
        
        self.configure(padx=6, pady=4)
        self.bind("<Enter>", lambda e: self.configure(bg=Theme.BG_HOVER))
        self.bind("<Leave>", lambda e: self.configure(bg=Theme.BG_CARD))
        if on_click:
            self.bind("<Button-1>", lambda e: on_click(trade))
        
        # Compact vertical layout
        # Row 1: Question (truncated)
        q_text = trade.question[:30] + "..." if len(trade.question) > 30 else trade.question
        tk.Label(
            self,
            text=q_text,
            font=("Segoe UI", 8),
            bg=Theme.BG_CARD,
            fg=Theme.TEXT_PRIMARY,
            anchor="w",
        ).pack(fill=tk.X, anchor="w")
        
        # Row 2: Entry price and current value
        details_row = tk.Frame(self, bg=Theme.BG_CARD)
        details_row.pack(fill=tk.X)
        
        tk.Label(
            details_row,
            text=f"${trade.cost_basis:.0f}@{trade.entry_price:.2f}",
            font=("Segoe UI", 7),
            bg=Theme.BG_CARD,
            fg=Theme.TEXT_SECONDARY,
        ).pack(side=tk.LEFT)
        
        # Current price
        tk.Label(
            details_row,
            text=f"→${trade.current_price:.2f}",
            font=("Segoe UI", 7),
            bg=Theme.BG_CARD,
            fg=Theme.TEXT_MUTED,
        ).pack(side=tk.LEFT, padx=(4, 0))
        
        # Row 3: P&L in dollars and percentage
        pnl_row = tk.Frame(self, bg=Theme.BG_CARD)
        pnl_row.pack(fill=tk.X)
        
        pnl_color = Theme.PROFIT if trade.pnl >= 0 else Theme.LOSS
        pnl_dollar = f"+${trade.pnl:.2f}" if trade.pnl >= 0 else f"-${abs(trade.pnl):.2f}"
        
        tk.Label(
            pnl_row,
            text=pnl_dollar,
            font=("Segoe UI", 9, "bold"),
            bg=Theme.BG_CARD,
            fg=pnl_color,
        ).pack(side=tk.LEFT)
        
        tk.Label(
            pnl_row,
            text=f"({trade.pnl_pct:+.1f}%)",
            font=("Segoe UI", 8),
            bg=Theme.BG_CARD,
            fg=pnl_color,
        ).pack(side=tk.LEFT, padx=(4, 0))
        
        # Row 4: Sell button (small)
        if on_sell and trade.status == "open":
            sell_btn = tk.Label(
                self,
                text="SELL",
                font=("Segoe UI", 6, "bold"),
                bg=Theme.ACCENT_RED,
                fg=Theme.TEXT_PRIMARY,
                padx=4,
                pady=1,
                cursor="hand2",
            )
            sell_btn.pack(anchor="e", pady=(2, 0))
            sell_btn.bind("<Button-1>", lambda e: on_sell(trade))


class MarketRow(tk.Frame):
    """A row displaying a tracked market."""
    
    def __init__(
        self,
        parent,
        market_data: Dict,
        opportunity: Optional[MarketOpportunity] = None,
        on_click: callable = None,
        on_remove: callable = None,
        **kwargs
    ):
        super().__init__(parent, bg=Theme.BG_CARD, **kwargs)
        
        self.market_data = market_data
        self.opportunity = opportunity
        
        self.configure(padx=10, pady=8)
        self.bind("<Enter>", lambda e: self.configure(bg=Theme.BG_HOVER))
        self.bind("<Leave>", lambda e: self.configure(bg=Theme.BG_CARD))
        if on_click:
            self.bind("<Button-1>", lambda e: on_click(market_data))
        
        # Left side
        left = tk.Frame(self, bg=Theme.BG_CARD)
        left.pack(side=tk.LEFT, fill=tk.X, expand=True)
        
        # Question
        question = market_data.get("question", "Unknown")
        q_text = question[:40] + "..." if len(question) > 40 else question
        tk.Label(
            left,
            text=q_text,
            font=("Segoe UI", 10),
            bg=Theme.BG_CARD,
            fg=Theme.TEXT_PRIMARY,
            anchor="w",
            cursor="hand2",
        ).pack(fill=tk.X)
        
        # Tags row
        tags = tk.Frame(left, bg=Theme.BG_CARD)
        tags.pack(fill=tk.X, pady=(2, 0))
        
        # Outcome badge
        outcome = market_data.get("outcome", "Yes")
        tk.Label(
            tags,
            text=outcome,
            font=("Segoe UI", 8),
            bg=Theme.ACCENT_BLUE,
            fg=Theme.TEXT_PRIMARY,
            padx=4,
            pady=1,
        ).pack(side=tk.LEFT)
        
        # Bot decision badge
        if opportunity:
            decision_colors = {
                BotDecision.BUY: Theme.ACCENT_GREEN,
                BotDecision.SELL: Theme.ACCENT_RED,
                BotDecision.HOLD: Theme.ACCENT_YELLOW,
                BotDecision.SKIP: Theme.TEXT_MUTED,
            }
            tk.Label(
                tags,
                text=opportunity.decision.value.upper(),
                font=("Segoe UI", 8, "bold"),
                bg=decision_colors.get(opportunity.decision, Theme.TEXT_MUTED),
                fg=Theme.TEXT_PRIMARY,
                padx=4,
                pady=1,
            ).pack(side=tk.LEFT, padx=(4, 0))
        
        # Right side - price and metrics
        right = tk.Frame(self, bg=Theme.BG_CARD)
        right.pack(side=tk.RIGHT)
        
        price = market_data.get("best_ask") or market_data.get("price") or 0
        tk.Label(
            right,
            text=f"${price:.3f}",
            font=("Segoe UI", 12, "bold"),
            bg=Theme.BG_CARD,
            fg=Theme.TEXT_PRIMARY,
        ).pack(anchor="e")
        
        if opportunity and opportunity.g_score:
            g_color = Theme.ACCENT_GREEN if opportunity.g_score > 0.003 else Theme.TEXT_SECONDARY
            tk.Label(
                right,
                text=f"g: {opportunity.g_score:.4f}",
                font=("Segoe UI", 9),
                bg=Theme.BG_CARD,
                fg=g_color,
            ).pack(anchor="e")
        
        # Remove button
        if on_remove:
            remove_btn = tk.Label(
                right,
                text="✕",
                font=("Segoe UI", 10),
                bg=Theme.BG_CARD,
                fg=Theme.TEXT_MUTED,
                cursor="hand2",
            )
            remove_btn.pack(anchor="e", pady=(2, 0))
            remove_btn.bind("<Enter>", lambda e: remove_btn.configure(fg=Theme.ACCENT_RED))
            remove_btn.bind("<Leave>", lambda e: remove_btn.configure(fg=Theme.TEXT_MUTED))
            remove_btn.bind("<Button-1>", lambda e: on_remove(market_data))


# ============================================================================
# Main Application
# ============================================================================

class TradingBotApp(tk.Tk):
    """Modern Polymarket Trading Bot Application."""
    
    def __init__(self):
        super().__init__()
        
        self.title("🚀 Polymarket Trading Bot")
        self.geometry("1400x900")
        self.configure(bg=Theme.BG_PRIMARY)
        self.minsize(1100, 700)
        
        # Message queue for thread-safe UI updates
        self.message_queue = queue.Queue()
        
        # Initialize components
        self.config = ensure_config(CONFIG_PATH)
        self.notifications = NotificationManager(NOTIFICATIONS_PATH)
        
        # Initialize auto-trading bot with DUAL-SPEED config:
        # - FAST: Held positions update every 5 seconds (critical for stop-loss)
        # - SLOW: Market discovery every 30 seconds (reduces API load)
        self.bot = AutoTradingBot(
            config=BotConfig(
                initial_capital=10000.0,
                max_position_size=500.0,
                min_volume=500.0,       # Lowered for diversity
                scan_interval_seconds=30,  # Discovery: 30 sec (new markets)
                max_markets_per_scan=100,  # Discovery: 100 markets per scan
                price_update_interval=5,   # Positions: 5 sec (held positions - FAST)
                max_positions=50,  # Allow many positions
                swing_trade_enabled=True,  # Enable swing trading
                prefer_high_volume=False,  # DON'T just focus on popular markets
                use_news_analysis=NEWS_ANALYZER_AVAILABLE,  # Use news if available
            ),
            storage_path=BOT_STATE_PATH,
            on_trade=self._on_bot_trade,
            on_opportunity=self._on_bot_opportunity,
            on_message=self._on_bot_message,
        )
        
        # Initialize insider detector - SIMPLE: just trades over $10k
        self.insider_detector = InsiderDetector(
            config=InsiderDetectorConfig(
                large_trade_threshold=10000.0,  # Alert on trades $10,000+
                poll_interval_seconds=30,  # Discovery speed (not critical)
                max_alerts_stored=100,
            ),
            storage_path=INSIDER_PATH,
        )
        self.insider_detector.add_listener(self._on_insider_alert)
        
        # Initialize log manager for memory cleanup
        self.log_manager = get_log_manager()
        
        # Tracked markets
        self.tracked_markets: Dict[str, Dict] = {}
        self.market_opportunities: Dict[str, MarketOpportunity] = {}
        
        # UI state
        self.auto_trade_enabled = tk.BooleanVar(value=False)
        self.selected_position: Optional[str] = None
        self._last_stats_update = datetime.now()
        
        # Build UI
        self._build_ui()
        
        # Load saved markets
        self._load_markets()
        
        # Start message processing
        self._process_messages()
        
        # Welcome message
        self.chat.add_message(
            "Welcome! I'm your Polymarket trading assistant. "
            "Add markets to track, and I'll analyze them for trading opportunities.",
            "bot",
            "Bot"
        )
        self.chat.add_message(
            "Enable 'Auto Trade' to let me automatically find and execute profitable trades. "
            "Bot now supports SWING trades on popular markets for quick profits!",
            "info"
        )
        
        # Start insider detector monitoring (always active to catch alerts)
        self.insider_detector.start_monitoring()
        self.chat.add_message(
            "Insider trading detector is now monitoring markets for suspicious activity.",
            "info"
        )
        
        # Start periodic updates
        self._start_updates()
    
    def _build_ui(self) -> None:
        """Build the main UI."""
        # Configure grid - 2 columns, 2 rows for main content
        self.grid_columnconfigure(0, weight=3)  # Left panel (chat)
        self.grid_columnconfigure(1, weight=2)  # Right panel (markets/positions)
        self.grid_rowconfigure(1, weight=3)     # Main content row
        self.grid_rowconfigure(2, weight=1)     # Bottom row for trade log
        
        # Top bar
        self._build_top_bar()
        
        # Left panel - Chat feed
        self._build_chat_panel()
        
        # Right panel - Markets and positions
        self._build_right_panel()
        
        # Bottom left - Stats Dashboard
        self._build_stats_dashboard()
        
        # Bottom right - Trade Log
        self._build_trade_log_panel()
        
        # Track last update time for throttling
        self._last_ui_update = 0
    
    def _build_top_bar(self) -> None:
        """Build the top navigation bar."""
        top_bar = tk.Frame(self, bg=Theme.BG_SECONDARY, height=60)
        top_bar.grid(row=0, column=0, columnspan=2, sticky="ew")
        top_bar.grid_propagate(False)
        
        # Logo
        tk.Label(
            top_bar,
            text="🚀 Polymarket Bot",
            font=("Segoe UI", 16, "bold"),
            bg=Theme.BG_SECONDARY,
            fg=Theme.TEXT_PRIMARY,
        ).pack(side=tk.LEFT, padx=20, pady=15)
        
        # Stats row
        stats_frame = tk.Frame(top_bar, bg=Theme.BG_SECONDARY)
        stats_frame.pack(side=tk.LEFT, padx=30)
        
        self.portfolio_label = tk.Label(
            stats_frame,
            text="Portfolio: $10,000.00",
            font=("Segoe UI", 11),
            bg=Theme.BG_SECONDARY,
            fg=Theme.TEXT_PRIMARY,
        )
        self.portfolio_label.pack(side=tk.LEFT, padx=10)
        
        self.pnl_label = tk.Label(
            stats_frame,
            text="P&L: $0.00 (0.0%)",
            font=("Segoe UI", 11),
            bg=Theme.BG_SECONDARY,
            fg=Theme.TEXT_SECONDARY,
        )
        self.pnl_label.pack(side=tk.LEFT, padx=10)
        
        # Right side controls
        controls = tk.Frame(top_bar, bg=Theme.BG_SECONDARY)
        controls.pack(side=tk.RIGHT, padx=20)
        
        # Auto trade toggle
        self.auto_trade_btn = tk.Button(
            controls,
            text="▶ Start Auto Trade",
            font=("Segoe UI", 10),
            bg=Theme.ACCENT_GREEN,
            fg=Theme.TEXT_PRIMARY,
            relief=tk.FLAT,
            padx=15,
            pady=5,
            cursor="hand2",
            command=self._toggle_auto_trade,
        )
        self.auto_trade_btn.pack(side=tk.LEFT, padx=5)
        
        # Scan button
        tk.Button(
            controls,
            text="Scan Markets",
            font=("Segoe UI", 10),
            bg=Theme.BG_TERTIARY,
            fg=Theme.TEXT_PRIMARY,
            relief=tk.FLAT,
            padx=15,
            pady=5,
            cursor="hand2",
            command=self._manual_scan,
        ).pack(side=tk.LEFT, padx=5)
        
        # Settings
        tk.Button(
            controls,
            text="Settings",
            font=("Segoe UI", 10),
            bg=Theme.BG_TERTIARY,
            fg=Theme.TEXT_PRIMARY,
            relief=tk.FLAT,
            padx=10,
            pady=5,
            cursor="hand2",
            command=self._show_settings,
        ).pack(side=tk.LEFT, padx=5)
    
    def _build_chat_panel(self) -> None:
        """Build the left chat panel."""
        left_panel = tk.Frame(self, bg=Theme.BG_PRIMARY)
        left_panel.grid(row=1, column=0, sticky="nsew", padx=(10, 5), pady=10)
        
        # Header
        header = tk.Frame(left_panel, bg=Theme.BG_PRIMARY)
        header.pack(fill=tk.X, pady=(0, 10))
        
        tk.Label(
            header,
            text="Bot Activity",
            font=("Segoe UI", 14, "bold"),
            bg=Theme.BG_PRIMARY,
            fg=Theme.TEXT_PRIMARY,
        ).pack(side=tk.LEFT)
        
        self.status_label = tk.Label(
            header,
            text="● Idle",
            font=("Segoe UI", 10),
            bg=Theme.BG_PRIMARY,
            fg=Theme.TEXT_SECONDARY,
        )
        self.status_label.pack(side=tk.RIGHT)
        
        # Chat feed
        self.chat = SmoothScrollText(left_panel)
        self.chat.pack(fill=tk.BOTH, expand=True)
    
    def _build_right_panel(self) -> None:
        """Build the right panel with markets and positions."""
        right_panel = tk.Frame(self, bg=Theme.BG_PRIMARY)
        right_panel.grid(row=1, column=1, sticky="nsew", padx=(5, 10), pady=10)
        
        # Create notebook for tabs
        self.notebook = ttk.Notebook(right_panel)
        self.notebook.pack(fill=tk.BOTH, expand=True)
        
        # Configure notebook style for dark theme
        style = ttk.Style()
        
        # Use clam theme as base (better for customization)
        try:
            style.theme_use("clam")
        except:
            pass
        
        # Configure the notebook itself
        style.configure("TNotebook", 
            background=Theme.BG_PRIMARY,
            borderwidth=0,
            tabmargins=[0, 0, 0, 0],
        )
        
        style.configure("TNotebook.Tab", 
            background=Theme.BG_TERTIARY,
            foreground=Theme.TEXT_PRIMARY,
            padding=[15, 8],
            borderwidth=0,
            font=("Segoe UI", 10),
        )
        
        # Map for different states (selected, active, etc.)
        style.map("TNotebook.Tab",
            background=[
                ("selected", Theme.BG_SECONDARY),
                ("active", Theme.BG_HOVER),
                ("!selected", Theme.BG_TERTIARY),
            ],
            foreground=[
                ("selected", Theme.TEXT_PRIMARY),
                ("active", Theme.TEXT_PRIMARY),
                ("!selected", Theme.TEXT_SECONDARY),
            ],
            expand=[("selected", [1, 1, 1, 0])],
        )
        
        # Tab 0: Overview (Dashboard)
        overview_tab = tk.Frame(self.notebook, bg=Theme.BG_PRIMARY)
        self.notebook.add(overview_tab, text="  Overview  ")
        self._build_overview_tab(overview_tab)
        
        # Tab 1: Orders (Mass Actions)
        orders_tab = tk.Frame(self.notebook, bg=Theme.BG_PRIMARY)
        self.notebook.add(orders_tab, text="  Orders  ")
        self._build_orders_tab(orders_tab)
        
        # Tab 2: Watched Markets
        markets_tab = tk.Frame(self.notebook, bg=Theme.BG_PRIMARY)
        self.notebook.add(markets_tab, text="  Watched Markets  ")
        self._build_markets_tab(markets_tab)
        
        # Tab 2: Bot Positions
        positions_tab = tk.Frame(self.notebook, bg=Theme.BG_PRIMARY)
        self.notebook.add(positions_tab, text="  Positions  ")
        self._build_positions_tab(positions_tab)
        
        # Tab 3: Alerts
        alerts_tab = tk.Frame(self.notebook, bg=Theme.BG_PRIMARY)
        self.notebook.add(alerts_tab, text="  Alerts  ")
        self._build_alerts_tab(alerts_tab)
        
        # Tab 4: Config
        config_tab = tk.Frame(self.notebook, bg=Theme.BG_PRIMARY)
        self.notebook.add(config_tab, text="  Config  ")
        self._build_config_tab(config_tab)
    
    def _build_overview_tab(self, parent: tk.Frame) -> None:
        """Build the overview/dashboard tab."""
        # Create scrollable container
        canvas = tk.Canvas(parent, bg=Theme.BG_PRIMARY, highlightthickness=0)
        scrollbar = ttk.Scrollbar(parent, orient="vertical", command=canvas.yview)
        scrollable_frame = tk.Frame(canvas, bg=Theme.BG_PRIMARY)
        
        scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        
        canvas_window = canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        
        def on_canvas_configure(event):
            canvas.itemconfig(canvas_window, width=event.width)
        canvas.bind("<Configure>", on_canvas_configure)
        
        def on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        canvas.bind("<MouseWheel>", on_mousewheel)
        scrollable_frame.bind("<MouseWheel>", on_mousewheel)
        
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        
        # =====================================================================
        # Header
        # =====================================================================
        header_frame = tk.Frame(scrollable_frame, bg=Theme.BG_PRIMARY)
        header_frame.pack(fill=tk.X, padx=15, pady=(15, 10))
        
        tk.Label(
            header_frame,
            text="📊 Portfolio Overview",
            font=("Segoe UI", 16, "bold"),
            bg=Theme.BG_PRIMARY,
            fg=Theme.TEXT_PRIMARY,
        ).pack(side=tk.LEFT)
        
        # Bot status indicator
        self.overview_status_frame = tk.Frame(header_frame, bg=Theme.BG_PRIMARY)
        self.overview_status_frame.pack(side=tk.RIGHT)
        
        self.overview_status_dot = tk.Label(
            self.overview_status_frame,
            text="●",
            font=("Segoe UI", 12),
            bg=Theme.BG_PRIMARY,
            fg=Theme.TEXT_MUTED,
        )
        self.overview_status_dot.pack(side=tk.LEFT, padx=(0, 5))
        
        self.overview_status_text = tk.Label(
            self.overview_status_frame,
            text="Bot Idle",
            font=("Segoe UI", 10),
            bg=Theme.BG_PRIMARY,
            fg=Theme.TEXT_MUTED,
        )
        self.overview_status_text.pack(side=tk.LEFT)
        
        # =====================================================================
        # Row 1: Key Portfolio Metrics (Large Cards)
        # =====================================================================
        row1 = tk.Frame(scrollable_frame, bg=Theme.BG_PRIMARY)
        row1.pack(fill=tk.X, padx=10, pady=5)
        
        # Starting Capital (reference point)
        self.ov_starting_card = self._create_overview_card(
            row1, "🏦 Starting Capital", "$10,000.00", "Initial investment", wide=True
        )
        self.ov_starting_card.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        # Total Portfolio Value (Big hero card)
        self.ov_portfolio_card = self._create_overview_card(
            row1, "💰 Current Value", "$0.00", "Cash + Open Positions", wide=True
        )
        self.ov_portfolio_card.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        # Total Profit/Loss Card (the key number!)
        self.ov_profit_card = self._create_overview_card(
            row1, "📈 Total Profit/Loss", "$0.00 (0.0%)", "Realized + Unrealized", wide=True
        )
        self.ov_profit_card.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        # =====================================================================
        # Row 2: Cash & Position Breakdown
        # =====================================================================
        row2 = tk.Frame(scrollable_frame, bg=Theme.BG_PRIMARY)
        row2.pack(fill=tk.X, padx=10, pady=5)
        
        self.ov_cash_card = self._create_overview_card(
            row2, "💵 Cash Available", "$0.00", "Ready to trade"
        )
        self.ov_cash_card.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        self.ov_positions_value_card = self._create_overview_card(
            row2, "📊 In Positions", "$0.00", "Invested in markets"
        )
        self.ov_positions_value_card.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        self.ov_realized_card = self._create_overview_card(
            row2, "✅ Realized P&L", "$0.00", "From closed trades"
        )
        self.ov_realized_card.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        self.ov_unrealized_card = self._create_overview_card(
            row2, "📉 Unrealized P&L", "$0.00", "Open positions gain/loss"
        )
        self.ov_unrealized_card.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        # =====================================================================
        # Row 3: Trading Performance
        # =====================================================================
        row3 = tk.Frame(scrollable_frame, bg=Theme.BG_PRIMARY)
        row3.pack(fill=tk.X, padx=10, pady=5)
        
        self.ov_positions_count_card = self._create_overview_card(
            row3, "📋 Open Positions", "0 / 0", "Current / Maximum"
        )
        self.ov_positions_count_card.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        self.ov_total_trades_card = self._create_overview_card(
            row3, "🔢 Total Trades", "0", "All-time trades"
        )
        self.ov_total_trades_card.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        self.ov_win_rate_card = self._create_overview_card(
            row3, "🏆 Win Rate", "0%", "Wins / Losses"
        )
        self.ov_win_rate_card.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        self.ov_avg_pnl_card = self._create_overview_card(
            row3, "📊 Avg Position P&L", "0%", "Open positions average"
        )
        self.ov_avg_pnl_card.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        # =====================================================================
        # Row 4: Best/Worst & Alerts
        # =====================================================================
        row4 = tk.Frame(scrollable_frame, bg=Theme.BG_PRIMARY)
        row4.pack(fill=tk.X, padx=10, pady=5)
        
        self.ov_best_position_card = self._create_overview_card(
            row4, "🚀 Best Position", "—", "Highest gain"
        )
        self.ov_best_position_card.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        self.ov_worst_position_card = self._create_overview_card(
            row4, "📉 Worst Position", "—", "Biggest loss"
        )
        self.ov_worst_position_card.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        self.ov_expiring_soon_card = self._create_overview_card(
            row4, "⏳ Expiring < 24h", "0", "Resolving soon"
        )
        self.ov_expiring_soon_card.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        self.ov_alerts_card = self._create_overview_card(
            row4, "🔔 Active Alerts", "0", "Insider activity"
        )
        self.ov_alerts_card.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        # =====================================================================
        # Row 8: Category Distribution
        # =====================================================================
        category_section = tk.Frame(scrollable_frame, bg=Theme.BG_SECONDARY)
        category_section.pack(fill=tk.X, padx=15, pady=10)
        
        tk.Label(
            category_section,
            text="📊 Position Distribution by Category",
            font=("Segoe UI", 11, "bold"),
            bg=Theme.BG_SECONDARY,
            fg=Theme.TEXT_PRIMARY,
        ).pack(anchor="w", padx=15, pady=(10, 5))
        
        self.ov_category_frame = tk.Frame(category_section, bg=Theme.BG_SECONDARY)
        self.ov_category_frame.pack(fill=tk.X, padx=15, pady=(0, 10))
        
        # Will be populated by update function
        self.ov_category_bars = {}
        
        # =====================================================================
        # Initialize timers for countdown display
        # =====================================================================
        self._last_holdings_update = time.time()
        self._last_scan_update = time.time()
        self._markets_scanned_count = 0
        self._buy_signals_count = 0
        
        # Start overview update timer
        self._update_overview_display()
    
    def _create_overview_card(self, parent: tk.Frame, title: str, value: str, 
                               subtitle: str, wide: bool = False) -> tk.Frame:
        """Create a metric card for the overview tab."""
        card = tk.Frame(parent, bg=Theme.BG_SECONDARY, relief=tk.FLAT)
        card.configure(highlightbackground=Theme.BG_TERTIARY, highlightthickness=1)
        
        # Store references for updating
        card.title_label = tk.Label(
            card,
            text=title,
            font=("Segoe UI", 9),
            bg=Theme.BG_SECONDARY,
            fg=Theme.TEXT_MUTED,
        )
        card.title_label.pack(anchor="w", padx=12, pady=(10, 2))
        
        card.value_label = tk.Label(
            card,
            text=value,
            font=("Segoe UI", 16 if wide else 14, "bold"),
            bg=Theme.BG_SECONDARY,
            fg=Theme.TEXT_PRIMARY,
        )
        card.value_label.pack(anchor="w", padx=12, pady=(0, 2))
        
        card.subtitle_label = tk.Label(
            card,
            text=subtitle,
            font=("Segoe UI", 8),
            bg=Theme.BG_SECONDARY,
            fg=Theme.TEXT_MUTED,
        )
        card.subtitle_label.pack(anchor="w", padx=12, pady=(0, 10))
        
        return card
    
    def _update_overview_display(self) -> None:
        """Update all overview metrics."""
        try:
            stats = self.bot.get_stats()
            
            # Calculate additional metrics
            positions_value = sum(t.shares * t.current_price for t in self.bot.open_trades.values())
            portfolio_value = stats['portfolio_value']
            initial_capital = self.bot.config.initial_capital
            
            # TRUE Total profit = Portfolio Value - Starting Capital
            # This is the most accurate measure of profit/loss
            total_profit = portfolio_value - initial_capital
            total_profit_pct = (total_profit / initial_capital * 100) if initial_capital > 0 else 0
            
            # Unrealized P&L (current gains/losses on open positions)
            unrealized_pnl = stats['unrealized_pnl']
            
            # Realized P&L from tracking (may be inaccurate, but show it for reference)
            tracked_realized_pnl = stats['total_pnl']
            
            # Update bot status
            if self.bot.is_running():
                self.overview_status_dot.configure(fg=Theme.ACCENT_GREEN)
                self.overview_status_text.configure(text="Bot Running", fg=Theme.ACCENT_GREEN)
            else:
                self.overview_status_dot.configure(fg=Theme.TEXT_MUTED)
                self.overview_status_text.configure(text="Bot Idle", fg=Theme.TEXT_MUTED)
            
            # Row 1: Starting Capital, Current Value, Total Profit
            self.ov_starting_card.value_label.configure(text=f"${initial_capital:,.2f}")
            self.ov_portfolio_card.value_label.configure(text=f"${portfolio_value:,.2f}")
            
            profit_color = Theme.ACCENT_GREEN if total_profit >= 0 else Theme.ACCENT_RED
            profit_sign = "+" if total_profit >= 0 else ""
            self.ov_profit_card.value_label.configure(
                text=f"{profit_sign}${total_profit:,.2f} ({profit_sign}{total_profit_pct:.1f}%)",
                fg=profit_color
            )
            
            # Row 2: Cash, Positions Value, Realized P&L, Unrealized P&L
            self.ov_cash_card.value_label.configure(text=f"${stats['cash_balance']:,.2f}")
            self.ov_positions_value_card.value_label.configure(text=f"${positions_value:,.2f}")
            
            realized_color = Theme.ACCENT_GREEN if tracked_realized_pnl >= 0 else Theme.ACCENT_RED
            realized_sign = "+" if tracked_realized_pnl >= 0 else ""
            self.ov_realized_card.value_label.configure(
                text=f"{realized_sign}${tracked_realized_pnl:,.2f}",
                fg=realized_color
            )
            
            unrealized_color = Theme.ACCENT_GREEN if unrealized_pnl >= 0 else Theme.ACCENT_RED
            unrealized_sign = "+" if unrealized_pnl >= 0 else ""
            self.ov_unrealized_card.value_label.configure(
                text=f"{unrealized_sign}${unrealized_pnl:,.2f}",
                fg=unrealized_color
            )
            
            # Row 3: Position Stats & Performance
            open_count = len(self.bot.open_trades)
            max_positions = self.bot.config.max_positions
            self.ov_positions_count_card.value_label.configure(text=f"{open_count} / {max_positions}")
            
            self.ov_total_trades_card.value_label.configure(text=str(stats['total_trades']))
            
            win_rate = stats['win_rate']
            self.ov_win_rate_card.value_label.configure(
                text=f"{win_rate:.1f}%",
                fg=Theme.ACCENT_GREEN if win_rate >= 50 else Theme.ACCENT_RED if win_rate > 0 else Theme.TEXT_PRIMARY
            )
            self.ov_win_rate_card.subtitle_label.configure(
                text=f"{stats['winning_trades']}W / {stats['losing_trades']}L"
            )
            
            # Average P&L of open positions
            if self.bot.open_trades:
                avg_pnl = sum(t.pnl_pct for t in self.bot.open_trades.values()) / len(self.bot.open_trades) * 100
                avg_color = Theme.ACCENT_GREEN if avg_pnl >= 0 else Theme.ACCENT_RED
                self.ov_avg_pnl_card.value_label.configure(
                    text=f"{avg_pnl:+.1f}%",
                    fg=avg_color
                )
            else:
                self.ov_avg_pnl_card.value_label.configure(text="—", fg=Theme.TEXT_PRIMARY)
            
            # Row 4: Best/Worst Performers, Expiring, Alerts
            if self.bot.open_trades:
                sorted_by_pnl = sorted(self.bot.open_trades.values(), key=lambda t: t.pnl_pct, reverse=True)
                
                best = sorted_by_pnl[0]
                self.ov_best_position_card.value_label.configure(
                    text=f"+{best.pnl_pct*100:.1f}%" if best.pnl_pct >= 0 else f"{best.pnl_pct*100:.1f}%",
                    fg=Theme.ACCENT_GREEN if best.pnl_pct >= 0 else Theme.ACCENT_RED
                )
                self.ov_best_position_card.subtitle_label.configure(text=best.question[:30] + "...")
                
                worst = sorted_by_pnl[-1]
                self.ov_worst_position_card.value_label.configure(
                    text=f"{worst.pnl_pct*100:+.1f}%",
                    fg=Theme.ACCENT_GREEN if worst.pnl_pct >= 0 else Theme.ACCENT_RED
                )
                self.ov_worst_position_card.subtitle_label.configure(text=worst.question[:30] + "...")
            else:
                self.ov_best_position_card.value_label.configure(text="—", fg=Theme.TEXT_PRIMARY)
                self.ov_best_position_card.subtitle_label.configure(text="No positions")
                self.ov_worst_position_card.value_label.configure(text="—", fg=Theme.TEXT_PRIMARY)
                self.ov_worst_position_card.subtitle_label.configure(text="No positions")
            
            # Expiring positions
            expiring_24h = 0
            for trade in self.bot.open_trades.values():
                days_left = getattr(trade, 'resolution_days', 999)
                if days_left < 1:
                    expiring_24h += 1
            
            self.ov_expiring_soon_card.value_label.configure(
                text=str(expiring_24h),
                fg=Theme.ACCENT_RED if expiring_24h > 0 else Theme.TEXT_PRIMARY
            )
            
            # Alerts count
            alerts_count = len(self.insider_detector.get_alerts())
            self.ov_alerts_card.value_label.configure(
                text=str(alerts_count),
                fg=Theme.ACCENT_YELLOW if alerts_count > 0 else Theme.TEXT_PRIMARY
            )
            
            # Row 8: Category distribution
            self._update_category_distribution()
            
        except Exception as e:
            pass  # Silently handle errors during update
        
        # Schedule next update (every 1 second for smooth countdown)
        self.after(1000, self._update_overview_display)
    
    def _update_category_distribution(self) -> None:
        """Update the category distribution bars."""
        # Clear existing bars
        for widget in self.ov_category_frame.winfo_children():
            widget.destroy()
        
        # Count positions by category
        category_counts = {}
        category_values = {}
        
        for trade in self.bot.open_trades.values():
            cat = getattr(trade, 'category', 'other')
            category_counts[cat] = category_counts.get(cat, 0) + 1
            category_values[cat] = category_values.get(cat, 0) + (trade.shares * trade.current_price)
        
        if not category_counts:
            tk.Label(
                self.ov_category_frame,
                text="No positions to display",
                font=("Segoe UI", 9, "italic"),
                bg=Theme.BG_SECONDARY,
                fg=Theme.TEXT_MUTED,
            ).pack(anchor="w")
            return
        
        total_value = sum(category_values.values())
        
        # Category colors and emojis
        cat_styles = {
            'sports': ('🏈', '#FF6B6B'),
            'politics': ('🏛️', '#4ECDC4'),
            'crypto': ('₿', '#FFE66D'),
            'entertainment': ('🎬', '#95E1D3'),
            'finance': ('📈', '#DDA0DD'),
            'technology': ('💻', '#87CEEB'),
            'world_events': ('🌍', '#F38181'),
            'other': ('📋', '#AA96DA'),
        }
        
        # Create bars for each category
        for cat, count in sorted(category_counts.items(), key=lambda x: -x[1]):
            limit = self.bot.config.category_limits.get(cat, 5)
            value = category_values.get(cat, 0)
            pct = (value / total_value * 100) if total_value > 0 else 0
            
            emoji, color = cat_styles.get(cat, ('📋', '#888888'))
            
            row = tk.Frame(self.ov_category_frame, bg=Theme.BG_SECONDARY)
            row.pack(fill=tk.X, pady=2)
            
            # Label
            tk.Label(
                row,
                text=f"{emoji} {cat.replace('_', ' ').title()}",
                font=("Segoe UI", 9),
                bg=Theme.BG_SECONDARY,
                fg=Theme.TEXT_PRIMARY,
                width=15,
                anchor="w",
            ).pack(side=tk.LEFT)
            
            # Bar container
            bar_container = tk.Frame(row, bg=Theme.BG_TERTIARY, height=16)
            bar_container.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
            bar_container.pack_propagate(False)
            
            # Filled bar
            fill_pct = min(count / limit, 1.0) if limit > 0 else 0
            bar_fill = tk.Frame(bar_container, bg=color)
            bar_fill.place(relwidth=fill_pct, relheight=1.0)
            
            # Stats
            tk.Label(
                row,
                text=f"{count}/{limit}",
                font=("Segoe UI", 9),
                bg=Theme.BG_SECONDARY,
                fg=Theme.ACCENT_YELLOW if count >= limit else Theme.TEXT_SECONDARY,
                width=6,
            ).pack(side=tk.LEFT)
            
            tk.Label(
                row,
                text=f"${value:,.0f} ({pct:.0f}%)",
                font=("Segoe UI", 9),
                bg=Theme.BG_SECONDARY,
                fg=Theme.TEXT_MUTED,
                width=14,
                anchor="e",
            ).pack(side=tk.RIGHT)
    
    def _on_holdings_updated(self) -> None:
        """Called when holdings prices are updated."""
        self._last_holdings_update = time.time()
    
    def _on_scan_completed(self) -> None:
        """Called when a market scan is completed."""
        self._last_scan_update = time.time()

    # =========================================================================
    # ORDERS TAB - Mass Actions
    # =========================================================================
    
    def _build_orders_tab(self, parent: tk.Frame) -> None:
        """Build the orders tab with mass action buttons."""
        # Create scrollable container
        canvas = tk.Canvas(parent, bg=Theme.BG_PRIMARY, highlightthickness=0)
        scrollbar = ttk.Scrollbar(parent, orient="vertical", command=canvas.yview)
        scrollable_frame = tk.Frame(canvas, bg=Theme.BG_PRIMARY)
        
        scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        
        canvas_window = canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        
        def on_canvas_configure(event):
            canvas.itemconfig(canvas_window, width=event.width)
        canvas.bind("<Configure>", on_canvas_configure)
        
        def on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        canvas.bind("<MouseWheel>", on_mousewheel)
        scrollable_frame.bind("<MouseWheel>", on_mousewheel)
        
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        
        # Header
        header = tk.Frame(scrollable_frame, bg=Theme.BG_PRIMARY)
        header.pack(fill=tk.X, padx=15, pady=(15, 10))
        
        tk.Label(
            header,
            text="⚡ Mass Orders & Actions",
            font=("Segoe UI", 14, "bold"),
            bg=Theme.BG_PRIMARY,
            fg=Theme.TEXT_PRIMARY,
        ).pack(side=tk.LEFT)
        
        tk.Label(
            header,
            text="Execute bulk actions on your portfolio",
            font=("Segoe UI", 9),
            bg=Theme.BG_PRIMARY,
            fg=Theme.TEXT_MUTED,
        ).pack(side=tk.RIGHT)
        
        # Status indicator for pause state
        self.orders_buy_status = tk.StringVar(value="● Buys: Active")
        self.orders_buy_status_label = tk.Label(
            header,
            textvariable=self.orders_buy_status,
            font=("Segoe UI", 10, "bold"),
            bg=Theme.BG_PRIMARY,
            fg=Theme.ACCENT_GREEN,
        )
        self.orders_buy_status_label.pack(side=tk.RIGHT, padx=20)
        
        # =====================================================================
        # Section 1: Sell Actions (DANGER ZONE)
        # =====================================================================
        sell_section = CollapsibleSection(scrollable_frame, "🔴 Sell Actions", initially_open=True)
        sell_section.pack(fill=tk.X, padx=10, pady=5)
        sell_content = sell_section.get_content_frame()
        
        # Warning label
        tk.Label(
            sell_content,
            text="⚠️ These actions will immediately close positions. Use with caution!",
            font=("Segoe UI", 9, "italic"),
            bg=Theme.BG_SECONDARY,
            fg=Theme.ACCENT_YELLOW,
        ).pack(fill=tk.X, pady=(0, 10))
        
        # Sell All button
        sell_all_frame = tk.Frame(sell_content, bg=Theme.BG_SECONDARY)
        sell_all_frame.pack(fill=tk.X, pady=5)
        
        tk.Button(
            sell_all_frame,
            text="🛑 SELL ALL POSITIONS",
            font=("Segoe UI", 11, "bold"),
            bg="#8B0000",  # Dark red
            fg=Theme.TEXT_PRIMARY,
            activebackground="#A52A2A",
            relief=tk.FLAT,
            padx=20,
            pady=10,
            cursor="hand2",
            command=self._order_sell_all,
        ).pack(side=tk.LEFT)
        
        tk.Label(
            sell_all_frame,
            text="Emergency exit: Close ALL open positions immediately",
            font=("Segoe UI", 9),
            bg=Theme.BG_SECONDARY,
            fg=Theme.TEXT_MUTED,
        ).pack(side=tk.LEFT, padx=15)
        
        # Sell Profitable button
        sell_profit_frame = tk.Frame(sell_content, bg=Theme.BG_SECONDARY)
        sell_profit_frame.pack(fill=tk.X, pady=5)
        
        tk.Button(
            sell_profit_frame,
            text="💰 Sell All Profitable",
            font=("Segoe UI", 10, "bold"),
            bg=Theme.ACCENT_GREEN,
            fg=Theme.TEXT_PRIMARY,
            activebackground="#2ea043",
            relief=tk.FLAT,
            padx=15,
            pady=8,
            cursor="hand2",
            command=self._order_sell_profitable,
        ).pack(side=tk.LEFT)
        
        tk.Label(
            sell_profit_frame,
            text="Lock in gains: Sell all positions currently in profit (P&L > 0)",
            font=("Segoe UI", 9),
            bg=Theme.BG_SECONDARY,
            fg=Theme.TEXT_MUTED,
        ).pack(side=tk.LEFT, padx=15)
        
        # Sell Losing button
        sell_loss_frame = tk.Frame(sell_content, bg=Theme.BG_SECONDARY)
        sell_loss_frame.pack(fill=tk.X, pady=5)
        
        tk.Button(
            sell_loss_frame,
            text="📉 Sell All Losing",
            font=("Segoe UI", 10, "bold"),
            bg=Theme.ACCENT_RED,
            fg=Theme.TEXT_PRIMARY,
            activebackground="#da3633",
            relief=tk.FLAT,
            padx=15,
            pady=8,
            cursor="hand2",
            command=self._order_sell_losing,
        ).pack(side=tk.LEFT)
        
        tk.Label(
            sell_loss_frame,
            text="Cut losses: Sell all positions currently at a loss (P&L < 0)",
            font=("Segoe UI", 9),
            bg=Theme.BG_SECONDARY,
            fg=Theme.TEXT_MUTED,
        ).pack(side=tk.LEFT, padx=15)
        
        # Sell by Category
        sell_cat_frame = tk.Frame(sell_content, bg=Theme.BG_SECONDARY)
        sell_cat_frame.pack(fill=tk.X, pady=5)
        
        tk.Button(
            sell_cat_frame,
            text="🏷️ Sell Category:",
            font=("Segoe UI", 10),
            bg=Theme.BG_TERTIARY,
            fg=Theme.TEXT_PRIMARY,
            activebackground=Theme.BG_HOVER,
            relief=tk.FLAT,
            padx=15,
            pady=8,
            cursor="hand2",
            command=self._order_sell_category,
        ).pack(side=tk.LEFT)
        
        self.orders_category_var = tk.StringVar(value="sports")
        category_menu = ttk.Combobox(
            sell_cat_frame,
            textvariable=self.orders_category_var,
            values=["sports", "politics", "crypto", "entertainment", "finance", "technology", "world_events", "other"],
            state="readonly",
            width=15,
        )
        category_menu.pack(side=tk.LEFT, padx=10)
        
        tk.Label(
            sell_cat_frame,
            text="Sell all positions in the selected category",
            font=("Segoe UI", 9),
            bg=Theme.BG_SECONDARY,
            fg=Theme.TEXT_MUTED,
        ).pack(side=tk.LEFT, padx=10)
        
        # =====================================================================
        # Section 2: Buy Controls
        # =====================================================================
        buy_section = CollapsibleSection(scrollable_frame, "🟢 Buy Controls", initially_open=True)
        buy_section.pack(fill=tk.X, padx=10, pady=5)
        buy_content = buy_section.get_content_frame()
        
        buy_control_frame = tk.Frame(buy_content, bg=Theme.BG_SECONDARY)
        buy_control_frame.pack(fill=tk.X, pady=5)
        
        self.pause_buys_btn = tk.Button(
            buy_control_frame,
            text="⏸️ Pause New Buys",
            font=("Segoe UI", 10, "bold"),
            bg=Theme.ACCENT_YELLOW,
            fg=Theme.BG_PRIMARY,
            activebackground="#c9922a",
            relief=tk.FLAT,
            padx=15,
            pady=8,
            cursor="hand2",
            command=self._order_toggle_pause_buys,
        )
        self.pause_buys_btn.pack(side=tk.LEFT)
        
        tk.Label(
            buy_control_frame,
            text="Stop opening new positions (existing positions still monitored)",
            font=("Segoe UI", 9),
            bg=Theme.BG_SECONDARY,
            fg=Theme.TEXT_MUTED,
        ).pack(side=tk.LEFT, padx=15)
        
        # Initialize pause state
        self._buys_paused = False
        
        # =====================================================================
        # Section 3: Cleanup Actions
        # =====================================================================
        cleanup_section = CollapsibleSection(scrollable_frame, "🧹 Cleanup Actions", initially_open=True)
        cleanup_section.pack(fill=tk.X, padx=10, pady=5)
        cleanup_content = cleanup_section.get_content_frame()
        
        cleanup_frame = tk.Frame(cleanup_content, bg=Theme.BG_SECONDARY)
        cleanup_frame.pack(fill=tk.X, pady=5)
        
        tk.Button(
            cleanup_frame,
            text="🧹 Clear Stagnant Now",
            font=("Segoe UI", 10),
            bg=Theme.BG_TERTIARY,
            fg=Theme.TEXT_PRIMARY,
            activebackground=Theme.BG_HOVER,
            relief=tk.FLAT,
            padx=15,
            pady=8,
            cursor="hand2",
            command=self._order_clear_stagnant,
        ).pack(side=tk.LEFT)
        
        tk.Label(
            cleanup_frame,
            text="Manually trigger cleanup of flat/stagnant positions to free up slots",
            font=("Segoe UI", 9),
            bg=Theme.BG_SECONDARY,
            fg=Theme.TEXT_MUTED,
        ).pack(side=tk.LEFT, padx=15)
        
        # =====================================================================
        # Section 4: Export
        # =====================================================================
        export_section = CollapsibleSection(scrollable_frame, "📁 Export Data", initially_open=False)
        export_section.pack(fill=tk.X, padx=10, pady=5)
        export_content = export_section.get_content_frame()
        
        export_frame = tk.Frame(export_content, bg=Theme.BG_SECONDARY)
        export_frame.pack(fill=tk.X, pady=5)
        
        tk.Button(
            export_frame,
            text="📊 Export Positions to CSV",
            font=("Segoe UI", 10),
            bg=Theme.BG_TERTIARY,
            fg=Theme.TEXT_PRIMARY,
            activebackground=Theme.BG_HOVER,
            relief=tk.FLAT,
            padx=15,
            pady=8,
            cursor="hand2",
            command=self._order_export_positions,
        ).pack(side=tk.LEFT, padx=(0, 10))
        
        tk.Button(
            export_frame,
            text="📜 Export Trade History",
            font=("Segoe UI", 10),
            bg=Theme.BG_TERTIARY,
            fg=Theme.TEXT_PRIMARY,
            activebackground=Theme.BG_HOVER,
            relief=tk.FLAT,
            padx=15,
            pady=8,
            cursor="hand2",
            command=self._order_export_history,
        ).pack(side=tk.LEFT)
        
        # =====================================================================
        # Live Summary
        # =====================================================================
        summary_section = CollapsibleSection(scrollable_frame, "📊 Current Portfolio Summary", initially_open=True)
        summary_section.pack(fill=tk.X, padx=10, pady=5)
        summary_content = summary_section.get_content_frame()
        
        self.orders_summary_frame = tk.Frame(summary_content, bg=Theme.BG_SECONDARY)
        self.orders_summary_frame.pack(fill=tk.X, pady=5)
        
        # Update summary initially
        self._update_orders_summary()
    
    def _update_orders_summary(self) -> None:
        """Update the orders tab portfolio summary."""
        # Clear existing
        for widget in self.orders_summary_frame.winfo_children():
            widget.destroy()
        
        # Count positions
        total = len(self.bot.open_trades)
        profitable = sum(1 for t in self.bot.open_trades.values() if t.pnl >= 0)
        losing = total - profitable
        
        # Count by category
        cat_counts = {}
        for trade in self.bot.open_trades.values():
            cat = getattr(trade, 'category', 'other')
            cat_counts[cat] = cat_counts.get(cat, 0) + 1
        
        # Count by type
        swing = sum(1 for t in self.bot.open_trades.values() if getattr(t, 'trade_type', 'long') == 'swing')
        long_term = total - swing
        
        # Create summary rows
        row1 = tk.Frame(self.orders_summary_frame, bg=Theme.BG_SECONDARY)
        row1.pack(fill=tk.X, pady=2)
        
        tk.Label(row1, text=f"Total Positions: {total}", font=("Segoe UI", 10, "bold"),
                bg=Theme.BG_SECONDARY, fg=Theme.TEXT_PRIMARY).pack(side=tk.LEFT, padx=10)
        tk.Label(row1, text=f"💰 Profitable: {profitable}", font=("Segoe UI", 10),
                bg=Theme.BG_SECONDARY, fg=Theme.ACCENT_GREEN).pack(side=tk.LEFT, padx=10)
        tk.Label(row1, text=f"📉 Losing: {losing}", font=("Segoe UI", 10),
                bg=Theme.BG_SECONDARY, fg=Theme.ACCENT_RED).pack(side=tk.LEFT, padx=10)
        
        row2 = tk.Frame(self.orders_summary_frame, bg=Theme.BG_SECONDARY)
        row2.pack(fill=tk.X, pady=2)
        
        tk.Label(row2, text=f"⚡ Swing: {swing}", font=("Segoe UI", 10),
                bg=Theme.BG_SECONDARY, fg=Theme.ACCENT_BLUE).pack(side=tk.LEFT, padx=10)
        tk.Label(row2, text=f"📈 Long-term: {long_term}", font=("Segoe UI", 10),
                bg=Theme.BG_SECONDARY, fg=Theme.TEXT_SECONDARY).pack(side=tk.LEFT, padx=10)
        
        # Category breakdown
        if cat_counts:
            row3 = tk.Frame(self.orders_summary_frame, bg=Theme.BG_SECONDARY)
            row3.pack(fill=tk.X, pady=(5, 2))
            tk.Label(row3, text="By Category:", font=("Segoe UI", 9, "bold"),
                    bg=Theme.BG_SECONDARY, fg=Theme.TEXT_MUTED).pack(side=tk.LEFT, padx=10)
            
            for cat, count in sorted(cat_counts.items(), key=lambda x: -x[1]):
                tk.Label(row3, text=f"{cat}: {count}", font=("Segoe UI", 9),
                        bg=Theme.BG_SECONDARY, fg=Theme.TEXT_SECONDARY).pack(side=tk.LEFT, padx=5)
    
    # =========================================================================
    # Order Action Handlers
    # =========================================================================
    
    def _order_sell_all(self) -> None:
        """Sell all positions."""
        count = len(self.bot.open_trades)
        if count == 0:
            messagebox.showinfo("No Positions", "There are no open positions to sell.")
            return
        
        if not messagebox.askyesno(
            "⚠️ Confirm SELL ALL",
            f"Are you sure you want to sell ALL {count} positions?\n\n"
            "This action cannot be undone!",
            icon="warning"
        ):
            return
        
        sold = 0
        for trade_id in list(self.bot.open_trades.keys()):
            if self.bot.sell_position(trade_id):
                sold += 1
        
        self.chat.add_message(f"🛑 SOLD ALL: Closed {sold} positions", "alert", "Orders")
        self._update_orders_summary()
        self._update_positions_display()
    
    def _order_sell_profitable(self) -> None:
        """Sell all profitable positions."""
        profitable = [tid for tid, t in self.bot.open_trades.items() if t.pnl >= 0]
        
        if not profitable:
            messagebox.showinfo("No Profitable Positions", "There are no profitable positions to sell.")
            return
        
        if not messagebox.askyesno(
            "Confirm Sell Profitable",
            f"Sell all {len(profitable)} profitable positions?\n\n"
            "This will lock in your current gains."
        ):
            return
        
        sold = 0
        total_pnl = 0
        for trade_id in profitable:
            trade = self.bot.open_trades.get(trade_id)
            if trade:
                total_pnl += trade.pnl
            if self.bot.sell_position(trade_id):
                sold += 1
        
        self.chat.add_message(f"💰 Sold {sold} profitable positions (realized ~${total_pnl:.2f})", "success", "Orders")
        self._update_orders_summary()
        self._update_positions_display()
    
    def _order_sell_losing(self) -> None:
        """Sell all losing positions."""
        losing = [tid for tid, t in self.bot.open_trades.items() if t.pnl < 0]
        
        if not losing:
            messagebox.showinfo("No Losing Positions", "There are no losing positions to sell.")
            return
        
        total_loss = sum(self.bot.open_trades[tid].pnl for tid in losing)
        
        if not messagebox.askyesno(
            "Confirm Sell Losing",
            f"Sell all {len(losing)} losing positions?\n\n"
            f"This will realize approximately ${abs(total_loss):.2f} in losses."
        ):
            return
        
        sold = 0
        for trade_id in losing:
            if self.bot.sell_position(trade_id):
                sold += 1
        
        self.chat.add_message(f"📉 Cut losses: Sold {sold} losing positions", "alert", "Orders")
        self._update_orders_summary()
        self._update_positions_display()
    
    def _order_sell_category(self) -> None:
        """Sell all positions in a category."""
        category = self.orders_category_var.get()
        
        in_category = [
            tid for tid, t in self.bot.open_trades.items() 
            if getattr(t, 'category', 'other') == category
        ]
        
        if not in_category:
            messagebox.showinfo("No Positions", f"There are no positions in the '{category}' category.")
            return
        
        if not messagebox.askyesno(
            f"Confirm Sell {category.title()}",
            f"Sell all {len(in_category)} positions in '{category}'?"
        ):
            return
        
        sold = 0
        for trade_id in in_category:
            if self.bot.sell_position(trade_id):
                sold += 1
        
        self.chat.add_message(f"🏷️ Sold {sold} positions in '{category}'", "info", "Orders")
        self._update_orders_summary()
        self._update_positions_display()
    
    def _order_toggle_pause_buys(self) -> None:
        """Toggle pause/resume buying."""
        # Toggle both UI state and bot state
        self._buys_paused = not self._buys_paused
        if self.bot:
            self.bot._buys_paused = self._buys_paused
        
        if self._buys_paused:
            self.pause_buys_btn.configure(
                text="▶️ Resume Buys",
                bg=Theme.ACCENT_GREEN,
            )
            self.orders_buy_status.set("● Buys: PAUSED")
            self.orders_buy_status_label.configure(fg=Theme.ACCENT_RED)
            self.chat.add_message("⏸️ New buys PAUSED - existing positions still monitored", "alert", "Orders")
        else:
            self.pause_buys_btn.configure(
                text="⏸️ Pause New Buys",
                bg=Theme.ACCENT_YELLOW,
            )
            self.orders_buy_status.set("● Buys: Active")
            self.orders_buy_status_label.configure(fg=Theme.ACCENT_GREEN)
            self.chat.add_message("▶️ Buys RESUMED - bot will open new positions", "success", "Orders")
    
    def _order_clear_stagnant(self) -> None:
        """Manually trigger stagnant position cleanup."""
        if not self.bot.open_trades:
            messagebox.showinfo("No Positions", "There are no positions to clean up.")
            return
        
        before = len(self.bot.open_trades)
        freed = self.bot._cleanup_stagnant_positions(min_positions_to_free=10)
        
        if freed > 0:
            self.chat.add_message(f"🧹 Cleared {freed} stagnant positions ({before} → {len(self.bot.open_trades)})", "info", "Orders")
        else:
            self.chat.add_message("🧹 No stagnant positions found to clear", "info", "Orders")
        
        self._update_orders_summary()
        self._update_positions_display()
    
    def _order_export_positions(self) -> None:
        """Export current positions to CSV."""
        if not self.bot.open_trades:
            messagebox.showinfo("No Data", "There are no open positions to export.")
            return
        
        filepath = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv")],
            title="Export Positions",
            initialfile=f"positions_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        )
        
        if not filepath:
            return
        
        try:
            import csv
            with open(filepath, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow([
                    'ID', 'Timestamp', 'Market', 'Question', 'Outcome', 'Shares', 
                    'Entry Price', 'Current Price', 'Cost Basis', 'Current Value',
                    'P&L $', 'P&L %', 'Trade Type', 'Category'
                ])
                
                for trade in self.bot.open_trades.values():
                    writer.writerow([
                        trade.id,
                        trade.timestamp,
                        trade.market_id,
                        trade.question,
                        trade.outcome,
                        f"{trade.shares:.4f}",
                        f"{trade.entry_price:.4f}",
                        f"{trade.current_price:.4f}",
                        f"{trade.cost_basis:.2f}",
                        f"{trade.value:.2f}",
                        f"{trade.pnl:.2f}",
                        f"{trade.pnl_pct*100:.2f}%",
                        getattr(trade, 'trade_type', 'long'),
                        getattr(trade, 'category', 'other'),
                    ])
            
            self.chat.add_message(f"📊 Exported {len(self.bot.open_trades)} positions to CSV", "success", "Orders")
            messagebox.showinfo("Export Complete", f"Positions exported to:\n{filepath}")
        except Exception as e:
            messagebox.showerror("Export Failed", f"Failed to export: {e}")
    
    def _order_export_history(self) -> None:
        """Export trade history to CSV."""
        if not self.bot.closed_trades and not self.bot.trade_log:
            messagebox.showinfo("No Data", "There is no trade history to export.")
            return
        
        filepath = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv")],
            title="Export Trade History",
            initialfile=f"trade_history_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        )
        
        if not filepath:
            return
        
        try:
            import csv
            with open(filepath, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow([
                    'ID', 'Open Time', 'Close Time', 'Market', 'Question', 'Outcome',
                    'Shares', 'Entry Price', 'Exit Price', 'P&L $', 'P&L %', 
                    'Trade Type', 'Category', 'Status'
                ])
                
                for trade in self.bot.closed_trades:
                    writer.writerow([
                        trade.id,
                        trade.timestamp,
                        trade.exit_timestamp or '',
                        trade.market_id,
                        trade.question,
                        trade.outcome,
                        f"{trade.shares:.4f}",
                        f"{trade.entry_price:.4f}",
                        f"{trade.exit_price:.4f}" if trade.exit_price else '',
                        f"{trade.pnl:.2f}",
                        f"{trade.pnl_pct*100:.2f}%",
                        getattr(trade, 'trade_type', 'long'),
                        getattr(trade, 'category', 'other'),
                        trade.status,
                    ])
            
            self.chat.add_message(f"📜 Exported {len(self.bot.closed_trades)} closed trades to CSV", "success", "Orders")
            messagebox.showinfo("Export Complete", f"Trade history exported to:\n{filepath}")
        except Exception as e:
            messagebox.showerror("Export Failed", f"Failed to export: {e}")

    def _build_markets_tab(self, parent: tk.Frame) -> None:
        """Build the markets tab."""
        # Header with add button
        header = tk.Frame(parent, bg=Theme.BG_PRIMARY)
        header.pack(fill=tk.X, pady=(10, 5))
        
        tk.Label(
            header,
            text="Tracked Markets",
            font=("Segoe UI", 12, "bold"),
            bg=Theme.BG_PRIMARY,
            fg=Theme.TEXT_PRIMARY,
        ).pack(side=tk.LEFT)
        
        tk.Button(
            header,
            text="+ Add Market",
            font=("Segoe UI", 9),
            bg=Theme.ACCENT_BLUE,
            fg=Theme.TEXT_PRIMARY,
            relief=tk.FLAT,
            padx=10,
            pady=3,
            cursor="hand2",
            command=self._add_market_dialog,
        ).pack(side=tk.RIGHT)
        
        # Markets list
        self.markets_container = tk.Frame(parent, bg=Theme.BG_PRIMARY)
        self.markets_container.pack(fill=tk.BOTH, expand=True, pady=5)
        
        # Canvas for scrolling
        self.markets_canvas = tk.Canvas(
            self.markets_container,
            bg=Theme.BG_PRIMARY,
            highlightthickness=0,
        )
        scrollbar = ttk.Scrollbar(self.markets_container, command=self.markets_canvas.yview)
        self.markets_frame = tk.Frame(self.markets_canvas, bg=Theme.BG_PRIMARY)
        
        self.markets_canvas.create_window((0, 0), window=self.markets_frame, anchor="nw")
        self.markets_canvas.configure(yscrollcommand=scrollbar.set)
        
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.markets_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        
        self.markets_frame.bind("<Configure>", 
            lambda e: self.markets_canvas.configure(scrollregion=self.markets_canvas.bbox("all")))
        self.markets_canvas.bind("<Configure>",
            lambda e: self.markets_canvas.itemconfig(
                self.markets_canvas.find_all()[0] if self.markets_canvas.find_all() else None,
                width=e.width
            ) if self.markets_canvas.find_all() else None)
    
    def _build_trade_log_panel(self) -> None:
        """Build the bottom right trade log panel."""
        log_panel = tk.Frame(self, bg=Theme.BG_SECONDARY)
        log_panel.grid(row=2, column=1, sticky="nsew", padx=(5, 10), pady=(5, 10))
        
        # Header
        header = tk.Frame(log_panel, bg=Theme.BG_SECONDARY)
        header.pack(fill=tk.X, padx=10, pady=(10, 5))
        
        tk.Label(
            header,
            text="📊 Trade Log",
            font=("Segoe UI", 11, "bold"),
            bg=Theme.BG_SECONDARY,
            fg=Theme.TEXT_PRIMARY,
        ).pack(side=tk.LEFT)
        
        self.trade_log_count = tk.Label(
            header,
            text="0 trades",
            font=("Segoe UI", 9),
            bg=Theme.BG_SECONDARY,
            fg=Theme.TEXT_MUTED,
        )
        self.trade_log_count.pack(side=tk.RIGHT)
        
        # Trade log text area with scroll
        log_container = tk.Frame(log_panel, bg=Theme.BG_SECONDARY)
        log_container.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))
        
        self.trade_log_text = tk.Text(
            log_container,
            bg=Theme.BG_PRIMARY,
            fg=Theme.TEXT_PRIMARY,
            font=("Consolas", 9),
            wrap=tk.WORD,
            relief=tk.FLAT,
            padx=8,
            pady=8,
            height=8,
            state=tk.DISABLED,
            highlightthickness=1,
            highlightbackground=Theme.BORDER,
        )
        
        scrollbar = ttk.Scrollbar(log_container, command=self.trade_log_text.yview)
        self.trade_log_text.configure(yscrollcommand=scrollbar.set)
        
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.trade_log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        
        # Configure tags for trade log
        self.trade_log_text.tag_configure("buy", foreground=Theme.ACCENT_GREEN)
        self.trade_log_text.tag_configure("sell_win", foreground=Theme.ACCENT_GREEN)
        self.trade_log_text.tag_configure("sell_loss", foreground=Theme.ACCENT_RED)
        self.trade_log_text.tag_configure("timestamp", foreground=Theme.TEXT_MUTED)
        self.trade_log_text.tag_configure("amount", foreground=Theme.ACCENT_BLUE)
        self.trade_log_text.tag_configure("pnl_pos", foreground=Theme.PROFIT)
        self.trade_log_text.tag_configure("pnl_neg", foreground=Theme.LOSS)
    
    def _build_stats_dashboard(self) -> None:
        """Build the bottom left stats dashboard panel."""
        stats_panel = tk.Frame(self, bg=Theme.BG_SECONDARY)
        stats_panel.grid(row=2, column=0, sticky="nsew", padx=(10, 5), pady=(5, 10))
        
        # Header
        header = tk.Frame(stats_panel, bg=Theme.BG_SECONDARY)
        header.pack(fill=tk.X, padx=10, pady=(10, 5))
        
        tk.Label(
            header,
            text="📈 Performance Dashboard",
            font=("Segoe UI", 11, "bold"),
            bg=Theme.BG_SECONDARY,
            fg=Theme.TEXT_PRIMARY,
        ).pack(side=tk.LEFT)
        
        # Main content area
        content = tk.Frame(stats_panel, bg=Theme.BG_SECONDARY)
        content.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))
        
        # Left stats column
        left_col = tk.Frame(content, bg=Theme.BG_CARD)
        left_col.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 5))
        
        tk.Label(left_col, text="Session Stats", font=("Segoe UI", 9, "bold"),
                bg=Theme.BG_CARD, fg=Theme.TEXT_PRIMARY).pack(pady=(8, 4))
        
        # Create labels for session stats
        self.session_stats_frame = tk.Frame(left_col, bg=Theme.BG_CARD)
        self.session_stats_frame.pack(fill=tk.X, padx=8, pady=4)
        
        self.stat_labels = {}
        for stat_name in ["Trades Today", "Win Rate", "Avg P&L", "Best Trade", "Worst Trade"]:
            row = tk.Frame(self.session_stats_frame, bg=Theme.BG_CARD)
            row.pack(fill=tk.X, pady=1)
            tk.Label(row, text=stat_name, font=("Segoe UI", 8), 
                    bg=Theme.BG_CARD, fg=Theme.TEXT_MUTED).pack(side=tk.LEFT)
            self.stat_labels[stat_name] = tk.Label(row, text="--", font=("Segoe UI", 8, "bold"),
                    bg=Theme.BG_CARD, fg=Theme.TEXT_PRIMARY)
            self.stat_labels[stat_name].pack(side=tk.RIGHT)
        
        # Right - Category breakdown
        right_col = tk.Frame(content, bg=Theme.BG_CARD)
        right_col.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(5, 0))
        
        tk.Label(right_col, text="Portfolio by Category", font=("Segoe UI", 9, "bold"),
                bg=Theme.BG_CARD, fg=Theme.TEXT_PRIMARY).pack(pady=(8, 4))
        
        self.category_frame = tk.Frame(right_col, bg=Theme.BG_CARD)
        self.category_frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=4)
        
        # Category colors
        self.category_colors = {
            "politics": "#E74C3C",
            "crypto": "#F39C12", 
            "sports": "#2ECC71",
            "finance": "#3498DB",
            "entertainment": "#9B59B6",
            "science": "#1ABC9C",
            "world": "#34495E",
            "other": "#95A5A6",
        }
    
    def _update_stats_dashboard(self) -> None:
        """Update the stats dashboard with current data."""
        try:
            stats = self.bot.get_stats()
            trades = self.bot.get_open_trades()
            
            # Calculate session stats
            total_trades = stats.get('total_trades', 0)
            win_rate = stats.get('win_rate', 0)
            
            # Find best and worst trades from current positions
            best_pnl = 0
            worst_pnl = 0
            total_pnl = 0
            for trade in trades:
                # Use pnl_pct which is the attribute on BotTrade
                pnl_pct = getattr(trade, 'pnl_pct', 0)
                total_pnl += pnl_pct
                if pnl_pct > best_pnl:
                    best_pnl = pnl_pct
                if pnl_pct < worst_pnl:
                    worst_pnl = pnl_pct
            
            avg_pnl = total_pnl / len(trades) if trades else 0
            
            # Update stat labels
            self.stat_labels["Trades Today"].configure(text=str(total_trades))
            self.stat_labels["Win Rate"].configure(
                text=f"{win_rate:.1f}%",
                fg=Theme.PROFIT if win_rate >= 50 else Theme.LOSS
            )
            self.stat_labels["Avg P&L"].configure(
                text=f"{avg_pnl:+.1f}%",
                fg=Theme.PROFIT if avg_pnl >= 0 else Theme.LOSS
            )
            self.stat_labels["Best Trade"].configure(
                text=f"+{best_pnl:.1f}%" if best_pnl > 0 else "--",
                fg=Theme.PROFIT
            )
            self.stat_labels["Worst Trade"].configure(
                text=f"{worst_pnl:.1f}%" if worst_pnl < 0 else "--",
                fg=Theme.LOSS
            )
            
            # Update category breakdown
            for widget in self.category_frame.winfo_children():
                widget.destroy()
            
            # Count positions by category
            category_counts = {}
            for trade in trades:
                cat = getattr(trade, 'category', 'other') or 'other'
                category_counts[cat] = category_counts.get(cat, 0) + 1
            
            if category_counts:
                total = sum(category_counts.values())
                for cat, count in sorted(category_counts.items(), key=lambda x: -x[1]):
                    pct = (count / total) * 100
                    color = self.category_colors.get(cat, "#95A5A6")
                    
                    row = tk.Frame(self.category_frame, bg=Theme.BG_CARD)
                    row.pack(fill=tk.X, pady=1)
                    
                    # Category name
                    tk.Label(row, text=cat.capitalize(), font=("Segoe UI", 8),
                            bg=Theme.BG_CARD, fg=color).pack(side=tk.LEFT)
                    
                    # Count and percentage
                    tk.Label(row, text=f"{count} ({pct:.0f}%)", font=("Segoe UI", 8),
                            bg=Theme.BG_CARD, fg=Theme.TEXT_MUTED).pack(side=tk.RIGHT)
            else:
                tk.Label(self.category_frame, text="No positions yet", font=("Segoe UI", 8),
                        bg=Theme.BG_CARD, fg=Theme.TEXT_MUTED).pack()
                        
        except Exception as e:
            print(f"Error updating stats dashboard: {e}")

    def _build_positions_tab(self, parent: tk.Frame) -> None:
        """Build the positions tab."""
        # Stats row
        stats = tk.Frame(parent, bg=Theme.BG_PRIMARY)
        stats.pack(fill=tk.X, pady=10)
        
        self.stat_value = StatDisplay(stats, "Portfolio Value", "$10,000.00")
        self.stat_value.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))
        
        self.stat_pnl = StatDisplay(stats, "Total P&L", "$0.00")
        self.stat_pnl.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(5, 0))
        
        # Positions header
        header = tk.Frame(parent, bg=Theme.BG_PRIMARY)
        header.pack(fill=tk.X, pady=(10, 5))
        
        tk.Label(
            header,
            text="Open Positions",
            font=("Segoe UI", 12, "bold"),
            bg=Theme.BG_PRIMARY,
            fg=Theme.TEXT_PRIMARY,
        ).pack(side=tk.LEFT)
        
        # Sort options
        sort_frame = tk.Frame(header, bg=Theme.BG_PRIMARY)
        sort_frame.pack(side=tk.RIGHT)
        
        tk.Label(
            sort_frame,
            text="Sort:",
            font=("Segoe UI", 9),
            bg=Theme.BG_PRIMARY,
            fg=Theme.TEXT_SECONDARY,
        ).pack(side=tk.LEFT, padx=(0, 5))
        
        self.position_sort_var = tk.StringVar(value="profit")
        sort_options = ["profit", "recent", "loss", "size"]
        self.position_sort_menu = ttk.Combobox(
            sort_frame,
            textvariable=self.position_sort_var,
            values=sort_options,
            width=8,
            state="readonly",
        )
        self.position_sort_menu.pack(side=tk.LEFT, padx=(0, 10))
        self.position_sort_menu.bind("<<ComboboxSelected>>", lambda e: self._update_positions_display())
        
        self.positions_count = tk.Label(
            sort_frame,
            text="0 positions",
            font=("Segoe UI", 10),
            bg=Theme.BG_PRIMARY,
            fg=Theme.TEXT_SECONDARY,
        )
        self.positions_count.pack(side=tk.LEFT)
        
        # Positions list
        self.positions_container = tk.Frame(parent, bg=Theme.BG_PRIMARY)
        self.positions_container.pack(fill=tk.BOTH, expand=True, pady=5)
        
        self.positions_canvas = tk.Canvas(
            self.positions_container,
            bg=Theme.BG_PRIMARY,
            highlightthickness=0,
        )
        scrollbar = ttk.Scrollbar(self.positions_container, command=self.positions_canvas.yview)
        self.positions_frame = tk.Frame(self.positions_canvas, bg=Theme.BG_PRIMARY)
        
        self.positions_window = self.positions_canvas.create_window((0, 0), window=self.positions_frame, anchor="nw")
        self.positions_canvas.configure(yscrollcommand=scrollbar.set)
        
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.positions_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        
        # Bind to expand frame width to canvas width
        def on_canvas_configure(event):
            self.positions_canvas.itemconfig(self.positions_window, width=event.width)
        
        # Mouse wheel scrolling (bind to canvas and frame)
        def on_mousewheel(event):
            self.positions_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        
        self.positions_canvas.bind("<Configure>", on_canvas_configure)
        self.positions_canvas.bind("<MouseWheel>", on_mousewheel)
        self.positions_frame.bind("<MouseWheel>", on_mousewheel)
        self.positions_frame.bind("<Configure>",
            lambda e: self.positions_canvas.configure(scrollregion=self.positions_canvas.bbox("all")))
    
    def _build_alerts_tab(self, parent: tk.Frame) -> None:
        """Build the alerts tab."""
        # Header
        header = tk.Frame(parent, bg=Theme.BG_PRIMARY)
        header.pack(fill=tk.X, pady=(10, 5))
        
        tk.Label(
            header,
            text="Insider Trading Alerts",
            font=("Segoe UI", 12, "bold"),
            bg=Theme.BG_PRIMARY,
            fg=Theme.TEXT_PRIMARY,
        ).pack(side=tk.LEFT)
        
        tk.Label(
            header,
            text="(Focused on small markets)",
            font=("Segoe UI", 9),
            bg=Theme.BG_PRIMARY,
            fg=Theme.TEXT_MUTED,
        ).pack(side=tk.LEFT, padx=(10, 0))
        
        # Alerts list
        self.alerts_frame = tk.Frame(parent, bg=Theme.BG_PRIMARY)
        self.alerts_frame.pack(fill=tk.BOTH, expand=True, pady=5)
        
        self._update_alerts_display()
    
    def _build_config_tab(self, parent: tk.Frame) -> None:
        """Build the configuration tab with all settings."""
        # Create scrollable container
        canvas = tk.Canvas(parent, bg=Theme.BG_PRIMARY, highlightthickness=0)
        scrollbar = ttk.Scrollbar(parent, orient="vertical", command=canvas.yview)
        scrollable_frame = tk.Frame(canvas, bg=Theme.BG_PRIMARY)
        
        scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        
        canvas_window = canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        
        # Make canvas expand to fill width
        def on_canvas_configure(event):
            canvas.itemconfig(canvas_window, width=event.width)
        canvas.bind("<Configure>", on_canvas_configure)
        
        # Mouse wheel scrolling
        def on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        canvas.bind("<MouseWheel>", on_mousewheel)
        scrollable_frame.bind("<MouseWheel>", on_mousewheel)
        
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        
        # Header
        header = tk.Frame(scrollable_frame, bg=Theme.BG_PRIMARY)
        header.pack(fill=tk.X, padx=10, pady=(10, 5))
        
        tk.Label(
            header,
            text="⚙️ Bot Configuration",
            font=("Segoe UI", 14, "bold"),
            bg=Theme.BG_PRIMARY,
            fg=Theme.TEXT_PRIMARY,
        ).pack(side=tk.LEFT)
        
        tk.Label(
            header,
            text="Hover over settings for descriptions",
            font=("Segoe UI", 9),
            bg=Theme.BG_PRIMARY,
            fg=Theme.TEXT_MUTED,
        ).pack(side=tk.RIGHT)
        
        # Store all config entries for later access
        self.config_entries = {}
        
        # =====================================================================
        # Section 1: Capital & Position Sizing
        # =====================================================================
        section1 = CollapsibleSection(scrollable_frame, "💰 Capital & Position Sizing", initially_open=True)
        section1.pack(fill=tk.X, padx=10, pady=5)
        content1 = section1.get_content_frame()
        
        self.config_entries['initial_capital'] = ConfigEntry(
            content1, "Initial Capital", 
            "The starting capital for the simulation. This is the amount of money the bot starts with. "
            "⚠️ REQUIRES RESTART: Changes only apply when you reset/restart the bot. Does not affect current cash balance.",
            var_type="dollar", default_value=self.bot.config.initial_capital
        )
        self.config_entries['initial_capital'].pack(fill=tk.X)
        
        self.config_entries['max_position_size'] = ConfigEntry(
            content1, "Max Position Size",
            "Maximum dollar amount the bot will invest in a single trade. "
            "Larger values = more risk per trade, but potentially higher returns. "
            "✓ IMMEDIATE: Applies to new trades only, does not affect existing positions.",
            var_type="dollar", default_value=self.bot.config.max_position_size
        )
        self.config_entries['max_position_size'].pack(fill=tk.X)
        
        self.config_entries['max_portfolio_pct'] = ConfigEntry(
            content1, "Max Portfolio %",
            "Maximum percentage of total portfolio that can be allocated to a single market. "
            "Helps prevent over-concentration in one position. "
            "✓ IMMEDIATE: Applies to new trades. Use 'Force Holdings' to reduce existing oversized positions.",
            var_type="percent", default_value=self.bot.config.max_portfolio_pct
        )
        self.config_entries['max_portfolio_pct'].pack(fill=tk.X)
        
        self.config_entries['test_trade_size'] = ConfigEntry(
            content1, "Test Trade Size",
            "Dollar amount for low-confidence 'test' trades. These smaller trades are used "
            "when the bot is less certain about an opportunity, limiting potential losses.",
            var_type="dollar", default_value=self.bot.config.test_trade_size
        )
        self.config_entries['test_trade_size'].pack(fill=tk.X)
        
        self.config_entries['test_trade_enabled'] = ConfigEntry(
            content1, "Enable Test Trades",
            "When enabled, the bot will make smaller 'test' trades on opportunities "
            "that don't meet the high confidence threshold. Good for diversification.",
            var_type="bool", default_value=self.bot.config.test_trade_enabled
        )
        self.config_entries['test_trade_enabled'].pack(fill=tk.X)
        
        # =====================================================================
        # Section 2: Position Limits
        # =====================================================================
        section2 = CollapsibleSection(scrollable_frame, "📊 Position Limits", initially_open=True)
        section2.pack(fill=tk.X, padx=10, pady=5)
        content2 = section2.get_content_frame()
        
        self.config_entries['max_positions'] = ConfigEntry(
            content2, "Max Total Positions",
            "Maximum number of open positions the bot can hold at once. "
            "Higher values = more diversification but harder to monitor. "
            "✓ IMMEDIATE: Reducing this does NOT auto-close positions. Bot just won't open new ones until below limit.",
            var_type="int", default_value=self.bot.config.max_positions
        )
        self.config_entries['max_positions'].pack(fill=tk.X)
        
        self.config_entries['max_long_term_positions'] = ConfigEntry(
            content2, "Max Long-Term Positions",
            "Maximum positions for markets resolving in more than 7 days. "
            "Long-term positions tie up capital but can have larger payoffs. "
            "✓ IMMEDIATE: Reducing this does NOT auto-close positions.",
            var_type="int", default_value=self.bot.config.max_long_term_positions
        )
        self.config_entries['max_long_term_positions'].pack(fill=tk.X)
        
        self.config_entries['max_swing_positions'] = ConfigEntry(
            content2, "Max Swing Positions",
            "Maximum positions for short-term swing trades (< 7 days). "
            "Swing trades aim for quick profits but require more active management. "
            "✓ IMMEDIATE: Reducing this does NOT auto-close positions.",
            var_type="int", default_value=self.bot.config.max_swing_positions
        )
        self.config_entries['max_swing_positions'].pack(fill=tk.X)
        
        # =====================================================================
        # Section 3: Trading Filters
        # =====================================================================
        section3 = CollapsibleSection(scrollable_frame, "🎯 Trading Filters", initially_open=False)
        section3.pack(fill=tk.X, padx=10, pady=5)
        content3 = section3.get_content_frame()
        
        self.config_entries['min_price'] = ConfigEntry(
            content3, "Min Price",
            "Don't buy outcomes priced below this value. Very low prices often indicate "
            "unlikely outcomes with high risk. Range: 0.01 - 0.50",
            var_type="float", default_value=self.bot.config.min_price
        )
        self.config_entries['min_price'].pack(fill=tk.X)
        
        self.config_entries['max_price'] = ConfigEntry(
            content3, "Max Price",
            "Don't buy outcomes priced above this value. Very high prices have limited "
            "upside potential. Range: 0.50 - 0.99",
            var_type="float", default_value=self.bot.config.max_price
        )
        self.config_entries['max_price'].pack(fill=tk.X)
        
        self.config_entries['min_days'] = ConfigEntry(
            content3, "Min Days to Resolution",
            "Minimum days until market resolves. Very short timeframes may not allow "
            "enough time for price movement.",
            var_type="float", default_value=self.bot.config.min_days
        )
        self.config_entries['min_days'].pack(fill=tk.X)
        
        self.config_entries['max_days'] = ConfigEntry(
            content3, "Max Days to Resolution",
            "Maximum days until market resolves. Very long timeframes tie up capital "
            "and have more uncertainty.",
            var_type="float", default_value=self.bot.config.max_days
        )
        self.config_entries['max_days'].pack(fill=tk.X)
        
        self.config_entries['min_volume'] = ConfigEntry(
            content3, "Min Volume",
            "Minimum total trading volume in dollars. Low volume markets may have "
            "poor liquidity and wider spreads.",
            var_type="dollar", default_value=self.bot.config.min_volume
        )
        self.config_entries['min_volume'].pack(fill=tk.X)
        
        self.config_entries['min_liquidity'] = ConfigEntry(
            content3, "Min Liquidity",
            "Minimum available liquidity in dollars. Low liquidity means larger trades "
            "may significantly move the price (slippage).",
            var_type="dollar", default_value=self.bot.config.min_liquidity
        )
        self.config_entries['min_liquidity'].pack(fill=tk.X)
        
        # =====================================================================
        # Section 4: Strategy Thresholds
        # =====================================================================
        section4 = CollapsibleSection(scrollable_frame, "📈 Strategy Thresholds", initially_open=False)
        section4.pack(fill=tk.X, padx=10, pady=5)
        content4 = section4.get_content_frame()
        
        self.config_entries['min_g_score'] = ConfigEntry(
            content4, "Min G-Score",
            "Minimum growth rate (g) score required. G-score measures expected return per day "
            "until resolution. Higher = better risk-adjusted opportunity. Typical range: 0.0001 - 0.01",
            var_type="float", default_value=self.bot.config.min_g_score
        )
        self.config_entries['min_g_score'].pack(fill=tk.X)
        
        self.config_entries['min_expected_roi'] = ConfigEntry(
            content4, "Min Expected ROI",
            "Minimum expected return on investment. The bot won't buy if the potential "
            "profit is below this threshold.",
            var_type="percent", default_value=self.bot.config.min_expected_roi
        )
        self.config_entries['min_expected_roi'].pack(fill=tk.X)
        
        self.config_entries['confidence_threshold'] = ConfigEntry(
            content4, "Confidence Threshold",
            "Minimum confidence score (0-1) required to make any trade. "
            "Below this, opportunities are skipped entirely.",
            var_type="percent", default_value=self.bot.config.confidence_threshold
        )
        self.config_entries['confidence_threshold'].pack(fill=tk.X)
        
        self.config_entries['high_confidence_threshold'] = ConfigEntry(
            content4, "High Confidence Threshold",
            "Confidence score required for full-size trades. Between this and the minimum "
            "threshold, only test trades are made.",
            var_type="percent", default_value=self.bot.config.high_confidence_threshold
        )
        self.config_entries['high_confidence_threshold'].pack(fill=tk.X)
        
        # =====================================================================
        # Section 5: Risk Management - Long Term
        # =====================================================================
        section5 = CollapsibleSection(scrollable_frame, "🛡️ Risk Management - Long Term", initially_open=True)
        section5.pack(fill=tk.X, padx=10, pady=5)
        content5 = section5.get_content_frame()
        
        self.config_entries['stop_loss_pct'] = ConfigEntry(
            content5, "Stop Loss",
            "Automatically sell if position drops by this percentage. "
            "Protects against large losses. Example: 30% means sell if down 30%. "
            "⚡ IMMEDIATE: Applies to ALL existing positions on next price update! "
            "Lowering this may trigger immediate sells on positions already below the new threshold.",
            var_type="percent", default_value=self.bot.config.stop_loss_pct
        )
        self.config_entries['stop_loss_pct'].pack(fill=tk.X)
        
        self.config_entries['take_profit_pct'] = ConfigEntry(
            content5, "Take Profit",
            "Automatically sell if position gains this percentage. "
            "Locks in profits. Example: 50% means sell when up 50%. "
            "⚡ IMMEDIATE: Applies to ALL existing positions on next price update! "
            "Lowering this may trigger immediate sells on profitable positions.",
            var_type="percent", default_value=self.bot.config.take_profit_pct
        )
        self.config_entries['take_profit_pct'].pack(fill=tk.X)
        
        # =====================================================================
        # Section 6: Risk Management - Swing Trades
        # =====================================================================
        section6 = CollapsibleSection(scrollable_frame, "⚡ Risk Management - Swing Trades", initially_open=True)
        section6.pack(fill=tk.X, padx=10, pady=5)
        content6 = section6.get_content_frame()
        
        self.config_entries['swing_trade_enabled'] = ConfigEntry(
            content6, "Enable Swing Trading",
            "Allow short-term swing trades on high-volume markets. "
            "Swing trades aim for quick 10-15% profits within days.",
            var_type="bool", default_value=self.bot.config.swing_trade_enabled
        )
        self.config_entries['swing_trade_enabled'].pack(fill=tk.X)
        
        self.config_entries['swing_stop_loss_pct'] = ConfigEntry(
            content6, "Swing Stop Loss",
            "Stop loss percentage for swing trades. Usually tighter than long-term "
            "since swing trades have shorter timeframes. "
            "⚡ IMMEDIATE: Applies to ALL existing swing positions on next price update!",
            var_type="percent", default_value=self.bot.config.swing_stop_loss_pct
        )
        self.config_entries['swing_stop_loss_pct'].pack(fill=tk.X)
        
        self.config_entries['swing_take_profit_pct'] = ConfigEntry(
            content6, "Swing Take Profit",
            "Take profit percentage for swing trades. Usually lower than long-term "
            "to capture quick gains. "
            "⚡ IMMEDIATE: Applies to ALL existing swing positions on next price update!",
            var_type="percent", default_value=self.bot.config.swing_take_profit_pct
        )
        self.config_entries['swing_take_profit_pct'].pack(fill=tk.X)
        
        self.config_entries['swing_min_volume'] = ConfigEntry(
            content6, "Swing Min Volume",
            "Minimum market volume required for swing trades. High volume ensures "
            "liquidity for quick entry and exit.",
            var_type="dollar", default_value=self.bot.config.swing_min_volume
        )
        self.config_entries['swing_min_volume'].pack(fill=tk.X)
        
        # =====================================================================
        # Section 6.5: Realistic Execution Simulation
        # =====================================================================
        section6_5 = CollapsibleSection(scrollable_frame, "📊 Realistic Execution", initially_open=False)
        section6_5.pack(fill=tk.X, padx=10, pady=5)
        content6_5 = section6_5.get_content_frame()
        
        self.config_entries['realistic_execution'] = ConfigEntry(
            content6_5, "Enable Realistic Execution",
            "Simulate realistic trade execution by walking the order book. "
            "When enabled, trades account for slippage, liquidity depth, and execution delays. "
            "Disable for faster (but less realistic) simulation. "
            "✓ IMMEDIATE: Applies to new trades only.",
            var_type="bool", default_value=self.bot.config.realistic_execution
        )
        self.config_entries['realistic_execution'].pack(fill=tk.X)
        
        self.config_entries['max_slippage_pct'] = ConfigEntry(
            content6_5, "Max Slippage %",
            "Maximum acceptable slippage percentage. Trades that would cause more "
            "slippage than this are rejected. Higher = more trades execute, but at worse prices. "
            "✓ IMMEDIATE: Applies to new trades only.",
            var_type="float", default_value=self.bot.config.max_slippage_pct
        )
        self.config_entries['max_slippage_pct'].pack(fill=tk.X)
        
        self.config_entries['min_book_depth_multiplier'] = ConfigEntry(
            content6_5, "Min Liquidity Multiplier",
            "Required order book depth as multiplier of trade size. "
            "Example: 1.5 means the book must have 1.5x your order size available. "
            "Higher = safer but fewer trades. "
            "✓ IMMEDIATE: Applies to new trades only.",
            var_type="float", default_value=self.bot.config.min_book_depth_multiplier
        )
        self.config_entries['min_book_depth_multiplier'].pack(fill=tk.X)
        
        self.config_entries['execution_delay_enabled'] = ConfigEntry(
            content6_5, "Simulate Execution Delay",
            "Add random price movement to simulate the time between decision and fill. "
            "In real trading, prices can move while your order is being placed. "
            "✓ IMMEDIATE: Applies to new trades only.",
            var_type="bool", default_value=self.bot.config.execution_delay_enabled
        )
        self.config_entries['execution_delay_enabled'].pack(fill=tk.X)
        
        self.config_entries['execution_delay_max_pct'] = ConfigEntry(
            content6_5, "Max Delay Movement %",
            "Maximum random price movement (±) during simulated execution delay. "
            "Example: 2.0 means price can move ±2% between decision and fill. "
            "✓ IMMEDIATE: Applies to new trades only.",
            var_type="float", default_value=self.bot.config.execution_delay_max_pct
        )
        self.config_entries['execution_delay_max_pct'].pack(fill=tk.X)
        
        # =====================================================================
        # Section 7: Timing & Performance
        # =====================================================================
        section7 = CollapsibleSection(scrollable_frame, "⏱️ Timing & Performance", initially_open=False)
        section7.pack(fill=tk.X, padx=10, pady=5)
        content7 = section7.get_content_frame()
        
        self.config_entries['scan_interval_seconds'] = ConfigEntry(
            content7, "Scan Interval",
            "Seconds between market scans for new opportunities. Lower = more responsive "
            "but higher API usage and CPU load. Recommended: 20-60 seconds.",
            var_type="seconds", default_value=self.bot.config.scan_interval_seconds
        )
        self.config_entries['scan_interval_seconds'].pack(fill=tk.X)
        
        self.config_entries['max_markets_per_scan'] = ConfigEntry(
            content7, "Markets Per Scan",
            "Maximum number of markets to analyze each scan cycle. "
            "Higher = more opportunities but slower scans.",
            var_type="int", default_value=self.bot.config.max_markets_per_scan
        )
        self.config_entries['max_markets_per_scan'].pack(fill=tk.X)
        
        self.config_entries['price_update_interval'] = ConfigEntry(
            content7, "Position Update Interval",
            "⚠️ DISPLAY ONLY: This setting controls the Overview tab timer display. "
            "Actual position updates run on a fixed 5-second cycle for responsiveness. "
            "This value does NOT change actual update frequency.",
            var_type="seconds", default_value=self.bot.config.price_update_interval
        )
        self.config_entries['price_update_interval'].pack(fill=tk.X)
        
        self.config_entries['market_cooldown_minutes'] = ConfigEntry(
            content7, "Market Cooldown",
            "Minutes before re-scanning the same market. Prevents repeatedly analyzing "
            "markets that were just rejected.",
            var_type="minutes", default_value=self.bot.config.market_cooldown_minutes
        )
        self.config_entries['market_cooldown_minutes'].pack(fill=tk.X)
        
        # =====================================================================
        # Section 8: Insider Detection
        # =====================================================================
        section8 = CollapsibleSection(scrollable_frame, "🔔 Insider Detection", initially_open=False)
        section8.pack(fill=tk.X, padx=10, pady=5)
        content8 = section8.get_content_frame()
        
        self.config_entries['large_trade_threshold'] = ConfigEntry(
            content8, "Large Trade Alert",
            "Alert when a single trade exceeds this dollar amount. "
            "Large trades may indicate insider knowledge.",
            var_type="dollar", default_value=self.insider_detector.config.large_trade_threshold
        )
        self.config_entries['large_trade_threshold'].pack(fill=tk.X)
        
        self.config_entries['insider_poll_interval'] = ConfigEntry(
            content8, "Insider Poll Interval",
            "Seconds between checks for suspicious trading activity. "
            "Lower = faster alerts but more API usage.",
            var_type="seconds", default_value=self.insider_detector.config.poll_interval_seconds
        )
        self.config_entries['insider_poll_interval'].pack(fill=tk.X)
        
        # =====================================================================
        # Section 9: News Analysis
        # =====================================================================
        section9 = CollapsibleSection(scrollable_frame, "📰 News Analysis", initially_open=False)
        section9.pack(fill=tk.X, padx=10, pady=5)
        content9 = section9.get_content_frame()
        
        self.config_entries['use_news_analysis'] = ConfigEntry(
            content9, "Enable News Analysis",
            "Use news sentiment to influence trading decisions. "
            "When news aligns with a position, confidence is boosted. "
            "⚠️ PARTIAL: Disabling works immediately. Enabling mid-simulation may require restart if it wasn't enabled at startup.",
            var_type="bool", default_value=self.bot.config.use_news_analysis
        )
        self.config_entries['use_news_analysis'].pack(fill=tk.X)
        
        self.config_entries['news_confidence_boost'] = ConfigEntry(
            content9, "News Confidence Boost",
            "How much to boost confidence when news sentiment aligns with the trade direction. "
            "Example: 15% means add 0.15 to confidence score.",
            var_type="percent", default_value=self.bot.config.news_confidence_boost
        )
        self.config_entries['news_confidence_boost'].pack(fill=tk.X)
        
        # =====================================================================
        # Section 10: Category Limits
        # =====================================================================
        section10 = CollapsibleSection(scrollable_frame, "🏷️ Category Limits", initially_open=False)
        section10.pack(fill=tk.X, padx=10, pady=5)
        content10 = section10.get_content_frame()
        
        # Add info label
        tk.Label(
            content10,
            text="Maximum positions allowed per market category (for diversification). ✓ All limits apply immediately to new trades only.",
            font=("Segoe UI", 8, "italic"),
            bg=Theme.BG_SECONDARY,
            fg=Theme.TEXT_MUTED,
        ).pack(fill=tk.X, pady=(0, 10))
        
        category_descriptions = {
            'sports': "Sports events including NBA, NFL, MLB, soccer, etc.",
            'politics': "Political events, elections, legislation, government actions.",
            'crypto': "Cryptocurrency prices, events, regulations.",
            'entertainment': "Movies, music, awards shows, celebrities.",
            'finance': "Stock market, economic indicators, corporate events.",
            'technology': "Tech companies, product launches, AI developments.",
            'world_events': "International affairs, conflicts, treaties.",
            'other': "Markets that don't fit other categories.",
        }
        
        for category, limit in self.bot.config.category_limits.items():
            self.config_entries[f'category_{category}'] = ConfigEntry(
                content10, f"{category.replace('_', ' ').title()}",
                category_descriptions.get(category, f"Maximum positions for {category} markets."),
                var_type="int", default_value=limit
            )
            self.config_entries[f'category_{category}'].pack(fill=tk.X)
        
        # =====================================================================
        # Buttons - Row 1
        # =====================================================================
        button_frame = tk.Frame(scrollable_frame, bg=Theme.BG_PRIMARY)
        button_frame.pack(fill=tk.X, padx=10, pady=(20, 5))
        
        tk.Button(
            button_frame,
            text="✓ Apply Changes",
            font=("Segoe UI", 10, "bold"),
            bg=Theme.ACCENT_GREEN,
            fg=Theme.TEXT_PRIMARY,
            relief=tk.FLAT,
            padx=20,
            pady=8,
            cursor="hand2",
            command=self._apply_config_changes,
        ).pack(side=tk.LEFT, padx=(0, 10))
        
        tk.Button(
            button_frame,
            text="↺ Reset to Defaults",
            font=("Segoe UI", 10),
            bg=Theme.BG_TERTIARY,
            fg=Theme.TEXT_PRIMARY,
            relief=tk.FLAT,
            padx=20,
            pady=8,
            cursor="hand2",
            command=self._reset_config_to_defaults,
        ).pack(side=tk.LEFT, padx=(0, 10))
        
        tk.Button(
            button_frame,
            text="↻ Reload Current",
            font=("Segoe UI", 10),
            bg=Theme.BG_TERTIARY,
            fg=Theme.TEXT_PRIMARY,
            relief=tk.FLAT,
            padx=20,
            pady=8,
            cursor="hand2",
            command=self._reload_config_display,
        ).pack(side=tk.LEFT)
        
        # =====================================================================
        # Buttons - Row 2 (Force Holdings)
        # =====================================================================
        button_frame2 = tk.Frame(scrollable_frame, bg=Theme.BG_PRIMARY)
        button_frame2.pack(fill=tk.X, padx=10, pady=(5, 10))
        
        tk.Button(
            button_frame2,
            text="⚠️ Force Holdings to Settings",
            font=("Segoe UI", 10, "bold"),
            bg=Theme.ACCENT_RED,
            fg=Theme.TEXT_PRIMARY,
            relief=tk.FLAT,
            padx=20,
            pady=8,
            cursor="hand2",
            command=self._show_force_holdings_warning,
        ).pack(side=tk.LEFT, padx=(0, 10))
        
        tk.Label(
            button_frame2,
            text="Sells positions to enforce all limits (position count, size, categories)",
            font=("Segoe UI", 8, "italic"),
            bg=Theme.BG_PRIMARY,
            fg=Theme.TEXT_MUTED,
        ).pack(side=tk.LEFT)
        
        # Status label
        self.config_status = tk.Label(
            button_frame2,
            text="",
            font=("Segoe UI", 9),
            bg=Theme.BG_PRIMARY,
            fg=Theme.ACCENT_GREEN,
        )
        self.config_status.pack(side=tk.RIGHT)
    
    def _apply_config_changes(self) -> None:
        """Apply all configuration changes from the Config tab."""
        try:
            # Capital & Position Sizing
            self.bot.config.initial_capital = self.config_entries['initial_capital'].get_value() or 10000.0
            self.bot.config.max_position_size = self.config_entries['max_position_size'].get_value() or 500.0
            self.bot.config.max_portfolio_pct = self.config_entries['max_portfolio_pct'].get_value() or 0.10
            self.bot.config.test_trade_size = self.config_entries['test_trade_size'].get_value() or 25.0
            self.bot.config.test_trade_enabled = self.config_entries['test_trade_enabled'].get_value()
            
            # Position Limits
            self.bot.config.max_positions = self.config_entries['max_positions'].get_value() or 50
            self.bot.config.max_long_term_positions = self.config_entries['max_long_term_positions'].get_value() or 40
            self.bot.config.max_swing_positions = self.config_entries['max_swing_positions'].get_value() or 30
            
            # Trading Filters
            self.bot.config.min_price = self.config_entries['min_price'].get_value() or 0.03
            self.bot.config.max_price = self.config_entries['max_price'].get_value() or 0.85
            self.bot.config.min_days = self.config_entries['min_days'].get_value() or 0.05
            self.bot.config.max_days = self.config_entries['max_days'].get_value() or 365.0
            self.bot.config.min_volume = self.config_entries['min_volume'].get_value() or 500.0
            self.bot.config.min_liquidity = self.config_entries['min_liquidity'].get_value() or 200.0
            
            # Strategy Thresholds
            self.bot.config.min_g_score = self.config_entries['min_g_score'].get_value() or 0.0003
            self.bot.config.min_expected_roi = self.config_entries['min_expected_roi'].get_value() or 0.03
            self.bot.config.confidence_threshold = self.config_entries['confidence_threshold'].get_value() or 0.45
            self.bot.config.high_confidence_threshold = self.config_entries['high_confidence_threshold'].get_value() or 0.65
            
            # Risk Management - Long Term
            self.bot.config.stop_loss_pct = self.config_entries['stop_loss_pct'].get_value() or 0.30
            self.bot.config.take_profit_pct = self.config_entries['take_profit_pct'].get_value() or 0.50
            
            # Risk Management - Swing Trades
            self.bot.config.swing_trade_enabled = self.config_entries['swing_trade_enabled'].get_value()
            self.bot.config.swing_stop_loss_pct = self.config_entries['swing_stop_loss_pct'].get_value() or 0.10
            self.bot.config.swing_take_profit_pct = self.config_entries['swing_take_profit_pct'].get_value() or 0.15
            self.bot.config.swing_min_volume = self.config_entries['swing_min_volume'].get_value() or 50000.0
            
            # Timing & Performance
            self.bot.config.scan_interval_seconds = self.config_entries['scan_interval_seconds'].get_value() or 30
            self.bot.config.max_markets_per_scan = self.config_entries['max_markets_per_scan'].get_value() or 100
            self.bot.config.price_update_interval = self.config_entries['price_update_interval'].get_value() or 5
            self.bot.config.market_cooldown_minutes = self.config_entries['market_cooldown_minutes'].get_value() or 3
            
            # Insider Detection
            self.insider_detector.config.large_trade_threshold = self.config_entries['large_trade_threshold'].get_value() or 10000.0
            self.insider_detector.config.poll_interval_seconds = self.config_entries['insider_poll_interval'].get_value() or 30
            
            # News Analysis
            self.bot.config.use_news_analysis = self.config_entries['use_news_analysis'].get_value()
            self.bot.config.news_confidence_boost = self.config_entries['news_confidence_boost'].get_value() or 0.15
            
            # Realistic Execution
            self.bot.config.realistic_execution = self.config_entries['realistic_execution'].get_value()
            self.bot.config.max_slippage_pct = self.config_entries['max_slippage_pct'].get_value() or 5.0
            self.bot.config.min_book_depth_multiplier = self.config_entries['min_book_depth_multiplier'].get_value() or 1.5
            self.bot.config.execution_delay_enabled = self.config_entries['execution_delay_enabled'].get_value()
            self.bot.config.execution_delay_max_pct = self.config_entries['execution_delay_max_pct'].get_value() or 2.0
            
            # Category Limits
            for category in self.bot.config.category_limits.keys():
                entry_key = f'category_{category}'
                if entry_key in self.config_entries:
                    self.bot.config.category_limits[category] = self.config_entries[entry_key].get_value() or 5
            
            # Show success message
            self.config_status.configure(text="✓ Changes applied!", fg=Theme.ACCENT_GREEN)
            self.chat.add_message("Configuration updated successfully!", "success", "Config")
            
            # Clear status after 3 seconds
            self.after(3000, lambda: self.config_status.configure(text=""))
            
        except Exception as e:
            self.config_status.configure(text=f"✗ Error: {e}", fg=Theme.ACCENT_RED)
            self.chat.add_message(f"Config error: {e}", "error", "Config")
    
    def _reset_config_to_defaults(self) -> None:
        """Reset all configuration to default values."""
        if not messagebox.askyesno("Confirm Reset", "Reset all settings to default values?"):
            return
        
        # Create fresh config
        from auto_trader import BotConfig
        default_config = BotConfig()
        
        # Apply defaults to entries
        defaults = {
            'initial_capital': default_config.initial_capital,
            'max_position_size': default_config.max_position_size,
            'max_portfolio_pct': default_config.max_portfolio_pct,
            'test_trade_size': default_config.test_trade_size,
            'test_trade_enabled': default_config.test_trade_enabled,
            'max_positions': default_config.max_positions,
            'max_long_term_positions': default_config.max_long_term_positions,
            'max_swing_positions': default_config.max_swing_positions,
            'min_price': default_config.min_price,
            'max_price': default_config.max_price,
            'min_days': default_config.min_days,
            'max_days': default_config.max_days,
            'min_volume': default_config.min_volume,
            'min_liquidity': default_config.min_liquidity,
            'min_g_score': default_config.min_g_score,
            'min_expected_roi': default_config.min_expected_roi,
            'confidence_threshold': default_config.confidence_threshold,
            'high_confidence_threshold': default_config.high_confidence_threshold,
            'stop_loss_pct': default_config.stop_loss_pct,
            'take_profit_pct': default_config.take_profit_pct,
            'swing_trade_enabled': default_config.swing_trade_enabled,
            'swing_stop_loss_pct': default_config.swing_stop_loss_pct,
            'swing_take_profit_pct': default_config.swing_take_profit_pct,
            'swing_min_volume': default_config.swing_min_volume,
            'scan_interval_seconds': default_config.scan_interval_seconds,
            'max_markets_per_scan': default_config.max_markets_per_scan,
            'price_update_interval': default_config.price_update_interval,
            'market_cooldown_minutes': default_config.market_cooldown_minutes,
            'large_trade_threshold': 10000.0,
            'insider_poll_interval': 30,
            'use_news_analysis': default_config.use_news_analysis,
            'news_confidence_boost': default_config.news_confidence_boost,
            # Realistic Execution
            'realistic_execution': default_config.realistic_execution,
            'max_slippage_pct': default_config.max_slippage_pct,
            'min_book_depth_multiplier': default_config.min_book_depth_multiplier,
            'execution_delay_enabled': default_config.execution_delay_enabled,
            'execution_delay_max_pct': default_config.execution_delay_max_pct,
        }
        
        # Add category defaults
        for category, limit in default_config.category_limits.items():
            defaults[f'category_{category}'] = limit
        
        # Update entries
        for key, value in defaults.items():
            if key in self.config_entries:
                self.config_entries[key].set_value(value)
        
        self.config_status.configure(text="↺ Reset to defaults", fg=Theme.ACCENT_YELLOW)
        self.after(3000, lambda: self.config_status.configure(text=""))
    
    def _reload_config_display(self) -> None:
        """Reload config display with current bot values."""
        # Capital & Position Sizing
        self.config_entries['initial_capital'].set_value(self.bot.config.initial_capital)
        self.config_entries['max_position_size'].set_value(self.bot.config.max_position_size)
        self.config_entries['max_portfolio_pct'].set_value(self.bot.config.max_portfolio_pct)
        self.config_entries['test_trade_size'].set_value(self.bot.config.test_trade_size)
        self.config_entries['test_trade_enabled'].set_value(self.bot.config.test_trade_enabled)
        
        # Position Limits
        self.config_entries['max_positions'].set_value(self.bot.config.max_positions)
        self.config_entries['max_long_term_positions'].set_value(self.bot.config.max_long_term_positions)
        self.config_entries['max_swing_positions'].set_value(self.bot.config.max_swing_positions)
        
        # Trading Filters
        self.config_entries['min_price'].set_value(self.bot.config.min_price)
        self.config_entries['max_price'].set_value(self.bot.config.max_price)
        self.config_entries['min_days'].set_value(self.bot.config.min_days)
        self.config_entries['max_days'].set_value(self.bot.config.max_days)
        self.config_entries['min_volume'].set_value(self.bot.config.min_volume)
        self.config_entries['min_liquidity'].set_value(self.bot.config.min_liquidity)
        
        # Strategy Thresholds
        self.config_entries['min_g_score'].set_value(self.bot.config.min_g_score)
        self.config_entries['min_expected_roi'].set_value(self.bot.config.min_expected_roi)
        self.config_entries['confidence_threshold'].set_value(self.bot.config.confidence_threshold)
        self.config_entries['high_confidence_threshold'].set_value(self.bot.config.high_confidence_threshold)
        
        # Risk Management
        self.config_entries['stop_loss_pct'].set_value(self.bot.config.stop_loss_pct)
        self.config_entries['take_profit_pct'].set_value(self.bot.config.take_profit_pct)
        self.config_entries['swing_trade_enabled'].set_value(self.bot.config.swing_trade_enabled)
        self.config_entries['swing_stop_loss_pct'].set_value(self.bot.config.swing_stop_loss_pct)
        self.config_entries['swing_take_profit_pct'].set_value(self.bot.config.swing_take_profit_pct)
        self.config_entries['swing_min_volume'].set_value(self.bot.config.swing_min_volume)
        
        # Timing
        self.config_entries['scan_interval_seconds'].set_value(self.bot.config.scan_interval_seconds)
        self.config_entries['max_markets_per_scan'].set_value(self.bot.config.max_markets_per_scan)
        self.config_entries['price_update_interval'].set_value(self.bot.config.price_update_interval)
        self.config_entries['market_cooldown_minutes'].set_value(self.bot.config.market_cooldown_minutes)
        
        # Insider Detection
        self.config_entries['large_trade_threshold'].set_value(self.insider_detector.config.large_trade_threshold)
        self.config_entries['insider_poll_interval'].set_value(self.insider_detector.config.poll_interval_seconds)
        
        # News Analysis
        self.config_entries['use_news_analysis'].set_value(self.bot.config.use_news_analysis)
        self.config_entries['news_confidence_boost'].set_value(self.bot.config.news_confidence_boost)
        
        # Realistic Execution
        self.config_entries['realistic_execution'].set_value(self.bot.config.realistic_execution)
        self.config_entries['max_slippage_pct'].set_value(self.bot.config.max_slippage_pct)
        self.config_entries['min_book_depth_multiplier'].set_value(self.bot.config.min_book_depth_multiplier)
        self.config_entries['execution_delay_enabled'].set_value(self.bot.config.execution_delay_enabled)
        self.config_entries['execution_delay_max_pct'].set_value(self.bot.config.execution_delay_max_pct)
        
        # Category Limits
        for category, limit in self.bot.config.category_limits.items():
            entry_key = f'category_{category}'
            if entry_key in self.config_entries:
                self.config_entries[entry_key].set_value(limit)
        
        self.config_status.configure(text="↻ Reloaded current values", fg=Theme.ACCENT_BLUE)
        self.after(3000, lambda: self.config_status.configure(text=""))
    
    def _show_force_holdings_warning(self) -> None:
        """Show warning dialog before forcing holdings to settings."""
        # Create warning window
        warning_window = tk.Toplevel(self)
        warning_window.title("⚠️ Force Holdings to Settings")
        warning_window.geometry("500x400")
        warning_window.configure(bg=Theme.BG_PRIMARY)
        warning_window.transient(self)
        warning_window.grab_set()
        
        # Center the window
        warning_window.update_idletasks()
        x = self.winfo_x() + (self.winfo_width() - 500) // 2
        y = self.winfo_y() + (self.winfo_height() - 400) // 2
        warning_window.geometry(f"+{x}+{y}")
        
        # Warning icon and title
        header_frame = tk.Frame(warning_window, bg=Theme.BG_PRIMARY)
        header_frame.pack(fill=tk.X, padx=20, pady=(20, 10))
        
        tk.Label(
            header_frame,
            text="⚠️ WARNING: Massive Trade Changes",
            font=("Segoe UI", 14, "bold"),
            bg=Theme.BG_PRIMARY,
            fg=Theme.ACCENT_RED,
        ).pack()
        
        # Calculate what will happen
        changes = self._calculate_force_holdings_changes()
        
        # Summary frame
        summary_frame = tk.Frame(warning_window, bg=Theme.BG_SECONDARY, relief=tk.FLAT)
        summary_frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=10)
        
        tk.Label(
            summary_frame,
            text="This action will make the following changes:",
            font=("Segoe UI", 10, "bold"),
            bg=Theme.BG_SECONDARY,
            fg=Theme.TEXT_PRIMARY,
        ).pack(anchor="w", padx=15, pady=(15, 10))
        
        # Changes list with scrollbar
        changes_text = tk.Text(
            summary_frame,
            font=("Consolas", 9),
            bg=Theme.BG_TERTIARY,
            fg=Theme.TEXT_PRIMARY,
            relief=tk.FLAT,
            wrap=tk.WORD,
            height=12,
        )
        changes_text.pack(fill=tk.BOTH, expand=True, padx=15, pady=(0, 15))
        
        # Populate changes
        if changes['positions_to_close']:
            changes_text.insert(tk.END, f"🔴 POSITIONS TO CLOSE ({len(changes['positions_to_close'])}):\n", "header")
            for pos in changes['positions_to_close']:
                changes_text.insert(tk.END, f"  • {pos['question'][:40]}...\n")
                changes_text.insert(tk.END, f"    Reason: {pos['reason']}\n", "reason")
                changes_text.insert(tk.END, f"    Value: ${pos['value']:.2f} | P&L: {pos['pnl_pct']:+.1%}\n\n", "value")
        
        if changes['positions_to_reduce']:
            changes_text.insert(tk.END, f"🟡 POSITIONS TO REDUCE ({len(changes['positions_to_reduce'])}):\n", "header")
            for pos in changes['positions_to_reduce']:
                changes_text.insert(tk.END, f"  • {pos['question'][:40]}...\n")
                changes_text.insert(tk.END, f"    Reason: {pos.get('reason', 'Exceeds size limit')}\n", "reason")
                changes_text.insert(tk.END, f"    Reduce by: ${pos['reduce_amount']:.2f} ({pos['reduce_pct']:.0%} of position)\n", "reason")
                changes_text.insert(tk.END, f"    Current: ${pos['current_value']:.2f} → ${pos['new_value']:.2f}\n\n", "value")
        
        if not changes['positions_to_close'] and not changes['positions_to_reduce']:
            changes_text.insert(tk.END, "✅ No changes needed!\n\n", "header")
            changes_text.insert(tk.END, "All current holdings already comply with settings.\n")
        
        # Summary stats
        changes_text.insert(tk.END, "\n" + "="*50 + "\n")
        changes_text.insert(tk.END, f"📊 SUMMARY:\n", "header")
        changes_text.insert(tk.END, f"  Total positions to close: {len(changes['positions_to_close'])}\n")
        changes_text.insert(tk.END, f"  Total positions to reduce: {len(changes['positions_to_reduce'])}\n")
        changes_text.insert(tk.END, f"  Estimated cash freed: ${changes['total_value_freed']:.2f}\n")
        changes_text.insert(tk.END, f"  Estimated realized P&L: ${changes['total_pnl']:.2f}\n")
        
        changes_text.configure(state=tk.DISABLED)
        
        # Configure text tags for colors
        changes_text.tag_configure("header", foreground=Theme.ACCENT_YELLOW, font=("Consolas", 9, "bold"))
        changes_text.tag_configure("reason", foreground=Theme.TEXT_MUTED)
        changes_text.tag_configure("value", foreground=Theme.TEXT_SECONDARY)
        
        # Warning text
        tk.Label(
            warning_window,
            text="This cannot be undone! Positions will be sold at current market prices.",
            font=("Segoe UI", 9, "italic"),
            bg=Theme.BG_PRIMARY,
            fg=Theme.ACCENT_RED,
        ).pack(pady=(0, 10))
        
        # Buttons
        btn_frame = tk.Frame(warning_window, bg=Theme.BG_PRIMARY)
        btn_frame.pack(fill=tk.X, padx=20, pady=(0, 20))
        
        tk.Button(
            btn_frame,
            text="❌ I'm Not Sure - Cancel",
            font=("Segoe UI", 10),
            bg=Theme.BG_TERTIARY,
            fg=Theme.TEXT_PRIMARY,
            relief=tk.FLAT,
            padx=20,
            pady=10,
            cursor="hand2",
            command=warning_window.destroy,
        ).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(0, 5))
        
        def execute_and_close():
            warning_window.destroy()
            self._execute_force_holdings(changes)
        
        # Only enable confirm if there are changes
        confirm_state = tk.NORMAL if (changes['positions_to_close'] or changes['positions_to_reduce']) else tk.DISABLED
        
        tk.Button(
            btn_frame,
            text="✓ I'm Sure - Execute",
            font=("Segoe UI", 10, "bold"),
            bg=Theme.ACCENT_RED,
            fg=Theme.TEXT_PRIMARY,
            relief=tk.FLAT,
            padx=20,
            pady=10,
            cursor="hand2",
            command=execute_and_close,
            state=confirm_state,
        ).pack(side=tk.RIGHT, expand=True, fill=tk.X, padx=(5, 0))
    
    def _calculate_force_holdings_changes(self) -> dict:
        """Calculate what changes would be needed to force holdings to settings."""
        changes = {
            'positions_to_close': [],
            'positions_to_reduce': [],
            'total_value_freed': 0.0,
            'total_pnl': 0.0,
        }
        
        if not self.bot.open_trades:
            return changes
        
        # Get all trades as list with scores for ranking
        trades_with_scores = []
        for trade_id, trade in self.bot.open_trades.items():
            # Calculate a score for each position (lower = worse, sell first)
            # Score based on: P&L %, time held, volume, g-score potential
            score = trade.pnl_pct * 100  # P&L is main factor
            score += min(trade.volume / 10000, 5)  # Volume bonus (max 5)
            score += min(trade.resolution_days / 30, 3)  # Time value (max 3)
            
            trades_with_scores.append({
                'trade_id': trade_id,
                'trade': trade,
                'score': score,
                'category': getattr(trade, 'category', 'other'),
                'trade_type': getattr(trade, 'trade_type', 'long'),
            })
        
        # Sort by score (worst first - to sell first)
        trades_with_scores.sort(key=lambda x: x['score'])
        
        # Track what we're keeping
        remaining_trades = list(trades_with_scores)
        
        # 1. Check total position limit
        max_positions = self.bot.config.max_positions
        if len(remaining_trades) > max_positions:
            excess = len(remaining_trades) - max_positions
            for item in remaining_trades[:excess]:
                trade = item['trade']
                changes['positions_to_close'].append({
                    'trade_id': item['trade_id'],
                    'question': trade.question,
                    'reason': f"Exceeds max positions ({max_positions})",
                    'value': trade.shares * trade.current_price,
                    'pnl': trade.pnl,
                    'pnl_pct': trade.pnl_pct,
                })
                changes['total_value_freed'] += trade.shares * trade.current_price
                changes['total_pnl'] += trade.pnl
            remaining_trades = remaining_trades[excess:]
        
        # 2. Check swing position limit
        max_swing = self.bot.config.max_swing_positions
        swing_trades = [t for t in remaining_trades if t['trade_type'] == 'swing']
        if len(swing_trades) > max_swing:
            excess = len(swing_trades) - max_swing
            for item in swing_trades[:excess]:
                trade = item['trade']
                if item['trade_id'] not in [c['trade_id'] for c in changes['positions_to_close']]:
                    changes['positions_to_close'].append({
                        'trade_id': item['trade_id'],
                        'question': trade.question,
                        'reason': f"Exceeds max swing positions ({max_swing})",
                        'value': trade.shares * trade.current_price,
                        'pnl': trade.pnl,
                        'pnl_pct': trade.pnl_pct,
                    })
                    changes['total_value_freed'] += trade.shares * trade.current_price
                    changes['total_pnl'] += trade.pnl
                    remaining_trades.remove(item)
        
        # 3. Check long-term position limit
        max_long = self.bot.config.max_long_term_positions
        long_trades = [t for t in remaining_trades if t['trade_type'] == 'long']
        if len(long_trades) > max_long:
            excess = len(long_trades) - max_long
            for item in long_trades[:excess]:
                trade = item['trade']
                if item['trade_id'] not in [c['trade_id'] for c in changes['positions_to_close']]:
                    changes['positions_to_close'].append({
                        'trade_id': item['trade_id'],
                        'question': trade.question,
                        'reason': f"Exceeds max long-term positions ({max_long})",
                        'value': trade.shares * trade.current_price,
                        'pnl': trade.pnl,
                        'pnl_pct': trade.pnl_pct,
                    })
                    changes['total_value_freed'] += trade.shares * trade.current_price
                    changes['total_pnl'] += trade.pnl
                    remaining_trades.remove(item)
        
        # 4. Check category limits
        category_counts = {}
        for item in remaining_trades:
            cat = item['category']
            category_counts[cat] = category_counts.get(cat, 0) + 1
        
        for category, count in category_counts.items():
            limit = self.bot.config.category_limits.get(category, 5)
            if count > limit:
                # Find trades in this category to close
                cat_trades = [t for t in remaining_trades if t['category'] == category]
                cat_trades.sort(key=lambda x: x['score'])  # Worst first
                excess = count - limit
                for item in cat_trades[:excess]:
                    trade = item['trade']
                    if item['trade_id'] not in [c['trade_id'] for c in changes['positions_to_close']]:
                        changes['positions_to_close'].append({
                            'trade_id': item['trade_id'],
                            'question': trade.question,
                            'reason': f"Exceeds {category} limit ({limit})",
                            'value': trade.shares * trade.current_price,
                            'pnl': trade.pnl,
                            'pnl_pct': trade.pnl_pct,
                        })
                        changes['total_value_freed'] += trade.shares * trade.current_price
                        changes['total_pnl'] += trade.pnl
                        remaining_trades.remove(item)
        
        # 5. Check max position size - reduce oversized positions
        max_size = self.bot.config.max_position_size
        closed_ids = [c['trade_id'] for c in changes['positions_to_close']]
        
        for item in remaining_trades:
            if item['trade_id'] in closed_ids:
                continue
            trade = item['trade']
            current_value = trade.shares * trade.current_price
            
            if current_value > max_size:
                reduce_amount = current_value - max_size
                reduce_pct = reduce_amount / current_value
                
                changes['positions_to_reduce'].append({
                    'trade_id': item['trade_id'],
                    'question': trade.question,
                    'current_value': current_value,
                    'new_value': max_size,
                    'reduce_amount': reduce_amount,
                    'reduce_pct': reduce_pct,
                    'shares_to_sell': reduce_amount / trade.current_price,
                    'pnl_on_sold': (trade.current_price - trade.entry_price) * (reduce_amount / trade.current_price),
                    'reason': f"Exceeds max position size (${max_size:.0f})",
                })
                changes['total_value_freed'] += reduce_amount
                changes['total_pnl'] += (trade.current_price - trade.entry_price) * (reduce_amount / trade.current_price)
        
        # 6. Check max portfolio percentage - reduce if any position is too large % of portfolio
        # Calculate total portfolio value first
        total_portfolio = self.bot.cash_balance + sum(
            t.shares * t.current_price for t in self.bot.open_trades.values()
        )
        max_portfolio_pct = self.bot.config.max_portfolio_pct
        max_value_by_pct = total_portfolio * max_portfolio_pct
        
        # Track already-reduced positions to avoid double-reducing
        already_reduced_ids = [r['trade_id'] for r in changes['positions_to_reduce']]
        
        for item in remaining_trades:
            if item['trade_id'] in closed_ids:
                continue
            trade = item['trade']
            current_value = trade.shares * trade.current_price
            
            # Check if this position exceeds max portfolio %
            position_pct = current_value / total_portfolio if total_portfolio > 0 else 0
            
            if position_pct > max_portfolio_pct:
                # Check if already being reduced for position size
                if item['trade_id'] in already_reduced_ids:
                    # Find the existing reduction and update if this requires more reduction
                    for reduction in changes['positions_to_reduce']:
                        if reduction['trade_id'] == item['trade_id']:
                            # If portfolio % requires more reduction than position size limit
                            if max_value_by_pct < reduction['new_value']:
                                additional_reduce = reduction['new_value'] - max_value_by_pct
                                reduction['new_value'] = max_value_by_pct
                                reduction['reduce_amount'] = current_value - max_value_by_pct
                                reduction['reduce_pct'] = reduction['reduce_amount'] / current_value
                                reduction['shares_to_sell'] = reduction['reduce_amount'] / trade.current_price
                                reduction['pnl_on_sold'] = (trade.current_price - trade.entry_price) * reduction['shares_to_sell']
                                reduction['reason'] = f"Exceeds max portfolio % ({max_portfolio_pct:.0%}) AND max size"
                                # Update totals
                                changes['total_value_freed'] += additional_reduce
                                changes['total_pnl'] += (trade.current_price - trade.entry_price) * (additional_reduce / trade.current_price)
                            break
                else:
                    # New reduction needed for portfolio %
                    reduce_amount = current_value - max_value_by_pct
                    reduce_pct = reduce_amount / current_value
                    
                    changes['positions_to_reduce'].append({
                        'trade_id': item['trade_id'],
                        'question': trade.question,
                        'current_value': current_value,
                        'new_value': max_value_by_pct,
                        'reduce_amount': reduce_amount,
                        'reduce_pct': reduce_pct,
                        'shares_to_sell': reduce_amount / trade.current_price,
                        'pnl_on_sold': (trade.current_price - trade.entry_price) * (reduce_amount / trade.current_price),
                        'reason': f"Exceeds max portfolio % ({max_portfolio_pct:.0%} = ${max_value_by_pct:.0f})",
                    })
                    changes['total_value_freed'] += reduce_amount
                    changes['total_pnl'] += (trade.current_price - trade.entry_price) * (reduce_amount / trade.current_price)
        
        return changes
    
    def _execute_force_holdings(self, changes: dict) -> None:
        """Execute the force holdings changes."""
        closed_count = 0
        reduced_count = 0
        total_freed = 0.0
        total_pnl = 0.0
        
        # Close positions
        for pos in changes['positions_to_close']:
            trade_id = pos['trade_id']
            if trade_id in self.bot.open_trades:
                trade = self.bot.open_trades[trade_id]
                self.bot._close_trade(trade, trade.current_price, "force_settings")
                closed_count += 1
                total_freed += pos['value']
                total_pnl += pos['pnl']
        
        # Reduce oversized positions
        for pos in changes['positions_to_reduce']:
            trade_id = pos['trade_id']
            if trade_id in self.bot.open_trades:
                trade = self.bot.open_trades[trade_id]
                shares_to_sell = pos['shares_to_sell']
                
                # Calculate proceeds from partial sale
                proceeds = shares_to_sell * trade.current_price
                pnl_on_sold = (trade.current_price - trade.entry_price) * shares_to_sell
                
                # Update trade
                trade.shares -= shares_to_sell
                self.bot.cash_balance += proceeds
                self.bot.total_pnl += pnl_on_sold
                
                if pnl_on_sold >= 0:
                    self.bot.winning_trades += 1
                else:
                    self.bot.losing_trades += 1
                
                # Log the reduction
                self.bot._add_to_trade_log(
                    action="SELL",
                    question=trade.question,
                    amount=proceeds,
                    price=trade.current_price,
                    pnl=pnl_on_sold,
                    result="WIN" if pnl_on_sold >= 0 else "LOSS",
                )
                
                self.chat.add_message(
                    f"Reduced '{trade.question[:30]}...' by ${proceeds:.0f} (now ${trade.shares * trade.current_price:.0f})",
                    "trade", "Config"
                )
                
                reduced_count += 1
                total_freed += proceeds
                total_pnl += pnl_on_sold
        
        # Save state
        self.bot._save()
        
        # Update UI
        self._update_positions_display()
        self._update_stats()
        
        # Show result
        pnl_str = f"+${total_pnl:.2f}" if total_pnl >= 0 else f"-${abs(total_pnl):.2f}"
        result_msg = f"Force applied: {closed_count} closed, {reduced_count} reduced | Freed ${total_freed:.0f} | P&L: {pnl_str}"
        
        self.config_status.configure(text=result_msg, fg=Theme.ACCENT_GREEN if total_pnl >= 0 else Theme.ACCENT_RED)
        self.chat.add_message(result_msg, "success" if total_pnl >= 0 else "alert", "Config")
        
        self.after(5000, lambda: self.config_status.configure(text=""))

    # =========================================================================
    # Event Handlers
    # =========================================================================
    
    def _toggle_auto_trade(self) -> None:
        """Toggle auto-trading on/off."""
        if self.bot.is_running():
            self.bot.stop()
            self.insider_detector.stop_monitoring()
            self.auto_trade_btn.configure(text="Start Auto Trade", bg=Theme.ACCENT_GREEN)
            self.status_label.configure(text="Idle", fg=Theme.TEXT_SECONDARY)
            self.chat.add_message("Auto-trading stopped", "info", "Bot")
        else:
            self.bot.start()
            self.insider_detector.start_monitoring()  # Start monitoring for insider trades
            self.auto_trade_btn.configure(text="Stop Auto Trade", bg=Theme.ACCENT_RED)
            self.status_label.configure(text="Trading", fg=Theme.ACCENT_GREEN)
            self.chat.add_message(
                "Auto-trading started! Scanning for opportunities... "
                f"(Up to {self.bot.config.max_positions} positions allowed)",
                "success",
                "Bot"
            )
    
    def _manual_scan(self) -> None:
        """Manually trigger a market scan."""
        self.chat.add_message("Starting market scan...", "info", "Scan")
        
        def scan():
            opportunities = self.bot.scan_markets()
            self.message_queue.put(("scan_complete", opportunities))
        
        threading.Thread(target=scan, daemon=True).start()
    
    def _on_bot_message(self, message: str, msg_type: str) -> None:
        """Handle bot messages (thread-safe)."""
        self.message_queue.put(("message", (message, msg_type)))
    
    def _on_bot_trade(self, trade: BotTrade) -> None:
        """Handle bot trade events (thread-safe)."""
        self.message_queue.put(("trade", trade))
    
    def _on_bot_opportunity(self, opportunity: MarketOpportunity) -> None:
        """Handle new opportunity discovered - also add to insider monitoring."""
        self.market_opportunities[f"{opportunity.market_id}|{opportunity.outcome}"] = opportunity
        
        # Add ALL scanned markets to insider detector for monitoring
        # This ensures we catch insider activity on popular markets
        self.insider_detector.add_market(
            opportunity.market_id,
            opportunity.question,
            opportunity.token_id
        )
    
    def _on_insider_alert(self, alert: InsiderAlert) -> None:
        """Handle insider trading alerts (thread-safe)."""
        # Always add to alerts tab
        self.message_queue.put(("insider_alert", alert))
        
        # For MAJOR alerts ($100k+ from new accounts), also show in bot activity
        if alert.trade_size >= 100000:
            self.message_queue.put(("major_insider_alert", alert))
    
    def _process_messages(self) -> None:
        """Process messages from the queue (runs on main thread) - BATCHED for performance."""
        messages_processed = 0
        max_per_cycle = 10  # Process max 10 messages per cycle to avoid UI stutter
        
        try:
            while messages_processed < max_per_cycle:
                msg_type, data = self.message_queue.get_nowait()
                messages_processed += 1
                
                if msg_type == "message":
                    message, mtype = data
                    self.chat.add_message(message, mtype)
                
                elif msg_type == "trade":
                    # Don't update UI immediately - let periodic update handle it
                    pass
                
                elif msg_type == "scan_complete":
                    opportunities = data
                    buy_ops = [o for o in opportunities if o.decision == BotDecision.BUY]
                    self.chat.add_message(
                        f"Scan complete: {len(opportunities)} markets analyzed, {len(buy_ops)} buy opportunities",
                        "success",
                        "Scan"
                    )
                    # Update overview timer
                    self._on_scan_completed()
                    # Defer market display update
                    self.after(500, self._update_markets_display)
                
                elif msg_type == "holdings_updated":
                    # Update overview timer for holdings
                    self._on_holdings_updated()
                
                elif msg_type == "insider_alert":
                    # Defer alert display update
                    pass
                
                elif msg_type == "major_insider_alert":
                    alert = data
                    self.chat.add_message(
                        f"MAJOR INSIDER ALERT: ${alert.trade_size:,.0f} {alert.trade_side.upper()} detected!\n"
                        f"Market: {alert.market_question[:50]}...\n"
                        f"Reason: {alert.reason}",
                        "alert",
                        "INSIDER"
                    )
                    
        except queue.Empty:
            pass
        
        # Schedule next check - 250ms instead of 100ms (less CPU usage)
        self.after(250, self._process_messages)
    
    def _add_market_dialog(self) -> None:
        """Show dialog to add a market."""
        dialog = tk.Toplevel(self)
        dialog.title("Add Market")
        dialog.geometry("500x400")
        dialog.configure(bg=Theme.BG_SECONDARY)
        dialog.transient(self)
        dialog.grab_set()
        
        # URL input
        tk.Label(
            dialog,
            text="Enter Polymarket URL or slug:",
            font=("Segoe UI", 11),
            bg=Theme.BG_SECONDARY,
            fg=Theme.TEXT_PRIMARY,
        ).pack(anchor="w", padx=20, pady=(20, 5))
        
        url_var = tk.StringVar()
        url_entry = tk.Entry(
            dialog,
            textvariable=url_var,
            font=("Segoe UI", 11),
            width=50,
            bg=Theme.BG_INPUT,
            fg=Theme.TEXT_PRIMARY,
            insertbackground=Theme.TEXT_PRIMARY,
            relief=tk.FLAT,
        )
        url_entry.pack(padx=20, pady=5, fill=tk.X)
        
        # Result frame
        result_frame = tk.Frame(dialog, bg=Theme.BG_SECONDARY)
        result_frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=10)
        
        result_var = tk.StringVar(value="Enter a URL and click Fetch")
        result_label = tk.Label(
            result_frame,
            textvariable=result_var,
            font=("Segoe UI", 10),
            bg=Theme.BG_SECONDARY,
            fg=Theme.TEXT_SECONDARY,
            wraplength=440,
            justify=tk.LEFT,
        )
        result_label.pack(anchor="w")
        
        # Outcome selection
        outcome_var = tk.StringVar()
        outcome_frame = tk.Frame(result_frame, bg=Theme.BG_SECONDARY)
        outcome_frame.pack(fill=tk.X, pady=10)
        
        market_data = {}
        
        def fetch():
            url = url_var.get().strip()
            if not url:
                result_var.set("Please enter a URL")
                return
            
            try:
                slug = extract_slug(url)
                ref_type, metadata = resolve_reference(slug)
                
                if ref_type == "event":
                    markets = metadata.get("markets", [])
                    if markets:
                        metadata = markets[0]
                        slug = metadata.get("slug") or str(metadata.get("id"))
                        metadata = fetch_market(slug)
                
                market_data.clear()
                market_data.update(metadata)
                
                question = metadata.get("question", "Unknown")
                
                # Get outcomes
                outcomes = list_outcomes(metadata)
                
                # Clear old outcome buttons
                for w in outcome_frame.winfo_children():
                    w.destroy()
                
                tk.Label(
                    outcome_frame,
                    text="Select outcome:",
                    font=("Segoe UI", 10),
                    bg=Theme.BG_SECONDARY,
                    fg=Theme.TEXT_PRIMARY,
                ).pack(anchor="w")
                
                for outcome in outcomes:
                    price_str = f" (${outcome.last_price:.3f})" if outcome.last_price else ""
                    tk.Radiobutton(
                        outcome_frame,
                        text=f"{outcome.name}{price_str}",
                        variable=outcome_var,
                        value=f"{outcome.name}|{outcome.token_id}",
                        bg=Theme.BG_SECONDARY,
                        fg=Theme.TEXT_PRIMARY,
                        selectcolor=Theme.BG_TERTIARY,
                        activebackground=Theme.BG_SECONDARY,
                        font=("Segoe UI", 10),
                    ).pack(anchor="w")
                
                if outcomes:
                    outcome_var.set(f"{outcomes[0].name}|{outcomes[0].token_id}")
                
                result_var.set(f"Found: {question[:80]}...")
                
            except Exception as e:
                result_var.set(f"Error: {e}")
        
        def add():
            if not market_data or not outcome_var.get():
                return
            
            outcome_name, token_id = outcome_var.get().split("|")
            
            # Evaluate with bot
            opportunity = self.bot.evaluate_market_for_user(market_data, outcome_name, token_id)
            
            # Store market
            market_key = f"{opportunity.market_id}|{outcome_name}"
            self.tracked_markets[market_key] = {
                "market_id": opportunity.market_id,
                "question": opportunity.question,
                "outcome": outcome_name,
                "token_id": token_id,
                "price": opportunity.price,
                "metadata": market_data,
            }
            self.market_opportunities[market_key] = opportunity
            
            # Add to insider detector
            volume = float(market_data.get("volumeNum") or market_data.get("volume") or 0)
            self.insider_detector.add_market(opportunity.market_id, opportunity.question, token_id)
            
            # Update UI
            self._update_markets_display()
            self._save_markets()
            
            # Show bot's decision
            decision_msg = {
                BotDecision.BUY: f"BUY SIGNAL! g={opportunity.g_score:.4f}, ROI={opportunity.expected_roi:.1%}",
                BotDecision.HOLD: f"HOLD - Not meeting buy criteria",
                BotDecision.SKIP: f"SKIP - {', '.join(opportunity.reasons)}",
                BotDecision.SELL: f"SELL signal",
            }
            
            self.chat.add_message(
                f"Added market: {opportunity.question[:50]}...\n"
                f"Bot Decision: {decision_msg.get(opportunity.decision, 'Unknown')}",
                "success" if opportunity.decision == BotDecision.BUY else "info",
                "Market Added"
            )
            
            dialog.destroy()
        
        # Buttons
        btn_frame = tk.Frame(dialog, bg=Theme.BG_SECONDARY)
        btn_frame.pack(fill=tk.X, padx=20, pady=20)
        
        tk.Button(
            btn_frame,
            text="Fetch",
            font=("Segoe UI", 10),
            bg=Theme.ACCENT_BLUE,
            fg=Theme.TEXT_PRIMARY,
            relief=tk.FLAT,
            padx=20,
            pady=5,
            command=fetch,
        ).pack(side=tk.LEFT, padx=(0, 10))
        
        tk.Button(
            btn_frame,
            text="Add Market",
            font=("Segoe UI", 10),
            bg=Theme.ACCENT_GREEN,
            fg=Theme.TEXT_PRIMARY,
            relief=tk.FLAT,
            padx=20,
            pady=5,
            command=add,
        ).pack(side=tk.LEFT, padx=(0, 10))
        
        tk.Button(
            btn_frame,
            text="Cancel",
            font=("Segoe UI", 10),
            bg=Theme.BG_TERTIARY,
            fg=Theme.TEXT_PRIMARY,
            relief=tk.FLAT,
            padx=20,
            pady=5,
            command=dialog.destroy,
        ).pack(side=tk.LEFT)
    
    def _remove_market(self, market_data: Dict) -> None:
        """Remove a tracked market."""
        market_key = f"{market_data['market_id']}|{market_data['outcome']}"
        if market_key in self.tracked_markets:
            del self.tracked_markets[market_key]
            self._update_markets_display()
            self._save_markets()
            self.chat.add_message(f"Removed market: {market_data['question'][:40]}...", "info")
    
    def _sell_position(self, trade: BotTrade) -> None:
        """Sell a position."""
        if messagebox.askyesno("Confirm Sell", f"Sell position in '{trade.question[:40]}...'?"):
            self.bot.sell_position(trade.id)
            self._update_positions_display()
            self._update_stats()
    
    # =========================================================================
    # UI Updates
    # =========================================================================
    
    def _update_markets_display(self) -> None:
        """Update the markets list."""
        for widget in self.markets_frame.winfo_children():
            widget.destroy()
        
        if not self.tracked_markets:
            tk.Label(
                self.markets_frame,
                text="No markets tracked.\nClick 'Add Market' to start.",
                font=("Segoe UI", 10),
                bg=Theme.BG_PRIMARY,
                fg=Theme.TEXT_MUTED,
                pady=30,
            ).pack()
            return
        
        for market_key, market_data in self.tracked_markets.items():
            opportunity = self.market_opportunities.get(market_key)
            row = MarketRow(
                self.markets_frame,
                market_data,
                opportunity=opportunity,
                on_remove=self._remove_market,
            )
            row.pack(fill=tk.X, pady=2)
    
    def _update_positions_display(self) -> None:
        """Update the positions list with 2-column grid layout."""
        trades = self.bot.get_open_trades()
        
        # Sort trades based on selected option
        sort_by = self.position_sort_var.get() if hasattr(self, 'position_sort_var') else "profit"
        if sort_by == "profit":
            trades = sorted(trades, key=lambda t: t.pnl, reverse=True)  # Highest profit first
        elif sort_by == "loss":
            trades = sorted(trades, key=lambda t: t.pnl)  # Biggest loss first
        elif sort_by == "recent":
            trades = sorted(trades, key=lambda t: t.timestamp, reverse=True)  # Newest first
        elif sort_by == "size":
            trades = sorted(trades, key=lambda t: t.cost_basis, reverse=True)  # Largest position first
        
        current_count = len(trades)
        self.positions_count.configure(text=f"{current_count} positions")
        
        # Always rebuild to show updated P&L values
        for widget in self.positions_frame.winfo_children():
            widget.destroy()
        
        if not trades:
            tk.Label(
                self.positions_frame,
                text="No open positions.\nThe bot will show positions here when it trades.",
                font=("Segoe UI", 10),
                bg=Theme.BG_PRIMARY,
                fg=Theme.TEXT_MUTED,
                pady=30,
            ).pack()
            return
        
        # Configure positions_frame for 2-column grid
        self.positions_frame.grid_columnconfigure(0, weight=1)
        self.positions_frame.grid_columnconfigure(1, weight=1)
        
        # Place positions in 2-column grid
        for i, trade in enumerate(trades):
            row_idx = i // 2
            col_idx = i % 2
            
            position_card = PositionRow(
                self.positions_frame,
                trade,
                on_sell=self._sell_position,
            )
            position_card.grid(row=row_idx, column=col_idx, sticky="nsew", padx=2, pady=2)
    
    def _update_stats(self) -> None:
        """Update portfolio statistics."""
        stats = self.bot.get_stats()
        
        self.portfolio_label.configure(
            text=f"Portfolio: ${stats['portfolio_value']:,.2f}"
        )
        
        pnl = stats['total_pnl'] + stats['unrealized_pnl']
        pnl_pct = stats['total_return_pct']
        pnl_color = Theme.PROFIT if pnl >= 0 else Theme.LOSS
        pnl_text = f"+${pnl:.2f}" if pnl >= 0 else f"-${abs(pnl):.2f}"
        
        self.pnl_label.configure(
            text=f"P&L: {pnl_text} ({pnl_pct:+.1f}%)",
            fg=pnl_color
        )
        
        self.stat_value.set_value(f"${stats['portfolio_value']:,.2f}")
        self.stat_pnl.set_value(
            pnl_text,
            f"Win rate: {stats['win_rate']:.0f}%",
            pnl_color
        )
    
    def _update_alerts_display(self) -> None:
        """Update the alerts list - OPTIMIZED with caching."""
        # Check if alerts changed before rebuilding (expensive operation)
        current_alert_count = len(self.insider_detector.alerts) if hasattr(self.insider_detector, 'alerts') else 0
        if hasattr(self, '_last_alert_count') and self._last_alert_count == current_alert_count:
            # Only update timestamp, not whole list
            return
        self._last_alert_count = current_alert_count
        
        for widget in self.alerts_frame.winfo_children():
            widget.destroy()
        
        # Header with current time
        header = tk.Frame(self.alerts_frame, bg=Theme.BG_PRIMARY)
        header.pack(fill=tk.X, pady=(0, 10))
        
        current_time = datetime.now().strftime("%H:%M:%S")
        tk.Label(
            header,
            text=f"Last Updated: {current_time}",
            font=("Segoe UI", 9),
            bg=Theme.BG_PRIMARY,
            fg=Theme.TEXT_MUTED,
        ).pack(side=tk.RIGHT)
        
        alerts = self.insider_detector.get_alerts(limit=15)  # REDUCED from 20
        
        if not alerts:
            tk.Label(
                self.alerts_frame,
                text="Monitoring ALL markets for large trades...\n\n"
                     "Alerting on trades over $10,000\n\n"
                     "Severity levels:\n"
                     "• LOW: $10k - $25k trades\n"
                     "• MEDIUM: $25k - $50k trades\n"
                     "• HIGH: $50k - $100k trades\n"
                     "• CRITICAL: $100k+ trades\n\n"
                     f"Currently monitoring: {len(self.insider_detector.monitored_markets)} markets\n"
                     f"Scanning every 30 seconds",
                font=("Segoe UI", 10),
                bg=Theme.BG_PRIMARY,
                fg=Theme.TEXT_MUTED,
                pady=20,
                justify=tk.LEFT,
            ).pack()
            return
        
        for alert in alerts:
            severity_colors = {
                AlertSeverity.LOW: Theme.TEXT_SECONDARY,
                AlertSeverity.MEDIUM: Theme.ACCENT_YELLOW,
                AlertSeverity.HIGH: Theme.ACCENT_ORANGE,
                AlertSeverity.CRITICAL: Theme.ACCENT_RED,
            }
            
            alert_frame = tk.Frame(self.alerts_frame, bg=Theme.BG_CARD)
            alert_frame.pack(fill=tk.X, pady=2)
            
            inner = tk.Frame(alert_frame, bg=Theme.BG_CARD)
            inner.pack(fill=tk.X, padx=10, pady=8)
            
            # Top row: severity + time
            top_row = tk.Frame(inner, bg=Theme.BG_CARD)
            top_row.pack(fill=tk.X)
            
            tk.Label(
                top_row,
                text=f"● {alert.severity.value.upper()}",
                font=("Segoe UI", 9, "bold"),
                bg=Theme.BG_CARD,
                fg=severity_colors.get(alert.severity, Theme.ACCENT_YELLOW),
            ).pack(side=tk.LEFT)
            
            # Parse and format timestamp nicely
            try:
                alert_time = datetime.fromisoformat(alert.timestamp.replace("Z", "+00:00"))
                time_str = alert_time.strftime("%m/%d %H:%M:%S")
            except Exception:
                time_str = alert.timestamp[:19]
            
            tk.Label(
                top_row,
                text=time_str,
                font=("Segoe UI", 8),
                bg=Theme.BG_CARD,
                fg=Theme.TEXT_MUTED,
            ).pack(side=tk.RIGHT)
            
            # Market name (highlighted)
            market_text = alert.market_question[:60] + "..." if len(alert.market_question) > 60 else alert.market_question
            tk.Label(
                inner,
                text=market_text,
                font=("Segoe UI", 10, "bold"),
                bg=Theme.BG_CARD,
                fg=Theme.ACCENT_BLUE,
                wraplength=350,
                justify=tk.LEFT,
            ).pack(anchor="w", pady=(4, 2))
            
            # Reason
            tk.Label(
                inner,
                text=alert.reason,
                font=("Segoe UI", 9),
                bg=Theme.BG_CARD,
                fg=Theme.TEXT_PRIMARY,
                wraplength=350,
                justify=tk.LEFT,
            ).pack(anchor="w")
            
            # Trade details
            details = f"${alert.trade_size:,.0f} {alert.trade_side.upper()} @ ${alert.price:.3f} | {alert.outcome}"
            tk.Label(
                inner,
                text=details,
                font=("Segoe UI", 8),
                bg=Theme.BG_CARD,
                fg=Theme.TEXT_SECONDARY,
            ).pack(anchor="w", pady=(2, 0))
    
    def _update_trade_log_display(self) -> None:
        """Update the trade log panel with recent trades."""
        trade_log = self.bot.get_trade_log(limit=30)
        
        self.trade_log_count.configure(text=f"{len(trade_log)} recent | {len(self.bot.trade_log)} total")
        
        self.trade_log_text.configure(state=tk.NORMAL)
        self.trade_log_text.delete(1.0, tk.END)
        
        if not trade_log:
            self.trade_log_text.insert(tk.END, "No trades yet.\n", "timestamp")
            self.trade_log_text.insert(tk.END, "When the bot makes trades, they will appear here with:\n")
            self.trade_log_text.insert(tk.END, "• Buy/Sell action\n")
            self.trade_log_text.insert(tk.END, "• Amount traded\n")
            self.trade_log_text.insert(tk.END, "• P&L outcome\n")
            self.trade_log_text.configure(state=tk.DISABLED)
            return
        
        # trade_log is already newest-first from get_trade_log
        for entry in trade_log:
            # Parse timestamp
            try:
                ts = datetime.fromisoformat(entry['timestamp'].replace('Z', '+00:00'))
                time_str = ts.strftime("%H:%M:%S")
            except:
                time_str = entry['timestamp'][:8]
            
            # Format based on action
            if entry['action'] == "BUY":
                self.trade_log_text.insert(tk.END, f"[{time_str}] ", "timestamp")
                self.trade_log_text.insert(tk.END, "BUY ", "buy")
                self.trade_log_text.insert(tk.END, f"${entry['amount']:.0f} ", "amount")
                self.trade_log_text.insert(tk.END, f"@ ${entry['price']:.3f}\n")
                # Question on next line
                q_text = entry['question'][:45] + "..." if len(entry['question']) > 45 else entry['question']
                self.trade_log_text.insert(tk.END, f"         {q_text}\n", "timestamp")
            
            elif entry['action'] == "SELL":
                self.trade_log_text.insert(tk.END, f"[{time_str}] ", "timestamp")
                
                result = entry.get('result', 'UNKNOWN')
                pnl = entry.get('pnl') or 0  # Handle None values
                
                if result == "WIN":
                    self.trade_log_text.insert(tk.END, "SELL ", "sell_win")
                    self.trade_log_text.insert(tk.END, f"${entry['amount']:.0f} ", "amount")
                    self.trade_log_text.insert(tk.END, "→ ", "timestamp")
                    self.trade_log_text.insert(tk.END, f"+${pnl:.2f} ✓\n", "pnl_pos")
                else:
                    self.trade_log_text.insert(tk.END, "SELL ", "sell_loss")
                    self.trade_log_text.insert(tk.END, f"${entry['amount']:.0f} ", "amount")
                    self.trade_log_text.insert(tk.END, "→ ", "timestamp")
                    self.trade_log_text.insert(tk.END, f"-${abs(pnl):.2f} ✗\n", "pnl_neg")
                
                q_text = entry['question'][:45] + "..." if len(entry['question']) > 45 else entry['question']
                self.trade_log_text.insert(tk.END, f"         {q_text}\n", "timestamp")
        
        self.trade_log_text.configure(state=tk.DISABLED)
        # Force scroll to TOP to show newest trades first
        self.trade_log_text.see("1.0")
        self.trade_log_text.yview_moveto(0.0)
    
    def _show_settings(self) -> None:
        """Show settings dialog."""
        dialog = tk.Toplevel(self)
        dialog.title("Settings")
        dialog.geometry("400x500")
        dialog.configure(bg=Theme.BG_SECONDARY)
        dialog.transient(self)
        
        tk.Label(
            dialog,
            text="Bot Settings",
            font=("Segoe UI", 16, "bold"),
            bg=Theme.BG_SECONDARY,
            fg=Theme.TEXT_PRIMARY,
        ).pack(pady=20)
        
        # Stats
        stats = self.bot.get_stats()
        
        info_frame = tk.Frame(dialog, bg=Theme.BG_CARD)
        info_frame.pack(fill=tk.X, padx=20, pady=10)
        
        for label, value in [
            ("Total Trades", str(stats['total_trades'])),
            ("Winning Trades", str(stats['winning_trades'])),
            ("Losing Trades", str(stats['losing_trades'])),
            ("Win Rate", f"{stats['win_rate']:.1f}%"),
            ("Cash Balance", f"${stats['cash_balance']:,.2f}"),
        ]:
            row = tk.Frame(info_frame, bg=Theme.BG_CARD)
            row.pack(fill=tk.X, padx=10, pady=3)
            tk.Label(row, text=label, bg=Theme.BG_CARD, fg=Theme.TEXT_SECONDARY, 
                    font=("Segoe UI", 10)).pack(side=tk.LEFT)
            tk.Label(row, text=value, bg=Theme.BG_CARD, fg=Theme.TEXT_PRIMARY,
                    font=("Segoe UI", 10, "bold")).pack(side=tk.RIGHT)
        
        # Reset button
        def reset():
            if messagebox.askyesno("Confirm", "Reset bot? This will clear all trades and positions."):
                self.bot.reset()
                self._update_stats()
                self._update_positions_display()
                self.chat.add_message("Bot has been reset to initial state", "info", "Reset")
                dialog.destroy()
        
        tk.Button(
            dialog,
            text="Reset Bot",
            font=("Segoe UI", 10),
            bg=Theme.ACCENT_RED,
            fg=Theme.TEXT_PRIMARY,
            relief=tk.FLAT,
            padx=20,
            pady=8,
            command=reset,
        ).pack(pady=20)
    
    def _start_updates(self) -> None:
        """Start periodic UI updates - DUAL SPEED: Fast for positions, slow for discovery."""
        self._slow_counter = 0
        self._log_check_counter = 0
        self._position_update_pending = False
        
        def fast_update():
            """FAST LOOP: Updates held positions every 5 seconds."""
            # Update held positions in background (PRIORITY - these are our money!)
            if not self._position_update_pending:
                self._position_update_pending = True
                threading.Thread(target=self._background_position_update, daemon=True).start()
            
            # Update stats display (lightweight)
            try:
                self._update_stats()
                self._update_positions_display_incremental()
            except Exception as e:
                print(f"[FastUpdate] Error: {e}")
            
            # Fast refresh: 5 seconds for held positions
            self.after(5000, fast_update)
        
        def slow_update():
            """SLOW LOOP: Updates UI chrome, alerts, logs every 10-15 seconds."""
            self._slow_counter += 1
            self._log_check_counter += 1
            
            # Update stats dashboard every tick (10 seconds)
            try:
                self._update_stats_dashboard()
            except Exception as e:
                print(f"[SlowUpdate] Stats dashboard error: {e}")
            
            # Update trade log every 2 ticks (20 seconds)
            if self._slow_counter % 2 == 0:
                try:
                    self._update_trade_log_display()
                except Exception as e:
                    print(f"[SlowUpdate] Trade log error: {e}")
            
            # Update alerts every 3 ticks (30 seconds)
            if self._slow_counter % 3 == 0:
                try:
                    self._update_alerts_display()
                except Exception as e:
                    print(f"[SlowUpdate] Alerts error: {e}")
            
            # Check for log export every 30 minutes (180 ticks at 10s each)
            if self._log_check_counter >= 180:
                self._log_check_counter = 0
                threading.Thread(target=self._perform_log_cleanup, daemon=True).start()
            
            # Slow refresh: 10 seconds for non-critical UI
            self.after(10000, slow_update)
        
        # Initial updates
        self._update_trade_log_display()
        self._update_stats_dashboard()
        
        # Start both loops with staggered timing
        self.after(1000, fast_update)   # Fast loop starts after 1s
        self.after(5000, slow_update)   # Slow loop starts after 5s
    
    def _background_position_update(self) -> None:
        """Update positions in background thread to avoid UI freeze."""
        try:
            self.bot.update_positions()
            # Signal that holdings were updated (for overview timer)
            self.message_queue.put(("holdings_updated", None))
        except Exception as e:
            print(f"[Update] Background position update error: {e}")
        finally:
            self._position_update_pending = False
    
    def _update_positions_display_incremental(self) -> None:
        """Update positions display WITHOUT destroying all widgets - incremental update."""
        trades = self.bot.get_open_trades()
        
        # Sort trades
        sort_by = self.position_sort_var.get() if hasattr(self, 'position_sort_var') else "profit"
        if sort_by == "profit":
            trades = sorted(trades, key=lambda t: t.pnl, reverse=True)
        elif sort_by == "loss":
            trades = sorted(trades, key=lambda t: t.pnl)
        elif sort_by == "recent":
            trades = sorted(trades, key=lambda t: t.timestamp, reverse=True)
        elif sort_by == "size":
            trades = sorted(trades, key=lambda t: t.cost_basis, reverse=True)
        
        current_count = len(trades)
        self.positions_count.configure(text=f"{current_count} positions")
        
        # Get existing widgets
        existing_widgets = self.positions_frame.winfo_children()
        existing_count = len(existing_widgets)
        
        # Only do full rebuild if count changed significantly or first time
        if abs(existing_count - current_count) > 2 or existing_count == 0:
            self._update_positions_display()
            return
        
        # Otherwise just update the stat displays (much faster)
        try:
            total_value = sum(t.shares * t.current_price for t in trades)
            total_pnl = sum(t.pnl for t in trades)
            pnl_pct = (total_pnl / (total_value - total_pnl) * 100) if (total_value - total_pnl) > 0 else 0
            
            self.stat_value.set_value(f"${self.bot.cash_balance + total_value:,.2f}")
            pnl_color = Theme.PROFIT if total_pnl >= 0 else Theme.LOSS
            self.stat_pnl.set_value(
                f"${total_pnl:+,.2f}",
                f"({pnl_pct:+.1f}%)",
                pnl_color
            )
        except Exception:
            pass
    
    def _perform_log_cleanup(self) -> None:
        """Export logs to CSV and clear memory to prevent slowdown."""
        try:
            # Get current logs from chat widget
            bot_activity = self.chat.get_messages_for_export() if hasattr(self.chat, 'get_messages_for_export') else []
            trade_log = self.bot.trade_log.copy() if hasattr(self.bot, 'trade_log') else []
            insider_alerts = list(self.insider_detector.alerts) if hasattr(self.insider_detector, 'alerts') else []
            
            # Export if we have data
            if trade_log or bot_activity or insider_alerts:
                exports = self.log_manager.perform_export_cycle(
                    bot_activity=bot_activity,
                    trade_log=trade_log,
                    insider_alerts=[{
                        'timestamp': a.timestamp,
                        'market_question': a.market_question,
                        'trade_size': a.trade_size,
                        'trade_side': a.trade_side,
                        'outcome': a.outcome,
                        'price': a.price,
                        'severity': a.severity.value,
                        'reason': a.reason,
                    } for a in insider_alerts]
                )
                
                # DON'T clear trade log - keep all trades visible
                # Only trim insider alerts which can grow large
                self.insider_detector.alerts = self.insider_detector.alerts[-100:]
                
                # Log the cleanup
                exported_count = sum(1 for v in exports.values() if v)
                if exported_count > 0:
                    self.chat.add_message(
                        f"Exported logs to CSV ({exported_count} files). Memory cleaned.",
                        "info"
                    )
        except Exception as e:
            print(f"[LogManager] Export error: {e}")
    
    def _load_markets(self) -> None:
        """Load saved markets."""
        if MARKETS_PATH.exists():
            try:
                self.tracked_markets = json.loads(MARKETS_PATH.read_text())
                self._update_markets_display()
            except Exception:
                pass
    
    def _save_markets(self) -> None:
        """Save tracked markets."""
        try:
            # Convert to JSON-serializable format
            data = {}
            for k, v in self.tracked_markets.items():
                data[k] = {key: val for key, val in v.items() if key != "metadata"}
            MARKETS_PATH.write_text(json.dumps(data, indent=2))
        except Exception:
            pass
    
    def destroy(self) -> None:
        """Clean up on close."""
        self.bot.stop()
        self._save_markets()
        # Remove lock file on exit
        try:
            LOCK_FILE.unlink()
        except:
            pass
        super().destroy()


def main():
    # Check if headless runner is already running
    if LOCK_FILE.exists():
        try:
            with open(LOCK_FILE, 'r') as f:
                info = f.read().strip()
            messagebox.showerror(
                "Bot Already Running",
                f"Another bot instance is already running!\n\n"
                f"Lock info: {info}\n\n"
                f"If this is incorrect, delete:\n{LOCK_FILE}"
            )
            return
        except:
            pass
    
    # Create lock file
    with open(LOCK_FILE, 'w') as f:
        f.write(f"trading_bot_v2.py (UI) started at {datetime.now().isoformat()}")
    
    try:
        app = TradingBotApp()
        app.mainloop()
    finally:
        # Clean up lock file
        try:
            LOCK_FILE.unlink()
        except:
            pass


if __name__ == "__main__":
    main()
