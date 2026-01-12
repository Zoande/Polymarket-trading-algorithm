"Polymarket capital rotation simulator UI.."

from __future__ import annotations

import json
import random
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

from config_manager import MarketPolicy, SimulatorConfig, ensure_config, load_config, save_config
from engine import AllocationEngine
from polymarket_api import (
    PolymarketAPIError,
    build_market_snapshot,
    extract_slug,
    fetch_market,
    fetch_snapshot_for_outcome,
    get_outcome_descriptor,
    list_outcomes,
    resolve_reference,
)
from runtime_state import (
    FreezeStatus,
    MarketState,
    RuntimeState,
    parse_volume,
    extract_parent_event,
    _now,
    _now_iso,
    _parse_iso,
)

CONFIG_PATH = Path("config.yaml")
RUNTIME_PATH = Path("runtime_state.json")
DEFAULT_BUDGET = 50000.0


def format_currency(value: float) -> str:
    return f"${value:,.2f}"


def format_percent(value: float) -> str:
    return f"{value * 100:.2f}%"


def seconds_to_human(seconds: float) -> str:
    if seconds < 60:
        return f"{int(seconds)}s"
    if seconds < 3600:
        return f"{int(seconds // 60)}m {int(seconds % 60)}s"
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    return f"{int(hours)}h {int(minutes)}m"


class NewBudgetDialog(simpledialog.Dialog):
    def body(self, master: tk.Widget) -> tk.Widget:
        ttk.Label(master, text="Total budget (USD):").grid(row=0, column=0, sticky="e", padx=5, pady=5)
        default_value = f"{DEFAULT_BUDGET:.0f}" if DEFAULT_BUDGET.is_integer() else f"{DEFAULT_BUDGET}"
        self.budget_var = tk.StringVar(value=default_value)
        ttk.Entry(master, textvariable=self.budget_var).grid(row=0, column=1, sticky="we", padx=5, pady=5)
        master.columnconfigure(1, weight=1)
        return master

    def validate(self) -> bool:
        try:
            budget = float(self.budget_var.get())
        except ValueError:
            messagebox.showerror("Invalid input", "Please enter a numeric budget.")
            return False
        if budget <= 0:
            messagebox.showerror("Invalid input", "Budget must be greater than zero.")
            return False
        return True

    def apply(self) -> None:
        self.result = float(self.budget_var.get())


class AddMarketDialog(simpledialog.Dialog):
    """Dialog for selecting a market/outcome and returning a MarketState."""

    def __init__(self, master: tk.Widget, default_outcome: str):
        self.default_outcome = default_outcome
        self.reference_type: Optional[str] = None
        self.reference_metadata: Optional[dict] = None
        self.selected_market_metadata: Optional[dict] = None
        self.outcome_descriptors: List = []
        self.selected_descriptor = None
        self.current_state: Optional[MarketState] = None
        super().__init__(master, title="Add Market")

    def body(self, master: tk.Widget) -> tk.Widget:
        ttk.Label(master, text="Polymarket URL or identifier:").grid(
            row=0, column=0, columnspan=3, sticky="w", padx=5, pady=5
        )
        self.identifier_var = tk.StringVar()
        entry = ttk.Entry(master, textvariable=self.identifier_var, width=56)
        entry.grid(row=1, column=0, columnspan=2, sticky="we", padx=5, pady=5)
        entry.focus()
        ttk.Button(master, text="Fetch", command=self.fetch_reference).grid(row=1, column=2, padx=5, pady=5)

        ttk.Label(master, text="Markets (events only):").grid(row=2, column=0, columnspan=3, sticky="w", padx=5)
        self.market_list = tk.Listbox(master, height=6, exportselection=False)
        self.market_list.grid(row=3, column=0, columnspan=3, sticky="nsew", padx=5, pady=(0, 5))
        self.market_list.bind("<<ListboxSelect>>", lambda *_: self.on_market_select())

        ttk.Label(master, text="Outcomes:").grid(row=4, column=0, columnspan=3, sticky="w", padx=5)
        self.outcome_list = tk.Listbox(master, height=6, exportselection=False)
        self.outcome_list.grid(row=5, column=0, columnspan=3, sticky="nsew", padx=5, pady=(0, 5))
        self.outcome_list.bind("<<ListboxSelect>>", lambda *_: self.on_outcome_select())

        self.summary_var = tk.StringVar(value="")
        ttk.Label(master, textvariable=self.summary_var, justify="left", wraplength=420).grid(
            row=6, column=0, columnspan=3, sticky="we", padx=5, pady=5
        )

        master.columnconfigure(0, weight=1)
        master.columnconfigure(1, weight=1)
        master.rowconfigure(3, weight=1)
        master.rowconfigure(5, weight=1)
        return entry

    def buttonbox(self) -> None:
        box = ttk.Frame(self)
        self.add_button = ttk.Button(box, text="Add", width=12, command=self.ok, state="disabled")
        self.add_button.grid(row=0, column=0, padx=5, pady=5)
        ttk.Button(box, text="Cancel", width=12, command=self.cancel).grid(row=0, column=1, padx=5, pady=5)
        box.pack(side=tk.BOTTOM, fill=tk.X)
    def fetch_reference(self) -> None:
        identifier = self.identifier_var.get().strip()
        if not identifier:
            messagebox.showerror("Missing input", "Enter a URL or market slug.")
            return
        try:
            slug = extract_slug(identifier)
            ref_type, metadata = resolve_reference(slug)
        except (ValueError, PolymarketAPIError) as exc:
            messagebox.showerror("Lookup failed", str(exc))
            return
        except Exception as exc:
            messagebox.showerror("Lookup failed", f"Unexpected error: {exc}")
            return

        self.reference_type = ref_type
        self.reference_metadata = metadata
        self.selected_market_metadata = None
        self.outcome_descriptors = []
        self.selected_descriptor = None
        self.market_list.delete(0, tk.END)
        self.outcome_list.delete(0, tk.END)
        self.summary_var.set("")
        self.current_state = None

        if ref_type == "event":
            markets = metadata.get("markets", [])
            for market in markets:
                label = market.get("groupItemTitle") or market.get("question") or market.get("slug") or "Market"
                prices = market.get("outcomePrices")
                if prices:
                    try:
                        price_list = json.loads(prices) if isinstance(prices, str) else prices
                        label = f"{label} (Yes {float(price_list[0]):.3f})"
                    except Exception:
                        pass
                self.market_list.insert(tk.END, label)
            if markets:
                self.market_list.selection_set(0)
                self.on_market_select()
        else:
            self.market_list.insert(tk.END, metadata.get("question") or metadata.get("slug") or "Market")
            self.market_list.selection_set(0)
            self.on_market_select()

    def on_market_select(self) -> None:
        if not self.reference_metadata:
            return
        selection = self.market_list.curselection()
        if not selection:
            return
        if self.reference_type == "event":
            markets = self.reference_metadata.get("markets", [])
            summary = markets[selection[0]]
            slug = summary.get("slug") or str(summary.get("id"))
            if not slug:
                messagebox.showerror("Invalid market", "Unable to determine slug for selected market.")
                return
            self.selected_market_metadata = fetch_market(slug)
        else:
            self.selected_market_metadata = self.reference_metadata
        self.populate_outcomes()

    def populate_outcomes(self) -> None:
        metadata = self.selected_market_metadata
        if not metadata:
            return
        try:
            descriptors = list_outcomes(metadata)
        except Exception as exc:
            messagebox.showerror("Outcome error", f"Unable to list outcomes: {exc}")
            return
        self.outcome_descriptors = descriptors
        self.outcome_list.delete(0, tk.END)
        default_index = None
        for idx, descriptor in enumerate(descriptors):
            label = descriptor.name
            if descriptor.last_price is not None:
                label += f" (last {descriptor.last_price:.3f})"
            self.outcome_list.insert(tk.END, label)
            if descriptor.name.lower() == self.default_outcome.lower():
                default_index = idx
        if default_index is not None:
            self.outcome_list.selection_set(default_index)
            self.on_outcome_select()

    def on_outcome_select(self) -> None:
        selection = self.outcome_list.curselection()
        if not selection or not self.selected_market_metadata:
            self.add_button.configure(state="disabled")
            return
        descriptor = self.outcome_descriptors[selection[0]]
        try:
            snapshot = build_market_snapshot(self.selected_market_metadata, descriptor)
        except PolymarketAPIError as exc:
            messagebox.showerror("Order book error", str(exc))
            return

        best_ask = snapshot.order_book.get("asks", [[None]])[0][0] if snapshot.order_book.get("asks") else None
        best_bid = snapshot.order_book.get("bids", [[None]])[0][0] if snapshot.order_book.get("bids") else None

        asks_text = "\n".join(
            [f"  {price:.3f} for {size:,.2f}" for price, size in snapshot.order_book.get("asks", [])[:10]]
        )
        bids_text = "\n".join(
            [f"  {price:.3f} for {size:,.2f}" for price, size in snapshot.order_book.get("bids", [])[:10]]
        )
        self.summary_var.set(
            f"Question: {snapshot.question}\nOutcome: {descriptor.name}\n"
            f"Resolves: {snapshot.resolution_datetime.isoformat()} (~{snapshot.resolution_days:.1f} days)\n\n"
            f"Top asks:\n{asks_text or '  (none)'}\n\nTop bids:\n{bids_text or '  (none)'}"
        )

        parent_event_id, parent_event_label = extract_parent_event(snapshot.raw_metadata)
        market_state = MarketState(
            market_id=snapshot.market_id,
            outcome=descriptor.name,
            question=snapshot.question,
            parent_event_id=parent_event_id,
            parent_event_label=parent_event_label,
            resolution_datetime=snapshot.resolution_datetime.isoformat(),
            resolution_days=snapshot.resolution_days,
            metadata=snapshot.raw_metadata,
        )
        market_state.update_from_snapshot(
            snapshot=snapshot,
            last_price=descriptor.last_price,
            best_bid=best_bid,
            best_ask=best_ask,
            volume=parse_volume(snapshot.raw_metadata),
        )
        self.current_state = market_state
        self.add_button.configure(state="normal")

    def validate(self) -> bool:
        return self.current_state is not None

    def apply(self) -> None:
        self.result = self.current_state

class PolymarketApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Polymarket Capital Rotation Simulator")
        self.geometry("1280x820")

        self.sim_config_path = CONFIG_PATH
        self.runtime_path = RUNTIME_PATH
        self.sim_config: SimulatorConfig = ensure_config(self.sim_config_path)

        self.state = self._load_or_create_state()
        self.engine = AllocationEngine(self.sim_config)

        self.latest_evaluation = None
        self.trading_enabled = tk.BooleanVar(value=True)
        self.state.mode = "live"
        self.mode = "live"
        self.mode_label_var = tk.StringVar(value="Live")
        self.poll_job: Optional[str] = None
        self.poll_backoff_seconds: Optional[int] = None
        self._loading_market_form = False

        self._build_menu()
        self._build_layout()
        self.refresh_views()
        self._schedule_poll(initial=True)

    def _load_or_create_state(self) -> RuntimeState:
        if self.runtime_path.exists():
            state = RuntimeState.load(self.runtime_path)
        else:
            budget_dialog = NewBudgetDialog(self)
            total_budget = float(budget_dialog.result) if budget_dialog.result else DEFAULT_BUDGET
            state = RuntimeState(total_budget=total_budget, cash_balance=total_budget, mode="live")
            state.filepath = self.runtime_path
            state.save(self.runtime_path)

        if state.mode != "live":
            state.mode = "live"
            try:
                state.save(self.runtime_path)
            except Exception:
                pass

        return state

    def _build_menu(self) -> None:
        menu_bar = tk.Menu(self)
        file_menu = tk.Menu(menu_bar, tearoff=False)
        file_menu.add_command(label="New Simulation", command=self.reset_simulation)
        file_menu.add_command(label="Load Runtime State", command=self.load_runtime_state)
        file_menu.add_command(label="Save Runtime State", command=self.save_runtime_state)
        file_menu.add_separator()
        file_menu.add_command(label="Reload Config", command=self.reload_config)
        file_menu.add_command(label="Open Config File", command=self.open_config_file)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self.destroy)
        menu_bar.add_cascade(label="File", menu=file_menu)
        self.config(menu=menu_bar)

    def _build_layout(self) -> None:
        header = ttk.Frame(self)
        header.pack(fill=tk.X, padx=10, pady=8)

        ttk.Label(header, text="Mode:").pack(side=tk.LEFT)
        ttk.Label(header, textvariable=self.mode_label_var).pack(side=tk.LEFT, padx=5)
        ttk.Checkbutton(header, text="Trading enabled", variable=self.trading_enabled, command=self.on_trading_toggle).pack(side=tk.LEFT, padx=15)
        ttk.Button(header, text="Refresh Data", command=self.refresh_data_manual).pack(side=tk.LEFT, padx=5)
        ttk.Button(header, text="Export Log (CSV)", command=self.export_log_csv).pack(side=tk.LEFT, padx=5)
        ttk.Button(header, text="Export Log (NDJSON)", command=self.export_log_ndjson).pack(side=tk.LEFT, padx=5)
        ttk.Button(header, text="Add Market", command=self.add_market).pack(side=tk.LEFT, padx=15)
        ttk.Button(header, text="Remove Market", command=self.remove_market).pack(side=tk.LEFT, padx=5)

        self.status_var = tk.StringVar(value="Ready.")
        ttk.Label(header, textvariable=self.status_var, foreground="#444").pack(side=tk.RIGHT)

        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))

        self._build_overview_tab()
        self._build_opportunities_tab()
        self._build_holdings_tab()
        self._build_decisions_tab()
        self._build_config_tab()

    def on_trading_toggle(self) -> None:
        if self.trading_enabled.get():
            self.status_var.set("Trading resumed.")
        else:
            self.status_var.set("Trading paused.")

    def _build_overview_tab(self) -> None:
        frame = ttk.Frame(self.notebook)
        self.notebook.add(frame, text="Overview")

        summary_frame = ttk.LabelFrame(frame, text="Portfolio Summary")
        summary_frame.pack(fill=tk.X, padx=8, pady=8)

        self.summary_labels = {
            "total_budget": tk.StringVar(),
            "cash": tk.StringVar(),
            "invested": tk.StringVar(),
            "realized": tk.StringVar(),
            "portfolio_value": tk.StringVar(),
            "data_age": tk.StringVar(),
        }
        row = 0
        for label, var in self.summary_labels.items():
            ttk.Label(summary_frame, text=label.replace("_", " ").title() + ":").grid(
                row=row, column=0, sticky="w", padx=5, pady=3
            )
            ttk.Label(summary_frame, textvariable=var, font=("Segoe UI", 11, "bold")).grid(
                row=row, column=1, sticky="w", padx=10, pady=3
            )
            row += 1

        watchlist_frame = ttk.LabelFrame(frame, text="Tracked Markets")
        watchlist_frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)
        watchlist_columns = ("market", "outcome", "shares", "best_ask", "g", "status", "notes", "updated")
        self.watchlist_tree = ttk.Treeview(
            watchlist_frame,
            columns=watchlist_columns,
            show="headings",
            height=8,
        )
        watchlist_headings = {
            "market": "Market",
            "outcome": "Outcome",
            "shares": "Held Shares",
            "best_ask": "Best Ask",
            "g": "g/day",
            "status": "Status",
            "notes": "Notes",
            "updated": "Last Update",
        }
        watchlist_widths = {
            "market": 280,
            "outcome": 140,
            "shares": 120,
            "best_ask": 100,
            "g": 110,
            "status": 140,
            "notes": 260,
            "updated": 140,
        }
        for column in watchlist_columns:
            self.watchlist_tree.heading(column, text=watchlist_headings[column])
            anchor = "e" if column in {"shares", "best_ask", "g"} else "w"
            self.watchlist_tree.column(column, width=watchlist_widths[column], anchor=anchor)
        self.watchlist_tree.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self.watchlist_tree.bind("<<TreeviewSelect>>", self.on_watchlist_select)
        self.watchlist_tree.tag_configure("tracked", foreground="#222222")
        self.watchlist_tree.tag_configure("eligible", foreground="#0a6c48")
        self.watchlist_tree.tag_configure("blocked", foreground="#b26a00")
        self.watchlist_tree.tag_configure("frozen", foreground="#b00020")
        self.watchlist_tree.tag_configure("holding", foreground="#1545a5")
        self.watchlist_tree.tag_configure("stale", foreground="#777777")

        timeline_frame = ttk.LabelFrame(frame, text="Resolution Timeline")
        timeline_frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)
        self.timeline_tree = ttk.Treeview(
            timeline_frame,
            columns=("month", "exposure", "count"),
            show="headings",
            height=8,
        )
        self.timeline_tree.heading("month", text="Resolution Month")
        self.timeline_tree.heading("exposure", text="Exposure")
        self.timeline_tree.heading("count", text="# Markets")
        self.timeline_tree.column("month", width=140)
        self.timeline_tree.column("exposure", width=140, anchor="e")
        self.timeline_tree.column("count", width=120, anchor="center")
        self.timeline_tree.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
    def _build_opportunities_tab(self) -> None:
        frame = ttk.Frame(self.notebook)
        self.notebook.add(frame, text="Opportunities")

        columns = ("market", "outcome", "price", "g", "slippage", "status", "reasons")
        self.opportunity_tree = ttk.Treeview(frame, columns=columns, show="headings", height=12)
        headings = {
            "market": "Market",
            "outcome": "Outcome",
            "price": "Price",
            "g": "g/day",
            "slippage": "Slippage (bps)",
            "status": "Status",
            "reasons": "Reasons",
        }
        widths = {
            "market": 260,
            "outcome": 120,
            "price": 90,
            "g": 90,
            "slippage": 120,
            "status": 120,
            "reasons": 280,
        }
        for column in columns:
            self.opportunity_tree.heading(column, text=headings[column])
            self.opportunity_tree.column(column, width=widths[column], anchor="w")
        self.opportunity_tree.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)
        self.opportunity_tree.tag_configure("eligible", foreground="#0a6c48")
        self.opportunity_tree.tag_configure("blocked", foreground="#777777")
        self.opportunity_tree.tag_configure("freeze", foreground="#b00020")

    def _build_holdings_tab(self) -> None:
        frame = ttk.Frame(self.notebook)
        self.notebook.add(frame, text="Holdings")

        columns = ("market", "outcome", "shares", "avg_price", "bid", "ask", "value", "g_held")
        self.holdings_tree = ttk.Treeview(frame, columns=columns, show="headings", height=12)
        headings = {
            "market": "Market",
            "outcome": "Outcome",
            "shares": "Shares",
            "avg_price": "Avg Price",
            "bid": "Best Bid",
            "ask": "Best Ask",
            "value": "Value",
            "g_held": "g_held/day",
        }
        widths = {
            "market": 260,
            "outcome": 120,
            "shares": 120,
            "avg_price": 110,
            "bid": 110,
            "ask": 110,
            "value": 140,
            "g_held": 110,
        }
        for column in columns:
            self.holdings_tree.heading(column, text=headings[column])
            self.holdings_tree.column(column, width=widths[column], anchor="w")
        self.holdings_tree.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

    def _build_decisions_tab(self) -> None:
        frame = ttk.Frame(self.notebook)
        self.notebook.add(frame, text="Decisions")
        self.decisions_text = tk.Text(frame, height=20, state="disabled", wrap="word")
        self.decisions_text.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

    def _build_config_tab(self) -> None:
        frame = ttk.Frame(self.notebook)
        self.notebook.add(frame, text="Config")
        ttk.Button(frame, text="Reload Config", command=self.reload_config).pack(anchor="w", padx=8, pady=5)

        global_frame = ttk.LabelFrame(frame, text="Global Settings")
        global_frame.pack(fill=tk.X, padx=8, pady=5)

        global_fields = [
            ("Settlement lambda (days)", "lambda"),
            ("Delta threshold (per day)", "delta"),
            ("Minimum g (per day)", "min_g"),
            ("Cash reserve pct (0-1)", "cash_reserve"),
            ("Max parent allocation pct (0-1)", "max_parent"),
            ("Max month allocation pct (0-1)", "max_month"),
            ("Entry slippage cap (bps)", "slippage"),
            ("Exit slippage cap (bps)", "exit_slippage"),
        ]
        self.global_vars: Dict[str, tk.StringVar] = {}
        for idx, (label, key) in enumerate(global_fields):
            ttk.Label(global_frame, text=label + ":").grid(row=idx, column=0, sticky="w", padx=5, pady=3)
            var = tk.StringVar()
            self.global_vars[key] = var
            ttk.Entry(global_frame, textvariable=var, width=18).grid(row=idx, column=1, sticky="w", padx=5, pady=3)
        ttk.Button(global_frame, text="Save Global Settings", command=self.save_global_settings).grid(
            row=len(global_fields), column=0, columnspan=2, pady=6, padx=5, sticky="w"
        )

        market_frame = ttk.LabelFrame(frame, text="Market Settings")
        market_frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=5)

        selector_frame = ttk.Frame(market_frame)
        selector_frame.pack(fill=tk.X, padx=5, pady=5)
        ttk.Label(selector_frame, text="Select market policy:").pack(side=tk.LEFT)
        self.market_selector = ttk.Combobox(selector_frame, state="readonly", width=30)
        self.market_selector.pack(side=tk.LEFT, padx=6)
        self.market_selector.bind("<<ComboboxSelected>>", lambda _event: self.load_market_settings())

        bool_frame = ttk.Frame(market_frame)
        bool_frame.pack(fill=tk.X, padx=5, pady=5)
        self.market_bool_vars = {
            "auto_buy": tk.BooleanVar(),
            "auto_sell": tk.BooleanVar(),
            "news_freeze": tk.BooleanVar(),
            "whitelist_autobuy": tk.BooleanVar(),
            "auto_buy_drop_freeze": tk.BooleanVar(),
        }
        ttk.Checkbutton(
            bool_frame,
            text="Auto buy",
            variable=self.market_bool_vars["auto_buy"],
            command=lambda key="auto_buy": self.on_market_bool_toggle(key),
        ).pack(side=tk.LEFT, padx=5)
        ttk.Checkbutton(
            bool_frame,
            text="Auto sell",
            variable=self.market_bool_vars["auto_sell"],
            command=lambda key="auto_sell": self.on_market_bool_toggle(key),
        ).pack(side=tk.LEFT, padx=5)
        ttk.Checkbutton(
            bool_frame,
            text="News freeze",
            variable=self.market_bool_vars["news_freeze"],
            command=lambda key="news_freeze": self.on_market_bool_toggle(key),
        ).pack(side=tk.LEFT, padx=5)
        ttk.Checkbutton(
            bool_frame,
            text="Whitelist autobuy",
            variable=self.market_bool_vars["whitelist_autobuy"],
            command=lambda key="whitelist_autobuy": self.on_market_bool_toggle(key),
        ).pack(side=tk.LEFT, padx=5)
        ttk.Checkbutton(
            bool_frame,
            text="Drop freeze enabled",
            variable=self.market_bool_vars["auto_buy_drop_freeze"],
            command=lambda key="auto_buy_drop_freeze": self.on_market_bool_toggle(key),
        ).pack(side=tk.LEFT, padx=5)

        numeric_fields = [
            ("Priority (lower = higher)", "priority"),
            ("Min g (per day)", "min_g"),
            ("Min price", "min_price"),
            ("Max price", "max_price"),
            ("Min days", "min_days"),
            ("Max days", "max_days"),
            ("Max allocation pct (0-1)", "max_allocation_pct"),
            ("Max per event pct (0-1)", "max_per_event_pct"),
            ("Max per month pct (0-1)", "max_per_month_pct"),
            ("Max notional", "max_notional"),
            ("Per pass buy cap", "per_pass_buy_cap"),
            ("Slippage cap (bps)", "slippage_cap_bps"),
            ("Exit slippage cap (bps)", "exit_slippage_cap_bps"),
            ("Drop freeze pct", "drop_freeze_pct"),
            ("Drop window (minutes)", "drop_window_min"),
            ("Recovery wait (hours)", "recovery_wait_hours"),
            ("Cooldown (minutes)", "cooldown_minutes"),
        ]
        self.market_vars: Dict[str, tk.StringVar] = {}
        grid_frame = ttk.Frame(market_frame)
        grid_frame.pack(fill=tk.X, padx=5, pady=5)
        for idx, (label, key) in enumerate(numeric_fields):
            ttk.Label(grid_frame, text=label + ":").grid(row=idx // 2, column=(idx % 2) * 2, sticky="w", padx=5, pady=3)
            var = tk.StringVar()
            self.market_vars[key] = var
            ttk.Entry(grid_frame, textvariable=var, width=18).grid(
                row=idx // 2, column=(idx % 2) * 2 + 1, sticky="w", padx=5, pady=3
            )

        ttk.Button(market_frame, text="Save Market Settings", command=self.save_market_settings).pack(
            anchor="w", padx=5, pady=6
        )

        self.populate_config_controls()

    def populate_config_controls(self) -> None:
        gp = self.sim_config.global_policy
        self.global_vars["lambda"].set(f"{gp.settlement_lambda_days}")
        self.global_vars["delta"].set(f"{gp.delta_threshold}")
        self.global_vars["min_g"].set(f"{gp.min_g}")
        self.global_vars["cash_reserve"].set(f"{gp.cash_reserve_pct}")
        self.global_vars["max_parent"].set(f"{gp.max_parent_allocation_pct}")
        self.global_vars["max_month"].set(f"{gp.max_month_allocation_pct}")
        self.global_vars["slippage"].set(f"{gp.slippage_cap_bps}")
        self.global_vars["exit_slippage"].set(f"{gp.exit_slippage_cap_bps}")

        market_ids = sorted(self.sim_config.market_policies.keys())
        self.market_selector["values"] = tuple(market_ids)
        if market_ids:
            if self.market_selector.get() not in market_ids:
                self.market_selector.set(market_ids[0])
            self.load_market_settings()
        else:
            self.market_selector.set("")
            self.clear_market_form()

    def ensure_policy_for_market(self, market_id: str) -> MarketPolicy:
        if market_id in self.sim_config.market_policies:
            return self.sim_config.market_policies[market_id]
        base_policy = self.sim_config.market_policies.get("default")
        policy = replace(base_policy) if base_policy else MarketPolicy()
        self.sim_config.market_policies[market_id] = policy
        if hasattr(self, "market_selector"):
            values = list(self.market_selector["values"])
            if market_id not in values:
                values.append(market_id)
                self.market_selector["values"] = tuple(sorted(values))
        return policy

    def clear_market_form(self) -> None:
        self._loading_market_form = True
        for var in self.market_bool_vars.values():
            var.set(False)
        for var in self.market_vars.values():
            var.set("")
        self._loading_market_form = False

    def load_market_settings(self) -> None:
        market_id = self.market_selector.get()
        if not market_id:
            self.clear_market_form()
            return
        policy = self.sim_config.market_policies.get(market_id)
        if not policy:
            policy = self.ensure_policy_for_market(market_id)
        self._loading_market_form = True
        self.market_bool_vars["auto_buy"].set(policy.auto_buy)
        self.market_bool_vars["auto_sell"].set(policy.auto_sell)
        self.market_bool_vars["news_freeze"].set(policy.news_freeze)
        self.market_bool_vars["whitelist_autobuy"].set(policy.whitelist_autobuy)
        self.market_bool_vars["auto_buy_drop_freeze"].set(policy.auto_buy_drop_freeze)
        self.market_vars["priority"].set(str(policy.priority))
        self.market_vars["min_g"].set(f"{policy.min_g}")
        self.market_vars["min_price"].set(f"{policy.min_price}")
        self.market_vars["max_price"].set(f"{policy.max_price}")
        self.market_vars["min_days"].set(f"{policy.min_days}")
        self.market_vars["max_days"].set(f"{policy.max_days}")
        self.market_vars["max_allocation_pct"].set(f"{policy.max_allocation_pct}")
        self.market_vars["max_per_event_pct"].set("" if policy.max_per_event_pct is None else f"{policy.max_per_event_pct}")
        self.market_vars["max_per_month_pct"].set("" if policy.max_per_month_pct is None else f"{policy.max_per_month_pct}")
        self.market_vars["max_notional"].set(f"{policy.max_notional}")
        self.market_vars["per_pass_buy_cap"].set(f"{policy.per_pass_buy_cap}")
        self.market_vars["slippage_cap_bps"].set(f"{policy.slippage_cap_bps}")
        self.market_vars["exit_slippage_cap_bps"].set(
            "" if policy.exit_slippage_cap_bps is None else f"{policy.exit_slippage_cap_bps}"
        )
        self.market_vars["drop_freeze_pct"].set(f"{policy.drop_freeze_pct}")
        self.market_vars["drop_window_min"].set(f"{policy.drop_window_min}")
        self.market_vars["recovery_wait_hours"].set(f"{policy.recovery_wait_hours}")
        self.market_vars["cooldown_minutes"].set(f"{policy.cooldown_minutes}")
        self._loading_market_form = False

    def on_market_bool_toggle(self, field: str) -> None:
        if self._loading_market_form:
            return
        market_id = self.market_selector.get()
        if not market_id:
            return
        policy = self.ensure_policy_for_market(market_id)
        value = self.market_bool_vars[field].get()
        setattr(policy, field, value)
        try:
            save_config(self.sim_config, self.sim_config_path)
        except ValueError as exc:
            messagebox.showerror("Config validation failed", str(exc))
            # revert toggle
            self._loading_market_form = True
            setattr(policy, field, not value)
            self.market_bool_vars[field].set(not value)
            self._loading_market_form = False
            return
        self.status_var.set(
            f"{field.replace('_', ' ').title()} {'enabled' if value else 'disabled'} for {market_id}."
        )
        self.update_opportunities_view()
        self.update_overview()

    def save_global_settings(self) -> None:
        current_policy = self.market_selector.get() if hasattr(self, "market_selector") else ""
        try:
            gp = self.sim_config.global_policy
            gp.settlement_lambda_days = float(self.global_vars["lambda"].get())
            gp.delta_threshold = float(self.global_vars["delta"].get())
            gp.min_g = float(self.global_vars["min_g"].get())
            gp.cash_reserve_pct = float(self.global_vars["cash_reserve"].get())
            gp.max_parent_allocation_pct = float(self.global_vars["max_parent"].get())
            gp.max_month_allocation_pct = float(self.global_vars["max_month"].get())
            gp.slippage_cap_bps = float(self.global_vars["slippage"].get())
            gp.exit_slippage_cap_bps = float(self.global_vars["exit_slippage"].get())
        except ValueError as exc:
            messagebox.showerror("Invalid input", f"Global settings error: {exc}")
            return
        try:
            save_config(self.sim_config, self.sim_config_path)
        except ValueError as exc:
            messagebox.showerror("Config validation failed", str(exc))
            try:
                self.sim_config = load_config(self.sim_config_path)
                self.populate_config_controls()
            except Exception:
                pass
            return
        self.reload_config()
        if current_policy and current_policy in self.sim_config.market_policies:
            self.market_selector.set(current_policy)
            self.load_market_settings()
        self.status_var.set("Global settings saved.")

    def save_market_settings(self) -> None:
        market_id = self.market_selector.get()
        if not market_id:
            messagebox.showerror("Market settings", "Select a market policy first.")
            return
        policy = self.ensure_policy_for_market(market_id)
        try:
            policy.priority = int(self.market_vars["priority"].get())
            policy.min_g = float(self.market_vars["min_g"].get())
            policy.min_price = float(self.market_vars["min_price"].get())
            policy.max_price = float(self.market_vars["max_price"].get())
            policy.min_days = float(self.market_vars["min_days"].get())
            policy.max_days = float(self.market_vars["max_days"].get())
            policy.max_allocation_pct = float(self.market_vars["max_allocation_pct"].get())
            policy.max_notional = float(self.market_vars["max_notional"].get())
            policy.per_pass_buy_cap = float(self.market_vars["per_pass_buy_cap"].get())
            policy.slippage_cap_bps = float(self.market_vars["slippage_cap_bps"].get())
            exit_slip = self.market_vars["exit_slippage_cap_bps"].get().strip()
            policy.exit_slippage_cap_bps = float(exit_slip) if exit_slip else None
            policy.drop_freeze_pct = float(self.market_vars["drop_freeze_pct"].get())
            policy.drop_window_min = int(float(self.market_vars["drop_window_min"].get()))
            policy.recovery_wait_hours = float(self.market_vars["recovery_wait_hours"].get())
            max_event = self.market_vars["max_per_event_pct"].get().strip()
            policy.max_per_event_pct = float(max_event) if max_event else None
            max_month = self.market_vars["max_per_month_pct"].get().strip()
            policy.max_per_month_pct = float(max_month) if max_month else None
            policy.cooldown_minutes = int(float(self.market_vars["cooldown_minutes"].get()))
        except ValueError as exc:
            messagebox.showerror("Invalid input", f"Market settings error: {exc}")
            return
        policy.auto_buy = self.market_bool_vars["auto_buy"].get()
        policy.auto_sell = self.market_bool_vars["auto_sell"].get()
        policy.news_freeze = self.market_bool_vars["news_freeze"].get()
        policy.whitelist_autobuy = self.market_bool_vars["whitelist_autobuy"].get()
        policy.auto_buy_drop_freeze = self.market_bool_vars["auto_buy_drop_freeze"].get()
        try:
            save_config(self.sim_config, self.sim_config_path)
        except ValueError as exc:
            messagebox.showerror("Config validation failed", str(exc))
            try:
                self.sim_config = load_config(self.sim_config_path)
                self.populate_config_controls()
            except Exception:
                pass
            return
        self.reload_config()
        self.market_selector.set(market_id)
        self.load_market_settings()
        self.update_opportunities_view()
        self.update_overview()
        self.status_var.set(f"Market policy '{market_id}' saved.")
    def reset_simulation(self) -> None:
        dialog = NewBudgetDialog(self)
        if not dialog.result:
            return
        budget = float(dialog.result)
        self.state = RuntimeState(total_budget=budget, cash_balance=budget, mode="live")
        self.state.filepath = self.runtime_path
        self.state.save(self.runtime_path)
        self.mode = "live"
        self.mode_label_var.set("Live")
        self.refresh_views()
        self.status_var.set("New simulation created.")

    def load_runtime_state(self) -> None:
        path = filedialog.askopenfilename(title="Load runtime state", filetypes=[("JSON files", "*.json")])
        if not path:
            return
        try:
            self.state = RuntimeState.load(Path(path))
            self.runtime_path = Path(path)
            if self.state.mode != "live":
                self.state.mode = "live"
                try:
                    self.state.save(self.runtime_path)
                except Exception:
                    pass
            self.mode = "live"
            self.mode_label_var.set("Live")
            self.refresh_views()
            self.status_var.set(f"Loaded runtime state from {path}.")
        except Exception as exc:
            messagebox.showerror("Load failed", f"Unable to load runtime state: {exc}")

    def save_runtime_state(self) -> None:
        try:
            self.state.mode = self.mode
            self.state.save(self.runtime_path)
            self.status_var.set("Runtime state saved.")
        except Exception as exc:
            messagebox.showerror("Save failed", f"Unable to save runtime state: {exc}")

    def reload_config(self) -> None:
        try:
            self.sim_config = load_config(self.sim_config_path)
            self.engine = AllocationEngine(self.sim_config)
            self.status_var.set("Configuration reloaded.")
            self.refresh_views()
        except Exception as exc:
            messagebox.showerror("Config error", f"Unable to reload config: {exc}")

    def open_config_file(self) -> None:
        if not self.sim_config_path.exists():
            save_config(self.sim_config, self.sim_config_path)
        try:
            Path(self.sim_config_path).touch(exist_ok=True)
            import os
            if hasattr(os, "startfile"):
                os.startfile(self.sim_config_path)  # type: ignore[attr-defined]
            else:
                import subprocess
                subprocess.call(["open", str(self.sim_config_path)])
        except Exception as exc:
            messagebox.showerror("Open failed", f"Unable to open config file: {exc}")

    def _schedule_poll(self, initial: bool = False) -> None:
        interval = self.sim_config.polling.interval_seconds
        jitter = self.sim_config.polling.jitter_pct
        if self.poll_backoff_seconds:
            interval = min(self.poll_backoff_seconds, self.sim_config.polling.max_backoff_seconds)
        else:
            interval = int(interval * (1 + random.uniform(-jitter, jitter)))
        interval = max(5, interval)
        if initial:
            interval = 1
        self.poll_job = self.after(interval * 1000, self._poll_market_data)

    def _poll_market_data(self) -> None:
        try:
            self._refresh_market_data(quiet=True, auto=True)
            self.poll_backoff_seconds = None
            if self.trading_enabled.get():
                self.status_var.set("Auto refresh & allocation complete.")
            else:
                self.status_var.set("Auto refresh complete (trading paused).")
        except Exception as exc:
            self.status_var.set(f"Auto refresh error: {exc}")
            self.poll_backoff_seconds = (
                min(self.poll_backoff_seconds * 2, self.sim_config.polling.max_backoff_seconds)
                if self.poll_backoff_seconds
                else self.sim_config.polling.interval_seconds * 2
            )
        finally:
            self._schedule_poll()

    def refresh_data_manual(self) -> None:
        try:
            self._refresh_market_data()
            self.status_var.set("Market data refreshed.")
        except Exception as exc:
            messagebox.showerror("Refresh failed", str(exc))

    def _refresh_market_data(
        self,
        quiet: bool = False,
        auto: bool = False,
        market_keys: Optional[List[str]] = None,
    ) -> None:
        markets = (
            [self.state.market(key) for key in market_keys if self.state.market(key)]
            if market_keys
            else self.state.list_markets()
        )
        for market in markets:
            try:
                snapshot = fetch_snapshot_for_outcome(market.market_id, market.outcome)
            except PolymarketAPIError as exc:
                if quiet:
                    continue
                raise RuntimeError(f"Failed to fetch {market.market_id}: {exc}") from exc
            descriptor = get_outcome_descriptor(snapshot.raw_metadata, market.outcome)
            best_ask = snapshot.order_book.get("asks", [[None]])[0][0] if snapshot.order_book.get("asks") else None
            best_bid = snapshot.order_book.get("bids", [[None]])[0][0] if snapshot.order_book.get("bids") else None
            market.update_from_snapshot(
                snapshot=snapshot,
                last_price=descriptor.last_price,
                best_bid=best_bid,
                best_ask=best_ask,
                volume=parse_volume(snapshot.raw_metadata),
            )
            parent_event_id, parent_event_label = extract_parent_event(snapshot.raw_metadata)
            market.parent_event_id = parent_event_id
            market.parent_event_label = parent_event_label
            self._evaluate_circuit_breakers(market)
        self.state.save(self.runtime_path)
        if self.trading_enabled.get():
            self.run_allocation(auto=True, refresh=False)
        self.refresh_views()
        if auto and not quiet and not self.trading_enabled.get():
            self.status_var.set("Auto refresh complete (trading paused).")

    def _evaluate_circuit_breakers(self, market: MarketState) -> None:
        policy = self.sim_config.get_market_policy(market.market_id)
        global_policy = self.sim_config.global_policy
        now = _now()

        if policy.auto_buy_drop_freeze and not policy.whitelist_autobuy and market.best_ask:
            window_minutes = policy.drop_window_min or global_policy.circuit_breakers.drop_window_minutes
            drop_pct = policy.drop_freeze_pct or global_policy.circuit_breakers.drop_pct
            window_start = now - timedelta(minutes=window_minutes)
            samples = []
            for sample in market.price_history:
                try:
                    sample_time = _parse_iso(sample.timestamp)
                except Exception:
                    continue
                if sample_time >= window_start:
                    samples.append(sample)
            if samples:
                oldest = samples[0]
                if oldest.best_ask and oldest.best_ask > 0:
                    drop = (oldest.best_ask - market.best_ask) / oldest.best_ask * 100
                    if drop >= drop_pct:
                        until = now + timedelta(
                            hours=policy.recovery_wait_hours or global_policy.circuit_breakers.recovery_wait_hours
                        )
                        self.state.set_freeze(
                            market.key(),
                            FreezeStatus(
                                reason="price_drop",
                                activated_at=_now_iso(),
                                until=until.isoformat(timespec="seconds") + "Z",
                                details={"drop_pct": drop},
                            ),
                        )
                        return

        volume = market.last_volume or 0.0
        if volume and len(market.price_history) >= 2:
            previous_volume = market.price_history[-2].volume or 0.0
            if previous_volume > 0:
                multiplier = global_policy.circuit_breakers.volume_spike_multiplier
                if volume / previous_volume >= multiplier:
                    until = now + timedelta(hours=policy.recovery_wait_hours or global_policy.circuit_breakers.recovery_wait_hours)
                    self.state.set_freeze(
                        market.key(),
                        FreezeStatus(
                            reason="volume_spike",
                            activated_at=_now_iso(),
                            until=until.isoformat(timespec="seconds") + "Z",
                            details={"volume_ratio": volume / previous_volume},
                        ),
                    )
    def refresh_views(self) -> None:
        self.state.ensure_cash()
        self.update_opportunities_view()
        self.update_overview()
        self.update_holdings_view()
        self.update_decisions_view()
        self.update_config_view()

    def update_overview(self) -> None:
        total_invested = sum(market.invested_amount() for market in self.state.list_markets())
        realized = sum(market.realized_profit for market in self.state.list_markets())
        portfolio_value = self.state.cash_balance + sum(market.market_value() for market in self.state.list_markets())
        age_seconds = self.state.max_data_age_seconds()

        self.summary_labels["total_budget"].set(format_currency(self.state.total_budget))
        self.summary_labels["cash"].set(format_currency(self.state.cash_balance))
        self.summary_labels["invested"].set(format_currency(total_invested))
        self.summary_labels["realized"].set(format_currency(realized))
        self.summary_labels["portfolio_value"].set(format_currency(portfolio_value))
        self.summary_labels["data_age"].set(
            seconds_to_human(age_seconds) if age_seconds < float("inf") else "n/a"
        )

        self._refresh_watchlist_table()

        self.timeline_tree.delete(*self.timeline_tree.get_children())
        exposures_by_month: Dict[str, float] = {}
        counts_by_month: Dict[str, int] = {}
        for market in self.state.list_markets():
            key = market.resolution_month()
            exposures_by_month.setdefault(key, 0.0)
            exposures_by_month[key] += market.market_value()
            counts_by_month.setdefault(key, 0)
            counts_by_month[key] += 1
        for month, exposure in sorted(exposures_by_month.items()):
            count = counts_by_month.get(month, 0)
            self.timeline_tree.insert("", tk.END, values=(month, format_currency(exposure), count))

    def on_watchlist_select(self, *_args) -> None:
        selection = self.watchlist_tree.selection()
        if not selection:
            return
        key = selection[0]
        market = self.state.market(key)
        if not market:
            return
        policy_created = False
        if market.market_id not in self.sim_config.market_policies:
            self.ensure_policy_for_market(market.market_id)
            policy_created = True
        values = list(self.market_selector["values"])
        if market.market_id not in values:
            values.append(market.market_id)
            values = sorted(values)
            self.market_selector["values"] = tuple(values)
        if policy_created:
            try:
                save_config(self.sim_config, self.sim_config_path)
            except ValueError as exc:
                messagebox.showerror("Config error", str(exc))
        self.market_selector.set(market.market_id)
        self.load_market_settings()

    def _refresh_watchlist_table(self) -> None:
        if not hasattr(self, "watchlist_tree"):
            return
        self.watchlist_tree.delete(*self.watchlist_tree.get_children())
        evaluation_map: Dict[str, object] = {}
        if self.latest_evaluation:
            evaluation_map = {opp.market_key: opp for opp in self.latest_evaluation.opportunities}
        now_dt = _now()
        stale_threshold = self.sim_config.polling.stale_after_seconds
        for market in sorted(self.state.list_markets(), key=lambda m: (m.question.lower(), m.outcome.lower())):
            opportunity = evaluation_map.get(market.key())
            freeze = self.state.get_freeze(market.key())
            status_key = "tracked"
            notes: List[str] = []
            g_value = None
            if opportunity:
                status_key = opportunity.status
                g_value = opportunity.g
                if opportunity.reasons:
                    notes.append(", ".join(opportunity.reasons))
            if freeze:
                status_key = "frozen"
                notes.append(f"freeze:{freeze.reason}")
                try:
                    until_dt = _parse_iso(freeze.until)
                    seconds_left = (until_dt - now_dt).total_seconds()
                    if seconds_left > 0:
                        notes.append(f"unfreeze in {seconds_to_human(seconds_left)}")
                except Exception:
                    pass
            held_tag_needed = market.held_shares > 0
            if status_key == "eligible" and held_tag_needed:
                status_label = "Eligible + Holding"
            elif status_key == "frozen":
                status_label = "Frozen"
            elif status_key == "blocked":
                status_label = "Blocked"
            elif status_key == "tracked":
                status_label = "Tracked"
            else:
                status_label = status_key.title()
                if held_tag_needed:
                    status_label += " + Holding"

            shares_display = f"{market.held_shares:,.2f}"
            best_ask_display = f"{market.best_ask:.3f}" if market.best_ask is not None else "-"
            g_display = f"{g_value:.6f}" if g_value is not None else "-"
            notes_display = "; ".join(notes) if notes else ""
            updated_display = "never"
            tags = set()
            if status_key in {"eligible", "blocked", "frozen"}:
                tags.add(status_key)
            else:
                tags.add("tracked")
            if held_tag_needed:
                tags.add("holding")
            if market.last_fetch_ts:
                try:
                    last_dt = _parse_iso(market.last_fetch_ts)
                    age_secs = (now_dt - last_dt).total_seconds()
                    updated_display = f"{seconds_to_human(age_secs)} ago"
                    if age_secs > stale_threshold:
                        tags.add("stale")
                except Exception:
                    updated_display = market.last_fetch_ts
            self.watchlist_tree.insert(
                "",
                tk.END,
                iid=market.key(),
                values=(
                    market.question,
                    market.outcome,
                    shares_display,
                    best_ask_display,
                    g_display,
                    status_label,
                    notes_display,
                    updated_display,
                ),
                tags=tuple(tags),
            )

        self.timeline_tree.delete(*self.timeline_tree.get_children())
        exposures_by_month: Dict[str, float] = {}
        counts_by_month: Dict[str, int] = {}
        for market in self.state.list_markets():
            key = market.resolution_month()
            exposures_by_month.setdefault(key, 0.0)
            exposures_by_month[key] += market.market_value()
            counts_by_month.setdefault(key, 0)
            counts_by_month[key] += 1
        for month, exposure in sorted(exposures_by_month.items()):
            count = counts_by_month.get(month, 0)
            self.timeline_tree.insert("", tk.END, values=(month, format_currency(exposure), count))

    def update_opportunities_view(self) -> None:
        result = self.engine.evaluate(self.state)
        self.latest_evaluation = result
        self.opportunity_tree.delete(*self.opportunity_tree.get_children())
        for opp in result.opportunities:
            tags: List[str] = []
            if opp.status == "eligible":
                tags.append("eligible")
            else:
                tags.append("blocked")
                if any(reason.startswith("freeze") for reason in opp.reasons):
                    tags.append("freeze")
            self.opportunity_tree.insert(
                "",
                tk.END,
                iid=opp.market_key,
                values=(
                    opp.question,
                    opp.outcome,
                    f"{opp.best_ask:.3f}" if opp.best_ask else "-",
                    f"{opp.g:.6f}" if opp.g is not None else "-",
                    f"{opp.slippage_bps:.2f}" if opp.slippage_bps is not None else "-",
                    opp.status,
                    ", ".join(opp.reasons),
                ),
                tags=tuple(tags),
            )

    def update_holdings_view(self) -> None:
        self.holdings_tree.delete(*self.holdings_tree.get_children())
        lambda_days = self.sim_config.global_policy.settlement_lambda_days
        for market in self.state.engaged_markets():
            g_held = market.g_held(lambda_days)
            self.holdings_tree.insert(
                "",
                tk.END,
                iid=market.key(),
                values=(
                    market.question,
                    market.outcome,
                    f"{market.held_shares:,.2f}",
                    f"{market.average_price:.3f}" if market.average_price else "-",
                    f"{market.best_bid:.3f}" if market.best_bid else "-",
                    f"{market.best_ask:.3f}" if market.best_ask else "-",
                    format_currency(market.market_value()),
                    f"{g_held:.6f}" if g_held is not None else "-",
                ),
            )

    def update_decisions_view(self) -> None:
        self.decisions_text.configure(state="normal")
        self.decisions_text.delete("1.0", tk.END)
        decision = self.state.last_decision
        if not decision:
            self.decisions_text.insert(tk.END, "No decisions yet.")
        else:
            self.decisions_text.insert(tk.END, f"Last run: {decision.timestamp}\n\n")
            self.decisions_text.insert(tk.END, "Buys:\n")
            if decision.buys:
                for buy in decision.buys:
                    self.decisions_text.insert(
                        tk.END,
                        f"  + {buy['market_id']} ({buy['outcome']}): {buy['shares']:,.2f} @ {buy['price']:.3f}"
                        f" (slip {buy.get('slippage_bps', 0):.2f}bps, g={buy.get('g', '-')})\n",
                    )
            else:
                self.decisions_text.insert(tk.END, "  none\n")
            self.decisions_text.insert(tk.END, "\nSells:\n")
            if decision.sells:
                for sell in decision.sells:
                    self.decisions_text.insert(
                        tk.END,
                        f"  - {sell['market_id']} ({sell['outcome']}): {sell['shares']:,.2f} @ {sell['price']:.3f}"
                        f" (slip {sell.get('slippage_bps', 0):.2f}bps)\n",
                    )
            else:
                self.decisions_text.insert(tk.END, "  none\n")
            self.decisions_text.insert(tk.END, "\nTop rejections:\n")
            for rejection in decision.rejections[:5]:
                reasons = ", ".join(rejection.get("reasons", []))
                self.decisions_text.insert(
                    tk.END,
                    f"  * {rejection['market_id']} ({rejection['outcome']}): {reasons}"
                    f" (g={rejection.get('g', '-')})\n",
                )
        self.decisions_text.configure(state="disabled")

    def update_config_view(self) -> None:
        self.populate_config_controls()
    def add_market(self) -> None:
        default_policy = self.sim_config.market_policies.get("default")
        default_outcome = default_policy.side if default_policy else "Yes"
        dialog = AddMarketDialog(self, default_outcome)
        market_state: Optional[MarketState] = dialog.result
        if not market_state:
            return
        try:
            self.state.add_market(market_state)
            self.state.save(self.runtime_path)
            created_policy = market_state.market_id not in self.sim_config.market_policies
            if created_policy:
                self.ensure_policy_for_market(market_state.market_id)
                try:
                    save_config(self.sim_config, self.sim_config_path)
                except ValueError as exc:
                    messagebox.showwarning(
                        "Policy warning",
                        f"Market added but policy save failed: {exc}",
                    )
            self._refresh_market_data(quiet=False, auto=False, market_keys=[market_state.key()])
            self.status_var.set(f"Added {market_state.question}.")
        except ValueError as exc:
            messagebox.showerror("Add market failed", str(exc))
        except Exception as exc:
            messagebox.showerror("Add market failed", f"Unable to refresh market: {exc}")

    def remove_market(self) -> None:
        selection = self._selected_market_key()
        if not selection:
            messagebox.showinfo("Remove market", "Select a market first.")
            return
        try:
            self.state.remove_market(selection)
            self.state.save(self.runtime_path)
            self.refresh_views()
            self.status_var.set(f"Removed {selection}.")
        except ValueError as exc:
            messagebox.showerror("Remove failed", str(exc))

    def _selected_market_key(self) -> Optional[str]:
        if hasattr(self, "watchlist_tree"):
            selection = self.watchlist_tree.selection()
            if selection:
                return selection[0]
        selection = self.opportunity_tree.selection()
        if selection:
            return selection[0]
        selection = self.holdings_tree.selection()
        if selection:
            return selection[0]
        return None

    def run_allocation(self, auto: bool = False, refresh: bool = True) -> None:
        if auto and not self.trading_enabled.get():
            return
        if not auto:
            stale = self.state.max_data_age_seconds()
            if stale > self.sim_config.polling.stale_after_seconds:
                proceed = messagebox.askyesno(
                    "Data staleness",
                    f"Market data is {seconds_to_human(stale)} old. Continue optimization?",
                )
                if not proceed:
                    return
        try:
            result = self.engine.execute(self.state, mode=self.mode)
            self.state.cash_balance = max(self.state.cash_balance, 0.0)
            self.state.mode = self.mode
            self.state.save(self.runtime_path)
            if refresh:
                self.refresh_views()
            if auto:
                self.status_var.set(
                    f"Auto allocation: {len(result.buys)} buys, {len(result.sells)} sells."
                )
            else:
                self.status_var.set(
                    f"Allocation complete: {len(result.buys)} buys, {len(result.sells)} sells,"
                    f" {len(result.rejections)} rejections."
                )
        except Exception as exc:
            if auto:
                self.status_var.set(f"Allocation error: {exc}")
            else:
                messagebox.showerror("Optimization error", str(exc))

    def export_log_csv(self) -> None:
        path = filedialog.asksaveasfilename(
            title="Export trade log CSV",
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv")],
        )
        if not path:
            return
        import csv

        with open(path, "w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(
                [
                    "timestamp",
                    "mode",
                    "action",
                    "market_id",
                    "question",
                    "outcome",
                    "shares",
                    "price",
                    "value",
                    "g_before",
                    "g_after",
                    "slippage_bps",
                    "reasons",
                ]
            )
            for entry in self.state.trade_log:
                writer.writerow(
                    [
                        entry.timestamp,
                        entry.mode,
                        entry.action,
                        entry.market_id,
                        entry.question,
                        entry.outcome,
                        entry.shares,
                        entry.price,
                        entry.value,
                        entry.g_before if entry.g_before is not None else "",
                        entry.g_after if entry.g_after is not None else "",
                        entry.slippage_bps if entry.slippage_bps is not None else "",
                        ";".join(entry.reasons),
                    ]
                )
        self.status_var.set(f"Trade log exported to {path}.")

    def export_log_ndjson(self) -> None:
        path = filedialog.asksaveasfilename(
            title="Export trade log NDJSON",
            defaultextension=".ndjson",
            filetypes=[("NDJSON files", "*.ndjson")],
        )
        if not path:
            return
        with open(path, "w", encoding="utf-8") as handle:
            for entry in self.state.trade_log:
                handle.write(json.dumps(entry.__dict__))
                handle.write("\n")
        self.status_var.set(f"Trade log exported to {path}.")

def main() -> None:
    ensure_config(CONFIG_PATH)
    app = PolymarketApp()
    app.mainloop()


if __name__ == "__main__":
    main()
