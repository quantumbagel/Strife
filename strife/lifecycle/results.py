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
from strife.routing import prefixes as P


def build_results_view(
    *,
    game_name: str,
    game_key: str,
    outcome: GameOutcome,
    players: list[Player],
    thread_id: int,
    match_id: int,
    owner_id: int,
    text: TextConfig,
    emoji: EmojiResolver,
) -> LayoutView:
    view = LayoutView()
    container = Container()

    game_emoji = emoji.get_game_emoji(game_key)
    container.add_text(
        TextDisplay(
            markdown_content=f"### {game_emoji} {text.get('match.result_title', game_name=game_name)}",
            size_style=TextSize.HEADER
        )
    )

    summary = outcome.summary or {}
    winner_seat = summary.get("winner")
    if winner_seat is not None and isinstance(winner_seat, int) and 0 <= winner_seat < len(players):
        winner_name = players[winner_seat].display_name
        body = text.get("match.winner", winner=winner_name)
        body_text = f"🏆 **{body}**"
    elif "winning_faction" in summary:
        body = text.get("match.winner", winner=summary["winning_faction"])
        body_text = f"🏆 **{body}**"
    else:
        body = text.get("match.draw")
        body_text = f"🤝 **{body}**"

    container.add_text(TextDisplay(markdown_content=body_text))
    container.add_separator()

    lines = []
    for player in players:
        result = outcome.results.get(player.seat, "—")
        role = f" ({player.role_key})" if player.role_key else ""

        if result == "win":
            res_emoji = "🏆"
            res_text = "Win"
        elif result == "loss":
            res_emoji = "❌"
            res_text = "Loss"
        elif result == "draw":
            res_emoji = "🤝"
            res_text = "Draw"
        else:
            res_emoji = "🔹"
            res_text = str(result).capitalize() if result else "—"

        lines.append(f"• **{player.display_name}**{role} — {res_emoji} {res_text}")

    container.add_text(TextDisplay(markdown_content="\n".join(lines)))
    view.add_container(container)

    row = ActionRow()
    row.add_button(
        Button(
            source="vote",
            label=text.get("match.rematch_label"),
            style=ButtonStyle.PRIMARY,
            route_prefix=P.REMATCH,
            resource_id=thread_id,
        )
    )
    row.add_button(
        Button(
            source="open",
            label=text.get("match.view_replay_label"),
            style=ButtonStyle.SECONDARY,
            route_prefix=P.R_NAV,
            resource_id=match_id,
            payload={"frame": 0, "owner": owner_id},
        )
    )
    view.add_action_row(row)
    return view
