"""Notification manager for alerts and system messages."""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional
from enum import Enum


class NotificationType(Enum):
    INFO = "info"
    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"
    TRADE = "trade"
    INSIDER_ALERT = "insider_alert"
    MARKET_UPDATE = "market_update"
    SYSTEM = "system"


@dataclass
class Notification:
    id: str
    type: NotificationType
    title: str
    message: str
    timestamp: str
    read: bool = False
    data: Dict = field(default_factory=dict)
    
    def to_dict(self) -> Dict:
        return {
            "id": self.id,
            "type": self.type.value,
            "title": self.title,
            "message": self.message,
            "timestamp": self.timestamp,
            "read": self.read,
            "data": self.data,
        }
    
    @staticmethod
    def from_dict(data: Dict) -> "Notification":
        return Notification(
            id=data["id"],
            type=NotificationType(data["type"]),
            title=data["title"],
            message=data["message"],
            timestamp=data["timestamp"],
            read=data.get("read", False),
            data=data.get("data", {}),
        )


class NotificationManager:
    """Manages notifications and alerts for the application."""
    
    _DEFAULT_DATA_DIR = Path(__file__).parent / "data"
    
    def __init__(self, storage_path: Optional[Path] = None):
        if storage_path is None:
            self._DEFAULT_DATA_DIR.mkdir(exist_ok=True)
            self.storage_path = self._DEFAULT_DATA_DIR / "notifications.json"
        else:
            self.storage_path = storage_path
        self.notifications: List[Notification] = []
        self.listeners: List[Callable[[Notification], None]] = []
        self._counter = 0
        self._lock = threading.Lock()
        self._load()
    
    def _generate_id(self) -> str:
        with self._lock:
            self._counter += 1
            return f"notif_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}_{self._counter}"
    
    def _now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    
    def add_listener(self, callback: Callable[[Notification], None]) -> None:
        """Register a callback to be called when new notifications arrive."""
        self.listeners.append(callback)
    
    def remove_listener(self, callback: Callable[[Notification], None]) -> None:
        """Remove a notification listener."""
        if callback in self.listeners:
            self.listeners.remove(callback)
    
    def notify(
        self,
        type: NotificationType,
        title: str,
        message: str,
        data: Optional[Dict] = None,
    ) -> Notification:
        """Create and dispatch a new notification."""
        notification = Notification(
            id=self._generate_id(),
            type=type,
            title=title,
            message=message,
            timestamp=self._now_iso(),
            data=data or {},
        )
        
        with self._lock:
            self.notifications.append(notification)
            # Keep only last 500 notifications
            if len(self.notifications) > 500:
                self.notifications = self.notifications[-500:]
        
        # Notify listeners
        for listener in self.listeners:
            try:
                listener(notification)
            except Exception:
                pass
        
        self._save()
        return notification
    
    def info(self, title: str, message: str, data: Optional[Dict] = None) -> Notification:
        return self.notify(NotificationType.INFO, title, message, data)
    
    def success(self, title: str, message: str, data: Optional[Dict] = None) -> Notification:
        return self.notify(NotificationType.SUCCESS, title, message, data)
    
    def warning(self, title: str, message: str, data: Optional[Dict] = None) -> Notification:
        return self.notify(NotificationType.WARNING, title, message, data)
    
    def error(self, title: str, message: str, data: Optional[Dict] = None) -> Notification:
        return self.notify(NotificationType.ERROR, title, message, data)
    
    def trade(self, title: str, message: str, data: Optional[Dict] = None) -> Notification:
        return self.notify(NotificationType.TRADE, title, message, data)
    
    def insider_alert(self, title: str, message: str, data: Optional[Dict] = None) -> Notification:
        return self.notify(NotificationType.INSIDER_ALERT, title, message, data)
    
    def market_update(self, title: str, message: str, data: Optional[Dict] = None) -> Notification:
        return self.notify(NotificationType.MARKET_UPDATE, title, message, data)
    
    def system(self, title: str, message: str, data: Optional[Dict] = None) -> Notification:
        return self.notify(NotificationType.SYSTEM, title, message, data)
    
    def mark_read(self, notification_id: str) -> None:
        """Mark a notification as read."""
        for notif in self.notifications:
            if notif.id == notification_id:
                notif.read = True
                break
        self._save()
    
    def mark_all_read(self) -> None:
        """Mark all notifications as read."""
        for notif in self.notifications:
            notif.read = True
        self._save()
    
    def get_unread_count(self) -> int:
        """Get count of unread notifications."""
        return sum(1 for n in self.notifications if not n.read)
    
    def get_recent(self, count: int = 50) -> List[Notification]:
        """Get most recent notifications."""
        return list(reversed(self.notifications[-count:]))
    
    def get_by_type(self, type: NotificationType, count: int = 50) -> List[Notification]:
        """Get notifications filtered by type."""
        filtered = [n for n in self.notifications if n.type == type]
        return list(reversed(filtered[-count:]))
    
    def clear_all(self) -> None:
        """Clear all notifications."""
        self.notifications = []
        self._save()
    
    def _save(self) -> None:
        """Persist notifications to disk."""
        try:
            data = [n.to_dict() for n in self.notifications]
            self.storage_path.write_text(json.dumps(data, indent=2))
        except Exception:
            pass
    
    def _load(self) -> None:
        """Load notifications from disk."""
        try:
            if self.storage_path.exists():
                data = json.loads(self.storage_path.read_text())
                self.notifications = [Notification.from_dict(n) for n in data]
        except Exception:
            self.notifications = []
