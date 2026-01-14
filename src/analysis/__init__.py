"""
Analysis Module
===============

Market analysis tools including insider detection and news sentiment.
"""

from src.analysis.insider_detector import (
    AlertSeverity,
    TraderProfile,
    InsiderAlert,
    InsiderDetectorConfig,
    InsiderDetector,
)

from src.analysis.news_analyzer import (
    Sentiment,
    MarketCategory,
    NewsArticle,
    MarketSignal,
    BULLISH_KEYWORDS,
    BEARISH_KEYWORDS,
    CATEGORY_KEYWORDS,
    NewsAnalyzer,
    get_market_category_display,
)

__all__ = [
    # Insider detector
    "AlertSeverity",
    "TraderProfile",
    "InsiderAlert",
    "InsiderDetectorConfig",
    "InsiderDetector",
    
    # News analyzer
    "Sentiment",
    "MarketCategory",
    "NewsArticle",
    "MarketSignal",
    "BULLISH_KEYWORDS",
    "BEARISH_KEYWORDS",
    "CATEGORY_KEYWORDS",
    "NewsAnalyzer",
    "get_market_category_display",
]
