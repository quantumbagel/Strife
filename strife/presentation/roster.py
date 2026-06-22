from __future__ import annotations

from strife.presentation.emoji import EmojiResolver


def player_mention(*, user_id: int | None, display_name: str, is_bot: bool) -> str:
    from strife.engine.players import Player
    p = Player(
        seat=-1,
        user_id=user_id,
        display_name=display_name,
        is_bot=is_bot,
    )
    return str(p)


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
    from strife.engine.players import Player
    p = Player(
        seat=-1,
        user_id=user_id,
        display_name=display_name,
        is_bot=is_bot,
        bot_difficulty=bot_difficulty,
    )
    line = p.display(emoji, owner_ids=owner_ids, creator_id=creator_id)
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
