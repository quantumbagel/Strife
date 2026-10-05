from __future__ import annotations

import random

from strife.engine.log import LogEntryKind
from strife.engine.players import Move, Player
from strife.games.coup import GAME as Coup
from strife.games.coup import META as CoupMeta
from strife.games.coup.bot import choose_move


def _players(*names: str) -> list[Player]:
    return [
        Player(seat=i, user_id=100 + i, display_name=name, is_bot=False)
        for i, name in enumerate(names)
    ]


def test_steal_after_target_exiled_uses_coin_snapshot() -> None:
    players = _players("A", "B")
    game = Coup(players, {}, random.Random(0))
    game.coins[0] = 2
    game.coins[1] = 0
    game.alive.discard(1)
    game._replay_apply_action_effects(0, "steal", 1, steal_snapshot=4)
    assert game.coins[0] == 4


def test_bot_blocks_assassinate_in_block_window_not_challenge() -> None:
    players = _players("A", "B")
    game = Coup(players, {}, random.Random(0))
    game.current_actor = 0
    game.current_action = "assassinate"
    game.current_target = 1
    game.state_phase = "challenge_window"
    game.hands[1] = ["contessa", "duke"]
    move = choose_move(game, "hard", 1)
    assert move.source in ("challenge", "pass")

    game.state_phase = "block_window"
    move = choose_move(game, "hard", 1)
    assert move.source == "block_contessa"


def test_supports_player_removal_and_remove_player() -> None:
    assert CoupMeta.supports_player_removal is True
    players = _players("A", "B", "C")
    game = Coup(players, {}, random.Random(1))
    game.hands[1] = ["duke", "captain"]
    game.coins[1] = 5
    game.remove_player(1)
    assert 1 not in game.alive
    assert game.hands[1] == []
    assert game.revealed[1] == ["duke", "captain"]
    assert game.coins[1] == 0


class _ExchangeForfeitCtx:
    """Seat 0 declares Exchange, nobody challenges, then forfeits while choosing cards."""

    started_at = None
    is_replay = False

    class emoji:
        @staticmethod
        def get(name: str, base: bool = False) -> str:
            return "·"

    def __init__(self, game: Coup) -> None:
        self.game = game
        self.turns = 0

    def is_bot(self, seat: int) -> bool:
        return True

    async def update(self, view) -> None:
        pass

    async def send_private(self, seat, view) -> None:
        pass

    async def record_event(self, source, arguments) -> None:
        pass

    async def request_inputs(self, view, *, actors, **kwargs):
        return {s: Move(actor_seat=s, source="pass", args={}) for s in actors}

    async def request_input(self, view, *, actor, sources, **kwargs):
        if "exchange_select" in sources:
            self.game.remove_player(actor)
            return Move(actor_seat=actor, source="forfeit", args={})
        self.turns += 1
        if self.turns > 1:
            raise _Stop()
        return Move(
            actor_seat=actor, source="submit_action", args={"action": "exchange"}
        )


class _Stop(Exception):
    pass


async def test_exchange_forfeit_returns_only_drawn_cards() -> None:
    import pytest

    players = _players("A", "B", "C")
    game = Coup(players, {}, random.Random(2))
    game.current = 0
    total = len(game.deck) + sum(len(h) for h in game.hands.values())
    ctx = _ExchangeForfeitCtx(game)
    with pytest.raises(_Stop):
        await game.play(ctx)
    assert 0 not in game.alive
    assert len(game.revealed[0]) == 2
    held = len(game.deck) + sum(len(h) for h in game.hands.values())
    assert held + sum(len(r) for r in game.revealed.values()) == total


class _ReplayCtx:
    started_at = None
    is_replay = True

    class emoji:
        @staticmethod
        def get(name: str, base: bool = False) -> str:
            return "·"


async def test_parse_replay_system_timeout_does_not_apply_coin_penalty() -> None:
    players = _players("A", "B")
    game = Coup(players, {}, random.Random(3))
    moves = [
        Move(
            None,
            "deal",
            {
                "hands": {0: ["duke", "duke"], 1: ["captain", "captain"]},
                "deck": game.deck,
                "current": 0,
                "coins": {0: 2, 1: 2},
            },
        ),
        Move(0, "timeout", {"reason": "timeout"}, kind=LogEntryKind.SYSTEM),
        Move(
            0,
            "turn_skip",
            {"player": 0, "penalty": True, "coins": {0: 1, 1: 2}},
        ),
    ]
    frames = await game.parse_replay(moves, _ReplayCtx())  # type: ignore[arg-type]
    assert game.coins[0] == 1
    assert len(frames) >= 2


async def test_parse_replay_forfeit_removes_seat() -> None:
    players = _players("A", "B", "C")
    game = Coup(players, {}, random.Random(4))
    moves = [
        Move(
            None,
            "deal",
            {
                "hands": {i: ["duke", "duke"] for i in range(3)},
                "deck": game.deck,
                "current": 0,
                "coins": {i: 2 for i in range(3)},
            },
        ),
        Move(
            1,
            "forfeit",
            {"reason": "forfeit", "removed": True},
            kind=LogEntryKind.SYSTEM,
        ),
        Move(0, "action_declare", {"player": 0, "type": "income", "target": None}),
        Move(
            None,
            "action_resolve",
            {
                "coins": {0: 3, 1: 0, 2: 2},
                "hands": {i: ["duke", "duke"] for i in range(3)},
                "revealed": {i: [] for i in range(3)},
                "alive": [0, 2],
                "deck": game.deck,
                "history": ["income"],
            },
        ),
    ]
    await game.parse_replay(moves, _ReplayCtx())  # type: ignore[arg-type]
    assert 1 not in game.alive
