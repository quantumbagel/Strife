from __future__ import annotations

import inspect
from typing import Any

import discord
from discord import app_commands

from strife.config.games import GamesConfig
from strife.engine.errors import SessionError
from strife.engine.metadata import ParamType, SlashMove
from strife.engine.registry import GameRegistry
from strife.matchmaking.registries import SessionRegistries
from strife.presentation.user_error import UserErrorPresenter
from strife.presentation.user_success import UserSuccessPresenter

_SLASH_ERRORS = {
    "cannot_act": "common.cannot_act",
    "invalid_action": "errors.invalid_action",
    "not_a_player": "errors.not_a_player",
}


def _annotation_for(param) -> type:
    if param.type == ParamType.INT:
        return int
    return str


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
        args = {param.name: kwargs.get(param.name) for param in slash_move.params}
        try:
            await session.handle_slash_command(interaction.user.id, slash_move.name, args)
            await user_success.send(interaction, "game.move_submitted")
        except SessionError as exc:
            code = _SLASH_ERRORS.get(exc.code, "common.error")
            await user_errors.send(interaction, code)
        except Exception:
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

    cmd = app_commands.Command(
        name=slash_move.name,
        description=slash_move.description,
        callback=callback,
    )

    descriptions = {
        param.name: param.description for param in slash_move.params if param.description
    }
    if descriptions:
        cmd._params_description = descriptions

    for param in slash_move.params:
        if param.autocomplete:
            async def autocomplete_wrapper(
                interaction: discord.Interaction, current: str, p=param
            ):
                try:
                    choices = await p.autocomplete(interaction, current)
                    return [discord.app_commands.Choice(name=c, value=c) for c in choices][:25]
                except Exception:
                    return []

            cmd.autocomplete(param.name)(autocomplete_wrapper)

    return cmd


def slash_group_for_game(
    meta,
    sessions: SessionRegistries,
    user_errors: UserErrorPresenter,
    user_success: UserSuccessPresenter,
) -> app_commands.Group | None:
    if not meta.slash_moves:
        return None
    group = app_commands.Group(name=meta.key, description=f"{meta.name} commands")
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
