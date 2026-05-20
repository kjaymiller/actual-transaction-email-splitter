"""Thin wrapper around actualpy.

actualpy's surface evolves; if a release bumps these query helpers the
fixes belong here (one place to grep). All public methods open a fresh
Actual context manager so the budget is always synced on entry/exit.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from actual import Actual
from actual.queries import (
    create_splits,
    create_transaction,
    get_account,
    get_accounts,
    get_categories,
    get_transactions,
)

from .config import Settings
from .parsers import LineItem, ParsedOrder

log = logging.getLogger(__name__)


@dataclass
class PostResult:
    transaction_id: str
    n_subs: int
    n_categorized: int


class ActualClient:
    def __init__(self, settings: Settings):
        self.s = settings

    def _open(self) -> Actual:
        return Actual(
            base_url=self.s.actual_url,
            password=self.s.actual_password,
            file=self.s.actual_budget_sync_id,
            encryption_password=self.s.actual_encryption_password,
        )

    def categories(self) -> dict[str, str]:
        """Return {category_name: category_id} from the budget."""
        with self._open() as a:
            cats = get_categories(a.session)
            return {c.name: c.id for c in cats}

    def list_accounts(self) -> list[dict]:
        """Return [{name, id, off_budget, closed}] for every account in the budget."""
        with self._open() as a:
            return [
                {
                    "name": ac.name,
                    "id": ac.id,
                    "off_budget": bool(getattr(ac, "offbudget", 0)),
                    "closed": bool(getattr(ac, "closed", 0)),
                }
                for ac in get_accounts(a.session)
            ]

    def list_categories(self) -> list[dict]:
        """Return [{name, id, group}] for every category in the budget."""
        with self._open() as a:
            out = []
            for c in get_categories(a.session):
                group = getattr(c, "group", None)
                out.append(
                    {
                        "name": c.name,
                        "id": c.id,
                        "group": (group.name if group is not None else None),
                    }
                )
            return out

    def recent_transactions(self, account_name: str, days: int):
        """Return recent (date, payee, notes, amount, category_id) tuples for matching."""
        out = []
        since = date.today() - timedelta(days=days)
        with self._open() as a:
            account = get_account(a.session, name=account_name)
            if account is None:
                return out
            for t in get_transactions(a.session, account=account, start_date=since):
                out.append(
                    {
                        "date": t.get_date(),
                        "payee": (t.payee.name if t.payee else None),
                        "notes": t.notes,
                        "amount": Decimal(t.get_amount()),
                        "category_id": (t.category_id),
                    }
                )
        return out

    def post_split(
        self,
        *,
        account_name: str,
        order: ParsedOrder,
        line_categories: list[str | None],
        order_date: date | None = None,
    ) -> PostResult:
        """Create one parent transaction and N subtransactions.

        `line_categories[i]` is the Actual category_id (or None) for
        `order.line_items[i]`. Amounts are stored as negative cents (debit).
        """
        assert len(line_categories) == len(order.line_items)
        when = order_date or order.order_date or date.today()
        payee = _payee_for(order.vendor)
        if order.summary:
            notes = f"{order.summary} — Order #{order.order_id}"
        else:
            notes = f"Order #{order.order_id} (auto-split)"

        with self._open() as a:
            account = get_account(a.session, name=account_name)
            if account is None:
                raise ValueError(f"Actual account not found: {account_name!r}")

            # actualpy's create_splits builds the parent from the sum of
            # its child transactions, so we create the children first
            # (each as a normal transaction) and hand the list to it.
            cat_by_id = {c.id: c for c in get_categories(a.session)}
            children = [
                create_transaction(
                    s=a.session,
                    date=when,
                    account=account,
                    payee=payee,
                    notes=li.description,
                    category=cat_by_id.get(cat_id) if cat_id else None,
                    amount=-li.amount,
                )
                for li, cat_id in zip(order.line_items, line_categories, strict=True)
            ]
            parent = create_splits(a.session, transactions=children, notes=notes)
            a.commit()

            n_cat = sum(1 for c in line_categories if c)
            return PostResult(transaction_id=parent.id, n_subs=len(children), n_categorized=n_cat)


def _payee_for(vendor: str) -> str:
    return {"amazon": "Amazon"}.get(vendor, vendor.title())
