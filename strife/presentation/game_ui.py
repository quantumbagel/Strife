from __future__ import annotations

from typing import TYPE_CHECKING

from strife.engine.players import Player
from strife.presentation.components import ActionRow, Container, LayoutView
from strife.presentation.emoji import EmojiResolver
from strife.presentation.style import add_header, add_spacer, notice_view

if TYPE_CHECKING:
    from strife.engine.context import GameContext


def action_status(
    ctx: GameContext,
    player: Player,
    *,
    prefix_emoji: str | None = None,
) -> str:
    """Format a standard ``{emoji} {mention} to act`` status line."""
    emoji_resolver: EmojiResolver = ctx.emoji
    prefix = f"{emoji_resolver.get(prefix_emoji)} " if prefix_emoji else ""
    label = player.mention_for(emoji_resolver)
    return f"{prefix}{label} to act"


def message_lead(
    container: Container,
    text: str | None,
    *,
    emoji: EmojiResolver | None = None,
    prefix_emoji: str | None = None,
) -> Container:
    """Add optional contextual lead text above game content."""
    if text is None:
        return container
    icon = emoji.get(prefix_emoji) if emoji and prefix_emoji else None
    add_header(container, text, emoji=icon)
    add_spacer(container)
    return container


def game_container(
    ctx: GameContext,
    *,
    lead: str | None = None,
    prefix_emoji: str | None = None,
) -> Container:
    """Create a container with an optional contextual lead line."""
    emoji_resolver: EmojiResolver = ctx.emoji
    container = Container()
    message_lead(container, lead, emoji=emoji_resolver, prefix_emoji=prefix_emoji)
    return container


def add_controls(container: Container, ctx: GameContext, row: ActionRow) -> None:
    """Add an action row only when the view is live (hidden in replay)."""
    if ctx.is_replay:
        return
    container.add_action_row(row)


def query_panel(
    ctx: GameContext,
    *,
    title: str,
    prefix_emoji: str | None = None,
    body: str | None = None,
    sections: list[tuple[str, str]] | None = None,
) -> LayoutView:
    """Ephemeral peek / notice panel matching platform command styling.

    Send with ``await ctx.respond_query(view)`` from ``handle_query``.
    """
    emoji_resolver: EmojiResolver = ctx.emoji
    icon = emoji_resolver.get(prefix_emoji) if prefix_emoji else None
    return notice_view(title=title, emoji=icon, body=body, sections=sections)
