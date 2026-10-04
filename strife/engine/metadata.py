from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from strife.engine.platform import PLATFORM_VERSION


class PlayerOrder(StrEnum):
    RANDOM = "random"
    JOINED = "joined"
    CREATOR_FIRST = "creator_first"
    REVERSED = "reversed"


class OptionType(StrEnum):
    BOOL = "bool"
    INT = "int"
    CHOICE = "choice"


class ParamType(StrEnum):
    STRING = "string"
    INT = "int"
    CHOICE = "choice"


@dataclass(frozen=True)
class PlayerCount:
    fixed: int | None = None
    allowed: tuple[int, ...] | None = None
    minimum: int | None = None
    maximum: int | None = None

    def is_valid(self, n: int) -> bool:
        if self.fixed is not None:
            return n == self.fixed
        if self.allowed is not None:
            return n in self.allowed
        if self.minimum is not None and n < self.minimum:
            return False
        if self.maximum is not None and n > self.maximum:
            return False
        return True

    def describe(self) -> str:
        if self.fixed is not None:
            return str(self.fixed)
        if self.allowed is not None:
            return "/".join(str(v) for v in self.allowed)
        if self.minimum is not None and self.maximum is not None:
            return f"{self.minimum}-{self.maximum}"
        if self.minimum is not None:
            return f"{self.minimum}+"
        return "?"

    @property
    def min_players(self) -> int:
        if self.fixed is not None:
            return self.fixed
        if self.minimum is not None:
            return self.minimum
        if self.allowed:
            return min(self.allowed)
        return 1

    @property
    def max_players(self) -> int | None:
        if self.fixed is not None:
            return self.fixed
        if self.maximum is not None:
            return self.maximum
        if self.allowed:
            return max(self.allowed)
        return None


@dataclass(frozen=True)
class SettingOption:
    key: str
    title: str
    description: str
    type: OptionType
    default: Any
    minimum: int | None = None
    maximum: int | None = None
    choices: tuple[str, ...] | None = None
    emoji: str | None = None
    choice_emojis: tuple[tuple[str, str], ...] | None = None


@dataclass(frozen=True)
class MoveParam:
    name: str
    type: ParamType
    description: str = ""
    required: bool = True
    choices: tuple[str, ...] | None = None
    autocomplete: Callable[[str], Awaitable[list[str]]] | None = None
    reload_state: bool = False


@dataclass(frozen=True)
class SlashMove:
    name: str
    description: str
    params: tuple[MoveParam, ...] = ()


@dataclass(frozen=True)
class RoleSpec:
    """Named identity for catalog copy and DMs. Not a host assignment deck."""

    key: str
    name: str
    instructions: str


@dataclass(frozen=True)
class BotSpec:
    difficulty: str
    description: str

    def display_label(self) -> str:
        return f"{self.difficulty.capitalize()} ({self.description})"


@dataclass(frozen=True)
class GameMetadata:
    """Plugin listing and capabilities.

    ``version`` and ``platform_version`` are stamped from ``plugin.toml`` at
    load. Authors set ``key`` (must match the manifest) and listing fields.
    """

    key: str
    name: str
    summary: str = ""
    description: str = ""
    tags: tuple[str, ...] = ()
    author: str = "Unknown"
    version: str = "0.1.0"
    platform_version: str = PLATFORM_VERSION
    author_link: str | None = None
    source_link: str | None = None
    time_estimate: str = "?"
    difficulty: int = 1
    player_count: PlayerCount = field(default_factory=lambda: PlayerCount(fixed=2))
    player_order: PlayerOrder = PlayerOrder.RANDOM
    bots: tuple[BotSpec, ...] = ()
    bot_takeover_difficulty: str = "hard"
    settings: tuple[SettingOption, ...] = ()
    slash_moves: tuple[SlashMove, ...] = ()
    roles: tuple[RoleSpec, ...] = ()
    supports_player_removal: bool | None = None
    how_to_play_link: str | None = None

    @property
    def supports_bots(self) -> bool:
        return bool(self.bots)


def game_metadata(meta: GameMetadata) -> Callable[[type], type]:
    """Attach ``GameMetadata`` to a ``Game`` subclass at definition time."""

    def decorator(cls: type) -> type:
        cls.metadata = meta  # type: ignore[attr-defined]
        return cls

    return decorator


def game_metadata_from(**kwargs: Any) -> Callable[[type], type]:
    """Build metadata from keyword arguments and attach it to a ``Game`` subclass."""

    meta = GameMetadata(**kwargs)
    return game_metadata(meta)
