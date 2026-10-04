from __future__ import annotations

import importlib.util
import random
from pathlib import Path

import pytest

from strife.engine.players import Move, Player
from strife.games.spyfall import GAME as Spyfall
from strife.games.spyfall.bot import choose_move


def _players(*names: str, bots: set[int] | None = None) -> list[Player]:
    bots = bots or set()
    return [
        Player(seat=i, user_id=None if i in bots else 100 + i, display_name=name, is_bot=i in bots)
        for i, name in enumerate(names)
    ]


def test_spy_forfeit_villagers_win() -> None:
    players = _players("A", "B", "C")
    game = Spyfall(players, {}, random.Random(1))
    outcome = game.forfeit_end_outcome(game.spy, "forfeit")
    assert outcome.summary["winner_faction"] == "villagers"
    assert outcome.results[game.spy] == "loss"
    assert all(outcome.results[p.seat] == "win" for p in players if p.seat != game.spy)


def test_villager_forfeit_allows_game_to_continue() -> None:
    players = _players("A", "B", "C")
    game = Spyfall(players, {}, random.Random(2))
    villager = next(s for s in game.alive if s != game.spy)
    outcome = game.forfeit_end_outcome(villager, "forfeit")
    assert outcome is not None
    assert "winner_faction" not in outcome.summary
    assert outcome.summary.get("reason") == "forfeit"
    assert villager not in game.alive
    assert game.spy in game.alive
    assert game.alive - {game.spy}


def test_remove_player_when_spy_leaves_villagers_win() -> None:
    players = _players("A", "B", "C")
    game = Spyfall(players, {}, random.Random(3))
    game.remove_player(game.spy)
    assert game._winner_after_removal() == "villagers"


def test_remove_player_cancels_accusation_when_accused_leaves() -> None:
    players = _players("A", "B", "C")
    game = Spyfall(players, {}, random.Random(4))
    accused = next(s for s in game.alive if s != game.spy)
    game.accused_player = accused
    game.accuser = next(s for s in game.alive if s != accused)
    game.remove_player(accused)
    assert game.accused_player is None
    assert game.accuser is None


def test_bot_skips_accuse_after_already_accusing() -> None:
    players = _players("A", "B", "C")
    game = Spyfall(players, {}, random.Random(5))
    game._accused_seats.add(0)
    game.rng = random.Random(0)
    for _ in range(30):
        move = choose_move(game, "hard", 0)
        assert move.source != "accuse"


def test_spy_bot_always_votes_guilty_when_not_accused() -> None:
    players = _players("A", "B", "C")
    game = Spyfall(players, {}, random.Random(6))
    game.accused_player = 1
    game.spy = 0
    for seed in range(20):
        game.rng = random.Random(seed)
        move = choose_move(game, "hard", 0)
        assert move.source == "vote"
        assert move.args["value"] == "guilty"


def test_spy_bot_never_guesses_location() -> None:
    players = _players("A", "B", "C")
    game = Spyfall(players, {}, random.Random(7))
    game.spy = 0
    for seed in range(50):
        game.rng = random.Random(seed)
        move = choose_move(game, "hard", 0)
        assert move.source != "guess_location"


