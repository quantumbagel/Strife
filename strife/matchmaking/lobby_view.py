from __future__ import annotations

from typing import TYPE_CHECKING

from strife.config.text import TextConfig
from strife.engine.metadata import GameMetadata, format_settings_rules, supports_role_selection
from strife.matchmaking.lobby import Lobby
from strife.matchmaking.role_validation import is_role_selection_complete
from strife.presentation.components import (
    ActionRow,
    Button,
    ButtonStyle,
    Container,
    DescribedSelect,
    LayoutView,
    Section,
    Select,
    SelectChoice,
    TextDisplay,
    TextSize,
)
from strife.presentation.emoji import EmojiResolver
from strife.presentation.roster import member_line
from strife.routing import prefixes as P
from strife.settings import get_settings

if TYPE_CHECKING:
    from strife.engine.game import Game


def build_lobby_view(
        lobby: Lobby,
        meta: GameMetadata,
        emoji: EmojiResolver,
        text: TextConfig,
        *,
        game_cls: type[Game] | None = None,
        role_invalid_reason: str | None = None,
) -> LayoutView:
    game_emoji = emoji.get_game_emoji(meta.key)
    view = LayoutView()
    container = Container()

    # Title moved into the container
    if meta.how_to_play_link:
        title_section = Section(
            accessory=Button(
                label=text.get("lobby.how_to_play"),
                style=ButtonStyle.LINK,
                emoji="learn",
                url=meta.how_to_play_link,
            )
        )
        title_section.add_text(
            TextDisplay(
                markdown_content=f"### {game_emoji} {text.get('lobby.title', game_name=meta.name)}",
                size_style=TextSize.HEADER
            )
        )
        container.add_section(title_section)
    else:
        container.add_text(
            TextDisplay(
                markdown_content=f"### {game_emoji} {text.get('lobby.title', game_name=meta.name)}",
                size_style=TextSize.HEADER
            )
        )

    max_players = meta.player_count.max_players
    lobby_full = lobby.is_full(meta)
    if max_players is not None and lobby.total_players > max_players:
        required_to_leave = lobby.total_players - max_players
        waiting_content = text.get("lobby.waiting_to_leave", count=required_to_leave)
    elif lobby.total_players < meta.player_count.min_players:
        waiting_content = text.get(
            "lobby.waiting_for_players",
            count=lobby.total_players,
            min=meta.player_count.min_players,
        )
    elif supports_role_selection(meta) and not is_role_selection_complete(lobby, meta):
        selected = sum(1 for member in lobby.members if member.user_id in lobby.role_selection)
        waiting_content = text.get(
            "lobby.waiting_for_role_selection",
            selected=selected,
            total=len(lobby.members),
        )
    elif role_invalid_reason:
        waiting_content = text.get("lobby.waiting_for_role_fix", reason=role_invalid_reason)
    else:
        waiting_content = text.get(
            "lobby.waiting_to_ready",
            ready=len(lobby.ready),
            total=len(lobby.members),
        )

    container.add_text(
        TextDisplay(
            markdown_content=f"-# {emoji.get('loading')} {waiting_content}",
            size_style=TextSize.BODY,
        )
    )

    diff_rating = min(max(0, meta.difficulty), 5)
    diff_display = "★" * diff_rating + "☆" * (5 - diff_rating)
    container.add_text(
        TextDisplay(
            markdown_content=(
                f"{meta.description}\n\n"
                f"-# {emoji.get('user')} {meta.player_count.describe()} • {emoji.get('time')} {meta.time_estimate} • {emoji.get('difficulty')} {diff_display}"
            ),
            size_style=TextSize.BODY,
        )
    )
    container.add_separator()

    if lobby.private:
        container.add_text(
            TextDisplay(
                markdown_content=text.get(
                    "lobby.private_indicator",
                    private_emoji=emoji.get("private"),
                ),
                size_style=TextSize.BODY,
            )
        )

    roster_lines = []
    settings = get_settings()
    for member in lobby.members:
        status = text.get("lobby.ready") if member.user_id in lobby.ready else text.get("lobby.not_ready")
        roster_lines.append(
            member_line(
                emoji,
                user_id=member.user_id,
                display_name=member.display_name,
                owner_ids=frozenset(settings.owner_ids),
                creator_id=lobby.creator_id,
                suffix=f"({status})",
            )
        )
    for bot in lobby.bots:
        roster_lines.append(
            member_line(
                emoji,
                user_id=None,
                display_name=bot.name,
                is_bot=True,
                bot_difficulty=bot.difficulty,
            )
        )

    container.add_text(
        TextDisplay(
            markdown_content=f"{text.get('lobby.players_title')}\n"
            + ("\n".join(roster_lines) or text.get("lobby.empty_roster")),
            size_style=TextSize.BODY,
        )
    )

    container.add_separator()

    if meta.settings:
        rule_lines = format_settings_rules(
            meta.settings,
            lobby.settings,
            on_label=text.get("lobby.on_label"),
            off_label=text.get("lobby.off_label"),
        )
        container.add_text(
            TextDisplay(
                markdown_content=f"{text.get('lobby.rules_title')}\n" + "\n".join(rule_lines),
                size_style=TextSize.BODY,
            )
        )

    container.add_separator()

    can_r, _, _ = lobby.can_ready(meta, text, game_cls=game_cls)
    join_style = ButtonStyle.SECONDARY if can_r else ButtonStyle.SUCCESS
    ready_style = ButtonStyle.SUCCESS if can_r else ButtonStyle.PRIMARY
    ready_disabled = role_invalid_reason is not None

    controls = ActionRow()
    if not lobby_full:
        join_label = (
            text.get("lobby.request_join_button")
            if lobby.private
            else text.get("lobby.join_button")
        )
        controls.add_button(
            Button(source="join", label=join_label, style=join_style, emoji="join", route_prefix=P.LOBBY_JOIN)
        )
    controls.add_button(
        Button(source="leave", label=text.get("lobby.leave_button"), style=ButtonStyle.SECONDARY, emoji="leave", route_prefix=P.LOBBY_LEAVE)
    )
    if can_r or lobby.ready:
        controls.add_button(
            Button(
                source="ready",
                label=text.get("lobby.ready_button"),
                style=ready_style,
                emoji="ready",
                route_prefix=P.LOBBY_READY,
                disabled=ready_disabled,
            )
        )

    controls.add_button(
        Button(
            source="settings",
            label=text.get("lobby.settings_button"),
            style=ButtonStyle.SECONDARY,
            emoji="settings",
            route_prefix=P.LOBBY_SETTINGS,
        )
    )

    # Controls added to the container instead of the view
    container.add_action_row(controls)

    if lobby.pending_requests:
        container.add_separator()
        container.add_text(
            TextDisplay(
                markdown_content=text.get(
                    "lobby.join_requests_title",
                    count=len(lobby.pending_requests),
                    user_emoji=emoji.get("user"),
                ),
                size_style=TextSize.SUBHEADER,
            )
        )
        pending_items = list(lobby.pending_requests.items())[:25]
        if not lobby_full:
            container.add_described_select(
                DescribedSelect(
                    description=text.get("lobby.approve_request_select_desc"),
                    select=Select(
                        source="approve",
                        placeholder=text.get("lobby.approve_request_placeholder"),
                        choices=[
                            SelectChoice(
                                label=name,
                                value=str(uid),
                                description=text.get("lobby.approve_request_desc"),
                                emoji="success",
                            )
                            for uid, name in pending_items
                        ],
                        route_prefix=P.LOBBY_APPROVE,
                        resource_id=lobby.thread_id,
                    ),
                )
            )
        container.add_described_select(
            DescribedSelect(
                description=text.get("lobby.deny_request_select_desc"),
                select=Select(
                    source="deny",
                    placeholder=text.get("lobby.deny_request_placeholder"),
                    choices=[
                        SelectChoice(
                            label=name,
                            value=str(uid),
                            description=text.get("lobby.deny_request_desc"),
                            emoji="error",
                        )
                        for uid, name in pending_items
                    ],
                    route_prefix=P.LOBBY_DENY,
                    resource_id=lobby.thread_id,
                ),
            )
        )

    # Finally, add the fully populated container to the view
    view.add_container(container)

    return view