from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, PrivateAttr, field_validator

from strife.engine.requests import TimeoutConsequence
from strife.logging import get_logger

log = get_logger("config.games")


def _parse_timeout_consequence(
    value: str | TimeoutConsequence | None,
) -> TimeoutConsequence:
    if value is None:
        return TimeoutConsequence.ABANDON
    if isinstance(value, TimeoutConsequence):
        return value
    try:
        return TimeoutConsequence(value)
    except ValueError:
        log.error("Unknown turn_timeout_consequence '%s'; using abandon", value)
        return TimeoutConsequence.ABANDON


class GameDefaults(BaseModel):
    turn_timeout_seconds: int = 90
    turn_warning_seconds: int = 30
    turn_timeout_max_strikes: int = 3
    turn_timeout_consequence: TimeoutConsequence = TimeoutConsequence.ABANDON
    play_hang_seconds: int = 45

    @field_validator("turn_timeout_consequence", mode="before")
    @classmethod
    def _validate_turn_timeout_consequence(cls, value: object) -> TimeoutConsequence:
        if isinstance(value, TimeoutConsequence):
            return value
        if isinstance(value, str):
            return _parse_timeout_consequence(value)
        return TimeoutConsequence.ABANDON


class GameConfig(BaseModel):
    enabled: bool = True
    turn_timeout_seconds: int | None = None
    turn_warning_seconds: int | None = None
    turn_timeout_max_strikes: int | None = None
    turn_timeout_consequence: TimeoutConsequence | None = None
    play_hang_seconds: int | None = None
    settings_overrides: dict[str, Any] = Field(default_factory=dict)

    @field_validator("turn_timeout_consequence", mode="before")
    @classmethod
    def _validate_turn_timeout_consequence(
        cls, value: object
    ) -> TimeoutConsequence | None:
        if value is None:
            return None
        if isinstance(value, TimeoutConsequence):
            return value
        if isinstance(value, str):
            return _parse_timeout_consequence(value)
        return None


class MergedGameConfig(BaseModel):
    enabled: bool = True
    turn_timeout_seconds: int = 90
    turn_warning_seconds: int = 30
    turn_timeout_max_strikes: int = 3
    turn_timeout_consequence: TimeoutConsequence = TimeoutConsequence.ABANDON
    play_hang_seconds: int = 45
    settings_overrides: dict[str, Any] = Field(default_factory=dict)


class GamesConfig(BaseModel):
    defaults: GameDefaults
    games: dict[str, GameConfig]
    _merged: dict[str, MergedGameConfig] = PrivateAttr(default_factory=dict)
    _path: Path | None = PrivateAttr(default=None)

    def model_post_init(self, context: object, /) -> None:
        self._merged = {
            key: self._merge_config(key, game) for key, game in self.games.items()
        }

    def _merge_config(self, key: str, game: GameConfig) -> MergedGameConfig:
        return MergedGameConfig(
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
            play_hang_seconds=(
                game.play_hang_seconds
                if game.play_hang_seconds is not None
                else self.defaults.play_hang_seconds
            ),
            settings_overrides=dict(game.settings_overrides),
        )

    def for_game(self, key: str) -> MergedGameConfig:
        """Tuning for *key*. Games without a row are enabled with the defaults;
        only an explicit ``enabled: false`` row hides a game."""
        if key in self._merged:
            return self._merged[key]
        return self._merge_config(key, GameConfig())

    def note_game(self, key: str, *, enabled: bool = True) -> None:
        existing = self.games.get(key)
        if existing is not None:
            existing.enabled = enabled
            self._merged[key] = self._merge_config(key, existing)
            return
        game = GameConfig(enabled=enabled)
        self.games[key] = game
        self._merged[key] = self._merge_config(key, game)


def load_games_config(path: Path) -> GamesConfig:
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    cfg = GamesConfig.model_validate(data)
    cfg._path = path
    return cfg
