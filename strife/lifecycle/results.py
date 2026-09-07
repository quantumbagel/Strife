from __future__ import annotations

from strife.config.text import TextConfig
from strife.engine.players import GameOutcome, Player
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
from strife.presentation.roster import member_line
from strife.routing import prefixes as P


def build_results_view(
    *,
    game_name: str,
    game_key: str,
    outcome: GameOutcome,
    players: list[Player],
    thread_id: int,
    match_id: int,
    text: TextConfig,
    emoji: EmojiResolver,
    rematch_count: int = 0,
    rematch_disabled: bool = False,
    rematch_expires_at: int | None = None,
    replay_disabled: bool = False,
) -> LayoutView:
    view = LayoutView()
    container = Container()

    game_emoji = emoji.get_game_emoji(game_key)
    forward = emoji.get("forward")
    container.add_text(
        TextDisplay(
            markdown_content=f"### {game_emoji} {text.get('match.result_title', game_name=game_name, forward=forward)}",
            size_style=TextSize.HEADER,
        )
    )

    summary = outcome.summary or {}
    winner_seat = summary.get("winner")
    description = getattr(outcome, "description", None) or summary.get("description")
    if description:
        body_text = f"{emoji.get('success')} **{description}**"
    elif winner_seat is not None and isinstance(winner_seat, int) and 0 <= winner_seat < len(players):
        winner = players[winner_seat]
        winner_label = winner.mention_for(emoji)
        body = text.get("match.winner", winner=winner_label)
        body_text = f"{emoji.get('success')} **{body}**"
    elif "winning_faction" in summary:
        body = text.get("match.winner", winner=summary["winning_faction"])
        body_text = f"{emoji.get('success')} **{body}**"
    else:
        body = text.get("match.draw")
        body_text = f"{emoji.get('hmm')} **{body}**"

    container.add_text(TextDisplay(markdown_content=body_text))
    container.add_separator()

    raw_player_descriptions = getattr(outcome, "player_descriptions", None) or summary.get("player_descriptions") or {}
    player_descriptions = {str(k): v for k, v in raw_player_descriptions.items()}

    lines = []
    for player in players:
        result = outcome.results.get(player.seat, "—")
        role = f" ({player.role_key})" if player.role_key else ""

        if result == "win":
            res_emoji = emoji.get("success")
            res_text = player_descriptions.get(str(player.seat), "Win")
        elif result == "loss":
            res_emoji = emoji.get("error")
            res_text = player_descriptions.get(str(player.seat), "Loss")
        elif result == "draw":
            res_emoji = emoji.get("hmm")
            res_text = player_descriptions.get(str(player.seat), "Draw")
        else:
            res_emoji = emoji.get("bullet")
            res_text = player_descriptions.get(str(player.seat)) or (str(result).capitalize() if result else "—")

        name = member_line(
            emoji,
            user_id=player.user_id,
            display_name=player.display_name,
            is_bot=player.is_bot,
            bot_difficulty=player.bot_difficulty,
        )
        lines.append(f"• {name}{role} {forward} {res_emoji} {res_text}")

    container.add_text(TextDisplay(markdown_content="\n".join(lines)))

    eligible_humans = [
        p for p in players if p.user_id and not p.is_bot and not p.taken_over
    ]
    total = len(eligible_humans)
    if total > 0:
        if rematch_expires_at is not None:
            rematch_label = text.get(
                "match.rematch_progress_expires",
                count=rematch_count,
                total=total,
                expires=rematch_expires_at,
            )
        else:
            rematch_label = text.get("match.rematch_progress", count=rematch_count, total=total)
    else:
        rematch_label = text.get("match.rematch_label")

    row = ActionRow()
    row.add_button(
        Button(
            source="vote",
            label=rematch_label,
            style=ButtonStyle.PRIMARY,
            emoji="rematch",
            route_prefix=P.REMATCH,
            resource_id=thread_id,
            disabled=rematch_disabled or total == 0,
        )
    )
    row.add_button(
        Button(
            source="open",
            label=text.get("match.view_replay_label"),
            style=ButtonStyle.SECONDARY,
            emoji="spectate",
            route_prefix=P.R_NAV,
            resource_id=match_id,
            payload={"frame": 0, "fresh": True},
            disabled=replay_disabled or match_id <= 0,
        )
    )
    container.add_action_row(row)
    view.add_container(container)
    return view
