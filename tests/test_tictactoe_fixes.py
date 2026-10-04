from __future__ import annotations

import asyncio
import random
from unittest.mock import MagicMock

import pytest

from strife.engine.log import LogEntryKind
from strife.engine.players import Move, Player
from strife.games.tictactoe.game import TicTacToe


def _players(*names: str) -> list[Player]:
    return [
        Player(seat=i, user_id=100 + i, display_name=name)
        for i, name in enumerate(names)
    ]


@pytest.mark.asyncio
async def test_tictactoe_timeout_skips_seat() -> None:
    players = _players("X", "O")
    game = TicTacToe(
        players,
        {"first_move": "creator", "creator_id": players[0].user_id},
        random.Random(0),
    )
    assert game.current == 0

    seats_at_take_turn: list[int] = []

    async def mock_take_turn(ctx, sources):
        seats_at_take_turn.append(game.current)
        if len(seats_at_take_turn) == 1:
            return Move(
                actor_seat=0,
                source="timeout",
                args={},
                kind=LogEntryKind.SYSTEM,
            )
        raise asyncio.CancelledError()

    game.take_turn = mock_take_turn
    ctx = MagicMock()

    with pytest.raises(asyncio.CancelledError):
        await game.play(ctx)

    assert seats_at_take_turn == [0, 1]
    assert game.current == 1
