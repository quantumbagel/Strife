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
    Select,
    SelectChoice,
    Separator,
    TextDisplay,
    TextSize,
)
from strife.presentation.emoji import EmojiResolver
from strife.routing import prefixes as P


def build_lobby_view(
    lobby: Lobby,
    meta: GameMetadata,
    emoji: EmojiResolver,
    text: TextConfig,
) -> LayoutView:
    brand = emoji.general("brand_logo")
    view = LayoutView()
    view.children.append(
        TextDisplay(markdown_content=f"## {brand} {text.get('lobby.title', game_name=meta.name)}", size_style=TextSize.HEADER)
    )
    container = Container()
    container.add_text(TextDisplay(markdown_content=meta.summary, size_style=TextSize.BODY))
    container.add_separator()
    roster_lines = []
    for member in lobby.members:
        status = text.get("lobby.ready") if member.user_id in lobby.ready else text.get("lobby.not_ready")
        roster_lines.append(f"<@{member.user_id}> ({status})")
    for bot in lobby.bots:
        roster_lines.append(f"**{bot.name}** ({bot.difficulty})")
    container.add_text(
        TextDisplay(
            markdown_content="**Players**\n" + ("\n".join(roster_lines) or "_Empty_"),
            size_style=TextSize.BODY,
        )
    )
    container.add_text(
        TextDisplay(
            markdown_content=text.get(
                "lobby.waiting",
                count=lobby.total_players,
                max=meta.player_count.describe(),
            ),
            size_style=TextSize.SUBHEADER,
        )
    )
    view.add_container(container)

    tid = lobby.thread_id
    controls = ActionRow()
    controls.add_button(
        Button(source="join", label="Join", style=ButtonStyle.SUCCESS, emoji="play", route_prefix=P.LOBBY_JOIN)
    )
    controls.add_button(
        Button(source="leave", label="Leave", style=ButtonStyle.SECONDARY, route_prefix=P.LOBBY_LEAVE)
    )
    controls.add_button(
        Button(source="ready", label="Ready", style=ButtonStyle.PRIMARY, route_prefix=P.LOBBY_READY)
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
            emoji="configure",
            route_prefix=P.LOBBY_SETTINGS,
        )
    )
    controls.add_button(
        Button(source="start", label="Start", style=ButtonStyle.SUCCESS, route_prefix=P.LOBBY_START)
    )
    view.add_action_row(controls)

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
            view.add_action_row(row)
    return view
