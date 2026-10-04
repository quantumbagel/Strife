from __future__ import annotations

import random

import pytest

from strife.engine.players import Player
from strife.games.chess import Chess


def _players(*names: str) -> list[Player]:
    return [
        Player(seat=i, user_id=100 + i, display_name=name)
        for i, name in enumerate(names)
    ]


@pytest.fixture
def chess_players() -> list[Player]:
    return _players("White", "Black")


def test_clock_loss_outcome_when_time_control_active(chess_players: list[Player]) -> None:
    pytest.importorskip("chess")
    game = Chess(chess_players, {"clock_minutes": 5}, random.Random(0))
    assert game.time_control_active

    outcome = game._clock_loss_outcome(loser_seat=0)
    assert outcome.summary == {"winner": 1, "reason": "timeout"}
    assert outcome.results == {1: "win", 0: "loss"}
    assert "won on time" in outcome.description
    assert outcome.player_descriptions == {1: "Won on time", 0: "Lost on time"}


def test_clock_loss_outcome_when_time_control_not_active(chess_players: list[Player]) -> None:
    pytest.importorskip("chess")
    game = Chess(chess_players, {"clock_minutes": 0}, random.Random(0))
    assert not game.time_control_active

    outcome = game.forfeit_end_outcome(0, reason="timeout")
    assert outcome.player_descriptions[1] == "Opponent timed out"
    assert outcome.player_descriptions[0] == "Timed out"
    assert outcome.summary.get("winner") == 1


def test_clock_loop_and_forfeit_timeout_paths_match(chess_players: list[Player]) -> None:
    pytest.importorskip("chess")
    game = Chess(chess_players, {"clock_minutes": 1}, random.Random(0))
    assert game.time_control_active

    loop_outcome = game._clock_loss_outcome(loser_seat=1)
    forfeit_outcome = game.forfeit_end_outcome(1, reason="timeout")

    assert forfeit_outcome == loop_outcome
