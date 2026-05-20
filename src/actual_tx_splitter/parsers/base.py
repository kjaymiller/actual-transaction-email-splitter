from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Protocol


@dataclass
class LineItem:
    description: str
    amount: Decimal  # positive, in major units (USD dollars). Tax/shipping count too.


@dataclass
class ParsedOrder:
    vendor: str
    order_id: str
    total: Decimal
    line_items: list[LineItem] = field(default_factory=list)
    card_last4: str | None = None
    order_date: date | None = None
    # Short human-friendly description (typically the first item name) — used
    # for the parent transaction's notes so Actual's list view is scannable.
    summary: str | None = None

    def reconciles(self, tolerance_cents: int = 5) -> bool:
        """Sum of line items should equal total within a few cents (rounding)."""
        s = sum((li.amount for li in self.line_items), Decimal(0))
        diff = abs(s - self.total)
        return int(diff * 100) <= tolerance_cents


class Parser(Protocol):
    name: str

    def matches(self, email: dict) -> bool: ...
    def parse(self, email: dict) -> ParsedOrder: ...
