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
    TextDisplay,
    TextSize,
)
from strife.presentation.emoji import EmojiResolver
from strife.routing import prefixes as P


def build_replay_view(
    match: MatchDetail,
    frame_index: int,
    total: int,
    *,
    owner_id: int,
    frame_view: LayoutView | None = None,
    text: TextConfig,
    game_name: str,
    emoji: EmojiResolver,
) -> LayoutView:
    view = LayoutView()
    container = Container()

    game_emoji = emoji.get_game_emoji(match.game_key)
    container.add_text(
        TextDisplay(
            markdown_content=f"### {game_emoji} {text.get('replay.title', code=match.code, game_name=game_name)}",
            size_style=TextSize.HEADER
        )
    )

    # Parse outcome dictionary to display clean win/draw text
    outcome_dict = match.outcome or {}
    summary = outcome_dict.get("summary") or {}
    winner_seat = summary.get("winner")

    if winner_seat is not None and isinstance(winner_seat, int) and 0 <= winner_seat < len(match.players):
        winner_player = next((p for p in match.players if p.seat_index == winner_seat), None)
        winner_name = winner_player.display_name if winner_player else f"Seat {winner_seat}"
        outcome_text = text.get("match.winner", winner=winner_name)
    elif "winning_faction" in summary:
        outcome_text = text.get("match.winner", winner=summary["winning_faction"])
    else:
        outcome_text = text.get("match.draw")

    container.add_text(
        TextDisplay(
            markdown_content=f"🏆 **Outcome:** {outcome_text}\n"
                             f"-# {emoji.get('time')} {text.get('replay.turn_label', current=frame_index + 1, total=total)}",
            size_style=TextSize.BODY
        )
    )
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
        container.add_text(TextDisplay(markdown_content=text.get("replay.turn_label", current=frame_index + 1, total=total)))
    
    view.add_container(container)
    for row in frame_action_rows:
        view.add_action_row(row)

    nav = ActionRow()
    nav.add_button(
        Button(
            source="first",
            label=text.get("common.first"),
            style=ButtonStyle.SECONDARY,
            route_prefix=P.R_NAV,
            payload={"owner": owner_id, "frame": 0},
            disabled=frame_index <= 0,
        )
    )
    nav.add_button(
        Button(
            source="prev",
            label=text.get("common.prev"),
            style=ButtonStyle.SECONDARY,
            route_prefix=P.R_NAV,
            payload={"owner": owner_id, "frame": max(0, frame_index - 1)},
            disabled=frame_index <= 0,
        )
    )
    nav.add_button(Button(source="", label=text.get("replay.turn_label", current=frame_index + 1, total=total), style=ButtonStyle.SECONDARY, disabled=True))
    nav.add_button(
        Button(
            source="next",
            label=text.get("common.next"),
            style=ButtonStyle.SECONDARY,
            route_prefix=P.R_NAV,
            payload={"owner": owner_id, "frame": min(total - 1, frame_index + 1)},
            disabled=frame_index >= total - 1,
        )
    )
    nav.add_button(
        Button(
            source="last",
            label=text.get("common.last"),
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
                placeholder=text.get("replay.jump_placeholder"),
                choices=[
                    SelectChoice(label=text.get("replay.turn_choice_label", current=idx + 1), value=str(idx), default=idx == frame_index)
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
