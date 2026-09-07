from __future__ import annotations

import discord
from discord import app_commands

from strife.commands.catalog import CatalogService
from strife.commands.server_settings import ServerSettingsService
from strife.engine.errors import SessionError
from strife.engine.metadata import OptionType, int_setting_bounds
from strife.lifecycle.service import LifecycleService
from strife.matchmaking.service import LobbyService
from strife.presentation.user_error import ErrorContext
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

    @profile_cmd.autocomplete("game")
    async def profile_game_autocomplete(
        interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        choices: list[app_commands.Choice[str]] = []
        needle = current.lower()
        for meta in registry.all():
            if needle and needle not in meta.key.lower() and needle not in meta.name.lower():
                continue
            choices.append(app_commands.Choice(name=meta.name, value=meta.key))
        return choices[:25]

    @group.command(name="settings", description="Open lobby settings")
    async def settings_cmd(interaction: discord.Interaction) -> None:
        await lobby.open_settings(interaction)

    @group.command(name="server", description="Configure server-level Strife settings")
    @app_commands.default_permissions(administrator=True)
    async def server_cmd(interaction: discord.Interaction) -> None:
        await server_settings.open(interaction)

    @group.command(name="forfeit", description="Forfeit your current game")
    async def forfeit_cmd(interaction: discord.Interaction) -> None:
        loc = lobby.registries.location_of(interaction.user.id)
        if loc is None:
            await lobby.user_errors.send(
                interaction,
                "errors.not_in_game",
                context=ErrorContext(interaction=interaction),
            )
            return
        if loc.kind == "lobby":
            await lobby.user_errors.send(
                interaction,
                "errors.not_in_game",
                context=ErrorContext(interaction=interaction, location=loc),
            )
            return
        if loc.kind == "game":
            try:
                await lifecycle.forfeit(loc.thread_id, interaction.user.id)
                await lobby.user_success.send(interaction, "match.forfeited")
            except SessionError as e:
                code = "errors.no_session" if e.code == "no_session" else "common.error"
                await lobby.user_errors.send(interaction, code)
            except PermissionError:
                await lobby.user_errors.send(
                    interaction,
                    "errors.not_in_game",
                    context=ErrorContext(interaction=interaction, location=loc),
                )

    @group.command(name="replay", description="Open a match replay")
    @app_commands.describe(match="Match code or ID")
    async def replay_cmd(interaction: discord.Interaction, match: str) -> None:
        await replay.open(interaction, match)

    @replay_cmd.autocomplete("match")
    async def replay_autocomplete(
        interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        matches = await replay.autocomplete_matches(interaction.user.id, limit=25)
        choices: list[app_commands.Choice[str]] = []
        for match in matches:
            if match.status != "completed":
                continue
            game_name = registry.metadata(match.game_key).name

            result = getattr(match, "result", None)
            result_str = result.capitalize() if result else ""

            duration_str = ""
            if match.started_at and match.ended_at:
                diff = match.ended_at - match.started_at
                seconds = int(diff.total_seconds())
                mins, secs = divmod(seconds, 60)
                duration_str = f"{mins}m {secs}s"
            elif match.total_turns:
                duration_str = f"{match.total_turns} actions"

            start_time = match.started_at or match.created_at

            parts = [f"#{match.code}", game_name]
            if result_str:
                parts.append(result_str)
            if match.player_count is not None:
                parts.append(f"{match.player_count} players")
            if duration_str:
                parts.append(duration_str)
            parts.append(start_time.strftime("%Y-%m-%d"))
            label = " · ".join(parts)

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
    async def bot_add(interaction: discord.Interaction, difficulty: str | None = None, number: int = 1) -> None:
        await lobby.add_bots(interaction, difficulty, max(1, min(number, 5)))

    @bot_add.autocomplete("difficulty")
    async def bot_add_difficulty_autocomplete(
        interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        lobby_obj = lobby.lobby_of_user(interaction.user.id)
        if lobby_obj is None:
            return []
        meta = registry.metadata(lobby_obj.game_key)
        choices = []
        for spec in meta.bots or ():
            haystack = f"{spec.difficulty} {spec.description}".lower()
            if current.lower() in haystack:
                choices.append(
                    app_commands.Choice(
                        name=spec.display_label(),
                        value=spec.difficulty,
                    )
                )
        return choices[:25]

    @bot_group.command(name="remove", description="Remove a bot from your lobby")
    @app_commands.describe(name="Bot name")
    async def bot_remove(interaction: discord.Interaction, name: str) -> None:
        await lobby.remove_bot(interaction, name)

    @bot_remove.autocomplete("name")
    async def bot_remove_name_autocomplete(
        interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        lobby_obj = lobby.lobby_of_user(interaction.user.id)
        if lobby_obj is None:
            return []
        return [
            app_commands.Choice(name=bot.name, value=bot.name)
            for bot in lobby_obj.bots
            if current.lower() in bot.name.lower()
        ][:25]

    lobby_group = app_commands.Group(name="lobby", description="Manage game lobbies", parent=group)

    def _option_key_choices(lobby_obj, current: str) -> list[app_commands.Choice[str]]:
        if lobby_obj is None:
            return []
        try:
            meta = registry.metadata(lobby_obj.game_key)
        except KeyError:
            return []
        needle = current.lower()
        return [
            app_commands.Choice(name=option.title[:100], value=option.key)
            for option in meta.settings
            if needle in option.title.lower() or needle in option.key.lower()
        ][:25]

    def _option_value_choices(
        lobby_obj, key: str | None, current: str
    ) -> list[app_commands.Choice[str]]:
        if lobby_obj is None or not key:
            return []
        try:
            meta = registry.metadata(lobby_obj.game_key)
        except KeyError:
            return []
        option = next((o for o in meta.settings if o.key == key), None)
        if option is None:
            return []

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
                app_commands.Choice(name=choice.capitalize()[:100], value=choice)
                for choice in option.choices or ()
                if needle in choice.lower()
            ][:25]
        if option.type == OptionType.INT:
            minimum, maximum = int_setting_bounds(option)
            current_value = lobby_obj.settings.get(option.key, option.default)
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
        return []

    @lobby_group.command(name="join", description="Join a lobby by its creator")
    @app_commands.describe(creator="Lobby creator to join")
    async def lobby_join(interaction: discord.Interaction, creator: discord.User) -> None:
        await lobby.join_by_creator(interaction, creator.id)

    @lobby_group.command(name="leave", description="Leave your current lobby")
    async def lobby_leave(interaction: discord.Interaction) -> None:
        await lobby.leave_current(interaction)

    @lobby_group.command(name="ready", description="Toggle ready in your lobby")
    async def lobby_ready(interaction: discord.Interaction) -> None:
        await lobby.toggle_ready(interaction)

    @lobby_group.command(name="kick", description="Kick a player from your lobby")
    @app_commands.describe(user="Player to kick")
    async def lobby_kick(interaction: discord.Interaction, user: discord.User) -> None:
        await lobby.kick_member(interaction, user.id)

    @lobby_group.command(name="end", description="End your lobby")
    async def lobby_end(interaction: discord.Interaction) -> None:
        await lobby.end_lobby(interaction)

    @lobby_group.command(name="clear-ready", description="Clear ready for everyone in your lobby")
    async def lobby_clear_ready(interaction: discord.Interaction) -> None:
        await lobby.clear_ready(interaction)

    @lobby_group.command(name="privacy", description="Set lobby privacy")
    @app_commands.describe(private="Whether the lobby requires join approval")
    async def lobby_privacy(interaction: discord.Interaction, private: bool) -> None:
        await lobby.set_privacy(interaction, private)

    @lobby_group.command(name="reset-privacy", description="Reset privacy and access lists")
    async def lobby_reset_privacy(interaction: discord.Interaction) -> None:
        await lobby.reset_privacy(interaction)

    @lobby_group.command(name="reset-rules", description="Reset game rules to defaults")
    async def lobby_reset_rules(interaction: discord.Interaction) -> None:
        await lobby.reset_rules(interaction)

    @lobby_group.command(name="option", description="Set a game rule option for your lobby")
    @app_commands.describe(key="Option to change", value="New value")
    async def lobby_option(interaction: discord.Interaction, key: str, value: str) -> None:
        await lobby.set_option(interaction, key, value)

    @lobby_option.autocomplete("key")
    async def lobby_option_key_autocomplete(
        interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        return _option_key_choices(lobby.lobby_of_user(interaction.user.id), current)

    @lobby_option.autocomplete("value")
    async def lobby_option_value_autocomplete(
        interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        key = None
        if interaction.namespace is not None:
            key = getattr(interaction.namespace, "key", None)
        return _option_value_choices(lobby.lobby_of_user(interaction.user.id), key, current)

    @lobby_group.command(name="approve", description="Approve a pending join request")
    @app_commands.describe(user="Player to approve")
    async def lobby_approve(interaction: discord.Interaction, user: discord.User) -> None:
        await lobby.approve_request(interaction, user.id)

    @lobby_group.command(name="deny", description="Deny a pending join request")
    @app_commands.describe(user="Player to deny")
    async def lobby_deny(interaction: discord.Interaction, user: discord.User) -> None:
        await lobby.deny_request(interaction, user.id)

    @lobby_group.command(name="preapprove", description="Pre-approve a player for a private lobby")
    @app_commands.describe(user="Player to pre-approve")
    async def lobby_preapprove(interaction: discord.Interaction, user: discord.User) -> None:
        await lobby.preapprove_user(interaction, user.id)

    @lobby_group.command(name="revoke-approval", description="Revoke a player's pre-approval")
    @app_commands.describe(user="Player to revoke")
    async def lobby_revoke_approval(interaction: discord.Interaction, user: discord.User) -> None:
        await lobby.revoke_approval(interaction, user.id)

    @lobby_group.command(name="blacklist-add", description="Blacklist a player from your lobby")
    @app_commands.describe(user="Player to blacklist")
    async def lobby_blacklist_add(interaction: discord.Interaction, user: discord.User) -> None:
        await lobby.blacklist_add(interaction, user.id)

    @lobby_group.command(name="blacklist-remove", description="Remove a player from the blacklist")
    @app_commands.describe(user="Player to unblacklist")
    async def lobby_blacklist_remove(interaction: discord.Interaction, user: discord.User) -> None:
        await lobby.blacklist_remove(interaction, user.id)

    tree.add_command(group)
