from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, PrivateAttr


class GameDefaults(BaseModel):
    turn_timeout_seconds: int = 90
    turn_warning_seconds: int = 30
    turn_timeout_max_strikes: int = 3
    turn_timeout_consequence: str = "abandon"


class GameConfig(BaseModel):
    enabled: bool = True
    turn_timeout_seconds: int | None = None
    turn_warning_seconds: int | None = None
    turn_timeout_max_strikes: int | None = None
    turn_timeout_consequence: str | None = None
    settings_overrides: dict[str, Any] = Field(default_factory=dict)


class GamesConfig(BaseModel):
    defaults: GameDefaults
    games: dict[str, GameConfig]
    _merged: dict[str, GameConfig] = PrivateAttr(default_factory=dict)

    def model_post_init(self, __context: object) -> None:
        self._merged = {key: self._merge_config(key, game) for key, game in self.games.items()}

    def _merge_config(self, key: str, game: GameConfig) -> GameConfig:
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
            turn_timeout_max_strikes=(
                game.turn_timeout_max_strikes
                if game.turn_timeout_max_strikes is not None
                else self.defaults.turn_timeout_max_strikes
            ),
            turn_timeout_consequence=(
                game.turn_timeout_consequence
                if game.turn_timeout_consequence is not None
                else self.defaults.turn_timeout_consequence
            ),
            settings_overrides=dict(game.settings_overrides),
        )

    def for_game(self, key: str) -> GameConfig:
        if key in self._merged:
            return self._merged[key]
        return self._merge_config(key, GameConfig())


def load_games_config(path: Path) -> GamesConfig:
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    return GamesConfig.model_validate(data)
