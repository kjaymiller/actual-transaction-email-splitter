from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import re
from datetime import datetime, timezone
from email.utils import parseaddr
from pathlib import Path

from fastapi import APIRouter, Header, HTTPException, Request

from . import metrics
from .actual_client import ActualClient
from .categorizer import guess_all
from .config import Allowlist, CardRouting, get_settings
from .db import Store
from .dispatch import UnknownVendor, parse_email
from .notify import ntfy

log = logging.getLogger(__name__)

router = APIRouter()

_ARCHIVE_NAME_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_name(message_id: str) -> str:
    return _ARCHIVE_NAME_SAFE.sub("_", message_id)[:120] or "no-id"


def _archive(raw: bytes, message_id: str, settings) -> Path:
    settings.archive_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = settings.archive_dir / f"{ts}_{_safe_name(message_id)}.json"
    path.write_bytes(raw)
    return path


def _verify_basic_auth(expected_user: str, expected_pass: str, header: str | None) -> bool:
    if not header or not header.lower().startswith("basic "):
        return False
    try:
        decoded = base64.b64decode(header.split(None, 1)[1].strip()).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return False
    user, sep, pw = decoded.partition(":")
    if not sep:
        return False
    return hmac.compare_digest(user, expected_user) and hmac.compare_digest(pw, expected_pass)


def _message_id(email: dict) -> str:
    h = (email.get("headers") or {})
    return (h.get("message_id") or h.get("Message-ID") or "").strip("<>") or ""


def _from_address(email: dict) -> str | None:
    h = (email.get("headers") or {})
    raw = h.get("from") or h.get("From") or ""
    _, addr = parseaddr(raw)
    return addr or None


_FWD_PREFIX_RE = re.compile(r"^(?:\s*(?:fwd|re|fw):\s*)+", re.I)
_WS_RE = re.compile(r"\s+")


def _content_hash(email: dict) -> str:
    """Stable hash over fields that survive re-forwarding.

    Spark and other mail clients regenerate Message-ID on each forward, so
    Message-ID dedup alone misses retries. Hash sender + normalized subject +
    whitespace-collapsed body instead. The body is the highest-entropy field;
    sender + subject just disambiguate identical-body emails.
    """
    h = email.get("headers") or {}
    sender = (_from_address(email) or "").lower()
    subject = (h.get("subject") or h.get("Subject") or "").strip()
    subject = _FWD_PREFIX_RE.sub("", subject).lower()
    body = (email.get("plain") or "") or (email.get("html") or "")
    body = _WS_RE.sub(" ", body).strip()
    payload = f"{sender}\n{subject}\n{body}".encode("utf-8", errors="replace")
    return hashlib.sha256(payload).hexdigest()


