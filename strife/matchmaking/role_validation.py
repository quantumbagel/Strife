from __future__ import annotations

import logging
import random
from typing import TYPE_CHECKING

from strife.engine.metadata import GameMetadata, supports_role_selection
from strife.engine.players import Player
from strife.matchmaking.lobby import Lobby

if TYPE_CHECKING:
    from strife.engine.game import Game

log = logging.getLogger(__name__)


def role_assignment(lobby: Lobby) -> dict[int, str]:
    return {
        member.user_id: lobby.role_selection[member.user_id]
        for member in lobby.members
        if member.user_id in lobby.role_selection
    }


def is_role_selection_complete(lobby: Lobby, meta: GameMetadata) -> bool:
    if not supports_role_selection(meta):
        return True
    return all(member.user_id in lobby.role_selection for member in lobby.members)


def staging_game(game_cls: type[Game], lobby: Lobby, meta: GameMetadata) -> Game:
    return game_cls(
        players=[
            Player(
                seat=index,
                user_id=member.user_id,
                display_name=member.display_name,
                is_bot=False,
            )
            for index, member in enumerate(lobby.members)
        ],
        settings=lobby.settings,
        rng=random.Random(0),
    )


def invalid_role_reason(
    lobby: Lobby,
    meta: GameMetadata,
    game_cls: type[Game] | None,
) -> str | None:
    if game_cls is None or not supports_role_selection(meta):
        return None
    if not is_role_selection_complete(lobby, meta):
        return None
    try:
        game = staging_game(game_cls, lobby, meta)
        ok, reason = game.validate_roles(role_assignment(lobby))
        if ok:
            return None
        return reason or "Invalid role assignment."
    except Exception as e:
        log.exception("Error during role validation staging or execution")
        return f"Error validating roles: {e}"


def clear_ready_if_roles_invalid(
    lobby: Lobby,
    meta: GameMetadata,
    game_cls: type[Game] | None,
) -> None:
    if invalid_role_reason(lobby, meta, game_cls) is not None:
        lobby.ready.clear()
