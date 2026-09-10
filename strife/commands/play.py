from __future__ import annotations

import discord
from discord import app_commands

from strife.commands.autocomplete import catalog_game_choices
from strife.matchmaking.service import LobbyService


def register_play(tree: app_commands.CommandTree, lobby: LobbyService, registry) -> None:
    @tree.command(name="play", description="Start a game lobby")
    @app_commands.describe(game="Which game to play", private="Create a private lobby")
    async def play(interaction: discord.Interaction, game: str, private: bool = False) -> None:
        await lobby.create_lobby(interaction, game, private)

    @play.autocomplete("game")
    async def game_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        enabled = [
            meta
            for meta in registry.all()
            if lobby.config.games.for_game(meta.key).enabled
        ]
        return catalog_game_choices(
            enabled, current, lobby.text, empty_key="autocomplete.no_enabled_games"
        )
