from __future__ import annotations

import logging

import httpx

log = logging.getLogger(__name__)


def ntfy(url: str | None, title: str, body: str, *, priority: str = "default", tags: str = "") -> None:
    if not url:
        return
    try:
        httpx.post(
            url,
            content=body.encode("utf-8"),
            headers={
                "Title": title,
                "Priority": priority,
                **({"Tags": tags} if tags else {}),
            },
            timeout=5,
        )
    except Exception as e:  # ntfy is best-effort; never fail the request because of it.
        log.warning("ntfy post failed: %s", e)
