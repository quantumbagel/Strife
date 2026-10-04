from __future__ import annotations

import inspect
from typing import Any

import discord
from discord import app_commands

from strife.commands.autocomplete import notice_choices
from strife.config.games import GamesConfig
from strife.session.errors import SessionError
from strife.engine.metadata import MoveParam, ParamType, SlashMove
from strife.engine.registry import GameRegistry
from strife.logging import get_logger
from strife.matchmaking.registries import SessionRegistries
from strife.presentation.user_error import UserErrorPresenter
from strife.presentation.user_success import UserSuccessPresenter

log = get_logger("commands.game")

# Discord allows at most 25 fixed choices per option.
_MAX_CHOICES = 25

_SLASH_ERRORS = {
    "cannot_act": "common.cannot_act",
    "invalid_action": "errors.invalid_action",
    "not_a_player": "errors.not_a_player",
    "match_resuming": "errors.match_resuming",
}


def _annotation_for(param) -> type:
    if param.type == ParamType.INT:
        return int
    return str


def _choice_value(param: MoveParam, raw: str) -> int | str:
    return int(raw) if param.type == ParamType.INT else str(raw)[:100]


def _fixed_choices(game_key: str, param: MoveParam) -> list[app_commands.Choice] | None:
    """Discord choices for ``param.choices``, or None to leave the option free text."""
    if not param.choices or param.autocomplete:
        return None
    if len(param.choices) > _MAX_CHOICES:
        log.warning(
            "%s /%s option %r has %d choices; Discord allows %d, leaving it free text",
            game_key,
            param.name,
            param.name,
            len(param.choices),
            _MAX_CHOICES,
        )
        return None
    try:
        return [
            app_commands.Choice(name=str(choice)[:100], value=_choice_value(param, choice))
            for choice in param.choices
        ]
    except ValueError:
        log.warning("%s option %r has non-integer choices for an int param", game_key, param.name)
        return None


def _autocomplete_for(param: MoveParam, user_errors: UserErrorPresenter):
    """Build a two-argument autocomplete callback, as discord.py requires."""

    async def complete(
        interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice]:
        try:
            raw = await param.autocomplete(current)
            choices = [
                app_commands.Choice(name=str(c)[:100], value=_choice_value(param, c))
                for c in raw
            ][:_MAX_CHOICES]
            if choices:
                return choices
        except Exception:
            log.exception("Slash autocomplete failed for option %r", param.name)
        if param.type == ParamType.INT:
            # Discord rejects a string notice on an integer option.
            return []
        return notice_choices(user_errors.text.get("autocomplete.no_matching_options"))

    return complete


def create_slash_command(
    game_key: str,
    slash_move: SlashMove,
    sessions: SessionRegistries,
    user_errors: UserErrorPresenter,
    user_success: UserSuccessPresenter,
) -> app_commands.Command:
    async def callback(interaction: discord.Interaction, **kwargs: Any) -> None:
        channel = interaction.channel
        session = sessions.get_game(channel.id) if channel is not None else None
        if session is None:
            await user_errors.send(interaction, "errors.no_game_in_channel")
            return
        if session.game_key != game_key:
            await user_errors.send(interaction, "errors.wrong_game_command")
            return
        args = {param.name: kwargs.get(param.name) for param in slash_move.params}
        try:
            if not interaction.response.is_done():
                await interaction.response.defer(ephemeral=True)
            await session.handle_slash_command(interaction.user.id, slash_move.name, args)
            await user_success.send(interaction, "game.move_submitted")
        except SessionError as exc:
            code = _SLASH_ERRORS.get(exc.code, "common.error")
            await user_errors.send(interaction, code)
        except Exception:
            log.exception("Slash move %s /%s failed", game_key, slash_move.name)
            await user_errors.send(interaction, "common.error")

    parameters = [
        inspect.Parameter(
            "interaction",
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            annotation=discord.Interaction,
        )
    ]
    annotations: dict[str, Any] = {"interaction": discord.Interaction, "return": None}
    for param in slash_move.params:
        annotation = _annotation_for(param)
        default = inspect.Parameter.empty if param.required else None
        parameters.append(
            inspect.Parameter(
                param.name,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                annotation=annotation,
                default=default,
            )
        )
        annotations[param.name] = annotation

    callback.__signature__ = inspect.Signature(parameters)
    callback.__annotations__ = annotations
    callback.__name__ = f"slash_{game_key}_{slash_move.name}"
    callback.__qualname__ = callback.__name__

    # Option metadata goes on the callback before Command() reads it.
    descriptions = {
        param.name: param.description for param in slash_move.params if param.description
    }
    if descriptions:
        app_commands.describe(**descriptions)(callback)
    choices = {
        param.name: fixed
        for param in slash_move.params
        if (fixed := _fixed_choices(game_key, param)) is not None
    }
    if choices:
        app_commands.choices(**choices)(callback)
    completers = {
        param.name: _autocomplete_for(param, user_errors)
        for param in slash_move.params
        if param.autocomplete
    }
    if completers:
        app_commands.autocomplete(**completers)(callback)

    return app_commands.Command(
        name=slash_move.name,
        description=slash_move.description,
        callback=callback,
    )


def slash_group_for_game(
    meta,
    sessions: SessionRegistries,
    user_errors: UserErrorPresenter,
    user_success: UserSuccessPresenter,
) -> app_commands.Group | None:
    if not meta.slash_moves:
        return None
    group = app_commands.Group(
        name=meta.key,
        description=f"{meta.name} commands",
        guild_only=True,
        allowed_contexts=app_commands.AppCommandContext(guild=True),
    )
    for slash_move in meta.slash_moves:
        cmd = create_slash_command(
            meta.key, slash_move, sessions, user_errors, user_success
        )
        group.add_command(cmd)
    return group


def register_slash_group_for_game(
    tree: app_commands.CommandTree,
    meta,
    sessions: SessionRegistries,
    user_errors: UserErrorPresenter,
    user_success: UserSuccessPresenter,
) -> bool:
    group = slash_group_for_game(meta, sessions, user_errors, user_success)
    if group is None:
        return False
    if tree.get_command(meta.key) is not None:
        tree.remove_command(meta.key)
    tree.add_command(group)
    return True


def remove_slash_group_for_game(tree: app_commands.CommandTree, key: str) -> None:
    if tree.get_command(key) is not None:
        tree.remove_command(key)


def register_game_slash_commands(
    tree: app_commands.CommandTree,
    registry: GameRegistry,
    sessions: SessionRegistries,
    user_errors: UserErrorPresenter,
    user_success: UserSuccessPresenter,
    games_config: GamesConfig | None = None,
) -> None:
    for meta in registry.all():
        if games_config is not None and not games_config.for_game(meta.key).enabled:
            continue
        register_slash_group_for_game(tree, meta, sessions, user_errors, user_success)
