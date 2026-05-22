"""Amazon order-confirmation parser.

Amazon templates change periodically. This parser leans on a few stable
landmarks (subject prefix, "Order #", "Items shipped:" / "Order Summary",
"ending in NNNN") and degrades gracefully — if line items can't be teased
out, we still post a single-line transaction for the total.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from email.utils import parsedate_to_datetime

from bs4 import BeautifulSoup

from .base import LineItem, ParsedOrder

ORDER_ID_RE = re.compile(r"Order\s*#\s*([0-9-]{10,})", re.I)
FWD_PREFIX_RE = re.compile(r"^(?:\s*(?:fwd|re|fw):\s*)+", re.I)
ORDERED_SUBJECT_RE = re.compile(r'^(?:Ordered|Shipped|Delivered):\s*[“"](.+?)[”"]\s*$', re.I)
DESC_TRAILING_ELLIPSIS_RE = re.compile(r"\s*\.{3,}\s*$")
# Bidi marks, zero-width spaces, BOM — Amazon order emails interleave these
# around digits, which breaks the [0-9-]+ Order # regex below.
INVISIBLE_RE = re.compile(r"[​-‏‪-‮⁦-⁩﻿]")
CARD_LAST4_RE = re.compile(r"ending\s+in\s+(\d{4})", re.I)
TOTAL_RE = re.compile(r"(?:Order\s+Total|Grand\s+Total|Total\s+for\s+This\s+Order)[^$]*\$\s*([0-9,]+\.\d{2})", re.I)
PRICE_RE = re.compile(r"\$\s*([0-9,]+\.\d{2})")

_MONTHS = {
    "january": 1, "jan": 1,
    "february": 2, "feb": 2,
    "march": 3, "mar": 3,
    "april": 4, "apr": 4,
    "may": 5,
    "june": 6, "jun": 6,
    "july": 7, "jul": 7,
    "august": 8, "aug": 8,
    "september": 9, "sept": 9, "sep": 9,
    "october": 10, "oct": 10,
    "november": 11, "nov": 11,
    "december": 12, "dec": 12,
}

# "Order Placed: October 15, 2025", "Ordered on October 15, 2025",
# "Order Date: Oct 15, 2025"
BODY_DATE_RE = re.compile(
    r"(?:Order\s+(?:Placed|Date)|Ordered\s+on)\s*:?\s*"
    r"([A-Za-z]+)\s+(\d{1,2}),?\s+(\d{4})",
    re.I,
)
# A "Date:" header line embedded in a forwarded body (Spark/Gmail keep the
# original header in the quoted block).
BODY_RFC_DATE_RE = re.compile(r"^\s*Date:\s*(.+)$", re.I | re.M)


def _money(s: str) -> Decimal:
    return Decimal(s.replace(",", ""))


def _text_from_email(email: dict) -> str:
    """CloudMailin payloads: plain text under 'plain', HTML under 'html'."""
    plain = email.get("plain") or ""
    html = email.get("html") or ""
    if html:
        soup = BeautifulSoup(html, "lxml")
        # Drop styles/scripts and keep readable text.
        for tag in soup(["style", "script"]):
            tag.decompose()
        text = soup.get_text("\n", strip=True)
    else:
        text = plain
    return INVISIBLE_RE.sub("", text)


class AmazonParser:
    name = "amazon"

    def matches(self, email: dict) -> bool:
        headers = email.get("headers") or {}
        from_addr = (headers.get("from") or headers.get("From") or "").lower()
        subject = (headers.get("subject") or headers.get("Subject") or "").lower()
        if "amazon.com" in from_addr or "amazon.co" in from_addr:
            return True
        # Strip "fwd:" / "re:" prefixes that pile up on forwards.
        subj = re.sub(r"^(?:\s*(?:fwd|re|fw):\s*)+", "", subject)
        if subj.startswith("ordered:"):
            return True
        if "your amazon" in subj and "order" in subj:
            return True
        # Forwarded emails: original Amazon sender is in the body.
        body = _text_from_email(email).lower()
        if "auto-confirm@amazon.com" in body or "shipment-tracking@amazon" in body:
            return True
        return False

    def parse(self, email: dict) -> ParsedOrder:
        text = _text_from_email(email)
        headers = email.get("headers") or {}
        subject = headers.get("subject") or headers.get("Subject") or ""
        order_id = self._extract(ORDER_ID_RE, text) or "unknown"
        card_last4 = self._extract(CARD_LAST4_RE, text)
        total = self._extract_total(text)
        line_items = self._extract_line_items(text, total)
        summary = self._summary_from_subject(subject) or self._summary_from_items(line_items)
        order_date = self._extract_order_date(text, headers)
        return ParsedOrder(
            vendor="amazon",
            order_id=order_id,
            total=total,
            card_last4=card_last4,
            line_items=line_items,
            summary=summary,
            order_date=order_date,
        )

    @staticmethod
    def _extract_order_date(text: str, headers: dict) -> date | None:
        m = BODY_DATE_RE.search(text)
        if m:
            mon = _MONTHS.get(m.group(1).lower())
            if mon:
                try:
                    return date(int(m.group(3)), mon, int(m.group(2)))
                except ValueError:
                    pass
        # Embedded "Date:" header in a forwarded body — prefer the *first* one,
        # which is the forward's wrapper around the original message.
        for raw in BODY_RFC_DATE_RE.findall(text):
            try:
                dt = parsedate_to_datetime(raw.strip())
            except (TypeError, ValueError):
                continue
            if dt is not None:
                return dt.date()
        # Fall back to the outer envelope Date: header.
        raw = headers.get("date") or headers.get("Date")
        if raw:
            try:
                dt = parsedate_to_datetime(raw)
                if dt is not None:
                    return dt.date()
            except (TypeError, ValueError):
                pass
        return None

    @staticmethod
    def _summary_from_subject(subject: str) -> str | None:
        s = FWD_PREFIX_RE.sub("", subject).strip()
        m = ORDERED_SUBJECT_RE.match(s)
        if not m:
            return None
        item = m.group(1).rstrip(". ").strip()
        return f"{item}…" if item else None

    @staticmethod
    def _summary_from_items(items: list[LineItem]) -> str | None:
        for li in items:
            d = li.description.strip()
            if d and d.lower() not in {"amazon order", "shipping & handling", "tax", "estimated tax"}:
                return d[:80]
        return None

    @staticmethod
    def _extract(pattern: re.Pattern[str], text: str) -> str | None:
        m = pattern.search(text)
        return m.group(1) if m else None

    @staticmethod
    def _extract_total(text: str) -> Decimal:
        m = TOTAL_RE.search(text)
        if m:
            return _money(m.group(1))
        # Last-resort: highest dollar amount in the body (Amazon prints the
        # total in the largest font, so it usually appears multiple times).
        prices = [_money(p) for p in PRICE_RE.findall(text)]
        return max(prices) if prices else Decimal("0.00")

    @staticmethod
    def _extract_line_items(text: str, total: Decimal) -> list[LineItem]:
        """Best-effort line-item extraction.

        Strategy: walk the text line by line, collect "description ... $price"
        pairs that look like product rows (description has letters, price is
        not the total). If we end up with items that sum within $0.05 of
        total, return them; otherwise return a single "Amazon order" line for
        the full total so the caller can still post a transaction.
        """
        items: list[LineItem] = []
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        # Two-line pattern: description on one line, price on the next.
        i = 0
        while i < len(lines) - 1:
            desc = lines[i]
            nxt = lines[i + 1]
            m = re.fullmatch(r"\$?\s*([0-9,]+\.\d{2})", nxt)
            if m and re.search(r"[A-Za-z]", desc) and len(desc) > 3:
                amt = _money(m.group(1))
                if amt != total and amt > Decimal("0"):
                    cleaned = DESC_TRAILING_ELLIPSIS_RE.sub("", desc).strip()
                    items.append(LineItem(description=cleaned[:200], amount=amt))
                i += 2
                continue
            i += 1

        # Include shipping/tax/etc. as their own lines if visible.
        for label in ("Shipping & Handling", "Estimated tax", "Tax", "Gift Card Amount", "Promotion Applied"):
            m = re.search(rf"{re.escape(label)}[^$]*\$\s*(-?[0-9,]+\.\d{{2}})", text, re.I)
            if m:
                amt = _money(m.group(1))
                if amt != Decimal("0"):
                    items.append(LineItem(description=label, amount=amt))

        if not items:
            return [LineItem(description="Amazon order", amount=total)]
        # If reconciliation is way off, fall back to a single line.
        delta = abs(sum((li.amount for li in items), Decimal(0)) - total)
        if delta > Decimal("0.50"):
            return [LineItem(description="Amazon order", amount=total)]
        return items