@router.post("/webhook/cloudmailin")
async def cloudmailin(
    request: Request,
    authorization: str | None = Header(default=None),
):
    raw = await request.body()
    settings = get_settings()

    if not _verify_basic_auth(
        settings.cloudmailin_basic_user, settings.cloudmailin_basic_pass, authorization
    ):
        metrics.auth_rejects.labels(reason="basic_auth").inc()
        metrics.push(settings.pushgateway_url)
        raise HTTPException(status_code=401, detail="invalid credentials")

    try:
        email = json.loads(raw)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="invalid json")

    sender = _from_address(email)
    allowlist = Allowlist(settings.senders_path)
    if not allowlist.permits(sender):
        metrics.auth_rejects.labels(reason="sender").inc()
        metrics.push(settings.pushgateway_url)
        log.warning("rejected sender: %r", sender)
        raise HTTPException(status_code=403, detail="sender not allowlisted")

    message_id = _message_id(email) or f"no-id-{datetime.now(timezone.utc).isoformat()}"
    content_hash = _content_hash(email)
    store = Store(settings.db_path)
    if store.seen(message_id):
        metrics.dedup_hits.inc()
        metrics.push(settings.pushgateway_url)
        return {"status": "duplicate", "message_id": message_id, "reason": "message_id"}
    prior = store.seen_by_hash(content_hash)
    if prior:
        metrics.dedup_hits.inc()
        metrics.push(settings.pushgateway_url)
        log.info("dedup by content_hash: this msg_id=%s matches prior msg_id=%s", message_id, prior)
        return {"status": "duplicate", "message_id": message_id, "matched_message_id": prior, "reason": "content_hash"}

    archive_path = _archive(raw, message_id, settings)
    metrics.emails_received.inc()

    try:
        parser, order = parse_email(email)
    except UnknownVendor:
        metrics.unknown_vendor.inc()
        metrics.push(settings.pushgateway_url)
        ntfy(
            settings.ntfy_url,
            title="splitter: unknown vendor",
            body=f"From: {sender}\nSubject: {(email.get('headers') or {}).get('subject')}\nArchived: {archive_path.name}",
            priority="default",
            tags="mailbox_with_mail",
        )
        # Record so we don't keep retrying the same email if it's resent.
        store.record(
            message_id=message_id,
            received_at=datetime.now(timezone.utc).isoformat(),
            vendor=None,
            order_id=None,
            total_cents=None,
            actual_tx_id=None,
            archive_path=str(archive_path),
            content_hash=content_hash,
        )
        raise HTTPException(status_code=422, detail="no parser matched")

    routing = CardRouting(settings.card_routing_path)
    account_name = routing.resolve(order.card_last4)
    if not account_name:
        metrics.parse_failed.labels(vendor=parser.name, reason="no_account").inc()
        metrics.push(settings.pushgateway_url)
        raise HTTPException(status_code=422, detail=f"no Actual account routed for last4={order.card_last4!r}")

    client = ActualClient(settings)
    recent = client.recent_transactions(account_name, days=settings.lookback_days)
    line_categories = guess_all(order, recent)

    existing = client.find_existing_match(
        account_name=account_name,
        order=order,
        window_days=settings.match_window_days,
        tolerance_cents=settings.match_tolerance_cents,
    )
    try:
        if existing is not None:
            log.info(
                "attaching order #%s to existing tx %s (date=%s amount=%s)",
                order.order_id, existing.tx_id, existing.date, existing.amount,
            )
            result = client.attach_split_to_existing(
                account_name=account_name,
                existing_tx_id=existing.tx_id,
                order=order,
                line_categories=line_categories,
            )
        else:
            result = client.post_split(
                account_name=account_name,
                order=order,
                line_categories=line_categories,
            )
    except Exception as e:
        log.exception("actual post failed")
        metrics.parse_failed.labels(vendor=parser.name, reason="actual_post").inc()
        metrics.push(settings.pushgateway_url)
        raise HTTPException(status_code=500, detail=f"actual post failed: {e}")

    store.record(
        message_id=message_id,
        received_at=datetime.now(timezone.utc).isoformat(),
        vendor=parser.name,
        order_id=order.order_id,
        total_cents=int(order.total * 100),
        actual_tx_id=result.transaction_id,
        archive_path=str(archive_path),
        content_hash=content_hash,
    )
    metrics.splits_posted.labels(vendor=parser.name).inc()
    metrics.push(settings.pushgateway_url)

    needs_review = result.n_subs - result.n_categorized
    ntfy(
        settings.ntfy_url,
        title=f"splitter: {parser.name} ${order.total}",
        body=(
            f"{result.n_subs} items posted to {account_name}\n"
            f"{result.n_categorized} auto-categorized, {needs_review} need review\n"
            f"Order #{order.order_id}"
        ),
        tags="moneybag",
    )

    return {
        "status": "ok",
        "vendor": parser.name,
        "order_id": order.order_id,
        "account": account_name,
        "subs": result.n_subs,
        "categorized": result.n_categorized,
        "attached_to_existing": result.attached_to_existing,
    }
