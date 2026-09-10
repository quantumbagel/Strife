from __future__ import annotations

from strife.config.emoji import EmojiConfig, EmojiEntry
from strife.config.text import TextConfig
from strife.engine.metadata import BotSpec, GameMetadata, PlayerCount
from strife.matchmaking.lobby import Lobby, QueuedBot
from strife.matchmaking.settings_view import build_settings_view
from strife.presentation.components import Select, walk_interactive
from strife.presentation.emoji import EmojiResolver


def _emoji() -> EmojiResolver:
    return EmojiResolver(
        EmojiConfig(
            entries={
                "bot_indicator": EmojiEntry(id=1525857716587724850),
                "bot": EmojiEntry(id=1),
                "leave": EmojiEntry(id=2),
                "error": EmojiEntry(fallback="❓"),
            }
        )
    )


def test_remove_bot_select_uses_plain_names() -> None:
    """Select labels/descriptions cannot render markdown or custom-emoji mentions."""
    lobby = Lobby(
        thread_id=1,
        guild_id=2,
        channel_id=3,
        game_key="test",
        creator_id=10,
        private=False,
        bots=[
            QueuedBot(name="Bot-hard-1", difficulty="hard"),
            QueuedBot(name="Bot-hard-2", difficulty="hard"),
        ],
    )
    meta = GameMetadata(
        key="test",
        name="Test",
        player_count=PlayerCount(fixed=4),
        bots=(BotSpec(difficulty="hard", description="Tough"),),
    )
    text = TextConfig(
        {
            "lobby": {
                "remove_bot_desc": "Remove {name} from the lobby",
            }
        }
    )
    view = build_settings_view(lobby, meta, _emoji(), text)
    remove = next(item for item in walk_interactive(view) if isinstance(item, Select) and item.source == "bot_remove")

    assert [choice.label for choice in remove.choices] == ["Bot-hard-1", "Bot-hard-2"]
    assert [choice.description for choice in remove.choices] == [
        "Remove Bot-hard-1 from the lobby",
        "Remove Bot-hard-2 from the lobby",
    ]
    for choice in remove.choices:
        assert "bot_indicator" not in choice.label
        assert "bot_indicator" not in (choice.description or "")
        assert "**" not in choice.label
        assert "**" not in (choice.description or "")
        assert choice.emoji == "leave"
