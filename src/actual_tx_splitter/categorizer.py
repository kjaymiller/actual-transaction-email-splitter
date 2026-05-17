"""Categorize line items by looking at past similar transactions.

For each line item, we score recent transactions (on the same account)
by description similarity. The category most frequently used by the
top-K matches wins, provided the best score clears a threshold. No match
→ None (leaves the subtransaction uncategorized in Actual).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from rapidfuzz import fuzz

from .parsers import ParsedOrder


@dataclass
class CategoryGuess:
    category_id: str | None
    score: int  # 0..100
    sample_size: int


def _haystack(tx: dict) -> str:
    parts = [tx.get("payee"), tx.get("notes")]
    return " ".join(p for p in parts if p) or ""


def guess_category(
    needle: str,
    transactions: list[dict],
    *,
    top_k: int = 5,
    min_score: int = 75,
) -> CategoryGuess:
    if not transactions or not needle:
        return CategoryGuess(None, 0, 0)
    scored = []
    for t in transactions:
        if not t.get("category_id"):
            continue
        s = fuzz.token_set_ratio(needle, _haystack(t))
        scored.append((s, t["category_id"]))
    scored.sort(reverse=True)
    top = scored[:top_k]
    if not top or top[0][0] < min_score:
        return CategoryGuess(None, top[0][0] if top else 0, len(top))
    counts = Counter(cid for _, cid in top)
    cid, _ = counts.most_common(1)[0]
    return CategoryGuess(cid, top[0][0], len(top))


def guess_all(
    order: ParsedOrder, transactions: list[dict], *, min_score: int = 75
) -> list[str | None]:
    return [
        guess_category(li.description, transactions, min_score=min_score).category_id
        for li in order.line_items
    ]
