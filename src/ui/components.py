"""
UI Components - Reusable widgets for the trading bot interface
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from datetime import datetime
from typing import Dict, List

from src.ui.theme import Theme


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
                selectcolor=Theme.BG_INPUT,
            )
        else:
            self.input = tk.Entry(
                row,
                textvariable=self.var,
                font=("Consolas", 9),
                bg=Theme.BG_INPUT,
                fg=Theme.TEXT_PRIMARY,
                insertbackground=Theme.TEXT_PRIMARY,
                width=15,
                relief=tk.FLAT,
            )
        
        self.input.pack(side=tk.RIGHT, padx=(5, 0))
    
    def get_value(self):
        """Get the typed value from the entry."""
        if self.var_type == "bool":
            return self.var.get()
        
        raw = self.var.get().strip()
        if not raw:
            return None
        
        try:
            if self.var_type == "int":
                return int(raw)
            elif self.var_type == "percent":
                # Remove % if present
                raw = raw.replace("%", "").strip()
                return float(raw) / 100.0
            else:  # float
                return float(raw)
        except ValueError:
            return None
    
    def set_value(self, value):
        """Set the value of the entry."""
        if self.var_type == "bool":
            self.var.set(bool(value))
        elif self.var_type == "percent" and value is not None:
            self.var.set(f"{value * 100:.1f}")
        else:
            self.var.set(str(value) if value is not None else "")
