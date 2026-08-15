from __future__ import annotations

import inspect
from typing import Any

import discord
from discord import app_commands

from strife.engine.metadata import ParamType, SlashMove
from strife.engine.registry import GameRegistry
from strife.matchmaking.registries import SessionRegistries

_KNOWN_ERRORS = {
    "cannot_act": "It is not your turn or you cannot act right now.",
    "invalid_action": "Invalid action for this turn.",
    "not_a_player": "You are not a player in this game.",
}


def _annotation_for(param) -> type:
    if param.type == ParamType.INT:
        return int
    return str


def create_slash_command(
    game_key: str,
    slash_move: SlashMove,
    sessions: SessionRegistries,
) -> app_commands.Command:
    async def callback(interaction: discord.Interaction, **kwargs: Any) -> None:
        channel = interaction.channel
        session = sessions.get_game(channel.id) if channel is not None else None
        if session is None:
            await interaction.response.send_message(
                "No active game in this channel.", ephemeral=True
            )
            return
        args = {param.name: kwargs.get(param.name) for param in slash_move.params}
        try:
            await interaction.response.defer(ephemeral=True)
            await session.handle_slash_command(interaction.user.id, slash_move.name, args)
            await interaction.followup.send("Move submitted!", ephemeral=True)
        except Exception as exc:
            msg = str(exc)
            friendly = _KNOWN_ERRORS.get(msg, "Could not submit that move. Try again.")
            if interaction.response.is_done():
                await interaction.followup.send(friendly, ephemeral=True)
            else:
                await interaction.response.send_message(friendly, ephemeral=True)

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


def register_game_slash_commands(
    tree: app_commands.CommandTree,
    registry: GameRegistry,
    sessions: SessionRegistries,
) -> None:
    for meta in registry.all():
        if meta.slash_moves:
            group = app_commands.Group(name=meta.key, description=f"{meta.name} gameplay commands")
            for slash_move in meta.slash_moves:
                cmd = create_slash_command(meta.key, slash_move, sessions)
                group.add_command(cmd)
            tree.add_command(group)
