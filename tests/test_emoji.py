from __future__ import annotations

from strife.config.emoji import EmojiConfig, EmojiEntry
from strife.presentation.base_emojis import BASE_EMOJIS
from strife.presentation.emoji import EmojiResolver, plugin_emoji_name


def _resolver(**entries: str) -> EmojiResolver:
    config = EmojiConfig(
        entries={
            name: EmojiEntry(fallback=fallback)
            for name, fallback in entries.items()
        }
    )
    return EmojiResolver(config)


def test_plugin_emoji_name_prefixes_and_is_idempotent() -> None:
    assert plugin_emoji_name("coup", "duke") == "coup_duke"
    assert plugin_emoji_name("coup", "coup_duke") == "coup_duke"
    assert plugin_emoji_name("liars_dice", "die_1") == "liars_dice_die_1"


def test_host_get_uses_raw_name() -> None:
    resolver = _resolver(loading="⏳", error="❌")
    assert resolver.get("loading") == "⏳"
    assert resolver.get("loading", base=True) == "⏳"


def test_game_get_uses_namespaced_plugin_name() -> None:
    resolver = _resolver(coup_duke="👑", loading="⏳", error="❌")
    bound = resolver.bind_game("coup")
    assert bound.get("duke") == "👑"


def test_unbound_get_does_not_resolve_plugin_stem() -> None:
    resolver = _resolver(tictactoe_x="X", error="❌")
    assert resolver.get("x") == "❌"
    assert resolver.bind_game("tictactoe").get("x") == "X"


def test_game_get_base_true_uses_platform_set() -> None:
    resolver = _resolver(loading="⏳", coup_loading="🎰", error="❌")
    bound = resolver.bind_game("coup")
    assert bound.get("loading") == "🎰"
    assert bound.get("loading", base=True) == "⏳"


def test_game_get_falls_back_to_base_when_plugin_has_no_file() -> None:
    resolver = _resolver(loading="⏳", error="❌")
    bound = resolver.bind_game("coup")
    assert bound.get("loading") == "⏳"


def test_base_true_rejects_unknown_platform_name() -> None:
    resolver = _resolver(error="❌", coup_duke="👑")
    bound = resolver.bind_game("coup")
    assert bound.get("duke", base=True) == "❌"
    assert bound.get("not_a_base", base=True) == "❌"


def test_get_game_emoji_uses_plugin_game_file() -> None:
    resolver = _resolver(chess_game="♟️", game="🎲", error="❌")
    assert resolver.get_game_emoji("chess") == "♟️"
    assert resolver.get_game_emoji("missing") == "🎲"


def test_base_emojis_covers_loading_and_not_plugin_stems() -> None:
    assert "loading" in BASE_EMOJIS
    assert "error" in BASE_EMOJIS
    assert "duke" not in BASE_EMOJIS
    assert "die_1" not in BASE_EMOJIS
