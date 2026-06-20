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
    Separator,
    TextDisplay,
    TextSize,
)
from strife.presentation.emoji import EmojiResolver
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
                label="How to Play",
                style=ButtonStyle.LINK,
                emoji="learn",
                url=meta.how_to_play_link,
            )
        )
        title_section.add_text(
            TextDisplay(
                markdown_content=f"## {game_emoji} {text.get('lobby.title', game_name=meta.name)}",
                size_style=TextSize.HEADER
            )
        )
        container.add_section(title_section)
    else:
        container.add_text(
            TextDisplay(
                markdown_content=f"## {game_emoji} {text.get('lobby.title', game_name=meta.name)}",
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

    container.add_text(TextDisplay(markdown_content=meta.summary, size_style=TextSize.BODY))
    container.add_separator()

    roster_lines = []
    settings = get_settings()
    for member in lobby.members:
        status = text.get("lobby.ready") if member.user_id in lobby.ready else text.get("lobby.not_ready")
        prefix = ""
        if member.user_id in settings.owner_ids:
            prefix += f"{emoji.get('admin')} "
        if member.user_id == lobby.creator_id:
            prefix += f"{emoji.get('creator')} "
        roster_lines.append(f"{prefix}<@{member.user_id}> ({status})")
    for bot in lobby.bots:
        roster_lines.append(f"**{bot.name}** ({bot.difficulty})")

    container.add_text(
        TextDisplay(
            markdown_content="**Players**\n" + ("\n".join(roster_lines) or "_Empty_"),
            size_style=TextSize.BODY,
        )
    )

    can_r, _ = lobby.can_ready(meta)
    join_style = ButtonStyle.SECONDARY if can_r else ButtonStyle.SUCCESS
    ready_style = ButtonStyle.SUCCESS if can_r else ButtonStyle.PRIMARY

    controls = ActionRow()
    controls.add_button(
        Button(source="join", label="Join", style=join_style, emoji="join", route_prefix=P.LOBBY_JOIN)
    )
    controls.add_button(
        Button(source="leave", label="Leave", style=ButtonStyle.SECONDARY, emoji="leave", route_prefix=P.LOBBY_LEAVE)
    )
    if can_r or lobby.ready:
        controls.add_button(
            Button(source="ready", label="Ready", style=ready_style, emoji="ready", route_prefix=P.LOBBY_READY)
        )


    if meta.role_flow.value in {"selectable", "selectable_random"}:
        controls.add_button(
            Button(source="assign", label="Assign Roles", style=ButtonStyle.SECONDARY, route_prefix=P.LOBBY_ASSIGN)
        )

    controls.add_button(
        Button(
            source="settings",
            label="Settings",
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
                    placeholder=f"{member.display_name}: Role",
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