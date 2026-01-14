"""
Configuration Module
====================

Handles loading, saving, and managing configuration for the trading bot.
"""

from src.config.config_manager import (
    # Constants
    CONFIG_SCHEMA_VERSION,
    
    # Data classes
    PollingConfig,
    CircuitBreakerConfig,
    GlobalPolicy,
    MarketPolicy,
    SimulatorConfig,
    
    # Functions
    load_config,
    save_config,
    ensure_config,
    validate_config,
)

__all__ = [
    "CONFIG_SCHEMA_VERSION",
    "PollingConfig",
    "CircuitBreakerConfig",
    "GlobalPolicy",
    "MarketPolicy",
    "SimulatorConfig",
    "load_config",
    "save_config",
    "ensure_config",
    "validate_config",
]
