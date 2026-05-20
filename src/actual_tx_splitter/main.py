from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.responses import PlainTextResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from .actual_client import ActualClient
from .config import get_settings
from .metrics import REGISTRY
from .webhook import router as webhook_router

app = FastAPI(title="actual-transaction-email-splitter", version="2026.5.3")
app.include_router(webhook_router)


@app.get("/actual/accounts")
def actual_accounts():
    """List all accounts in the budget. Tailnet-only — public route at
    splitter-public.kjaymiller.dev does not match this path."""
    return ActualClient(get_settings()).list_accounts()


@app.get("/actual/categories")
def actual_categories():
    """List all categories in the budget. Tailnet-only."""
    return ActualClient(get_settings()).list_categories()


@app.on_event("startup")
def _setup_logging():
    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/metrics")
def metrics_endpoint():
    return PlainTextResponse(generate_latest(REGISTRY).decode("utf-8"), media_type=CONTENT_TYPE_LATEST)
