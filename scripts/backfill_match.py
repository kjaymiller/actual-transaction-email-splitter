"""Reconcile already-posted orders with their bank-imported transactions.

For every row in splitter.db with vendor + actual_tx_id set, this script:

1. Re-parses the archived email JSON to recover line items + order date.
2. Looks for an existing transaction in the target account that matches the
   order total (within tolerance) and falls in the window
   [order_date, order_date + match_window_days].
3. If a match is found AND it isn't our own `actual_tx_id`, we delete our
   created split and convert the bank-imported tx into a split with the
   order's line items. splitter.db is updated to point at the bank tx.

Use --dry-run to see what would happen without touching Actual or the DB.
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
from pathlib import Path

from actual_tx_splitter.actual_client import ActualClient
from actual_tx_splitter.categorizer import guess_all
from actual_tx_splitter.config import CardRouting, get_settings
from actual_tx_splitter.dispatch import UnknownVendor, parse_email

log = logging.getLogger("backfill_match")


def _iter_posted_orders(db_path: Path):
    with sqlite3.connect(db_path) as c:
        c.row_factory = sqlite3.Row
        yield from c.execute(
            """
            SELECT message_id, vendor, order_id, total_cents, actual_tx_id, archive_path
            FROM processed
            WHERE actual_tx_id IS NOT NULL
              AND vendor IS NOT NULL
              AND archive_path IS NOT NULL
            ORDER BY received_at
            """
        ).fetchall()


def _update_actual_tx_id(db_path: Path, message_id: str, new_tx_id: str) -> None:
    with sqlite3.connect(db_path) as c:
        c.execute(
            "UPDATE processed SET actual_tx_id = ? WHERE message_id = ?",
            (new_tx_id, message_id),
        )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dry-run", action="store_true", help="Report matches without modifying anything")
    p.add_argument("--vendor", default="amazon", help="Only process this vendor (default: amazon)")
    p.add_argument("--limit", type=int, default=None, help="Stop after N rows (debug)")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    settings = get_settings()
    routing = CardRouting(settings.card_routing_path)
    client = ActualClient(settings)

    n_total = n_no_archive = n_parse_fail = n_no_account = 0
    n_no_match = n_already_correct = n_reconciled = n_errors = 0

    for i, row in enumerate(_iter_posted_orders(settings.db_path)):
        if args.limit and i >= args.limit:
            break
        if args.vendor and row["vendor"] != args.vendor:
            continue
        n_total += 1

        archive_path = Path(row["archive_path"])
        if not archive_path.exists():
            log.warning("skip %s: archive missing %s", row["order_id"], archive_path)
            n_no_archive += 1
            continue

        try:
            email = json.loads(archive_path.read_bytes())
        except json.JSONDecodeError as e:
            log.warning("skip %s: bad archive json (%s)", row["order_id"], e)
            n_no_archive += 1
            continue

        try:
            _, order = parse_email(email)
        except UnknownVendor:
            log.warning("skip %s: parser no longer matches", row["order_id"])
            n_parse_fail += 1
            continue
        except Exception as e:
            log.warning("skip %s: parse error (%s)", row["order_id"], e)
            n_parse_fail += 1
            continue

        account_name = routing.resolve(order.card_last4)
        if not account_name:
            log.warning("skip %s: no account for last4=%r", row["order_id"], order.card_last4)
            n_no_account += 1
            continue

        match = client.find_existing_match(
            account_name=account_name,
            order=order,
            window_days=settings.match_window_days,
            tolerance_cents=settings.match_tolerance_cents,
        )
        if match is None:
            log.info(
                "no match: order=%s total=$%s date=%s account=%s",
                order.order_id, order.total, order.order_date, account_name,
            )
            n_no_match += 1
            continue

        our_tx_id = row["actual_tx_id"]
        if match.tx_id == our_tx_id:
            log.debug("already correct: order=%s tx=%s", order.order_id, our_tx_id)
            n_already_correct += 1
            continue

        log.info(
            "RECONCILE order=%s: delete our tx %s, attach to bank tx %s (date=%s amount=%s)",
            order.order_id, our_tx_id, match.tx_id, match.date, match.amount,
        )

        if args.dry_run:
            n_reconciled += 1
            continue

        try:
            recent = client.recent_transactions(account_name, days=settings.lookback_days)
            line_categories = guess_all(order, recent)
            client.delete_transaction(our_tx_id)
            client.attach_split_to_existing(
                account_name=account_name,
                existing_tx_id=match.tx_id,
                order=order,
                line_categories=line_categories,
            )
            _update_actual_tx_id(settings.db_path, row["message_id"], match.tx_id)
            n_reconciled += 1
        except Exception as e:
            log.exception("reconcile failed for order %s: %s", order.order_id, e)
            n_errors += 1

    print(
        f"\nSummary ({'dry-run' if args.dry_run else 'live'}):\n"
        f"  processed:        {n_total}\n"
        f"  reconciled:       {n_reconciled}\n"
        f"  already correct:  {n_already_correct}\n"
        f"  no match:         {n_no_match}\n"
        f"  no archive:       {n_no_archive}\n"
        f"  parse failed:     {n_parse_fail}\n"
        f"  no account route: {n_no_account}\n"
        f"  errors:           {n_errors}"
    )
    return 1 if n_errors else 0


if __name__ == "__main__":
    sys.exit(main())
