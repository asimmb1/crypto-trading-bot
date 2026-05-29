"""
Tests for the strategy selector.
Run: pytest tests/ -v
"""
import json
import pytest
from src.strategy_selector import StrategySelector, SIT_OUT, GRID, DCA


@pytest.fixture
def matrix_file(tmp_path):
    """Create a minimal profitability matrix for testing."""
    matrix = {
        "generated_at": "2026-01-01",
        "pairs": {
            "BTC/USDT": {
                "BEAR_TREND": {
                    "grid": {"approved": True,  "avg_return": 8.0, "win_rate": 1.0, "occurrences": 4},
                    "dca":  {"approved": False, "avg_return": -5.0, "win_rate": 0.3, "occurrences": 4},
                },
                "RANGING": {
                    "grid": {"approved": False, "avg_return": 0.0, "win_rate": 0.0, "occurrences": 1},
                    "dca":  {"approved": False, "avg_return": 0.0, "win_rate": 0.0, "occurrences": 1},
                },
            },
            "LINK/USDT": {
                "RANGING": {
                    "grid": {"approved": True, "avg_return": 12.0, "win_rate": 1.0, "occurrences": 3},
                    "dca":  {"approved": True, "avg_return":  5.0, "win_rate": 0.75, "occurrences": 4},
                },
            },
        },
    }
    path = tmp_path / "matrix.json"
    path.write_text(json.dumps(matrix))
    return str(path)


def test_select_grid_when_only_approved(matrix_file):
    sel = StrategySelector(matrix_file)
    assert sel.select("BTC/USDT", "BEAR_TREND") == GRID


def test_sit_out_when_nothing_approved(matrix_file):
    sel = StrategySelector(matrix_file)
    assert sel.select("BTC/USDT", "RANGING") == SIT_OUT


def test_picks_higher_return_when_both_approved(matrix_file):
    """Grid has higher avg_return than DCA for LINK RANGING — should pick grid."""
    sel = StrategySelector(matrix_file)
    assert sel.select("LINK/USDT", "RANGING") == GRID


def test_sit_out_for_unknown_pair(matrix_file):
    sel = StrategySelector(matrix_file)
    assert sel.select("SOL/USDT", "RANGING") == SIT_OUT


def test_sit_out_when_no_matrix(tmp_path):
    """Missing matrix file should default to sit_out safely."""
    sel = StrategySelector(str(tmp_path / "missing.json"))
    assert sel.select("BTC/USDT", "RANGING") == SIT_OUT
