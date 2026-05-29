"""
Tests for config loading and validation.
Run: pytest tests/ -v
"""
import os
import pytest


def test_config_loads():
    """Config should load without raising."""
    from src.config import Config
    assert Config.ENV in ("testnet", "live")


def test_config_grid_defaults():
    from src.config import Config
    assert Config.GRID_LEVELS > 0
    assert Config.GRID_SPACING_PCT > 0
    assert Config.GRID_STOP_LOSS_PCT > 0


def test_config_dca_defaults():
    from src.config import Config
    assert Config.DCA_MAX_SAFETY_ORDERS > 0
    assert Config.DCA_PRICE_DROP_PCT > 0
    assert Config.DCA_TAKE_PROFIT_PCT > 0


def test_testnet_keys_present():
    """Testnet keys must be set in .env for tests to run."""
    from src.config import Config
    if Config.ENV == "testnet":
        assert Config.BINANCE_TESTNET_API_KEY, "BINANCE_TESTNET_API_KEY missing"
        assert Config.BINANCE_TESTNET_SECRET,  "BINANCE_TESTNET_SECRET missing"
