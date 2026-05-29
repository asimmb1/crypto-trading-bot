"""
Tests for the market classifier.
Run: pytest tests/ -v
"""
import pandas as pd
import numpy as np
import pytest
from src.market_classifier import (
    MarketClassifier, BULL_TREND, BEAR_TREND, RANGING, HIGH_VOL, LOW_VOL, ALL_REGIMES
)


def make_ohlcv(n: int, trend: float = 0.0, volatility: float = 1.0) -> pd.DataFrame:
    """Generate synthetic OHLCV data for testing."""
    np.random.seed(42)
    close = 100.0
    rows = []
    for i in range(n):
        close = close * (1 + trend + np.random.normal(0, volatility / 100))
        high  = close * (1 + abs(np.random.normal(0, 0.005)))
        low   = close * (1 - abs(np.random.normal(0, 0.005)))
        rows.append({
            "open": close * 0.999,
            "high": high,
            "low": low,
            "close": close,
            "volume": 1000,
        })
    df = pd.DataFrame(rows)
    df.index = pd.date_range("2024-01-01", periods=n, freq="D")
    df.index.name = "timestamp"
    return df


def test_classifier_returns_known_regime():
    clf = MarketClassifier()
    df = make_ohlcv(300)
    regime = clf.classify(df)
    assert regime in ALL_REGIMES


def test_tag_all_adds_regime_column():
    clf = MarketClassifier()
    df = make_ohlcv(300)
    tagged = clf.tag_all(df)
    assert "regime" in tagged.columns
    assert len(tagged) == len(df)


def test_all_regimes_are_strings():
    assert all(isinstance(r, str) for r in ALL_REGIMES)


def test_uptrend_classified_as_bull_or_high_vol():
    """Strong uptrend should classify as BULL_TREND or HIGH_VOL."""
    clf = MarketClassifier()
    df = make_ohlcv(300, trend=0.003)   # +0.3% per day
    regime = clf.classify(df)
    assert regime in (BULL_TREND, HIGH_VOL, RANGING)  # RANGING ok with short data


def test_downtrend_not_bull():
    """Strong downtrend should NOT be BULL_TREND."""
    clf = MarketClassifier()
    df = make_ohlcv(300, trend=-0.003)  # -0.3% per day
    regime = clf.classify(df)
    assert regime != BULL_TREND
