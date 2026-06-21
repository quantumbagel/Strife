from __future__ import annotations

from strife.config.text import TextConfig
from strife.persistence.repositories import MatchDetail
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
from strife.routing import prefixes as P


def build_replay_view(
    match: MatchDetail,
    frame_index: int,
    total: int,
    *,
    owner_id: int,
    frame_view: LayoutView | None = None,
    text: TextConfig,
) -> LayoutView:
    view = LayoutView()
    container = Container()
    container.add_text(
        TextDisplay(
            markdown_content=f"### Replay: Match #{match.code} ({match.game_key})",
            size_style=TextSize.HEADER
        )
    )
    container.add_text(TextDisplay(markdown_content=str(match.outcome)))
    container.add_separator()
    
    frame_action_rows = []
    if frame_view:
        for child in frame_view.children:
            if isinstance(child, ActionRow):
                frame_action_rows.append(child)
            elif isinstance(child, Container):
                container.children.extend(child.children)
            else:
                container.children.append(child)
    else:
        container.add_text(TextDisplay(markdown_content=f"Frame {frame_index + 1}/{total}"))
    
    view.add_container(container)
    for row in frame_action_rows:
        view.add_action_row(row)

    nav = ActionRow()
    nav.add_button(
        Button(
            source="first",
            label="First",
            style=ButtonStyle.SECONDARY,
            route_prefix=P.R_NAV,
            payload={"owner": owner_id, "frame": 0},
            disabled=frame_index <= 0,
        )
    )
    nav.add_button(
        Button(
            source="prev",
            label="Prev",
            style=ButtonStyle.SECONDARY,
            route_prefix=P.R_NAV,
            payload={"owner": owner_id, "frame": max(0, frame_index - 1)},
            disabled=frame_index <= 0,
        )
    )
    nav.add_button(Button(source="", label=f"Turn {frame_index + 1}/{total}", style=ButtonStyle.SECONDARY, disabled=True))
    nav.add_button(
        Button(
            source="next",
            label="Next",
            style=ButtonStyle.SECONDARY,
            route_prefix=P.R_NAV,
            payload={"owner": owner_id, "frame": min(total - 1, frame_index + 1)},
            disabled=frame_index >= total - 1,
        )
    )
    nav.add_button(
        Button(
            source="last",
            label="Last",
            style=ButtonStyle.SECONDARY,
            route_prefix=P.R_NAV,
            payload={"owner": owner_id, "frame": max(0, total - 1)},
            disabled=frame_index >= total - 1,
        )
    )
    view.add_action_row(nav)

    if total > 1:
        bookmarks = _bookmark_frames(total)
        seek = ActionRow()
        seek.add_select(
            Select(
                source="seek",
                placeholder="Jump to turn...",
                choices=[
                    SelectChoice(label=f"Turn {idx + 1}", value=str(idx), default=idx == frame_index)
                    for idx in bookmarks
                ],
                route_prefix=P.R_NAV,
                payload={"owner": owner_id, "mode": "seek"},
            )
        )
        view.add_action_row(seek)
    return view


def _bookmark_frames(total: int, limit: int = 25) -> list[int]:
    if total <= limit:
        return list(range(total))
    step = max(1, total // limit)
    return list(range(0, total, step))[:limit]
