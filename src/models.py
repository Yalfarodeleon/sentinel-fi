"""
Data models — Pydantic v2 for validation, serialization, and clear contracts
between the producer, consumer, and anomaly rules.
"""

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class Transaction(BaseModel):
    """A single financial transaction flowing through the pipeline."""

    transaction_id: str
    user_id: str
    amount: float = Field(gt=0)
    timestamp: float
    merchant: str


class Severity(str, Enum):
    """Anomaly severity levels, ordered by urgency."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class Anomaly(BaseModel):
    """A detected anomaly tied to a specific transaction."""

    rule: str
    detail: str
    severity: Severity
    transaction_id: str
    user_id: str
    amount: float
    timestamp: float


class ProcessedTransaction(BaseModel):
    """
    A transaction after passing through the anomaly engine.
    Bundles the original transaction with any flags raised.
    """

    transaction: Transaction
    anomalies: list[Anomaly] = Field(default_factory=list)
    is_flagged: bool = False

    def model_post_init(self, __context) -> None:
        self.is_flagged = len(self.anomalies) > 0