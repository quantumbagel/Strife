from __future__ import annotations

import asyncio
import random

import pytest

from strife.engine.log import LogEntryKind
from strife.engine.players import Move, Player
from strife.games.liars_dice import META as LiarsDiceMeta
from strife.games.liars_dice.game import LiarsDice
from strife.presentation.components import LayoutView


def _players(*names: str) -> list[Player]:
    return [
        Player(seat=i, user_id=100 + i, display_name=name)
        for i, name in enumerate(names)
    ]


def _game(
    *names: str, dice_count: int = 5, wild_ones: bool = True, seed: int = 0
) -> LiarsDice:
    players = _players(*names)
    game = LiarsDice(
        players,
        {"dice_count": dice_count, "wild_ones": wild_ones},
        random.Random(seed),
    )
    game.current = 0
    return game


class _Emoji:
    def get(self, name: str | None = None, base: bool = False) -> str:
        return name or ""


class ScriptedContext:
    """Quiet MockContext-style host for scripted live play."""

    def __init__(
        self, players: list[Player], scripted: list[tuple[int, str, dict]]
    ) -> None:
        self.players = players
        self.rng = random.Random(0)
        self.settings: dict = {}
        self.emoji = _Emoji()
        self.started_at = None
        self.is_replay = False
        self._scripted = list(scripted)
        self.recorded: list[Move] = []
        self.views: list[LayoutView] = []
        self._turn_index = 0

    def is_bot(self, seat: int) -> bool:
        return self.players[seat].is_bot

    async def update(self, view: LayoutView) -> None:
        self.views.append(view)

    async def request_input(
        self,
        view: LayoutView,
        *,
        actor: int,
        sources=None,
        record: bool = True,
        **kwargs,
    ):
        del sources, kwargs
        self.views.append(view)
        if not self._scripted:
            raise asyncio.CancelledError()
        seat, source, args = self._scripted.pop(0)
        move = Move(
            actor_seat=seat,
            source=source,
            args=dict(args or {}),
            turn_index=self._turn_index,
        )
        if record:
            self.recorded.append(move)
            self._turn_index += 1
        return move

    async def send_private(self, seat: int, view: LayoutView) -> None:
        del seat, view

    async def record_event(self, source: str, arguments: dict) -> None:
        self.recorded.append(
            Move(
                actor_seat=None,
                source=source,
                args=arguments,
                kind=LogEntryKind.GAME,
                turn_index=self._turn_index,
            )
        )
        self._turn_index += 1


class _ReplayCtx:
    started_at = None
    is_replay = True
    emoji = _Emoji()


def _view_text(view: LayoutView) -> str:
    parts: list[str] = []
    for container in view.containers:
        for child in container.children:
            md = getattr(child, "markdown_content", None)
            if md:
                parts.append(md)
    return "\n".join(parts)


def test_supports_player_removal_and_remove_player() -> None:
    assert LiarsDiceMeta.supports_player_removal is True
    game = _game("A", "B", "C")
    game.hands[1] = [2, 3, 4]
    game.remove_player(1)
    assert 1 not in game.alive
    assert 1 not in game.hands
    assert any("left the game" in line for line in game.history)


async def test_forfeit_of_last_bidder_removes_player_and_restarts_round() -> None:
    game = _game("A", "B", "C")
    ctx = ScriptedContext(
        game.players,
        [
            (0, "bid", {"quantity": 1, "value": 2}),
            (0, "forfeit", {"reason": "forfeit", "removed": True}),
        ],
    )
    with pytest.raises(asyncio.CancelledError):
        await game.play(ctx)
    assert 0 not in game.alive
    assert 0 not in game.hands
    assert 1 in game.alive and 2 in game.alive
    assert game.current_bid is None
    assert any("left the game" in line for line in game.history)
    round_starts = [move for move in ctx.recorded if move.source == "round_start"]
    assert len(round_starts) >= 2
    assert 0 not in round_starts[-1].args["hands"]


