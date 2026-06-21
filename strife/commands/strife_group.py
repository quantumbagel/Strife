from __future__ import annotations

import discord
from discord import app_commands

from strife.commands.catalog import CatalogService
from strife.lifecycle.service import LifecycleService
from strife.matchmaking.service import LobbyService
from strife.persistence.repositories import GuildRepository
from strife.replay.profile import ProfileService
from strife.replay.service import ReplayService


def register_strife_group(
    tree: app_commands.CommandTree,
    *,
    lobby: LobbyService,
    lifecycle: LifecycleService,
    replay: ReplayService,
    profile: ProfileService,
    catalog: CatalogService,
    guilds: GuildRepository,
    registry,
) -> None:
    group = app_commands.Group(name="strife", description="Strife platform commands")

    @group.command(name="catalog", description="Browse available games")
    @app_commands.describe(page="Page number")
    async def catalog_cmd(interaction: discord.Interaction, page: int = 1) -> None:
        await catalog.show(interaction, max(0, page - 1))

    @group.command(name="profile", description="View player stats and recent matches")
    @app_commands.describe(user="Player to look up", game="Filter by game", page="Page number")
    async def profile_cmd(
        interaction: discord.Interaction,
        user: discord.User | None = None,
        game: str | None = None,
        page: int = 1,
    ) -> None:
        target = user or interaction.user
        await profile.show(interaction, target, game, max(0, page - 1))

    @group.command(name="settings", description="Open lobby settings")
    @app_commands.describe(private="Quick-toggle private lobby")
    async def settings_cmd(interaction: discord.Interaction, private: bool | None = None) -> None:
        await lobby.open_settings(interaction, private)

    @group.command(name="forfeit", description="Forfeit your current game")
    async def forfeit_cmd(interaction: discord.Interaction) -> None:
        loc = lobby.registries.location_of(interaction.user.id)
        if loc is None or loc.kind != "game":
            await interaction.response.send_message(lobby.text.get("errors.not_in_game"), ephemeral=True)
            return
        try:
            await lifecycle.forfeit(loc.thread_id, interaction.user.id)
            await interaction.response.send_message(lobby.text.get("match.forfeited"), ephemeral=True)
        except RuntimeError as e:
            if str(e) == "no_session":
                await interaction.response.send_message(lobby.text.get("errors.no_session"), ephemeral=True)
            else:
                await interaction.response.send_message(lobby.text.get("common.error"), ephemeral=True)
        except PermissionError:
            await interaction.response.send_message(lobby.text.get("errors.not_in_game"), ephemeral=True)

    @group.command(name="replay", description="Open a match replay")
    @app_commands.describe(match_ref="Match code or ID")
    async def replay_cmd(interaction: discord.Interaction, match_ref: str) -> None:
        await replay.open(interaction, match_ref)

    @group.command(name="about", description="Information about the Strife platform")
    async def about_cmd(interaction: discord.Interaction) -> None:
        from strife.presentation.about_view import build_about_view
        from strife.routing import prefixes as P

        view = build_about_view(lobby.emoji, lobby.text)
        compiled = lobby.compiler.compile(view, resource_id=interaction.user.id, prefix=P.ABOUT_NAV)
        await interaction.response.send_message(view=compiled, ephemeral=True)

    @group.command(name="set_channel", description="Set the default lobby channel")
    @app_commands.describe(channel="Default channel for new lobbies")
    @app_commands.default_permissions(administrator=True)
    async def set_channel_cmd(interaction: discord.Interaction, channel: discord.TextChannel) -> None:
        await guilds.upsert(interaction.guild_id)
        await guilds.set_default_channel(interaction.guild_id, channel.id)
        await interaction.response.send_message(lobby.text.get("guild.channel_set", mention=channel.mention), ephemeral=True)

    bot_group = app_commands.Group(name="bot", description="Manage lobby bots", parent=group)

    @bot_group.command(name="add", description="Add bots to your lobby")
    @app_commands.describe(difficulty="Bot difficulty", number="How many bots")
    async def bot_add(interaction: discord.Interaction, difficulty: str = "medium", number: int = 1) -> None:
        await lobby.add_bots(interaction, difficulty, max(1, min(number, 5)))

    @bot_group.command(name="remove", description="Remove a bot from your lobby")
    @app_commands.describe(name="Bot name")
    async def bot_remove(interaction: discord.Interaction, name: str) -> None:
        await lobby.remove_bot(interaction, name)

    tree.add_command(group)
