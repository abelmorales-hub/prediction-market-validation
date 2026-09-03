"""Settings loaded from .env. No URLs or keys are hardcoded here."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Kalshi serves market data and order books from one host, unlike Polymarket's
    # gamma/clob split. MarketDataSource (M1) is what keeps the rest venue-agnostic.
    kalshi_base_url: str

    # Cheap public GETs used only by the pre-flight check.
    preflight_paths: list[str] = ["/exchange/status", "/markets?limit=1"]

    http_timeout_s: float = 10.0
    data_dir: Path = Path("data")

    # GEFS ensemble, served as JSON. Unset means the weather feature job is skipped
    # rather than silently guessing a host. See src/ingest/gefs.py for why the run's
    # init time is not recoverable from the payload and fetched_at is the guarantee.
    gefs_base_url: str | None = None
    gefs_model: str = "gfs025"
    gefs_forecast_days: int = 4

    # Postgres for the small relational state only. Book snapshots go to Parquet.
    supabase_db_url: str | None = None

    # The series catalogue is one ~21 MB response covering all ~13.7k series, and it is
    # the only place category lives. Fetched once a day rather than joined per market.
    metadata_cache_ttl_s: float = 86_400.0

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # values come from .env
