from __future__ import annotations

import discord

from strife.config.text import TextConfig
from strife.persistence.repositories import GuildRepository
from strife.presentation.compiler import Compiler
from strife.presentation.components import (
    ChannelSelect,
    Container,
    DescribedSelect,
    LayoutView,
    TextDisplay,
    TextSize,
)
from strife.presentation.emoji import EmojiResolver
from strife.presentation.user_error import UserErrorPresenter
from strife.presentation.user_success import UserSuccessPresenter
from strife.routing import prefixes as P


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
            channel_text = self.text.get("server.current_channel", mention=f"<#{channel_id}>")
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
        view.add_container(container)
        return view

    async def open(self, interaction: discord.Interaction, *, edit: bool = False) -> None:
        if interaction.guild is None:
            await self.user_errors.send(interaction, "common.error")
            return
        await self.guilds.upsert(interaction.guild_id)
        channel_id = await self.guilds.get_default_channel(interaction.guild_id)
        view = self._build_view(interaction.guild_id, channel_id)
        compiled = self.compiler.compile(view, resource_id=interaction.guild_id, prefix=P.SERVER_NAV)
        if edit:
            if interaction.response.is_done():
                await interaction.edit_original_response(view=compiled)
            else:
                await interaction.response.edit_message(view=compiled)
        else:
            await interaction.response.send_message(view=compiled, ephemeral=True)

    async def set_channel(self, interaction: discord.Interaction, channel_id: int) -> None:
        if interaction.guild is None:
            await self.user_errors.send(interaction, "common.error")
            return
        await self.guilds.upsert(interaction.guild_id)
        await self.guilds.set_default_channel(interaction.guild_id, channel_id)
        await interaction.response.defer(ephemeral=True)
        view = self._build_view(interaction.guild_id, channel_id)
        compiled = self.compiler.compile(view, resource_id=interaction.guild_id, prefix=P.SERVER_NAV)
        await interaction.edit_original_response(view=compiled)
        await self.user_success.send(
            interaction,
            "guild.channel_set",
            format_kwargs={"mention": f"<#{channel_id}>"},
        )
