"""Settings loaded from .env. No URLs or keys are hardcoded here."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    gamma_base_url: str
    clob_base_url: str

    gamma_preflight_path: str = "/markets?limit=1"
    clob_preflight_path: str = "/ok"

    http_timeout_s: float = 10.0
    data_dir: Path = Path("data")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # values come from .env
