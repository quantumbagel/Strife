from __future__ import annotations

from datetime import datetime

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
from strife.presentation.roster import bot_label, player_mention
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


def _meta_line(match: MatchDetail, emoji: EmojiResolver) -> str | None:
    parts: list[str] = []

    player_labels = [
        player_mention(
            user_id=p.user_id,
            display_name=p.display_name,
            is_bot=p.is_bot,
            emoji=emoji,
        )
        for p in sorted(match.players, key=lambda p: p.seat_index)
    ]
    if player_labels:
        joiner = " vs " if len(player_labels) == 2 else ", "
        parts.append(f"{emoji.get('user')} {joiner.join(player_labels)}")

    if match.started_at and match.ended_at:
        seconds = int((match.ended_at - match.started_at).total_seconds())
        mins, secs = divmod(seconds, 60)
        parts.append(f"{emoji.get('timer')} {mins}m {secs}s")

    if not parts:
        return None
    return " • ".join(parts)


def build_replay_view(
    match: MatchDetail,
    frame_index: int,
    total: int,
    *,
    owner_id: int,
    frame_view: LayoutView | None = None,
    takeover_info: dict | None = None,
    timestamp: datetime | None = None,
    turn_label: str | None = None,
    text: TextConfig,
    game_name: str,
    emoji: EmojiResolver,
) -> LayoutView:
    view = LayoutView()
    container = Container()

    game_emoji = emoji.get_game_emoji(match.game_key)
    forward_emoji = emoji.get("forward")
    container.add_text(
        TextDisplay(
            markdown_content=f"### {game_emoji} {text.get('replay.title', code=match.code, game_name=game_name, forward_emoji=forward_emoji)}",
            size_style=TextSize.HEADER,
        )
    )

    meta = _meta_line(match, emoji)
    if meta:
        container.add_text(
            TextDisplay(
                markdown_content=f"-# {meta}",
                size_style=TextSize.BODY,
            )
        )

    outcome_dict = match.outcome or {}
    summary = outcome_dict.get("summary") or outcome_dict
    winner_seat = summary.get("winner")
    description = outcome_dict.get("description") or summary.get("description")

    if description:
        outcome_text = description
    elif winner_seat is not None and isinstance(winner_seat, int):
        winner_player = next((p for p in match.players if p.seat_index == winner_seat), None)
        if winner_player:
            winner_label = player_mention(
                user_id=winner_player.user_id,
                display_name=winner_player.display_name,
                is_bot=winner_player.is_bot,
                emoji=emoji,
            )
            outcome_text = text.get("match.winner", winner=winner_label)
        else:
            outcome_text = text.get("match.winner", winner=f"Seat {winner_seat}")
    elif "winning_faction" in summary:
        outcome_text = text.get("match.winner", winner=summary["winning_faction"])
    else:
        outcome_text = text.get("match.draw")

    if match.status == "abandoned":
        result_emoji = emoji.get("error")
    elif winner_seat is not None or "winning_faction" in summary:
        result_emoji = emoji.get("success")
    else:
        result_emoji = emoji.get("hmm")

    if turn_label and turn_label != f"Action {frame_index + 1}":
        turn_info = text.get(
            "replay.action_label_detail",
            current=frame_index + 1,
            total=total,
            label=turn_label,
        )
    else:
        turn_info = text.get("replay.action_label", current=frame_index + 1, total=total)
    if timestamp:
        ts_val = int(timestamp.timestamp())
        turn_info = f"{turn_info} • <t:{ts_val}:f> (<t:{ts_val}:R>)"

    outcome_line = text.get(
        "replay.outcome_line",
        result_emoji=result_emoji,
        outcome=outcome_text,
    )
    container.add_text(
        TextDisplay(
            markdown_content=(
                f"{outcome_line}\n"
                f"-# {emoji.get('time')} {turn_info}"
            ),
            size_style=TextSize.BODY,
        )
    )
    container.add_separator()

    if takeover_info:
        display_name = takeover_info.get("display_name")
        reason = takeover_info.get("reason", "timeout")
        info_type = takeover_info.get("type", "bot_takeover")
        user_id = takeover_info.get("user_id")
        is_bot = takeover_info.get("is_bot", False)
        if user_id is not None or is_bot:
            name_label = player_mention(
                user_id=user_id,
                display_name=display_name,
                is_bot=is_bot or user_id is None,
                emoji=emoji,
            )
        elif display_name:
            name_label = bot_label(emoji, display_name)
        else:
            name_label = "Unknown"

        if info_type == "bot_takeover":
            reason_str = (
                text.get("replay.reason_inactivity") if reason == "timeout" else reason
            )
            notice = text.get(
                "replay.notice_bot_takeover",
                error_emoji=emoji.get("error"),
                name=name_label,
                reason=reason_str,
            )
        else:  # removal
            action_str = (
                text.get("replay.action_timed_out")
                if reason == "timeout"
                else text.get("replay.action_forfeited")
            )
            notice = text.get(
                "replay.notice_removal",
                error_emoji=emoji.get("error"),
                name=name_label,
                action=action_str,
            )
        container.add_text(
            TextDisplay(
                markdown_content=notice,
                size_style=TextSize.BODY,
            )
        )
        container.add_separator()

    if frame_view:
        _merge_frame_into(container, frame_view)
        if getattr(frame_view, "files", None):
            view.files.extend(frame_view.files)
    else:
        container.add_text(
            TextDisplay(
                markdown_content=text.get("replay.action_label", current=frame_index + 1, total=total)
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
            label=text.get("replay.action_label", current=frame_index + 1, total=total),
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
