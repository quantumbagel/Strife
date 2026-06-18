from __future__ import annotations

import logging
import sys
from typing import Any


class _ContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "match_id"):
            record.match_id = "-"
        if not hasattr(record, "guild_id"):
            record.guild_id = "-"
        return True


def configure_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(level.upper())

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)s [%(name)s] match=%(match_id)s guild=%(guild_id)s %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    handler.addFilter(_ContextFilter())
    root.addHandler(handler)

    discord_logger = logging.getLogger("discord")
    discord_logger.handlers.clear()
    discord_logger.propagate = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"strife.{name}")


def log_extra(*, match_id: int | str | None = None, guild_id: int | str | None = None) -> dict[str, Any]:
    return {
        "match_id": match_id if match_id is not None else "-",
        "guild_id": guild_id if guild_id is not None else "-",
    }
