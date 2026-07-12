from __future__ import annotations

import math

import discord

from strife.config.text import TextConfig
from strife.engine.registry import GameRegistry
from strife.persistence.repositories import MatchRepository, UserRepository
from strife.presentation.compiler import Compiler
from strife.presentation.components import (
    ActionRow,
    Button,
    ButtonStyle,
    Container,
    DescribedSelect,
    LayoutView,
    Select,
    SelectChoice,
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
        registry: GameRegistry | None = None,
        replay: ReplayService | None = None,
    ) -> None:
        self.users = users
        self.matches = matches
        self.compiler = compiler
        self.text = text
        self.registry = registry
        self.replay = replay
        self._page_size = 6

    def _game_name(self, game_key: str) -> str | None:
        if self.registry is None:
            return None
        try:
            return self.registry.metadata(game_key).name
        except KeyError:
            return None

    @staticmethod
    def _duration_str(m) -> str | None:
        if m.started_at and m.ended_at:
            seconds = int((m.ended_at - m.started_at).total_seconds())
            mins, secs = divmod(seconds, 60)
            return f"{mins}m {secs}s"
        if m.total_turns:
            return f"{m.total_turns} actions"
        return None

    def _result_desc(self, m) -> tuple[str, str]:
        """Return (status_emoji_name, player-facing result description)."""
        result = getattr(m, "result", None)
        seat_index = getattr(m, "seat_index", None)

        if m.status == "completed":
            summary = m.outcome.get("summary") or m.outcome
            player_descriptions = summary.get("player_descriptions", {})
            player_desc = None
            if seat_index is not None:
                player_desc = player_descriptions.get(str(seat_index))
            if not player_desc:
                if result == "win":
                    player_desc = self.text.get("profile.result_win")
                elif result == "loss":
                    player_desc = self.text.get("profile.result_loss")
                elif result == "draw":
                    player_desc = self.text.get("profile.result_draw")
                else:
                    player_desc = (
                        result.capitalize()
                        if result
                        else self.text.get("profile.result_completed")
                    )
            return "success", player_desc
        if m.status == "abandoned":
            return "error", self.text.get("profile.result_abandoned")
        return "loading", self.text.get("profile.result_active")

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
        container = Container()

        emoji = self.compiler.emoji
        text = self.text
        user_emoji = emoji.get("user")
        forward = emoji.get("forward")

        header = f"### {user_emoji} {text.get('profile.title', name=user.display_name, forward=forward)}"
        if game:
            game_name = self._game_name(game) or game
            header += f" {forward} {emoji.get_game_emoji(game)} {game_name}"
        container.add_text(
            TextDisplay(
                markdown_content=header,
                size_style=TextSize.HEADER,
            )
        )
        container.add_separator(Separator(visible=False))

        stats_line = text.get(
            "profile.stats",
            success=emoji.get("success"),
            error=emoji.get("error"),
            hmm=emoji.get("hmm"),
            wins=stats.wins,
            losses=stats.losses,
            draws=stats.draws,
        )
        stats_sub = text.get("profile.stats_sub", played=stats.played, rate=rate)
        container.add_text(
            TextDisplay(
                markdown_content=(
                    f"{emoji.get('difficulty')} **{text.get('profile.stats_title')}**\n"
                    f"{stats_line}\n"
                    f"-# {emoji.get('game')} {stats_sub}"
                ),
                size_style=TextSize.BODY,
            )
        )
        container.add_separator()

        game_emoji = emoji.get("game")
        replay_choices: list[SelectChoice] = []
        if matches:
            lines = []
            for m in matches:
                g_emoji = emoji.get_game_emoji(m.game_key)
                role_key = getattr(m, "role_key", None)
                status_emoji_name, player_desc = self._result_desc(m)
                status_emoji = emoji.get(status_emoji_name)

                role_suffix = f" ({role_key.title()})" if role_key else ""

                players_str = ""
                if m.player_count is not None:
                    players_str = f" • {text.get('profile.players_count', count=m.player_count)}"

                start_time = m.started_at or m.created_at
                started_str = f" • <t:{int(start_time.timestamp())}:R>"

                duration = self._duration_str(m)
                duration_str = f" ({duration})" if duration else ""

                lines.append(
                    f"{g_emoji} `#{m.code}` {status_emoji} {player_desc}{role_suffix}{players_str}{started_str}{duration_str}"
                )

                if m.status == "completed":
                    game_name = self._game_name(m.game_key) or m.game_key
                    desc_parts = [player_desc]
                    if m.player_count is not None:
                        desc_parts.append(
                            text.get("profile.players_count", count=m.player_count)
                        )
                    if duration:
                        desc_parts.append(duration)
                    replay_choices.append(
                        SelectChoice(
                            label=f"#{m.code} — {game_name}",
                            value=m.code,
                            description=" • ".join(desc_parts),
                            emoji="spectate",
                        )
                    )

            recent_title = text.get("profile.recent", page=page + 1, pages=pages)
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
                        f"{game_emoji} **{text.get('profile.recent_title')}**\n"
                        f"{text.get('profile.no_matches')}"
                    ),
                    size_style=TextSize.BODY,
                )
            )

        if replay_choices:
            container.add_described_select(
                DescribedSelect(
                    description=text.get("profile.replay_select_desc"),
                    select=Select(
                        source="open",
                        placeholder=text.get("profile.replay_select_placeholder"),
                        choices=replay_choices,
                        route_prefix=P.R_NAV,
                    ),
                )
            )

        nav = ActionRow()
        nav.add_button(
            Button(
                source="prev",
                label=text.get("common.prev"),
                style=ButtonStyle.SECONDARY,
                emoji="previous",
                route_prefix=P.PROF_NAV,
                payload={"game": game, "page": max(0, page - 1), "user": user.id},
                disabled=page <= 0,
            )
        )
        nav.add_button(
            Button(
                source="jump",
                label=text.get("catalog.page", page=page + 1, pages=pages),
                style=ButtonStyle.SECONDARY,
                route_prefix=P.PROF_NAV,
                payload={"game": game, "jump": True, "pages": pages, "page": page, "user": user.id},
            )
        )
        nav.add_button(
            Button(
                source="next",
                label=text.get("common.next"),
                style=ButtonStyle.SECONDARY,
                emoji="next",
                route_prefix=P.PROF_NAV,
                payload={"game": game, "page": page + 1, "user": user.id},
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
        total_matches = await self.matches.count_for_user(user.id, game)
        match_list = await self.matches.list_for_user(
            user.id, game, limit=self._page_size, offset=page * self._page_size
        )
        pages = max(1, math.ceil(total_matches / self._page_size)) if total_matches else 1
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
        user_id = route.payload.get("user")
        if user_id is not None:
            user = interaction.client.get_user(int(user_id))
            if user is None:
                user = await interaction.client.fetch_user(int(user_id))
        else:
            user = interaction.user
        await self.show(interaction, user, game, page, edit=True)

    async def open_jump_modal(self, interaction: discord.Interaction, route: Route) -> None:
        pages = int(route.payload.get("pages", 1))
        page = int(route.payload.get("page", 0))
        game = route.payload.get("game")
        user_id = route.payload.get("user")

        async def on_submit(modal_interaction: discord.Interaction, new_page: int) -> None:
            await modal_interaction.response.defer(ephemeral=True)
            if user_id is not None:
                user = modal_interaction.client.get_user(int(user_id))
                if user is None:
                    user = await modal_interaction.client.fetch_user(int(user_id))
            else:
                user = modal_interaction.user
            await self.show(modal_interaction, user, game, new_page, edit=True)

        modal = PageJumpModal(
            title=self.text.get("profile.jump_modal_title"),
            label=self.text.get("common.jump_page_label"),
            placeholder=self.text.get("common.jump_page_placeholder"),
            current=page + 1,
            total=pages,
            on_submit_cb=on_submit,
            error_message=self.text.get("common.invalid_page"),
        )
        await interaction.response.send_modal(modal)
