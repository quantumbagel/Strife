from __future__ import annotations

import math

import discord

from strife.config.text import TextConfig
from strife.persistence.repositories import MatchRepository, UserRepository
from strife.presentation.compiler import Compiler
from strife.presentation.components import ActionRow, Button, ButtonStyle, Container, LayoutView, TextDisplay, Separator, TextSize
from strife.replay.service import ReplayService
from strife.routing import prefixes as P
from strife.routing.custom_id import Route


class ProfileService:
    def __init__(
        self,
        users: UserRepository,
        matches: MatchRepository,
        compiler: Compiler,
        text: TextConfig,
        replay: ReplayService | None = None,
    ) -> None:
        self.users = users
        self.matches = matches
        self.compiler = compiler
        self.text = text
        self.replay = replay
        self._page_size = 6

    async def show(
        self,
        interaction: discord.Interaction,
        user: discord.User,
        game: str | None,
        page: int,
    ) -> None:
        stats = await self.users.get_stats(user.id, game)
        matches = await self.matches.list_for_user(user.id, game, limit=self._page_size, offset=page * self._page_size)
        pages = max(1, math.ceil(stats.played / self._page_size)) if stats.played else 1
        rate = round((stats.wins / stats.played) * 100) if stats.played else 0
        view = LayoutView()
        suffix = f" ({game})" if game else ""
        container = Container()
        container.add_text(
            TextDisplay(markdown_content=f"### {user.display_name} - Profile{suffix}", size_style=TextSize.HEADER)
        )
        container.add_text(
            TextDisplay(
                markdown_content=self.text.get(
                    "profile.stats",
                    wins=stats.wins,
                    losses=stats.losses,
                    draws=stats.draws,
                    played=stats.played,
                    rate=rate,
                )
            )
        )
        container.add_separator()
        if matches:
            lines = [
                f"#{m.code} {m.game_key} {m.status} {m.created_at:%Y-%m-%d}"
                for m in matches
            ]
            container.add_text(
                TextDisplay(
                    markdown_content=self.text.get("profile.recent", page=page + 1, pages=pages)
                    + "\n"
                    + "\n".join(lines)
                )
            )
        else:
            container.add_text(TextDisplay(markdown_content=self.text.get("profile.no_matches")))
        view.add_container(container)

        nav = ActionRow()
        nav.add_button(
            Button(
                source="prev",
                label="Prev",
                style=ButtonStyle.SECONDARY,
                route_prefix=P.PROF_NAV,
                payload={"game": game, "page": max(0, page - 1)},
                disabled=page <= 0,
            )
        )
        nav.add_button(
            Button(
                source="next",
                label="Next",
                style=ButtonStyle.SECONDARY,
                route_prefix=P.PROF_NAV,
                payload={"game": game, "page": page + 1},
                disabled=page + 1 >= pages,
            )
        )
        view.add_action_row(nav)
        compiled = self.compiler.compile(view, resource_id=user.id, prefix=P.PROF_NAV)
        await interaction.response.send_message(view=compiled, ephemeral=True)

    async def navigate(self, interaction: discord.Interaction, route: Route) -> None:
        page = int(route.payload.get("page", 0))
        game = route.payload.get("game")
        user = interaction.user
        await self.show(interaction, user, game, page)
