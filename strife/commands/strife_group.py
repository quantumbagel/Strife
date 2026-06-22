from __future__ import annotations

import discord
from discord import app_commands

from strife.commands.catalog import CatalogService
from strife.commands.server_settings import ServerSettingsService
from strife.lifecycle.service import LifecycleService
from strife.matchmaking.service import LobbyService
from strife.presentation.message import send_ephemeral_error
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
    server_settings: ServerSettingsService,
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

    @group.command(name="server", description="Configure server-level Strife settings")
    @app_commands.default_permissions(administrator=True)
    async def server_cmd(interaction: discord.Interaction) -> None:
        await server_settings.open(interaction)

    @group.command(name="forfeit", description="Forfeit your current game")
    async def forfeit_cmd(interaction: discord.Interaction) -> None:
        loc = lobby.registries.location_of(interaction.user.id)
        if loc is None:
            await send_ephemeral_error(interaction, lobby.text.get("errors.not_in_game"))
            return
        if loc.kind == "lobby":
            try:
                await lobby.leave_lobby(loc.thread_id, interaction.user.id, interaction)
                await send_ephemeral_error(interaction, lobby.text.get("lobby.left"))
            except RuntimeError as e:
                if str(e) == "no_session":
                    await send_ephemeral_error(interaction, lobby.text.get("errors.no_session"))
                else:
                    await send_ephemeral_error(interaction, lobby.text.get("common.error"))
            except PermissionError:
                await send_ephemeral_error(interaction, lobby.text.get("errors.not_in_lobby"))
        elif loc.kind == "game":
            try:
                await lifecycle.forfeit(loc.thread_id, interaction.user.id)
                await send_ephemeral_error(interaction, lobby.text.get("match.forfeited"))
            except RuntimeError as e:
                if str(e) == "no_session":
                    await send_ephemeral_error(interaction, lobby.text.get("errors.no_session"))
                else:
                    await send_ephemeral_error(interaction, lobby.text.get("common.error"))
            except PermissionError:
                await send_ephemeral_error(interaction, lobby.text.get("errors.not_in_game"))

    @group.command(name="replay", description="Open a match replay")
    @app_commands.describe(match="Match code or ID")
    async def replay_cmd(interaction: discord.Interaction, match: str) -> None:
        await replay.open(interaction, match)

    @replay_cmd.autocomplete("match")
    async def replay_autocomplete(
        interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        matches = await replay.matches.list_for_user(interaction.user.id, None, limit=25)
        choices: list[app_commands.Choice[str]] = []
        for match in matches:
            if match.status != "completed":
                continue
            game_name = registry.metadata(match.game_key).name
            
            # Format players count
            players_str = f"{match.player_count} players" if match.player_count is not None else ""
            
            # Format exact start time (UTC)
            start_time = match.started_at or match.created_at
            started_str = "Started: " + start_time.strftime("%Y-%m-%d %H:%M:%S UTC")
            
            # Format length/duration
            duration_str = ""
            if match.started_at and match.ended_at:
                diff = match.ended_at - match.started_at
                seconds = int(diff.total_seconds())
                mins, secs = divmod(seconds, 60)
                duration_str = f"{mins}m {secs}s"
            elif match.total_turns:
                duration_str = f"{match.total_turns} turns"
                
            parts = [f"#{match.code}", game_name]
            if players_str:
                parts.append(players_str)
            parts.append(started_str)
            if duration_str:
                parts.append(duration_str)
            label = " - ".join(parts)
            
            if current.lower() not in label.lower() and current.lower() not in match.code.lower():
                continue
            choices.append(app_commands.Choice(name=label[:100], value=match.code))
        return choices[:25]

    @group.command(name="about", description="Information about the Strife platform")
    async def about_cmd(interaction: discord.Interaction) -> None:
        from strife.presentation.about_view import build_about_view
        from strife.routing import prefixes as P

        view = build_about_view(lobby.emoji, lobby.text)
        compiled = lobby.compiler.compile(view, resource_id=interaction.user.id, prefix=P.ABOUT_NAV)
        await interaction.response.send_message(view=compiled, ephemeral=True)

    bot_group = app_commands.Group(name="bot", description="Manage lobby bots", parent=group)

    @bot_group.command(name="add", description="Add bots to your lobby")
    @app_commands.describe(difficulty="Bot difficulty", number="How many bots")
    async def bot_add(interaction: discord.Interaction, difficulty: str = "medium", number: int = 1) -> None:
        await lobby.add_bots(interaction, difficulty, max(1, min(number, 5)))

    @bot_add.autocomplete("difficulty")
    async def bot_add_difficulty_autocomplete(
        interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        loc = lobby.registries.location_of(interaction.user.id)
        if loc is None or loc.kind != "lobby":
            return []
        lobby_obj = lobby.registries.get_lobby(loc.thread_id)
        if lobby_obj is None:
            return []
        meta = registry.metadata(lobby_obj.game_key)
        choices = []
        for spec in meta.bots or ():
            if current.lower() in spec.difficulty.lower():
                choices.append(
                    app_commands.Choice(name=spec.difficulty.capitalize(), value=spec.difficulty)
                )
        if not choices:
            for fallback in ("easy", "medium", "hard"):
                if current.lower() in fallback:
                    choices.append(app_commands.Choice(name=fallback.capitalize(), value=fallback))
        return choices[:25]

    @bot_group.command(name="remove", description="Remove a bot from your lobby")
    @app_commands.describe(name="Bot name")
    async def bot_remove(interaction: discord.Interaction, name: str) -> None:
        await lobby.remove_bot(interaction, name)

    @bot_remove.autocomplete("name")
    async def bot_remove_name_autocomplete(
        interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        loc = lobby.registries.location_of(interaction.user.id)
        if loc is None or loc.kind != "lobby":
            return []
        lobby_obj = lobby.registries.get_lobby(loc.thread_id)
        if lobby_obj is None:
            return []
        return [
            app_commands.Choice(name=bot.name, value=bot.name)
            for bot in lobby_obj.bots
            if current.lower() in bot.name.lower()
        ][:25]

    tree.add_command(group)
