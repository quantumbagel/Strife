from __future__ import annotations

from strife.engine.players import Player
from strife.presentation.emoji import EmojiResolver
from strife.presentation.mentions import format_discord_mention


def player_mention(
    *,
    user_id: int | None,
    display_name: str,
    is_bot: bool,
    bot_difficulty: str | None = None,
    emoji: EmojiResolver | None = None,
) -> str:
    return format_discord_mention(
        Player(
            seat=-1,
            user_id=user_id,
            display_name=display_name,
            is_bot=is_bot,
            bot_difficulty=bot_difficulty,
        ),
        emoji,
    )


def bot_label(
    emoji: EmojiResolver,
    name: str,
    *,
    difficulty: str | None = None,
) -> str:
    return player_mention(
        user_id=None,
        display_name=name,
        is_bot=True,
        bot_difficulty=difficulty,
        emoji=emoji,
    )


def member_line(
    emoji: EmojiResolver,
    *,
    user_id: int | None,
    display_name: str,
    is_bot: bool = False,
    bot_difficulty: str | None = None,
    owner_ids: frozenset[int] | set[int] | None = None,
    creator_id: int | None = None,
    suffix: str | None = None,
) -> str:
    p = Player(
        seat=-1,
        user_id=user_id,
        display_name=display_name,
        is_bot=is_bot,
        bot_difficulty=bot_difficulty,
    )
    prefix = ""
    if creator_id is not None and user_id == creator_id:
        prefix += f"{emoji.get('creator', base=True)} "
    if owner_ids is not None and user_id is not None and user_id in owner_ids:
        prefix += f"{emoji.get('admin', base=True)} "
    line = f"{prefix}{format_discord_mention(p, emoji)}"
    if suffix:
        line = f"{line} {suffix}"
    return line


def format_roster(
    emoji: EmojiResolver,
    lines: list[str],
    *,
    title: str | None = None,
) -> str:
    body = "\n".join(lines) if lines else "_Empty_"
    if title:
        return f"**{title}**\n{body}"
    return body
