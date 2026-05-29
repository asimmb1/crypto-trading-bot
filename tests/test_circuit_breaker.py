"""
Tests for the circuit breaker.
Run: pytest tests/ -v
"""
import os
import pytest


@pytest.fixture(autouse=True)
def clean_state(tmp_path, monkeypatch):
    """Use a temp state file so tests don't pollute logs/."""
    monkeypatch.setattr("src.circuit_breaker.STATE_FILE",
                        str(tmp_path / "system_state.json"))


def test_not_tripped_on_init():
    from src.circuit_breaker import CircuitBreaker
    cb = CircuitBreaker(starting_capital=1000)
    assert not cb.is_tripped()


def test_drawdown_does_not_trip_below_threshold():
    from src.circuit_breaker import CircuitBreaker
    cb = CircuitBreaker(starting_capital=1000)
    cb.check_drawdown(900)   # 10% drop — below 15% threshold
    assert not cb.is_tripped()


def test_drawdown_trips_above_threshold(monkeypatch):
    monkeypatch.setattr("src.circuit_breaker.notify", lambda msg: None)
    from src.circuit_breaker import CircuitBreaker
    cb = CircuitBreaker(starting_capital=1000)
    cb.check_drawdown(840)   # 16% drop — above 15% threshold
    assert cb.is_tripped()


def test_crash_velocity_trips(monkeypatch):
    monkeypatch.setattr("src.circuit_breaker.notify", lambda msg: None)
    from src.circuit_breaker import CircuitBreaker
    cb = CircuitBreaker(starting_capital=1000)
    cb.check_velocity("BTC/USDT", prev_price=70000, current_price=64000)  # 8.6% drop
    assert cb.is_tripped()


def test_crash_velocity_no_trip_small_drop(monkeypatch):
    monkeypatch.setattr("src.circuit_breaker.notify", lambda msg: None)
    from src.circuit_breaker import CircuitBreaker
    cb = CircuitBreaker(starting_capital=1000)
    cb.check_velocity("BTC/USDT", prev_price=70000, current_price=67000)  # 4.3% drop
    assert not cb.is_tripped()


def test_reset_clears_lock(monkeypatch):
    monkeypatch.setattr("src.circuit_breaker.notify", lambda msg: None)
    from src.circuit_breaker import CircuitBreaker
    cb = CircuitBreaker(starting_capital=1000)
    cb.check_drawdown(840)
    assert cb.is_tripped()
    cb.reset()
    assert not cb.is_tripped()


def test_exchange_errors_accumulate(monkeypatch):
    monkeypatch.setattr("src.circuit_breaker.notify", lambda msg: None)
    from src.circuit_breaker import CircuitBreaker, EXCHANGE_ERROR_THRESHOLD
    cb = CircuitBreaker(starting_capital=1000)
    for _ in range(EXCHANGE_ERROR_THRESHOLD - 1):
        cb.check_exchange(success=False)
    assert not cb.is_tripped()
    cb.check_exchange(success=False)   # final error — should trip
    assert cb.is_tripped()
