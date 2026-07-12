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


INT_SETTING_SELECT_LIMIT = 25


def supports_role_selection(meta: GameMetadata) -> bool:
    return meta.role_flow.value in {"selectable", "selectable_random"} and bool(meta.roles)


def int_setting_bounds(option: SettingOption) -> tuple[int, int]:
    minimum = option.minimum if option.minimum is not None else int(option.default)
    maximum = option.maximum if option.maximum is not None else minimum + 5
    return minimum, maximum


def int_setting_fits_select(option: SettingOption) -> bool:
    minimum, maximum = int_setting_bounds(option)
    return maximum - minimum + 1 <= INT_SETTING_SELECT_LIMIT


def choice_emoji_for(option: SettingOption, value: str, *, default: str = "pointing") -> str:
    if option.choice_emojis:
        for choice_value, emoji_key in option.choice_emojis:
            if choice_value == value:
                return emoji_key
    if option.emoji:
        return option.emoji
    return default


def format_setting_display(
    option: SettingOption,
    value: Any,
    *,
    on_label: str = "On",
    off_label: str = "Off",
) -> str:
    if option.type == OptionType.BOOL:
        display_value = on_label if bool(value) else off_label
    elif option.type == OptionType.CHOICE:
        display_value = str(value).capitalize()
    else:
        display_value = str(value)
    return f"{option.title}: {display_value}"


def format_settings_rules(
    options: tuple[SettingOption, ...],
    settings: dict[str, Any],
    *,
    on_label: str,
    off_label: str,
) -> list[str]:
    return [
        format_setting_display(
            option,
            settings.get(option.key, option.default),
            on_label=on_label,
            off_label=off_label,
        )
        for option in options
    ]


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

    def display_label(self) -> str:
        return f"{self.difficulty.capitalize()} ({self.description})"


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
    difficulty: int
    player_count: PlayerCount
    player_order: PlayerOrder
    bots: tuple[BotSpec, ...] = ()
    settings: tuple[SettingOption, ...] = ()
    slash_moves: tuple[SlashMove, ...] = ()
    role_mode: RoleMode = RoleMode.NONE
    role_flow: RoleFlow = RoleFlow.NONE
    roles: tuple[RoleSpec, ...] = ()
    supports_player_removal: bool = False
    supports_replay: bool = True
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
