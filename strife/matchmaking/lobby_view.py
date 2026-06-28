from __future__ import annotations

from strife.config.text import TextConfig
from strife.engine.metadata import GameMetadata
from strife.matchmaking.lobby import Lobby
from strife.presentation.components import (
    ActionRow,
    Button,
    ButtonStyle,
    Container,
    LayoutView,
    Section,
    Select,
    SelectChoice,
    TextDisplay,
    TextSize,
)
from strife.presentation.emoji import EmojiResolver
from strife.presentation.roster import format_roster, member_line
from strife.routing import prefixes as P
from strife.settings import get_settings


def build_lobby_view(
        lobby: Lobby,
        meta: GameMetadata,
        emoji: EmojiResolver,
        text: TextConfig,
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
    if max_players is not None and lobby.total_players > max_players:
        required_to_leave = lobby.total_players - max_players
        waiting_content = text.get("lobby.waiting_to_leave", count=required_to_leave)
    elif lobby.total_players < meta.player_count.min_players:
        waiting_content = text.get(
            "lobby.waiting_for_players",
            count=lobby.total_players,
            min=meta.player_count.min_players,
        )
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
                f"-# {emoji.get('user')} {meta.player_count.describe()} • {emoji.get('time')} {meta.time_estimate} • {emoji.get('difficulty')}: {diff_display}"
            ),
            size_style=TextSize.BODY,
        )
    )
    container.add_separator()

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

    can_r, _, _ = lobby.can_ready(meta, text)
    join_style = ButtonStyle.SECONDARY if can_r else ButtonStyle.SUCCESS
    ready_style = ButtonStyle.SUCCESS if can_r else ButtonStyle.PRIMARY

    controls = ActionRow()
    controls.add_button(
        Button(source="join", label=text.get("lobby.join_button"), style=join_style, emoji="join", route_prefix=P.LOBBY_JOIN)
    )
    controls.add_button(
        Button(source="leave", label=text.get("lobby.leave_button"), style=ButtonStyle.SECONDARY, emoji="leave", route_prefix=P.LOBBY_LEAVE)
    )
    if can_r or lobby.ready:
        controls.add_button(
            Button(source="ready", label=text.get("lobby.ready_button"), style=ready_style, emoji="ready", route_prefix=P.LOBBY_READY)
        )

    if meta.role_flow.value in {"selectable", "selectable_random"}:
        controls.add_button(
            Button(
                source="assign",
                label=text.get("lobby.assign_roles_button"),
                style=ButtonStyle.SECONDARY,
                emoji="user",
                route_prefix=P.LOBBY_ASSIGN,
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

    if meta.role_flow.value == "selectable":
        for member in lobby.members:
            row = ActionRow()
            choices = [
                SelectChoice(
                    label=role.name,
                    value=role.key,
                    default=lobby.role_selection.get(member.user_id) == role.key,
                )
                for role in meta.roles
            ]
            row.add_select(
                Select(
                    source="role",
                    placeholder=text.get("lobby.role_placeholder", name=member.display_name),
                    choices=choices,
                    payload={"player_id": member.user_id},
                    route_prefix=P.LOBBY_ROLE,
                )
            )
            # Role rows added to the container instead of the view
            container.add_action_row(row)

    # Finally, add the fully populated container to the view
    view.add_container(container)

    return view