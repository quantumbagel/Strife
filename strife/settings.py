from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="STRIFE_")

    discord_token: str
    database_url: str
    owner_ids: tuple[int, ...] = ()
    log_level: str = "INFO"
    config_dir: Path = Path("config")
    migrations_dir: Path = Path("migrations")
    database_min_size: int = 2
    database_max_size: int = 20
    cpu_pool_size: int = 8
    sync_on_start: bool = False
    plugins_dir: Path = Path("plugins")
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
            parts = [p.strip() for p in value.split(",") if p.strip()]
            return tuple(int(p) for p in parts)
        return (int(value),)


@lru_cache
def get_settings() -> Settings:
    return Settings()
