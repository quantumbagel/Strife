from __future__ import annotations

import random
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from strife.engine.metadata import PlayerCount
from strife.engine.outcomes import forfeit_outcome
from strife.engine.players import Move, Player
from strife.games.coup import GAME as Coup
from strife.games.mafia import GAME as Mafia
from strife.games.spyfall import GAME as Spyfall
from strife.lifecycle.rematch import RematchManager, RematchOffer
from strife.lifecycle.timeout import (
    ResolvedTimeoutConsequence,
    determine_consequence,
    will_removal_end_game,
)
from strife.matchmaking.finalizer import SessionFinalizer
from strife.matchmaking.registries import SessionRegistries, UserLocation

from tests.test_registries import _FakeSession


def _players(*names: str, bots: set[int] | None = None) -> list[Player]:
    bots = bots or set()
    return [
        Player(seat=i, user_id=None if i in bots else 100 + i, display_name=name, is_bot=i in bots)
        for i, name in enumerate(names)
    ]


class _FakeGame:
    def __init__(self, n: int, *, min_players: int, removal: bool, bots: bool) -> None:
        self._alive = set(range(n))
        self.metadata = SimpleNamespace(
            supports_player_removal=removal,
            supports_bots=bots,
            player_count=PlayerCount(minimum=min_players, maximum=12),
        )

    def active_seats(self) -> set[int]:
        return set(self._alive)


class _FakeSessionState:
    def __init__(self, players: list[Player], game: _FakeGame) -> None:
        self.players = players
        self.game = game
        self.pending: dict = {}


def test_will_removal_not_use_lobby_min_players() -> None:
    players = _players("A", "B", "C", "D")
    session = _FakeSessionState(players, _FakeGame(4, min_players=4, removal=True, bots=True))
    assert will_removal_end_game(session, 0) is False


def test_forfeit_two_humans_with_bots_removes_instead_of_ending() -> None:
    players = _players("A", "B", "Bot1", "Bot2", "Bot3", "Bot4", bots={2, 3, 4, 5})
    session = _FakeSessionState(players, _FakeGame(6, min_players=4, removal=True, bots=True))
    assert determine_consequence(session, 0, reason="forfeit") == ResolvedTimeoutConsequence.REMOVED


def test_forfeit_last_human_ends_game() -> None:
    players = _players("A", "Bot", bots={1})
    session = _FakeSessionState(players, _FakeGame(2, min_players=2, removal=True, bots=True))
    assert determine_consequence(session, 0, reason="forfeit") == ResolvedTimeoutConsequence.GAME_ENDS


def test_forfeit_outcome_does_not_award_already_dead() -> None:
    players = _players("A", "B", "C")
    outcome = forfeit_outcome(players, 0, alive_seats={0, 1}, reason="timeout", must_end=True)
    assert outcome is not None
    assert outcome.results == {0: "loss", 1: "win", 2: "loss"}
    assert outcome.player_descriptions[2] == "Removed from play"


def test_mafia_forfeit_uses_faction_winner() -> None:
    players = _players("M1", "M2", "T1", "T2")
    game = Mafia(players, {"mafia_count": 2, "enable_doctor": False, "enable_detective": False}, random.Random(0))
    mafia_seats = [p.seat for p in players if game.role[p.seat] == "mafia"]
    town_seats = [p.seat for p in players if game.role[p.seat] != "mafia"]
    # Remove one town so the remaining town forfeit yields mafia parity.
    game.alive.discard(town_seats[0])
    outcome = game.forfeit_end_outcome(town_seats[1], "forfeit")
    for seat in mafia_seats:
        assert outcome.results[seat] == "win"
    for seat in town_seats:
        assert outcome.results[seat] == "loss"


def test_spyfall_spy_forfeit_is_villager_win() -> None:
    players = _players("A", "B", "C")
    game = Spyfall(players, {}, random.Random(1))
    outcome = game.forfeit_end_outcome(game.spy, "forfeit")
    assert outcome.results[game.spy] == "loss"
    assert all(outcome.results[p.seat] == "win" for p in players if p.seat != game.spy)


def test_spyfall_bots_must_pass_to_end_discussion() -> None:
    players = _players("A", "B", "Bot", bots={2})
    game = Spyfall(players, {}, random.Random(2))
    game._passes = {0, 1}
    assert game._discussion_round_done() is False
    game._passes.add(2)
    assert game._discussion_round_done() is True


def test_chess_illegal_attempt_consumes_clock_without_increment() -> None:
    pytest.importorskip("chess")
    from strife.games.chess import Chess

    players = _players("White", "Black")
    game = Chess(players, {"clock_minutes": 1, "increment_seconds": 5}, random.Random(0))
    start = datetime(2020, 1, 1, tzinfo=timezone.utc).replace(tzinfo=None)
    game.last_move_time = start
    move = Move(
        actor_seat=0,
        source="move",
        args={"move": "zzzz"},
        created_at=start + timedelta(seconds=10),
    )
    game._consume_thinking_time(move)
    assert game.clocks[0] == 50.0
    game._tick_clock(
        Move(
            actor_seat=0,
            source="e2e4",
            args={},
            created_at=start + timedelta(seconds=12),
        )
    )
    assert game.clocks[0] == 53.0


def test_coup_timeout_at_ten_coins_picks_a_coup_target() -> None:
    players = _players("A", "B", "C")
    game = Coup(players, {}, random.Random(3))
    game.coins[0] = 10
    game.selected_target = "2"
    assert game._timeout_coup_target(0) == 2
    game.selected_target = None
    assert game._timeout_coup_target(0) in {1, 2}


def test_coup_reaction_hint_is_sequential() -> None:
    players = _players("A", "B")
    game = Coup(players, {}, random.Random(4))
    game.current_action = "assassinate"
    game.state_phase = "challenge_window"
    assert "Block" not in game._reaction_hint()
    game.state_phase = "block_window"
    assert "target can Block" in game._reaction_hint()


async def test_session_complete_preserves_occupancy_in_another_match() -> None:
    regs = SessionRegistries()
    old = _FakeSession(
        1,
        22,
        [Player(seat=0, user_id=7, display_name="A"), Player(seat=1, user_id=8, display_name="B")],
    )
    regs.active_games[1] = old  # type: ignore[assignment]
    await regs.reserve_user(7, UserLocation("game", 2, 22))
    await regs.reserve_user(8, UserLocation("game", 1, 22))
    finalizer = SessionFinalizer(registries=regs, matches=None, users=None, guilds=None)  # type: ignore[arg-type]
    await finalizer.session_complete(old)  # type: ignore[arg-type]
    loc = regs.location_of(7)
    assert loc is not None
    assert loc.thread_id == 2
    assert regs.location_of(8) is None
    assert regs.get_game(1) is None


async def test_rematch_vote_lock_launches_once() -> None:
    class _Lobby:
        pass

    manager = RematchManager(SessionRegistries(), _Lobby(), SimpleNamespace())  # type: ignore[arg-type]
    manager._offers[1] = RematchOffer(eligible={10, 20}, expires=time.monotonic() + 60)
    launches = 0

    async def _reset(thread_id: int, offer: RematchOffer) -> None:
        nonlocal launches
        launches += 1

    manager._reset_to_lobby = _reset  # type: ignore[method-assign]
    await manager.vote(1, 10)
    await manager.vote(1, 20)
    assert launches == 1
    assert 1 not in manager._offers
