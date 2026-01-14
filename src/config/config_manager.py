"""Configuration loading and access helpers for the Polymarket simulator."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional

import yaml


CONFIG_SCHEMA_VERSION = "1"


@dataclass
class PollingConfig:
    interval_seconds: int = 60
    jitter_pct: float = 0.1
    max_backoff_seconds: int = 300
    stale_after_seconds: int = 300


@dataclass
class CircuitBreakerConfig:
    drop_pct: float = 20.0
    drop_window_minutes: int = 15
    recovery_wait_hours: int = 3
    volume_spike_multiplier: float = 3.0
    cooldown_minutes: int = 15


@dataclass
class GlobalPolicy:
    settlement_lambda_days: float = 1.0
    delta_threshold: float = 0.0002
    min_g: float = 0.0008
    cash_reserve_pct: float = 0.07
    max_parent_allocation_pct: float = 0.20
    max_month_allocation_pct: float = 0.35
    slippage_cap_bps: float = 40.0
    exit_slippage_cap_bps: float = 40.0
    circuit_breakers: CircuitBreakerConfig = field(default_factory=CircuitBreakerConfig)


@dataclass
class MarketPolicy:
    enabled: bool = True
    side: str = "yes"
    auto_buy: bool = False
    auto_sell: bool = True
    max_allocation_pct: float = 0.20
    max_notional: float = 15000.0
    per_pass_buy_cap: float = 3000.0
    min_price: float = 0.02
    max_price: float = 0.90
    min_days: float = 1.0
    max_days: float = 120.0
    min_g: float = 0.0008
    slippage_cap_bps: float = 40.0
    exit_slippage_cap_bps: Optional[float] = None
    drop_freeze_pct: float = 20.0
    drop_window_min: int = 15
    recovery_wait_hours: float = 3.0
    news_freeze: bool = False
    priority: int = 3
    whitelist_autobuy: bool = False
    max_per_event_pct: Optional[float] = None
    max_per_month_pct: Optional[float] = None
    auto_buy_drop_freeze: bool = True
    cooldown_minutes: int = 15

    def effective_slippage_cap(self, global_policy: GlobalPolicy) -> float:
        return self.slippage_cap_bps if self.slippage_cap_bps is not None else global_policy.slippage_cap_bps

    def effective_exit_slippage_cap(self, global_policy: GlobalPolicy) -> float:
        return (
            self.exit_slippage_cap_bps
            if self.exit_slippage_cap_bps is not None
            else global_policy.exit_slippage_cap_bps
        )


@dataclass
class SimulatorConfig:
    schema_version: str = CONFIG_SCHEMA_VERSION
    mode: str = "dry_run"
    polling: PollingConfig = field(default_factory=PollingConfig)
    global_policy: GlobalPolicy = field(default_factory=GlobalPolicy)
    market_policies: Dict[str, MarketPolicy] = field(default_factory=dict)

    def get_market_policy(self, market_id: str) -> MarketPolicy:
        if market_id in self.market_policies:
            return self.market_policies[market_id]
        return self.market_policies.get("default", MarketPolicy())


def _load_yaml(path: Path) -> Dict:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Invalid configuration format in {path}")
    return data


def load_config(path: Path) -> SimulatorConfig:
    raw = _load_yaml(path)
    schema_version = raw.get("schema_version", CONFIG_SCHEMA_VERSION)
    if str(schema_version) != CONFIG_SCHEMA_VERSION:
        raise ValueError(
            f"Unsupported config schema_version={schema_version}; expected {CONFIG_SCHEMA_VERSION}."
        )

    polling_raw = raw.get("polling", {})
    polling = PollingConfig(
        interval_seconds=int(polling_raw.get("interval_seconds", 60)),
        jitter_pct=float(polling_raw.get("jitter_pct", 0.1)),
        max_backoff_seconds=int(polling_raw.get("max_backoff_seconds", 300)),
        stale_after_seconds=int(polling_raw.get("stale_after_seconds", 300)),
    )

    cb_raw = raw.get("global", {}).get("circuit_breakers", {})
    circuit_breakers = CircuitBreakerConfig(
        drop_pct=float(cb_raw.get("drop_pct", 20.0)),
        drop_window_minutes=int(cb_raw.get("drop_window_minutes", 15)),
        recovery_wait_hours=float(cb_raw.get("recovery_wait_hours", 3.0)),
        volume_spike_multiplier=float(cb_raw.get("volume_spike_multiplier", 3.0)),
        cooldown_minutes=int(cb_raw.get("cooldown_minutes", 15)),
    )

    global_raw = raw.get("global", {})
    global_policy = GlobalPolicy(
        settlement_lambda_days=float(global_raw.get("settlement_lambda_days", 1.0)),
        delta_threshold=float(global_raw.get("delta_threshold", 0.0002)),
        min_g=float(global_raw.get("min_g", 0.0008)),
        cash_reserve_pct=float(global_raw.get("cash_reserve_pct", 0.07)),
        max_parent_allocation_pct=float(global_raw.get("max_parent_allocation_pct", 0.20)),
        max_month_allocation_pct=float(global_raw.get("max_month_allocation_pct", 0.35)),
        slippage_cap_bps=float(global_raw.get("slippage_cap_bps", 40.0)),
        exit_slippage_cap_bps=float(global_raw.get("exit_slippage_cap_bps", 40.0)),
        circuit_breakers=circuit_breakers,
    )

    markets_raw = raw.get("markets", {})
    market_policies: Dict[str, MarketPolicy] = {}
    for market_id, policy_raw in markets_raw.items():
        policy = MarketPolicy(
            enabled=policy_raw.get("enabled", True),
            side=policy_raw.get("side", "yes"),
            auto_buy=policy_raw.get("auto_buy", False),
            auto_sell=policy_raw.get("auto_sell", True),
            max_allocation_pct=float(policy_raw.get("max_allocation_pct", 0.20)),
            max_notional=float(policy_raw.get("max_notional", 15000.0)),
            per_pass_buy_cap=float(policy_raw.get("per_pass_buy_cap", 3000.0)),
            min_price=float(policy_raw.get("min_price", 0.02)),
            max_price=float(policy_raw.get("max_price", 0.90)),
            min_days=float(policy_raw.get("min_days", 1.0)),
            max_days=float(policy_raw.get("max_days", 120.0)),
            min_g=float(policy_raw.get("min_g", 0.0008)),
            slippage_cap_bps=float(policy_raw.get("slippage_cap_bps", global_policy.slippage_cap_bps)),
            exit_slippage_cap_bps=policy_raw.get("exit_slippage_cap_bps"),
            drop_freeze_pct=float(policy_raw.get("drop_freeze_pct", 20.0)),
            drop_window_min=int(policy_raw.get("drop_window_min", 15)),
            recovery_wait_hours=float(policy_raw.get("recovery_wait_hours", 3.0)),
            news_freeze=policy_raw.get("news_freeze", False),
            priority=int(policy_raw.get("priority", 3)),
            whitelist_autobuy=policy_raw.get("whitelist_autobuy", False),
            max_per_event_pct=policy_raw.get("max_per_event_pct"),
            max_per_month_pct=policy_raw.get("max_per_month_pct"),
            auto_buy_drop_freeze=policy_raw.get("auto_buy_drop_freeze", True),
            cooldown_minutes=int(policy_raw.get("cooldown_minutes", 15)),
        )
        market_policies[market_id] = policy

    config = SimulatorConfig(
        schema_version=CONFIG_SCHEMA_VERSION,
        mode=raw.get("mode", "dry_run"),
        polling=polling,
        global_policy=global_policy,
        market_policies=market_policies,
    )
    return config


def ensure_config(path: Path) -> SimulatorConfig:
    if not path.exists():
        default = SimulatorConfig(
            market_policies={"default": MarketPolicy()},
        )
        save_config(default, path)
        return default
    return load_config(path)


def validate_config(config: SimulatorConfig) -> None:
    def ensure(condition: bool, message: str) -> None:
        if not condition:
            raise ValueError(message)

    polling = config.polling
    ensure(polling.interval_seconds >= 5, "Polling interval_seconds must be at least 5 seconds.")
    ensure(polling.jitter_pct >= 0, "Polling jitter_pct must be non-negative.")
    ensure(
        polling.max_backoff_seconds >= polling.interval_seconds,
        "Polling max_backoff_seconds must be >= interval_seconds.",
    )
    ensure(polling.stale_after_seconds >= 0, "Polling stale_after_seconds must be non-negative.")

    gp = config.global_policy
    ensure(0 <= gp.cash_reserve_pct <= 1, f"Global cash_reserve_pct must be between 0 and 1 (got {gp.cash_reserve_pct}).")
    ensure(
        0 <= gp.max_parent_allocation_pct <= 1,
        f"Global max_parent_allocation_pct must be between 0 and 1 (got {gp.max_parent_allocation_pct}).",
    )
    ensure(
        0 <= gp.max_month_allocation_pct <= 1,
        f"Global max_month_allocation_pct must be between 0 and 1 (got {gp.max_month_allocation_pct}).",
    )
    ensure(gp.slippage_cap_bps >= 0, "Global slippage_cap_bps must be non-negative.")
    ensure(gp.exit_slippage_cap_bps >= 0, "Global exit_slippage_cap_bps must be non-negative.")
    cb = gp.circuit_breakers
    ensure(cb.drop_pct >= 0, "Circuit breaker drop_pct must be non-negative.")
    ensure(cb.drop_window_minutes >= 1, "Circuit breaker drop_window_minutes must be at least 1.")
    ensure(cb.recovery_wait_hours >= 0, "Circuit breaker recovery_wait_hours must be non-negative.")
    ensure(cb.volume_spike_multiplier >= 0, "Circuit breaker volume_spike_multiplier must be non-negative.")
    ensure(cb.cooldown_minutes >= 0, "Circuit breaker cooldown_minutes must be non-negative.")

    for market_id, policy in config.market_policies.items():
        prefix = f"Market '{market_id}': "
        ensure(0 <= policy.max_allocation_pct <= 1, prefix + "max_allocation_pct must be between 0 and 1.")
        ensure(policy.max_notional >= 0, prefix + "max_notional must be non-negative.")
        ensure(policy.per_pass_buy_cap >= 0, prefix + "per_pass_buy_cap must be non-negative.")
        ensure(0 <= policy.min_price <= 1, prefix + "min_price must be between 0 and 1.")
        ensure(0 <= policy.max_price <= 1, prefix + "max_price must be between 0 and 1.")
        ensure(policy.min_price <= policy.max_price, prefix + "min_price must be <= max_price.")
        ensure(policy.min_days >= 0, prefix + "min_days must be non-negative.")
        ensure(policy.max_days >= policy.min_days, prefix + "max_days must be >= min_days.")
        ensure(policy.min_g >= 0, prefix + "min_g must be non-negative.")
        ensure(policy.slippage_cap_bps >= 0, prefix + "slippage_cap_bps must be non-negative.")
        if policy.exit_slippage_cap_bps is not None:
            ensure(policy.exit_slippage_cap_bps >= 0, prefix + "exit_slippage_cap_bps must be non-negative.")
        if policy.max_per_event_pct is not None:
            ensure(0 <= policy.max_per_event_pct <= 1, prefix + "max_per_event_pct must be between 0 and 1.")
        if policy.max_per_month_pct is not None:
            ensure(0 <= policy.max_per_month_pct <= 1, prefix + "max_per_month_pct must be between 0 and 1.")
        ensure(policy.drop_freeze_pct >= 0, prefix + "drop_freeze_pct must be non-negative.")
        ensure(policy.drop_window_min >= 0, prefix + "drop_window_min must be non-negative.")
        ensure(policy.recovery_wait_hours >= 0, prefix + "recovery_wait_hours must be non-negative.")
        ensure(policy.cooldown_minutes >= 0, prefix + "cooldown_minutes must be non-negative.")


def save_config(config: SimulatorConfig, path: Path) -> None:
    validate_config(config)
    data = {
        "schema_version": config.schema_version,
        "mode": config.mode,
        "polling": {
            "interval_seconds": config.polling.interval_seconds,
            "jitter_pct": config.polling.jitter_pct,
            "max_backoff_seconds": config.polling.max_backoff_seconds,
            "stale_after_seconds": config.polling.stale_after_seconds,
        },
        "global": {
            "settlement_lambda_days": config.global_policy.settlement_lambda_days,
            "delta_threshold": config.global_policy.delta_threshold,
            "min_g": config.global_policy.min_g,
            "cash_reserve_pct": config.global_policy.cash_reserve_pct,
            "max_parent_allocation_pct": config.global_policy.max_parent_allocation_pct,
            "max_month_allocation_pct": config.global_policy.max_month_allocation_pct,
            "slippage_cap_bps": config.global_policy.slippage_cap_bps,
            "exit_slippage_cap_bps": config.global_policy.exit_slippage_cap_bps,
            "circuit_breakers": {
                "drop_pct": config.global_policy.circuit_breakers.drop_pct,
                "drop_window_minutes": config.global_policy.circuit_breakers.drop_window_minutes,
                "recovery_wait_hours": config.global_policy.circuit_breakers.recovery_wait_hours,
                "volume_spike_multiplier": config.global_policy.circuit_breakers.volume_spike_multiplier,
                "cooldown_minutes": config.global_policy.circuit_breakers.cooldown_minutes,
            },
        },
        "markets": {},
    }
    for market_id, policy in config.market_policies.items():
        data["markets"][market_id] = {
            "enabled": policy.enabled,
            "side": policy.side,
            "auto_buy": policy.auto_buy,
            "auto_sell": policy.auto_sell,
            "max_allocation_pct": policy.max_allocation_pct,
            "max_notional": policy.max_notional,
            "per_pass_buy_cap": policy.per_pass_buy_cap,
            "min_price": policy.min_price,
            "max_price": policy.max_price,
            "min_days": policy.min_days,
            "max_days": policy.max_days,
            "min_g": policy.min_g,
            "slippage_cap_bps": policy.slippage_cap_bps,
            "exit_slippage_cap_bps": policy.exit_slippage_cap_bps,
            "drop_freeze_pct": policy.drop_freeze_pct,
            "drop_window_min": policy.drop_window_min,
            "recovery_wait_hours": policy.recovery_wait_hours,
            "news_freeze": policy.news_freeze,
            "priority": policy.priority,
            "whitelist_autobuy": policy.whitelist_autobuy,
            "max_per_event_pct": policy.max_per_event_pct,
            "max_per_month_pct": policy.max_per_month_pct,
            "auto_buy_drop_freeze": policy.auto_buy_drop_freeze,
            "cooldown_minutes": policy.cooldown_minutes,
        }
    with path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(data, handle, sort_keys=False)
