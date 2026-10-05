from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    # extra="ignore": .env also carries Compose-only keys (e.g. POSTGRES_HOST_PORT).
    model_config = SettingsConfigDict(
        env_file=".env", env_prefix="STRIFE_", extra="ignore"
    )

    discord_token: str
    database_url: str
    owner_ids: Annotated[tuple[int, ...], NoDecode] = ()
    log_level: str = "INFO"
    config_dir: Path = Path("config")
    migrations_dir: Path = Path("migrations")
    database_min_size: int = 2
    database_max_size: int = 20
    cpu_pool_size: int = 8
    sync_on_start: bool = False
    plugins_dir: Path = Path("plugins")
    changelog_dir: Path = Path("changelog")
    sync_plugin_deps: bool = True
    signing_key: str | None = None

    @field_validator("owner_ids", mode="before")
    @classmethod
    def _parse_owner_ids(cls, value: object) -> tuple[int, ...]:
        if value is None or value == "":
            return ()
        if isinstance(value, (list, tuple)):
            return tuple(int(v) for v in value)
        if isinstance(value, str):
            text = value.strip()
            if text.startswith("[") and text.endswith("]"):
                try:
                    parsed = json.loads(text)
                except json.JSONDecodeError:
                    parsed = None
                if isinstance(parsed, list):
                    return tuple(int(v) for v in parsed)
            parts = [p.strip() for p in text.split(",") if p.strip()]
            return tuple(int(p) for p in parts)
        return (int(value),)


@lru_cache
def get_settings() -> Settings:
    return Settings()
