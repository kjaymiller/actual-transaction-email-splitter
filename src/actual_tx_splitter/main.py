from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.responses import PlainTextResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from .config import get_settings
from .metrics import REGISTRY
from .webhook import router as webhook_router

app = FastAPI(title="actual-transaction-email-splitter", version="0.1.0")
app.include_router(webhook_router)


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
