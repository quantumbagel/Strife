from __future__ import annotations

from strife.config.emoji import EmojiConfig, EmojiEntry
from strife.config.text import TextConfig
from strife.engine.metadata import GameMetadata, PlayerCount
from strife.matchmaking.lobby import Lobby, LobbyMember
from strife.matchmaking.lobby_view import build_lobby_view
from strife.presentation.components import Select, TextDisplay, walk_interactive
from strife.presentation.emoji import EmojiResolver


def _emoji() -> EmojiResolver:
    return EmojiResolver(EmojiConfig(entries={"user": EmojiEntry(fallback="👤")}))


def _text() -> TextConfig:
    return TextConfig(
        {
            "lobby": {
                "lobby_full_join_hint": "-# Lobby is full. Leave or remove a bot to free a seat.",
                "approve_full_hint": "-# Lobby is full. Free a seat before approving join requests.",
                "join_requests_creator_hint": "-# Only the lobby creator can approve or deny join requests.",
                "join_requests_title": "### {user_emoji} Join Requests ({count})",
                "approve_request_select_desc": "Approve",
                "approve_request_placeholder": "Approve...",
                "approve_request_desc": "Approve",
                "deny_request_select_desc": "Deny",
                "deny_request_placeholder": "Deny...",
                "deny_request_desc": "Deny",
            }
        }
    )


def _lobby(**kwargs) -> Lobby:
    defaults = dict(
        thread_id=1,
        guild_id=2,
        channel_id=3,
        game_key="coup",
        creator_id=10,
        private=True,
        members=[LobbyMember(10, "Host"), LobbyMember(11, "Guest")],
        pending_requests={12: "Waiter"},
    )
    defaults.update(kwargs)
    return Lobby(**defaults)


def _texts(view) -> list[str]:
    texts: list[str] = []
    for container in view.containers:
        for child in container.children:
            if isinstance(child, TextDisplay):
                texts.append(child.markdown_content)
    return texts


def test_full_lobby_explains_missing_join_and_approve() -> None:
    meta = GameMetadata(key="coup", name="Coup", player_count=PlayerCount(fixed=2))
    view = build_lobby_view(_lobby(), meta, _emoji(), _text())
    body = "\n".join(_texts(view))
    assert "Lobby is full. Leave or remove a bot" in body
    assert "Free a seat before approving" in body
    assert "Only the lobby creator can approve" in body
    sources = {item.source for item in walk_interactive(view) if isinstance(item, Select)}
    assert "approve" not in sources
    assert "deny" in sources
