from __future__ import annotations

from typing import Any

from strife.engine.players import Player, set_mention_formatter
from strife.presentation.emoji_context import active_emoji


def format_discord_mention(player: Player, emoji: Any = None) -> str:
    """Discord mention markup, bold names, and the bot-indicator emoji."""
    if player.user_id is not None and not player.is_bot:
        base = f"<@{player.user_id}>"
    else:
        difficulty = f" ({player.bot_difficulty})" if player.bot_difficulty else ""
        base = f"**{player.display_name}**{difficulty}"
    resolver = emoji or active_emoji()
    if (player.is_bot or player.user_id is None) and resolver is not None:
        return f"{resolver.get('bot_indicator', base=True)} {base}"
    return base


def install_discord_mentions() -> None:
    set_mention_formatter(format_discord_mention)
