"""
Tests for anomaly detection rules.
Run: pytest tests/ -v
"""

import time

import pytest

from src.models import Transaction, Severity


def _make_txn(**overrides) -> Transaction:
    """Helper to build a Transaction with sensible defaults."""
    defaults = {
        "transaction_id": "txn_test_001",
        "user_id": "user_0001",
        "amount": 50.00,
        "timestamp": time.time(),
        "merchant": "TestMart",
    }
    defaults.update(overrides)
    return Transaction(**defaults)


class TestTransactionModel:
    """Verify Pydantic validation catches bad data before it hits the engine."""

    def test_valid_transaction(self):
        txn = _make_txn()
        assert txn.user_id == "user_0001"
        assert txn.amount == 50.00

    def test_negative_amount_rejected(self):
        with pytest.raises(Exception):
            _make_txn(amount=-10.00)

    def test_zero_amount_rejected(self):
        with pytest.raises(Exception):
            _make_txn(amount=0)

    def test_serialization_roundtrip(self):
        txn = _make_txn()
        json_str = txn.model_dump_json()
        restored = Transaction.model_validate_json(json_str)
        assert restored.transaction_id == txn.transaction_id
        assert restored.amount == txn.amount


class TestSeverityOrdering:
    """Verify severity enum values for downstream filtering."""

    def test_severity_values(self):
        assert Severity.LOW.value == "LOW"
        assert Severity.CRITICAL.value == "CRITICAL"


# ── Integration tests (require Redis) ──
# These would use pytest-asyncio and a real or mocked Redis:
#
# @pytest.mark.asyncio
# async def test_velocity_triggers_above_threshold():
#     """Pump 11 transactions in 60s → expect velocity anomaly."""
#     ...
#
# @pytest.mark.asyncio
# async def test_spike_triggers_above_multiplier():
#     """Send 5 normal txns, then one at 4x average → expect spike."""
#     ...