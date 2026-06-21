from __future__ import annotations

from strife.config.text import TextConfig
from strife.persistence.repositories import MatchDetail
from strife.presentation.components import (
    ActionRow,
    Button,
    ButtonStyle,
    Container,
    LayoutView,
    TextDisplay,
    TextSize,
)
from strife.presentation.emoji import EmojiResolver
from strife.presentation.roster import player_mention
from strife.routing import prefixes as P


def _merge_frame_into(container: Container, frame_view: LayoutView | None) -> None:
    if frame_view is None:
        return
    for child in frame_view.children:
        if isinstance(child, Container):
            container.children.extend(child.children)
        elif isinstance(child, ActionRow):
            container.add_action_row(child)
        else:
            container.children.append(child)


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
            size_style=TextSize.HEADER,
        )
    )

    outcome_dict = match.outcome or {}
    summary = outcome_dict.get("summary") or outcome_dict
    winner_seat = summary.get("winner")

    if winner_seat is not None and isinstance(winner_seat, int):
        winner_player = next((p for p in match.players if p.seat_index == winner_seat), None)
        if winner_player:
            winner_label = player_mention(
                user_id=winner_player.user_id,
                display_name=winner_player.display_name,
                is_bot=winner_player.is_bot,
            )
            outcome_text = text.get("match.winner", winner=winner_label)
        else:
            outcome_text = text.get("match.winner", winner=f"Seat {winner_seat}")
    elif "winning_faction" in summary:
        outcome_text = text.get("match.winner", winner=summary["winning_faction"])
    else:
        outcome_text = text.get("match.draw")

    container.add_text(
        TextDisplay(
            markdown_content=(
                f"{emoji.get('success')} **Outcome:** {outcome_text}\n"
                f"-# {emoji.get('time')} {text.get('replay.turn_label', current=frame_index + 1, total=total)}"
            ),
            size_style=TextSize.BODY,
        )
    )
    container.add_separator()

    if frame_view:
        _merge_frame_into(container, frame_view)
    else:
        container.add_text(
            TextDisplay(
                markdown_content=text.get("replay.turn_label", current=frame_index + 1, total=total)
            )
        )

    nav = ActionRow()
    nav.add_button(
        Button(
            source="first",
            label=text.get("common.first"),
            style=ButtonStyle.SECONDARY,
            emoji="first",
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
            emoji="previous",
            route_prefix=P.R_NAV,
            payload={"owner": owner_id, "frame": max(0, frame_index - 1)},
            disabled=frame_index <= 0,
        )
    )
    nav.add_button(
        Button(
            source="jump",
            label=text.get("replay.turn_label", current=frame_index + 1, total=total),
            style=ButtonStyle.SECONDARY,
            route_prefix=P.R_NAV,
            payload={"owner": owner_id, "jump": True, "total": total, "frame": frame_index},
        )
    )
    nav.add_button(
        Button(
            source="next",
            label=text.get("common.next"),
            style=ButtonStyle.SECONDARY,
            emoji="next",
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
            emoji="last",
            route_prefix=P.R_NAV,
            payload={"owner": owner_id, "frame": max(0, total - 1)},
            disabled=frame_index >= total - 1,
        )
    )
    container.add_action_row(nav)
    view.add_container(container)
    return view
