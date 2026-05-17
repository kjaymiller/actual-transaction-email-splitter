from __future__ import annotations

import logging

from prometheus_client import CollectorRegistry, Counter, push_to_gateway

log = logging.getLogger(__name__)

# Local registry — exposed via /metrics AND pushed to pushgateway. Keeping
# them in one registry means the two sources never diverge.
REGISTRY = CollectorRegistry()

emails_received = Counter(
    "splitter_emails_received_total",
    "Webhook payloads accepted (after HMAC + allowlist + dedup)",
    registry=REGISTRY,
)
splits_posted = Counter(
    "splitter_splits_posted_total",
    "Transactions successfully posted to Actual",
    ["vendor"],
    registry=REGISTRY,
)
parse_failed = Counter(
    "splitter_parse_failed_total",
    "Parser raised or produced an unusable ParsedOrder",
    ["vendor", "reason"],
    registry=REGISTRY,
)
unknown_vendor = Counter(
    "splitter_unknown_vendor_total",
    "No parser matched the incoming email",
    registry=REGISTRY,
)
dedup_hits = Counter(
    "splitter_dedup_hits_total",
    "Duplicate Message-ID rejected before processing",
    registry=REGISTRY,
)
auth_rejects = Counter(
    "splitter_auth_rejects_total",
    "Webhook payload rejected (HMAC mismatch or sender not allowlisted)",
    ["reason"],
    registry=REGISTRY,
)


def push(url: str | None, job: str = "actual_tx_splitter") -> None:
    if not url:
        return
    try:
        push_to_gateway(url, job=job, registry=REGISTRY)
    except Exception as e:
        log.warning("pushgateway push failed: %s", e)