@pytest.mark.parametrize("source", ["timeout", "pass"])
async def test_timeout_or_pass_with_bid_auto_challenges(source: str) -> None:
    game = _game("A", "B")
    ctx = ScriptedContext(
        game.players,
        [
            (0, "bid", {"quantity": 1, "value": 2}),
            (1, source, {}),
        ],
    )
    with pytest.raises(asyncio.CancelledError):
        await game.play(ctx)
    assert game.last_reveal is not None
    assert game.last_reveal["bid"] == (1, 2)
    assert "actual_count" in game.last_reveal
    assert any("called liar" in line for line in game.history)
    note = "timed out" if source == "timeout" else "auto-passed"
    assert any(note in line for line in game.history)
    assert any(move.source == "challenge_resolve" for move in ctx.recorded)


@pytest.mark.parametrize("source", ["timeout", "pass"])
async def test_timeout_or_pass_without_bid_auto_bids(source: str) -> None:
    game = _game("A", "B")
    ctx = ScriptedContext(
        game.players,
        [(0, source, {})],
    )
    with pytest.raises(asyncio.CancelledError):
        await game.play(ctx)
    assert game.current_bid == (1, 2)
    assert game.last_bidder == 0
    note = "timed out" if source == "timeout" else "auto-passed"
    assert any(note in line and "bid" in line for line in game.history)
    bids = [move for move in ctx.recorded if move.source == "bid"]
    assert bids
    assert bids[0].args["quantity"] == 1
    assert bids[0].args["value"] == 2


async def test_history_order_liar_call_before_elimination() -> None:
    game = _game("A", "B", dice_count=1)
    ctx = ScriptedContext(
        game.players,
        [
            (0, "bid", {"quantity": 1, "value": 2}),
            (1, "challenge", {}),
        ],
    )
    await game.play(ctx)
    liar_idx = next(i for i, line in enumerate(game.history) if "called liar" in line)
    outcome_idx = next(
        i
        for i, line in enumerate(game.history)
        if "was eliminated" in line or "lost 1 die" in line
    )
    assert liar_idx < outcome_idx


async def test_challenge_reveal_visible_on_next_round_view() -> None:
    game = _game("A", "B")
    ctx = ScriptedContext(
        game.players,
        [
            (0, "bid", {"quantity": 1, "value": 2}),
            (1, "challenge", {}),
        ],
    )
    with pytest.raises(asyncio.CancelledError):
        await game.play(ctx)
    assert game.last_reveal is not None
    text = _view_text(ctx.views[-1])
    assert "Last round" in text
    assert "Actual count" in text
    assert "All dice revealed" in text


async def test_parse_replay_forfeit_removes_seat() -> None:
    game = _game("A", "B", "C")
    hands = {0: [2, 3], 1: [4, 5], 2: [6, 6]}
    moves = [
        Move(
            None,
            "round_start",
            {"hands": hands, "dice_counts": {0: 5, 1: 5, 2: 5}},
        ),
        Move(
            1,
            "forfeit",
            {"reason": "forfeit", "removed": True},
            kind=LogEntryKind.SYSTEM,
        ),
        Move(0, "bid", {"player": 0, "quantity": 1, "value": 2}),
    ]
    await game.parse_replay(moves, _ReplayCtx())  # type: ignore[arg-type]
    assert 1 not in game.alive
    assert 1 not in game.hands
    assert any("left the game" in line for line in game.history)


async def test_parse_replay_challenge_frame_includes_hands() -> None:
    game = _game("A", "B")
    hands = {0: [2, 2, 3], 1: [4, 5, 6]}
    moves = [
        Move(None, "round_start", {"hands": hands, "dice_counts": {0: 3, 1: 3}}),
        Move(0, "bid", {"player": 0, "quantity": 1, "value": 2}),
        Move(
            1,
            "challenge_resolve",
            {
                "challenger": 1,
                "bidder": 0,
                "bid": [1, 2],
                "actual_count": 2,
                "loser": 1,
                "verdict": "bidder was correct",
                "hands": hands,
                "history": ["called liar", "lost 1 die"],
            },
        ),
        Move(
            None,
            "round_start",
            {"hands": {0: [1, 2], 1: [3, 4]}, "dice_counts": {0: 3, 1: 2}},
        ),
    ]
    frames = await game.parse_replay(moves, _ReplayCtx())  # type: ignore[arg-type]
    challenge = next(frame for frame in frames if frame.turn_label == "Challenge")
    text = _view_text(challenge.view)
    assert "All dice revealed" in text
    assert "Actual count" in text
    next_round = next(frame for frame in frames if frame.index == challenge.index + 1)
    assert "Last round" in _view_text(next_round.view)
