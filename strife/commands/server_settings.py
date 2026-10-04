from __future__ import annotations

import discord

from strife.config.text import TextConfig
from strife.persistence.repositories import GuildRepository
from strife.presentation.compiler import Compiler
from strife.presentation.components import (
    ActionRow,
    Button,
    ButtonStyle,
    ChannelSelect,
    Container,
    DescribedSelect,
    LayoutView,
    TextDisplay,
    TextSize,
)
from strife.presentation.emoji import EmojiResolver
from strife.presentation.user_error import ErrorContext, UserErrorPresenter
from strife.presentation.user_success import UserSuccessPresenter
from strife.routing import prefixes as P

# What a lobby channel needs: post the lobby, then open and play in its thread.
# Keep in step with _GAME_CHANNEL_PERMISSIONS in matchmaking/lobby_flow.py.
LOBBY_CHANNEL_PERMISSIONS = (
    "view_channel",
    "send_messages",
    "embed_links",
    "create_public_threads",
    "send_messages_in_threads",
    "manage_threads",
)


class ServerSettingsService:
    def __init__(
        self,
        guilds: GuildRepository,
        compiler: Compiler,
        emoji: EmojiResolver,
        text: TextConfig,
        user_errors: UserErrorPresenter,
        user_success: UserSuccessPresenter,
    ) -> None:
        self.guilds = guilds
        self.compiler = compiler
        self.emoji = emoji
        self.text = text
        self.user_errors = user_errors
        self.user_success = user_success

    def _build_view(self, guild_id: int, channel_id: int | None) -> LayoutView:
        view = LayoutView()
        container = Container()
        logo = self.emoji.get("logo")
        forward = self.emoji.get("forward")
        settings_emoji = self.emoji.get("settings")

        container.add_text(
            TextDisplay(
                markdown_content=self.text.get(
                    "server.title",
                    logo=logo,
                    forward=forward,
                    settings_emoji=settings_emoji,
                ),
                size_style=TextSize.HEADER,
            )
        )
        container.add_separator()

        if channel_id is not None:
            channel_text = self.text.get(
                "server.current_channel", mention=f"<#{channel_id}>"
            )
        else:
            channel_text = self.text.get("server.no_channel")

        container.add_text(
            TextDisplay(
                markdown_content=channel_text,
                size_style=TextSize.BODY,
            )
        )
        container.add_separator()

        container.add_described_select(
            DescribedSelect(
                description=self.text.get("server.channel_select_desc"),
                select=ChannelSelect(
                    source="channel",
                    placeholder=self.text.get("server.channel_placeholder"),
                    channel_types=("text",),
                    default_id=channel_id,
                    route_prefix=P.SERVER_CHANNEL,
                    resource_id=guild_id,
                ),
            )
        )
        if channel_id is not None:
            row = ActionRow()
            row.add_button(
                Button(
                    source="clear",
                    label=self.text.get("server.clear_channel_btn"),
                    style=ButtonStyle.SECONDARY,
                    route_prefix=P.SERVER_CLEAR,
                    resource_id=guild_id,
                )
            )
            container.add_action_row(row)
        view.add_container(container)
        return view

    async def _require_admin(self, interaction: discord.Interaction) -> bool:
        # default_permissions is ignored on subcommands, so /strife server is
        # visible to everyone; enforce the same rule as the settings buttons.
        if interaction.guild is None:
            await self.user_errors.send(interaction, "errors.guild_only")
            return False
        member = interaction.user
        if (
            not isinstance(member, discord.Member)
            or not member.guild_permissions.administrator
        ):
            await self.user_errors.send(interaction, "errors.admin_required")
            return False
        return True

    def _permission_label(self, name: str) -> str:
        key = f"lobby.permission_{name}"
        label = self.text.get(key)
        return label if label != key else name.replace("_", " ").title()

    async def _missing_channel_permissions(
        self, interaction: discord.Interaction, channel_id: int
    ) -> list[str]:
        guild = interaction.guild
        assert guild is not None
        channel = guild.get_channel(channel_id)
        if channel is None:
            try:
                channel = await guild.fetch_channel(channel_id)
            except discord.HTTPException:
                channel = None
        me = guild.me
        if me is None and interaction.client.user is not None:
            try:
                me = await guild.fetch_member(interaction.client.user.id)
            except discord.HTTPException:
                me = None
        if channel is None or me is None:
            # The bot can't even see the channel.
            return [self._permission_label(name) for name in LOBBY_CHANNEL_PERMISSIONS]
        perms = channel.permissions_for(me)
        return [
            self._permission_label(name)
            for name in LOBBY_CHANNEL_PERMISSIONS
            if not getattr(perms, name)
        ]

    async def open(
        self, interaction: discord.Interaction, *, edit: bool = False
    ) -> None:
        if not await self._require_admin(interaction):
            return
        if not interaction.response.is_done():
            if edit:
                await interaction.response.defer()
            else:
                await interaction.response.defer(ephemeral=True)
        await self.guilds.upsert(interaction.guild_id)
        channel_id = await self.guilds.get_default_channel(interaction.guild_id)
        view = self._build_view(interaction.guild_id, channel_id)
        compiled = self.compiler.compile(
            view, resource_id=interaction.guild_id, prefix=P.SERVER_NAV
        )
        await interaction.edit_original_response(view=compiled)

    async def set_channel(
        self, interaction: discord.Interaction, channel_id: int
    ) -> None:
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)
        if not await self._require_admin(interaction):
            return
        missing = await self._missing_channel_permissions(interaction, channel_id)
        if missing:
            # Put the picker back on the saved channel before explaining why.
            current_id = await self.guilds.get_default_channel(interaction.guild_id)
            view = self._build_view(interaction.guild_id, current_id)
            compiled = self.compiler.compile(
                view, resource_id=interaction.guild_id, prefix=P.SERVER_NAV
            )
            await interaction.edit_original_response(view=compiled)
            await self.user_errors.send(
                interaction,
                "errors.server_channel_missing_permissions",
                context=ErrorContext(
                    interaction=interaction,
                    reason_kwargs={
                        "channel": f"<#{channel_id}>",
                        "permissions": ", ".join(missing),
                    },
                ),
            )
            return
        await self.guilds.upsert(interaction.guild_id)
        await self.guilds.set_default_channel(interaction.guild_id, channel_id)
        view = self._build_view(interaction.guild_id, channel_id)
        compiled = self.compiler.compile(
            view, resource_id=interaction.guild_id, prefix=P.SERVER_NAV
        )
        await interaction.edit_original_response(view=compiled)
        await self.user_success.send(
            interaction,
            "guild.channel_set",
            format_kwargs={"mention": f"<#{channel_id}>"},
        )

    async def clear_channel(self, interaction: discord.Interaction) -> None:
        if not await self._require_admin(interaction):
            return
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)
        await self.guilds.upsert(interaction.guild_id)
        await self.guilds.clear_default_channel(interaction.guild_id)
        view = self._build_view(interaction.guild_id, None)
        compiled = self.compiler.compile(
            view, resource_id=interaction.guild_id, prefix=P.SERVER_NAV
        )
        await interaction.edit_original_response(view=compiled)
        await self.user_success.send(interaction, "guild.channel_cleared")
