from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


class GameDefaults(BaseModel):
    turn_timeout_seconds: int = 90
    turn_warning_seconds: int = 30


class GameConfig(BaseModel):
    enabled: bool = True
    turn_timeout_seconds: int | None = None
    turn_warning_seconds: int | None = None
    settings_overrides: dict[str, Any] = Field(default_factory=dict)


class GamesConfig(BaseModel):
    defaults: GameDefaults
    games: dict[str, GameConfig]

    def for_game(self, key: str) -> GameConfig:
        game = self.games.get(key, GameConfig())
        return GameConfig(
            enabled=game.enabled,
            turn_timeout_seconds=(
                game.turn_timeout_seconds
                if game.turn_timeout_seconds is not None
                else self.defaults.turn_timeout_seconds
            ),
            turn_warning_seconds=(
                game.turn_warning_seconds
                if game.turn_warning_seconds is not None
                else self.defaults.turn_warning_seconds
            ),
            settings_overrides=dict(game.settings_overrides),
        )


def load_games_config(path: Path) -> GamesConfig:
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    return GamesConfig.model_validate(data)
