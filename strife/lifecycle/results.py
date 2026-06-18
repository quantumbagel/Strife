from __future__ import annotations

from strife.config.text import TextConfig
from strife.engine.players import GameOutcome, Player
from strife.presentation.components import (
    ActionRow,
    Button,
    ButtonStyle,
    Container,
    LayoutView,
    Separator,
    TextDisplay,
)
from strife.routing import prefixes as P


def build_results_view(
    *,
    game_name: str,
    outcome: GameOutcome,
    players: list[Player],
    thread_id: int,
    match_id: int,
    owner_id: int,
    text: TextConfig,
) -> LayoutView:
    view = LayoutView()
    view.children.append(
        TextDisplay(markdown_content=f"## ✅ {text.get('match.result_title', game_name=game_name)}")
    )
    container = Container()
    summary = outcome.summary or {}
    if summary.get("winner") is None and "winning_faction" not in summary:
        body = text.get("match.draw")
    else:
        body = str(summary)
    container.add_text(TextDisplay(markdown_content=body))
    container.add_separator()
    lines = []
    for player in players:
        result = outcome.results.get(player.seat, "—")
        role = f" ({player.role_key})" if player.role_key else ""
        lines.append(f"**{player.display_name}**{role}: {result}")
    container.add_text(TextDisplay(markdown_content="\n".join(lines)))
    view.add_container(container)

    row = ActionRow()
    row.add_button(
        Button(
            source="vote",
            label="Rematch",
            style=ButtonStyle.PRIMARY,
            route_prefix=P.REMATCH,
            resource_id=thread_id,
        )
    )
    row.add_button(
        Button(
            source="open",
            label="View Replay",
            style=ButtonStyle.SECONDARY,
            route_prefix=P.R_NAV,
            resource_id=match_id,
            payload={"frame": 0, "owner": owner_id},
        )
    )
    view.add_action_row(row)
    return view
