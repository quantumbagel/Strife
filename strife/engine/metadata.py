from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class RoleMode(StrEnum):
    NONE = "none"
    RANDOM = "random"
    CHOSEN = "chosen"
    SECRET = "secret"


class RoleFlow(StrEnum):
    NONE = "none"
    SELECTABLE = "selectable"
    RANDOM = "random"
    SELECTABLE_RANDOM = "selectable_random"


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


@dataclass(frozen=True)
class MoveParam:
    name: str
    type: ParamType
    required: bool = True
    choices: tuple[str, ...] | None = None
    autocomplete: Callable[..., Awaitable[list[str]]] | None = None
    reload_state: bool = False


@dataclass(frozen=True)
class SlashMove:
    name: str
    description: str
    params: tuple[MoveParam, ...] = ()


@dataclass(frozen=True)
class RoleSpec:
    key: str
    name: str
    instructions: str


@dataclass(frozen=True)
class BotSpec:
    difficulty: str
    description: str


@dataclass(frozen=True)
class GameMetadata:
    key: str
    name: str
    summary: str
    description: str
    tags: tuple[str, ...]
    author: str
    version: str
    author_link: str | None
    source_link: str | None
    time_estimate: str
    difficulty: str
    player_count: PlayerCount
    player_order: PlayerOrder
    bots: tuple[BotSpec, ...] = ()
    settings: tuple[SettingOption, ...] = ()
    slash_moves: tuple[SlashMove, ...] = ()
    role_mode: RoleMode = RoleMode.NONE
    role_flow: RoleFlow = RoleFlow.NONE
    roles: tuple[RoleSpec, ...] = ()
    supports_player_removal: bool = False
    accent_color: int | None = None

    @property
    def supports_bots(self) -> bool:
        return bool(self.bots)
