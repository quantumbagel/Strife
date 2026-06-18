from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from strife.config.emoji import EmojiConfig, load_emoji_config
from strife.config.games import GamesConfig, load_games_config
from strife.config.text import TextConfig, load_text_config


@dataclass(frozen=True)
class AppConfig:
    games: GamesConfig
    text: TextConfig
    emoji: EmojiConfig


def load_app_config(config_dir: Path) -> AppConfig:
    return AppConfig(
        games=load_games_config(config_dir / "games.yaml"),
        text=load_text_config(config_dir / "text.toml"),
        emoji=load_emoji_config(config_dir / "emoji.yaml"),
    )
