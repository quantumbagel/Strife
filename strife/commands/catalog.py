from __future__ import annotations

import math

import discord

from strife.config import AppConfig
from strife.config.text import TextConfig
from strife.engine.registry import GameRegistry
from strife.presentation.compiler import Compiler
from strife.presentation.components import (
    ActionRow,
    Button,
    ButtonStyle,
    Container,
    LayoutView,
    Separator,
    TextDisplay,
    TextSize,
)
from strife.presentation.emoji import EmojiResolver
from strife.presentation.modals import PageJumpModal
from strife.routing import prefixes as P


class CatalogService:
    def __init__(
        self,
        registry: GameRegistry,
        config: AppConfig,
        compiler: Compiler,
        emoji: EmojiResolver,
        text: TextConfig,
    ) -> None:
        self.registry = registry
        self.config = config
        self.compiler = compiler
        self.emoji = emoji
        self.text = text
        self._page_size = 3

    def _enabled_games(self) -> list:
        return [m for m in self.registry.all() if self.config.games.for_game(m.key).enabled]

    def _build_catalog_view(self, games: list, page: int, pages: int) -> LayoutView:
        chunk = games[page * self._page_size : (page + 1) * self._page_size]
        view = LayoutView()
        container = Container()

        logo = self.emoji.get("logo")
        container.add_text(
            TextDisplay(
                markdown_content=f"### {logo} {self.text.get('catalog.title')}",
                size_style=TextSize.HEADER,
            )
        )
        container.add_separator()

        for idx, meta in enumerate(chunk):
            if idx > 0:
                container.add_separator()

            game_emoji = self.emoji.get_game_emoji(meta.key)
            user_emoji = self.emoji.get("user")
            time_emoji = self.emoji.get("time")
            diff_emoji = self.emoji.get("difficulty")

            container.add_text(
                TextDisplay(
                    markdown_content=(
                        f"{game_emoji} **{meta.name}**\n"
                        f"{meta.summary}\n"
                        f"-# {user_emoji} {meta.player_count.describe()} • {time_emoji} {meta.time_estimate} • {diff_emoji} {meta.difficulty}/10"
                    ),
                    size_style=TextSize.BODY,
                )
            )

        nav = ActionRow()
        nav.add_button(
            Button(
                source="prev",
                label=self.text.get("common.prev"),
                style=ButtonStyle.SECONDARY,
                emoji="previous",
                route_prefix=P.CAT_NAV,
                payload={"page": max(0, page - 1)},
                disabled=page <= 0,
            )
        )
        nav.add_button(
            Button(
                source="jump",
                label=self.text.get("catalog.page", page=page + 1, pages=pages),
                style=ButtonStyle.SECONDARY,
                route_prefix=P.CAT_NAV,
                payload={"jump": True, "pages": pages, "page": page},
            )
        )
        nav.add_button(
            Button(
                source="next",
                label=self.text.get("common.next"),
                style=ButtonStyle.SECONDARY,
                emoji="next",
                route_prefix=P.CAT_NAV,
                payload={"page": min(pages - 1, page + 1)},
                disabled=page >= pages - 1,
            )
        )
        container.add_action_row(nav)
        view.add_container(container)
        return view

    async def show(self, interaction: discord.Interaction, page: int = 0) -> None:
        games = self._enabled_games()
        pages = max(1, math.ceil(len(games) / self._page_size))
        page = max(0, min(page, pages - 1))
        view = self._build_catalog_view(games, page, pages)
        compiled = self.compiler.compile(view, resource_id=interaction.user.id, prefix=P.CAT_NAV)
        await interaction.response.send_message(view=compiled, ephemeral=True)

    async def navigate(self, interaction: discord.Interaction, page: int) -> None:
        if interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)
        page = int(page)
        games = self._enabled_games()
        pages = max(1, math.ceil(len(games) / self._page_size))
        page = max(0, min(page, pages - 1))
        view = self._build_catalog_view(games, page, pages)
        compiled = self.compiler.compile(view, resource_id=interaction.user.id, prefix=P.CAT_NAV)
        await interaction.response.edit_message(view=compiled)

    async def open_jump_modal(self, interaction: discord.Interaction, *, pages: int, page: int = 0) -> None:
        async def on_submit(modal_interaction: discord.Interaction, new_page: int) -> None:
            await modal_interaction.response.defer(ephemeral=True)
            await self.navigate(modal_interaction, new_page)

        modal = PageJumpModal(
            title=self.text.get("catalog.title"),
            label=self.text.get("common.jump_page_label"),
            placeholder=self.text.get("common.jump_page_placeholder"),
            current=page + 1,
            total=pages,
            on_submit_cb=on_submit,
        )
        await interaction.response.send_modal(modal)
