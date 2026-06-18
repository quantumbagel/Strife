from __future__ import annotations

import discord
from discord import app_commands

from strife.matchmaking.service import LobbyService


def register_play(tree: app_commands.CommandTree, lobby: LobbyService, registry) -> None:
    @tree.command(name="play", description="Start a game lobby")
    @app_commands.describe(game_key="Which game to play", private="Create a private lobby")
    async def play(interaction: discord.Interaction, game_key: str, private: bool = False) -> None:
        await lobby.create_lobby(interaction, game_key, private)

    @play.autocomplete("game_key")
    async def game_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        choices = []
        for meta in registry.all():
            cfg = lobby.config.games.for_game(meta.key)
            if not cfg.enabled:
                continue
            if current.lower() in meta.key.lower() or current.lower() in meta.name.lower():
                choices.append(app_commands.Choice(name=meta.name, value=meta.key))
        return choices[:25]
