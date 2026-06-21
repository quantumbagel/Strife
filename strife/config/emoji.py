from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class EmojiEntry:
    fallback: str | None = None
    id: int | None = None
    animated: bool = False


@dataclass
class EmojiConfig:
    entries: dict[str, EmojiEntry] = field(default_factory=dict)
    path: Path | None = None

    def all_entries(self) -> dict[str, EmojiEntry]:
        return self.entries


def load_emoji_config(path: Path) -> EmojiConfig:
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    entries = {
        name: EmojiEntry(
            fallback=str(entry["fallback"]) if entry.get("fallback") is not None else None,
            id=int(entry["id"]) if entry.get("id") is not None else None,
            animated=bool(entry.get("animated", False)),
        )
        for name, entry in data.items()
    }
    return EmojiConfig(
        entries=entries,
        path=path,
    )


def save_emoji_config(config: EmojiConfig) -> None:
    if config.path is None:
        raise ValueError("EmojiConfig has no path")

    data = {
        name: {"fallback": entry.fallback, "id": entry.id, "animated": entry.animated}
        for name, entry in config.entries.items()
    }
    with config.path.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(data, fh, sort_keys=False, allow_unicode=True)
