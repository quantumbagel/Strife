from __future__ import annotations

import math

import discord

from strife.config import AppConfig
from strife.config.text import TextConfig
from strife.engine.registry import GameRegistry
from strife.presentation.compiler import Compiler
from strife.presentation.components import ActionRow, Button, ButtonStyle, Container, LayoutView, TextDisplay, TextSize
from strife.presentation.emoji import EmojiResolver
from strife.routing import prefixes as P


class CatalogService:
    def __init__(self, registry: GameRegistry, config: AppConfig, compiler: Compiler, emoji: EmojiResolver, text: TextConfig) -> None:
        self.registry = registry
        self.config = config
        self.compiler = compiler
        self.emoji = emoji
        self.text = text
        self._page_size = 3

    async def show(self, interaction: discord.Interaction, page: int = 0) -> None:
        games = [m for m in self.registry.all() if self.config.games.for_game(m.key).enabled]
        pages = max(1, math.ceil(len(games) / self._page_size))
        page = max(0, min(page, pages - 1))
        chunk = games[page * self._page_size : (page + 1) * self._page_size]
        view = LayoutView()
        container = Container()
        container.add_text(TextDisplay(markdown_content=f"### {self.text.get('catalog.title')}", size_style=TextSize.HEADER))
        for meta in chunk:
            game_emoji = self.emoji.get_game_emoji(meta.key)
            container.children.append(
                TextDisplay(
                    markdown_content=(
                        f"{game_emoji} **{meta.name}** — {meta.summary}\n"
                        f"Players: {meta.player_count.describe()} • {meta.time_estimate} • {meta.difficulty}"
                    )
                )
            )
        view.add_container(container)
        nav = ActionRow()
        nav.add_button(
            Button(
                source="prev",
                label=self.text.get("common.prev"),
                style=ButtonStyle.SECONDARY,
                route_prefix=P.CAT_NAV,
                payload={"page": max(0, page - 1)},
                disabled=page <= 0,
            )
        )
        nav.add_button(
            Button(
                source="next",
                label=self.text.get("common.next"),
                style=ButtonStyle.SECONDARY,
                route_prefix=P.CAT_NAV,
                payload={"page": min(pages - 1, page + 1)},
                disabled=page >= pages - 1,
            )
        )
        view.add_action_row(nav)
        compiled = self.compiler.compile(view, resource_id=interaction.user.id, prefix=P.CAT_NAV)
        await interaction.response.send_message(view=compiled, ephemeral=True)

    async def navigate(self, interaction: discord.Interaction, page: int) -> None:
        if interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)
        page = int(page)
        games = [m for m in self.registry.all() if self.config.games.for_game(m.key).enabled]
        pages = max(1, math.ceil(len(games) / self._page_size))
        page = max(0, min(page, pages - 1))
        chunk = games[page * self._page_size : (page + 1) * self._page_size]
        view = LayoutView()
        container = Container()
        container.add_text(TextDisplay(markdown_content=f"### {self.text.get('catalog.title')}", size_style=TextSize.HEADER))
        for meta in chunk:
            game_emoji = self.emoji.get_game_emoji(meta.key)
            container.children.append(
                TextDisplay(
                    markdown_content=(
                        f"{game_emoji} **{meta.name}** — {meta.summary}\n"
                        f"Players: {meta.player_count.describe()} • {meta.time_estimate} • {meta.difficulty}"
                    )
                )
            )
        view.add_container(container)
        nav = ActionRow()
        nav.add_button(
            Button(
                source="prev",
                label=self.text.get("common.prev"),
                style=ButtonStyle.SECONDARY,
                route_prefix=P.CAT_NAV,
                payload={"page": max(0, page - 1)},
                disabled=page <= 0,
            )
        )
        nav.add_button(
            Button(
                source="next",
                label=self.text.get("common.next"),
                style=ButtonStyle.SECONDARY,
                route_prefix=P.CAT_NAV,
                payload={"page": min(pages - 1, page + 1)},
                disabled=page >= pages - 1,
            )
        )
        view.add_action_row(nav)
        compiled = self.compiler.compile(view, resource_id=interaction.user.id, prefix=P.CAT_NAV)
        await interaction.response.edit_message(view=compiled)

