"""Platform emoji names that games may resolve with ``ctx.emoji.get(..., base=True)``.

Files live in ``assets/emoji/<name>.webp``. Plugin art does **not** belong here;
put it in the plugin's own ``emoji/`` folder so it is registered as
``{game_key}_{stem}``.
"""

from __future__ import annotations

BASE_EMOJIS = frozenset(
    {
        "admin",
        "ban",
        "bot",
        "bot_indicator",
        "bullet",
        "clueless",
        "configure",
        "creator",
        "difficulty",
        "error",
        "explosion",
        "external_link",
        "facepalm",
        "first",
        "first_move",
        "forward",
        "game",
        "github",
        "hmm",
        "join",
        "kick",
        "last",
        "learn",
        "leave",
        "loading",
        "logo",
        "next",
        "peek",
        "play",
        "playcord",
        "pointing",
        "previous",
        "private",
        "public",
        "ready",
        "rematch",
        "restart",
        "settings",
        "space",
        "spectate",
        "success",
        "time",
        "timer",
        "user",
    }
)
