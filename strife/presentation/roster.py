from __future__ import annotations

from strife.presentation.emoji import EmojiResolver


def player_mention(*, user_id: int | None, display_name: str, is_bot: bool) -> str:
    if is_bot or user_id is None:
        return f"**{display_name}**"
    return f"<@{user_id}>"


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
    prefix = ""
    if user_id is not None and not is_bot:
        owners = owner_ids or frozenset()
        if user_id in owners:
            prefix += f"{emoji.get('admin')} "
        if creator_id is not None and user_id == creator_id:
            prefix += f"{emoji.get('creator')} "
        line = f"{prefix}<@{user_id}>"
    else:
        difficulty = f" ({bot_difficulty})" if bot_difficulty else ""
        line = f"**{display_name}**{difficulty}"
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
