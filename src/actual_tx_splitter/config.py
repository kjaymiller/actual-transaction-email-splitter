from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    cloudmailin_basic_user: str
    cloudmailin_basic_pass: str

    actual_url: str
    actual_password: str
    actual_budget_sync_id: str
    actual_encryption_password: str | None = None

    senders_path: Path = Path("/config/senders.yml")
    card_routing_path: Path = Path("/config/card-routing.yml")
    archive_dir: Path = Path("/data/archive")
    db_path: Path = Path("/data/splitter.db")

    pushgateway_url: str | None = None
    ntfy_url: str | None = None

    lookback_days: int = 180

    # Match an incoming order against an already-imported bank transaction
    # in the target account before creating a new one. The window starts at
    # the order date and runs forward by this many days; the amount must be
    # within `match_tolerance_cents` of the order total (covers tax/shipping
    # drift).
    match_window_days: int = 14
    match_tolerance_cents: int = 100

    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]


def load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


class Allowlist:
    def __init__(self, path: Path):
        data = load_yaml(path)
        self.allowed = {a.lower().strip() for a in data.get("allowed", [])}

    def permits(self, address: str | None) -> bool:
        if not address:
            return False
        return address.lower().strip() in self.allowed


class CardRouting:
    def __init__(self, path: Path):
        data = load_yaml(path)
        self.cards: dict[str, str] = {str(k): v for k, v in (data.get("cards") or {}).items()}
        self.default: str | None = data.get("default")

    def resolve(self, last4: str | None) -> str | None:
        if last4 and last4 in self.cards:
            return self.cards[last4]
        return self.default
