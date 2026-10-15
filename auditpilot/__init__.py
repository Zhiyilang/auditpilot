"""AuditPilot — forensic audit of recurring charges, 100% offline.

AuditPilot reads a bank or credit-card statement (CSV or pasted text) and
finds the things that quietly drain money: subscriptions you forgot, silent
price hikes, duplicate charges, and free trials that converted.

Design rules:
- Zero dependencies (Python standard library only). Clone and run.
- Never fabricate: rows that cannot be parsed are counted and reported,
  never guessed. Findings always carry evidence (source line + raw row).
- The statement never leaves the machine. There is no upload, no account,
  no API key required.
"""

__version__ = "1.0.0"

from .statement import Txn, ParseResult, parse_statement, merchant_key, display_name
from .detectors import (
    detect_recurring,
    detect_price_changes,
    detect_duplicates,
    detect_trials,
)
from .agent import audit_statement, build_savings_plan, risk_score

__all__ = [
    "Txn",
    "ParseResult",
    "parse_statement",
    "merchant_key",
    "display_name",
    "detect_recurring",
    "detect_price_changes",
    "detect_duplicates",
    "detect_trials",
    "audit_statement",
    "build_savings_plan",
    "risk_score",
]
