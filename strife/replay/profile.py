from __future__ import annotations

import math

import discord

from strife.config.text import TextConfig
from strife.persistence.repositories import MatchRepository, UserRepository
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
from strife.presentation.modals import PageJumpModal
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

    def _build_view(
        self,
        user: discord.User,
        *,
        game: str | None,
        page: int,
        pages: int,
        stats,
        matches,
        rate: int,
    ) -> LayoutView:
        view = LayoutView()
        suffix = f" ({game})" if game else ""
        container = Container()

        emoji = self.compiler.emoji
        user_emoji = emoji.get("user")
        forward = emoji.get("forward")
        container.add_text(
            TextDisplay(
                markdown_content=f"### {user_emoji} {self.text.get('profile.title', name=user.display_name)}{suffix}",
                size_style=TextSize.HEADER,
            )
        )

        diff_emoji = emoji.get("difficulty")
        stats_str = self.text.get(
            "profile.stats",
            wins=stats.wins,
            losses=stats.losses,
            draws=stats.draws,
            played=stats.played,
            rate=rate,
        )
        container.add_text(
            TextDisplay(
                markdown_content=f"{diff_emoji} **Stats Summary**\n{stats_str}",
                size_style=TextSize.BODY,
            )
        )
        container.add_separator()

        game_emoji = emoji.get("game")
        if matches:
            lines = []
            for m in matches:
                g_emoji = emoji.get_game_emoji(m.game_key)
                if m.status == "completed":
                    status_emoji = emoji.get("success")
                elif m.status == "abandoned":
                    status_emoji = emoji.get("error")
                else:
                    status_emoji = emoji.get("loading")
                status_str = m.status.capitalize()
                lines.append(
                    f"{g_emoji} `#{m.code}` {status_emoji} {status_str} {forward} {m.created_at:%Y-%m-%d}"
                )

            recent_title = self.text.get("profile.recent", page=page + 1, pages=pages)
            container.add_text(
                TextDisplay(
                    markdown_content=f"{game_emoji} **{recent_title}**\n" + "\n".join(lines),
                    size_style=TextSize.BODY,
                )
            )
        else:
            container.add_text(
                TextDisplay(
                    markdown_content=(
                        f"{game_emoji} **Recent Matches**\n{self.text.get('profile.no_matches')}"
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
                route_prefix=P.PROF_NAV,
                payload={"game": game, "page": max(0, page - 1)},
                disabled=page <= 0,
            )
        )
        nav.add_button(
            Button(
                source="jump",
                label=self.text.get("catalog.page", page=page + 1, pages=pages),
                style=ButtonStyle.SECONDARY,
                route_prefix=P.PROF_NAV,
                payload={"game": game, "jump": True, "pages": pages, "page": page},
            )
        )
        nav.add_button(
            Button(
                source="next",
                label=self.text.get("common.next"),
                style=ButtonStyle.SECONDARY,
                emoji="next",
                route_prefix=P.PROF_NAV,
                payload={"game": game, "page": page + 1},
                disabled=page + 1 >= pages,
            )
        )
        container.add_action_row(nav)
        view.add_container(container)
        return view

    async def show(
        self,
        interaction: discord.Interaction,
        user: discord.User,
        game: str | None,
        page: int,
        *,
        edit: bool = False,
    ) -> None:
        stats = await self.users.get_stats(user.id, game)
        match_list = await self.matches.list_for_user(
            user.id, game, limit=self._page_size, offset=page * self._page_size
        )
        pages = max(1, math.ceil(stats.played / self._page_size)) if stats.played else 1
        rate = round((stats.wins / stats.played) * 100) if stats.played else 0
        view = self._build_view(
            user,
            game=game,
            page=page,
            pages=pages,
            stats=stats,
            matches=match_list,
            rate=rate,
        )
        compiled = self.compiler.compile(view, resource_id=user.id, prefix=P.PROF_NAV)

        if edit:
            if interaction.response.is_done():
                await interaction.edit_original_response(view=compiled)
            else:
                await interaction.response.edit_message(view=compiled)
        else:
            await interaction.response.send_message(view=compiled, ephemeral=True)

    async def navigate(self, interaction: discord.Interaction, route: Route) -> None:
        page = int(route.payload.get("page", 0))
        game = route.payload.get("game")
        user = interaction.user
        await self.show(interaction, user, game, page, edit=True)

    async def open_jump_modal(self, interaction: discord.Interaction, route: Route) -> None:
        pages = int(route.payload.get("pages", 1))
        page = int(route.payload.get("page", 0))
        game = route.payload.get("game")

        async def on_submit(modal_interaction: discord.Interaction, new_page: int) -> None:
            await modal_interaction.response.defer(ephemeral=True)
            await self.show(modal_interaction, modal_interaction.user, game, new_page, edit=True)

        modal = PageJumpModal(
            title=self.text.get("profile.title", name=interaction.user.display_name),
            label=self.text.get("common.jump_page_label"),
            placeholder=self.text.get("common.jump_page_placeholder"),
            current=page + 1,
            total=pages,
            on_submit_cb=on_submit,
        )
        await interaction.response.send_modal(modal)