def _mock_context():
    path = Path(__file__).resolve().parents[1] / "scripts" / "run_game.py"
    spec = importlib.util.spec_from_file_location("run_game", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    from strife.config.emoji import load_emoji_config
    from strife.presentation.emoji import EmojiResolver

    emoji = EmojiResolver(load_emoji_config(Path(__file__).resolve().parents[1] / "config" / "emoji.yaml"))
    return mod.MockContext, emoji


@pytest.mark.asyncio
async def test_repeat_accuse_blocked_without_voting_or_turn_advance() -> None:
    import asyncio

    MockContext, emoji = _mock_context()
    players = _players("A", "B", "C")
    game = Spyfall(players, {}, random.Random(0))
    game.turn = 2
    game._accused_seats.add(0)
    game.pending_accuse[0] = 1

    class AccuseOnceCtx(MockContext):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            self._discussion_rounds = 0

        async def request_inputs(self, view, *, actors, sources=None, until="all", **kwargs):
            if sources and "vote_guilty" in sources:
                raise AssertionError("repeat accuse should not open voting")
            self._discussion_rounds += 1
            if self._discussion_rounds == 1:
                return {0: Move(actor_seat=0, source="accuse", args={})}
            raise asyncio.CancelledError()

    ctx = AccuseOnceCtx(
        rng=random.Random(0),
        players=players,
        settings={},
        emoji=emoji,
        scripted_moves=[],
    )
    with pytest.raises(asyncio.CancelledError):
        await game.play(ctx)
    assert game.turn == 2
    assert game.accused_player is None


def _scripted_ctx(MockContext, emoji, players, handler):
    class Ctx(MockContext):
        async def request_inputs(self, view, *, actors, sources=None, until="all", **kwargs):
            return handler(sources)

    return Ctx(rng=random.Random(0), players=players, settings={}, emoji=emoji, scripted_moves=[])


@pytest.mark.asyncio
async def test_failed_accusation_does_not_advance_turn() -> None:
    import asyncio

    MockContext, emoji = _mock_context()
    players = _players("A", "B", "C", "D")
    game = Spyfall(players, {}, random.Random(0))
    accuser = next(s for s in game.alive if s != game.spy)
    target = next(s for s in game.alive if s not in (game.spy, accuser))
    game.pending_accuse[accuser] = target
    calls = {"discussion": 0}

    def handler(sources):
        if "vote_guilty" in sources:
            return {s: Move(actor_seat=s, source="vote_innocent", args={}) for s in game.alive - {target}}
        calls["discussion"] += 1
        if calls["discussion"] == 1:
            return {accuser: Move(actor_seat=accuser, source="accuse", args={})}
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await game.play(_scripted_ctx(MockContext, emoji, players, handler))
    assert game.turn == 1
    assert accuser in game._accused_seats
    assert "failed" in game.history[-1]


@pytest.mark.asyncio
async def test_spy_leaving_mid_vote_gives_villagers_the_win() -> None:
    MockContext, emoji = _mock_context()
    players = _players("A", "B", "C", "D")
    game = Spyfall(players, {}, random.Random(0))
    accuser = next(s for s in game.alive if s != game.spy)
    target = next(s for s in game.alive if s not in (game.spy, accuser))
    game.pending_accuse[accuser] = target

    def handler(sources):
        if "vote_guilty" in sources:
            spy = game.spy
            game.remove_player(spy)
            moves = {s: Move(actor_seat=s, source="vote_guilty", args={}) for s in game.alive - {target}}
            moves[spy] = Move(actor_seat=spy, source="forfeit", args={})
            return moves
        return {accuser: Move(actor_seat=accuser, source="accuse", args={})}

    outcome = await game.play(_scripted_ctx(MockContext, emoji, players, handler))
    assert outcome.summary["winner_faction"] == "villagers"


def test_villager_forfeit_villagers_win() -> None:
    """A villager is removed, villagers win → removed villager "loss"/"Forfeited", others "win"."""
    players = _players("A", "B", "C", "D")
    game = Spyfall(players, {}, random.Random(1))
    
    # Pick a villager to remove (not the spy)
    villager = next(s for s in game.alive if s != game.spy)
    
    # Remove the villager
    game.remove_player(villager)
    
    # Verify villager is in forfeited set
    assert villager in game.forfeited
    
    # Remove the spy to make villagers win
    game.alive.discard(game.spy)
    
    # Get the outcome for villagers winning
    outcome = game._faction_outcome("villagers")
    
    # The removed villager should have "loss" and "Forfeited"
    assert outcome.results[villager] == "loss"
    assert outcome.player_descriptions[villager] == "Forfeited"
    
    # Other villagers should have "win"
    for p in players:
        if p.seat != game.spy and p.seat != villager:
            assert outcome.results[p.seat] == "win"


def test_spy_forfeit_spy_loses_even_if_spy_wins() -> None:
    """A spy forfeits, spy would have won → forfeit is "loss"/"Forfeited", not "win"."""
    players = _players("A", "B", "C", "D")
    game = Spyfall(players, {}, random.Random(2))
    
    # Remove all villagers except one to make spy almost win
    villagers = [s for s in game.alive if s != game.spy]
    for v in villagers[:-1]:
        game.alive.discard(v)
    
    # Now remove the spy via remove_player
    game.remove_player(game.spy)
    
    # Verify spy is in forfeited set
    assert game.spy in game.forfeited
    
    # Call _faction_outcome for spy (which would normally have won)
    outcome = game._faction_outcome("spy")
    
    # The spy should have "loss" and "Forfeited" despite being the spy
    assert outcome.results[game.spy] == "loss"
    assert outcome.player_descriptions[game.spy] == "Forfeited"
    
    # Other villagers should have "loss"
    for p in players:
        if p.seat != game.spy:
            assert outcome.results[p.seat] == "loss"


def test_existing_spy_forfeit_villagers_win_still_works() -> None:
    """Existing test: test_spy_forfeit_villagers_win should still pass."""
    players = _players("A", "B", "C")
    game = Spyfall(players, {}, random.Random(1))
    outcome = game.forfeit_end_outcome(game.spy, "forfeit")
    assert outcome.summary["winner_faction"] == "villagers"
    assert outcome.results[game.spy] == "loss"
    assert all(outcome.results[p.seat] == "win" for p in players if p.seat != game.spy)
