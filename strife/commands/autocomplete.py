"""Slash autocomplete helpers for live lobby/match state.

Discord shows nothing if a handler returns ``[]``. Dynamic dropdowns return one
explanatory choice instead, so the user can see why the list is empty.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence

from discord import app_commands

from strife.config.text import TextConfig
from strife.engine.metadata import GameMetadata, OptionType, int_setting_bounds
from strife.matchmaking.lobby import Lobby

CHOICE_NAME_MAX = 100


def notice_choices(message: str) -> list[app_commands.Choice[str]]:
    label = " ".join(message.split())[:CHOICE_NAME_MAX]
    if not label:
        return []
    return [app_commands.Choice(name=label, value=label)]


def parse_user_id(raw: str) -> int | None:
    text = raw.strip()
    if text.startswith("<@") and text.endswith(">"):
        text = text[2:-1]
        if text.startswith("!"):
            text = text[1:]
    if text.isdigit():
        return int(text)
    return None


def _filter(choices: list[app_commands.Choice[str]], current: str) -> list[app_commands.Choice[str]]:
    needle = current.lower()
    if not needle:
        return choices[:25]
    return [
        choice
        for choice in choices
        if needle in choice.name.lower() or needle in choice.value.lower()
    ][:25]


def catalog_game_choices(
    metas: Sequence[GameMetadata],
    current: str,
    text: TextConfig,
    *,
    empty_key: str,
) -> list[app_commands.Choice[str]]:
    if not metas:
        return notice_choices(text.get(empty_key))
    return _filter(
        [app_commands.Choice(name=meta.name[:CHOICE_NAME_MAX], value=meta.key) for meta in metas],
        current,
    )


def bot_add_difficulty_choices(
    lobby: Lobby | None,
    meta: GameMetadata | None,
    text: TextConfig,
    current: str,
) -> list[app_commands.Choice[str]]:
    if lobby is None or meta is None:
        return notice_choices(text.get("autocomplete.not_creator_add_bots"))
    if not meta.supports_bots:
        return notice_choices(text.get("autocomplete.game_has_no_bots"))
    if lobby.is_full(meta):
        return notice_choices(text.get("autocomplete.lobby_full"))
    return _filter(
        [
            app_commands.Choice(
                name=spec.display_label()[:CHOICE_NAME_MAX],
                value=spec.difficulty,
            )
            for spec in meta.bots
        ],
        current,
    )


def bot_remove_name_choices(
    lobby: Lobby | None,
    text: TextConfig,
    current: str,
) -> list[app_commands.Choice[str]]:
    if lobby is None:
        return notice_choices(text.get("autocomplete.not_creator_remove_bots"))
    if not lobby.bots:
        return notice_choices(text.get("autocomplete.no_bots_to_remove"))
    return _filter(
        [app_commands.Choice(name=bot.name[:CHOICE_NAME_MAX], value=bot.name) for bot in lobby.bots],
        current,
    )


def option_key_choices(
    lobby: Lobby | None,
    meta: GameMetadata | None,
    text: TextConfig,
    current: str,
) -> list[app_commands.Choice[str]]:
    if lobby is None or meta is None:
        return notice_choices(text.get("autocomplete.not_creator_change_options"))
    if not meta.settings:
        return notice_choices(text.get("autocomplete.game_has_no_options"))
    return _filter(
        [
            app_commands.Choice(name=option.title[:CHOICE_NAME_MAX], value=option.key)
            for option in meta.settings
        ],
        current,
    )


def option_value_choices(
    lobby: Lobby | None,
    meta: GameMetadata | None,
    key: str | None,
    text: TextConfig,
    current: str,
) -> list[app_commands.Choice[str]]:
    if lobby is None or meta is None:
        return notice_choices(text.get("autocomplete.not_creator_change_options"))
    if not key:
        return notice_choices(text.get("autocomplete.choose_option_key_first"))
    option = next((item for item in meta.settings if item.key == key), None)
    if option is None:
        return notice_choices(text.get("autocomplete.unknown_option"))

    needle = current.lower()
    if option.type == OptionType.BOOL:
        values = [("On", "true"), ("Off", "false")]
        return [
            app_commands.Choice(name=label, value=value)
            for label, value in values
            if needle in label.lower() or needle in value
        ]
    if option.type == OptionType.CHOICE:
        return [
            app_commands.Choice(name=choice.capitalize()[:CHOICE_NAME_MAX], value=choice)
            for choice in option.choices or ()
            if needle in choice.lower()
        ][:25]
    if option.type == OptionType.INT:
        minimum, maximum = int_setting_bounds(option)
        current_value = lobby.settings.get(option.key, option.default)
        suggestions = {
            str(current_value),
            str(option.default),
            str(minimum),
            str(maximum),
        }
        return [
            app_commands.Choice(name=value, value=value)
            for value in sorted(suggestions, key=lambda item: (len(item), item))
            if needle in value
        ][:25]
    return notice_choices(text.get("autocomplete.unknown_option"))


def named_id_choices(
    pairs: Sequence[tuple[int, str]],
    current: str,
    text: TextConfig,
    *,
    lobby: Lobby | None,
    empty_key: str,
    not_creator_key: str,
) -> list[app_commands.Choice[str]]:
    if lobby is None:
        return notice_choices(text.get(not_creator_key))
    if not pairs:
        return notice_choices(text.get(empty_key))
    return _filter(
        [
            app_commands.Choice(name=name[:CHOICE_NAME_MAX], value=str(user_id))
            for user_id, name in pairs
        ],
        current,
    )


def open_lobby_creator_choices(
    lobbies: Iterable[Lobby],
    *,
    guild_id: int | None,
    game_name: Callable[[str], str],
    current: str,
    text: TextConfig,
) -> list[app_commands.Choice[str]]:
    choices: list[app_commands.Choice[str]] = []
    seen: set[int] = set()
    for lobby in lobbies:
        if guild_id is not None and lobby.guild_id != guild_id:
            continue
        if lobby.starting or lobby.launching:
            continue
        if lobby.creator_id in seen:
            continue
        seen.add(lobby.creator_id)
        creator_name = next(
            (member.display_name for member in lobby.members if member.user_id == lobby.creator_id),
            str(lobby.creator_id),
        )
        label = f"{creator_name} — {game_name(lobby.game_key)}"[:CHOICE_NAME_MAX]
        choices.append(app_commands.Choice(name=label, value=str(lobby.creator_id)))
    if not choices:
        return notice_choices(text.get("autocomplete.no_open_lobbies"))
    return _filter(choices, current)


def replay_match_choices_or_notice(
    choices: list[app_commands.Choice[str]],
    *,
    has_completed: bool,
    current: str,
    text: TextConfig,
) -> list[app_commands.Choice[str]]:
    if choices:
        return choices[:25]
    if has_completed and current.strip():
        return []
    return notice_choices(text.get("autocomplete.no_completed_matches"))
