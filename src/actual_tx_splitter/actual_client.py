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
    create_transaction_from_ids,
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
    attached_to_existing: bool = False


@dataclass
class ExistingMatch:
    tx_id: str
    date: date
    amount: Decimal  # signed, in major units (debits negative)
    payee: str | None
    notes: str | None
    is_parent: bool
    is_child: bool


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

    def find_existing_match(
        self,
        *,
        account_name: str,
        order: ParsedOrder,
        window_days: int,
        tolerance_cents: int,
    ) -> ExistingMatch | None:
        """Look for a bank-imported transaction that already covers this order.

        Match rule: same account, date in [order_date, order_date + window_days],
        amount within `tolerance_cents` of -order.total (debit). If multiple
        candidates exist, prefer ones that aren't already a split parent (no
        sense breaking up someone else's split) and pick the closest in amount,
        then closest in date.
        """
        if order.order_date is None:
            return None
        start = order.order_date
        end = order.order_date + timedelta(days=window_days)
        target = -order.total
        tol = Decimal(tolerance_cents) / Decimal(100)

        candidates: list[ExistingMatch] = []
        with self._open() as a:
            account = get_account(a.session, name=account_name)
            if account is None:
                return None
            for t in get_transactions(a.session, account=account, start_date=start, end_date=end):
                if t.is_child or t.is_parent:
                    continue  # leave existing splits alone
                amt = Decimal(t.get_amount())
                if abs(amt - target) > tol:
                    continue
                candidates.append(
                    ExistingMatch(
                        tx_id=t.id,
                        date=t.get_date(),
                        amount=amt,
                        payee=(t.payee.name if t.payee else None),
                        notes=t.notes,
                        is_parent=bool(t.is_parent),
                        is_child=bool(t.is_child),
                    )
                )

        if not candidates:
            return None
        candidates.sort(
            key=lambda m: (
                abs(m.amount - target),
                abs((m.date - order.order_date).days),
            )
        )
        return candidates[0]

    def attach_split_to_existing(
        self,
        *,
        account_name: str,
        existing_tx_id: str,
        order: ParsedOrder,
        line_categories: list[str | None],
    ) -> PostResult:
        """Convert an existing transaction into a split parent and add children.

        The existing tx keeps its id, date, amount, and cleared/imported state;
        we only flip is_parent and rewrite payee/notes, then create children
        pointing at it via parent_id.
        """
        assert len(line_categories) == len(order.line_items)
        payee = _payee_for(order.vendor)
        if order.summary:
            notes = f"{order.summary} — Order #{order.order_id}"
        else:
            notes = f"Order #{order.order_id} (auto-split)"

        with self._open() as a:
            account = get_account(a.session, name=account_name)
            if account is None:
                raise ValueError(f"Actual account not found: {account_name!r}")

            existing = next(
                (t for t in get_transactions(a.session, account=account) if t.id == existing_tx_id),
                None,
            )
            if existing is None:
                raise ValueError(f"existing tx not found: {existing_tx_id}")
            if existing.is_child:
                raise ValueError(f"existing tx {existing_tx_id} is already a split child")

            cat_by_id = {c.id: c for c in get_categories(a.session)}
            existing.is_parent = 1
            existing.is_child = 0
            existing.notes = notes

            # Single-item orders don't need children — the parent alone covers it.
            # But we already promised a split; create one child so the structure
            # matches the order. Caller can decide.
            for li, cat_id in zip(order.line_items, line_categories, strict=True):
                child = create_transaction_from_ids(
                    a.session,
                    existing.get_date(),
                    existing.acct,
                    None,
                    li.description,
                    cat_id if cat_id else None,
                    -li.amount,
                )
                child.is_parent = 0
                child.is_child = 1
                child.parent_id = existing.id
                # Children inherit the parent's payee for display
                if existing.payee_id:
                    child.payee_id = existing.payee_id
            a.commit()

            n_cat = sum(1 for c in line_categories if c)
            return PostResult(
                transaction_id=existing_tx_id,
                n_subs=len(order.line_items),
                n_categorized=n_cat,
                attached_to_existing=True,
            )

    def delete_transaction(self, tx_id: str) -> bool:
        """Delete a transaction (and its children, if it's a split parent)."""
        with self._open() as a:
            tx = next(
                (t for t in get_transactions(a.session, include_deleted=False) if t.id == tx_id),
                None,
            )
            if tx is None:
                return False
            if tx.is_parent:
                for t in get_transactions(a.session, include_deleted=False):
                    if t.parent_id == tx_id:
                        t.delete()
            tx.delete()
            a.commit()
            return True

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

            # Single-item orders don't need a split — a one-child "split" is
            # just visual noise in Actual versus a flat transaction.
            if len(order.line_items) == 1:
                li = order.line_items[0]
                cat_id = line_categories[0]
                tx = create_transaction(
                    s=a.session,
                    date=when,
                    account=account,
                    payee=payee,
                    notes=notes,
                    category=cat_by_id.get(cat_id) if cat_id else None,
                    amount=-li.amount,
                )
                a.commit()
                return PostResult(
                    transaction_id=tx.id,
                    n_subs=1,
                    n_categorized=1 if cat_id else 0,
                )

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
