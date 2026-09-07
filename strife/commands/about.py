from __future__ import annotations

import discord

from strife.changelog import ChangelogCatalog
from strife.config import AppConfig
from strife.config.text import TextConfig
from strife.engine.registry import GameRegistry
from strife.presentation.about_view import build_about_view
from strife.presentation.changelog_view import build_changes_view
from strife.presentation.compiler import Compiler
from strife.presentation.emoji import EmojiResolver
from strife.routing import prefixes as P


class AboutService:
    def __init__(
        self,
        compiler: Compiler,
        emoji: EmojiResolver,
        text: TextConfig,
        changelogs: ChangelogCatalog,
        registry: GameRegistry,
        config: AppConfig,
    ) -> None:
        self.compiler = compiler
        self.emoji = emoji
        self.text = text
        self.changelogs = changelogs
        self.registry = registry
        self.config = config

    def _enabled_games(self):
        return [m for m in self.registry.all() if self.config.games.for_game(m.key).enabled]

    def build(
        self,
        tab: str = "main",
        *,
        scope: str = "hub",
        game_key: str | None = None,
        page: int = 0,
    ):
        if tab == "changes":
            return build_changes_view(
                self.emoji,
                self.text,
                self.changelogs,
                self._enabled_games(),
                scope=scope,
                game_key=game_key,
                page=page,
            )
        return build_about_view(self.emoji, self.text, active_tab=tab)

    async def show(self, interaction: discord.Interaction, tab: str = "main") -> None:
        view = self.build(tab)
        compiled = self.compiler.compile(view, resource_id=interaction.user.id, prefix=P.ABOUT_NAV)
        await interaction.response.send_message(view=compiled, ephemeral=True)

    async def navigate(self, interaction: discord.Interaction, route) -> None:
        tab = str(route.payload.get("tab", "main"))
        scope = str(route.payload.get("scope", "hub"))
        game_key = route.payload.get("game")
        if game_key is not None:
            game_key = str(game_key)
        if route.source == "game" and game_key:
            scope = "game"
        page = int(route.payload.get("page", 0))
        view = self.build(tab, scope=scope, game_key=game_key, page=page)
        compiled = self.compiler.compile(view, resource_id=interaction.user.id, prefix=P.ABOUT_NAV)
        await interaction.response.edit_message(view=compiled)
