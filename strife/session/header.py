from __future__ import annotations

from strife.config.text import TextConfig
from strife.engine.players import Player
from strife.presentation.components import Container, LayoutView, Separator, TextDisplay, TextSize
from strife.presentation.emoji import EmojiResolver
from strife.presentation.roster import member_line


def discord_relative_timestamp(unix_seconds: int) -> str:
    return f"<t:{unix_seconds}:R>"


def build_game_thread_header_view(
    *,
    players: list[Player],
    text: TextConfig,
    emoji: EmojiResolver,
    pending_seats: list[int] | None = None,
    deadline_unix: int | None = None,
    wait_description: str | None = None,
    line_descriptions: dict[int, str] | None = None,
    timeout_consequence: str | None = None,
    finished: bool = False,
    owner_ids: frozenset[int] = frozenset(),
) -> LayoutView:
    """Build the pinned roster/status message at the top of a game thread."""
    view = LayoutView()
    container = Container()
    container.add_separator()

    roster_lines = [
        member_line(
            emoji,
            user_id=player.user_id,
            display_name=player.display_name,
            is_bot=player.is_bot,
            bot_difficulty=player.bot_difficulty,
            owner_ids=owner_ids,
        )
        for player in players
    ]
    container.add_text(
        TextDisplay(
            markdown_content=f"{text.get('lobby.players_title')}\n" + "\n".join(roster_lines),
            size_style=TextSize.BODY,
        )
    )
    container.add_separator(Separator(visible=False))

    if finished:
        status = f"-# {emoji.get('success', base=True)} {text.get('lobby.game_finished')}"
    elif pending_seats:
        deadline = discord_relative_timestamp(deadline_unix or 0)
        header = wait_description or text.get("lobby.awaiting_actions")
        action_lines = []
        for seat in pending_seats:
            line_desc = line_descriptions.get(seat) if line_descriptions else None
            if line_desc:
                action_lines.append(
                    text.get(
                        "lobby.awaiting_action_line_described",
                        player=players[seat].mention_for(emoji),
                        description=line_desc,
                        timestamp=deadline,
                    )
                )
            else:
                action_lines.append(
                    text.get(
                        "lobby.awaiting_action_line",
                        player=players[seat].mention_for(emoji),
                        timestamp=deadline,
                    )
                )
        status = f"-# {emoji.get('timer', base=True)} {header}\n" + "\n".join(action_lines)
        if timeout_consequence:
            status += (
                f"\n-# {text.get('lobby.timeout_consequence_hint', consequence=timeout_consequence)}"
            )
    else:
        status = f"-# {emoji.get('loading', base=True)} {text.get('lobby.game_in_progress')}"

    container.add_text(
        TextDisplay(
            markdown_content=status,
            size_style=TextSize.BODY,
        )
    )
    view.add_container(container)
    return view
