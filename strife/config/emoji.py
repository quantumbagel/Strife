from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class EmojiEntry:
    fallback: str
    id: int | None = None


@dataclass
class EmojiConfig:
    general: dict[str, EmojiEntry] = field(default_factory=dict)
    button: dict[str, EmojiEntry] = field(default_factory=dict)
    game: dict[str, EmojiEntry] = field(default_factory=dict)
    path: Path | None = None

    def all_entries(self) -> dict[str, EmojiEntry]:
        out: dict[str, EmojiEntry] = {}
        for bucket in (self.general, self.button, self.game):
            out.update(bucket)
        return out


def _parse_bucket(raw: dict[str, Any] | None) -> dict[str, EmojiEntry]:
    if not raw:
        return {}
    return {
        name: EmojiEntry(
            fallback=str(entry.get("fallback", "❓")),
            id=int(entry["id"]) if entry.get("id") is not None else None,
        )
        for name, entry in raw.items()
    }


def load_emoji_config(path: Path) -> EmojiConfig:
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    return EmojiConfig(
        general=_parse_bucket(data.get("general")),
        button=_parse_bucket(data.get("button")),
        game=_parse_bucket(data.get("game")),
        path=path,
    )


def save_emoji_config(config: EmojiConfig) -> None:
    if config.path is None:
        raise ValueError("EmojiConfig has no path")

    def dump_bucket(bucket: dict[str, EmojiEntry]) -> dict[str, dict[str, Any]]:
        return {
            name: {"fallback": entry.fallback, "id": entry.id}
            for name, entry in bucket.items()
        }

    data = {
        "general": dump_bucket(config.general),
        "button": dump_bucket(config.button),
        "game": dump_bucket(config.game),
    }
    with config.path.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(data, fh, sort_keys=False, allow_unicode=True)
