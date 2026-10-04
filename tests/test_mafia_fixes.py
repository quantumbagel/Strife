from __future__ import annotations

import importlib.util
import random
from pathlib import Path
from types import SimpleNamespace

import pytest

from strife.engine.log import LogEntryKind
from strife.engine.players import Move, Player
from strife.games.mafia import GAME as Mafia
from strife.games.mafia.roles import compose_roles
from strife.presentation.components import LayoutView


def _players(n: int) -> list[Player]:
    return [
        Player(seat=i, user_id=100 + i, display_name=f"P{i}")
        for i in range(n)
    ]


def _load_mock_context():
    path = Path(__file__).resolve().parents[1] / "scripts" / "run_game.py"
    spec = importlib.util.spec_from_file_location("run_game", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.MockContext


@pytest.mark.parametrize(
    ("player_count", "expected_mafia"),
    [(5, 1), (6, 2), (7, 2), (8, 3)],
)
def test_max_mafia_cap(player_count: int, expected_mafia: int) -> None:
    settings = {
        "mafia_count": 4,
        "enable_doctor": False,
        "enable_detective": False,
    }
    roles = compose_roles(Mafia.metadata, player_count, settings, random.Random(0))
    assert roles.count("mafia") == expected_mafia


async def test_mafia_kill_plurality_not_unanimous() -> None:
    MockContext = _load_mock_context()
    players = _players(12)
    settings = {
        "mafia_count": 5,
        "enable_doctor": False,
        "enable_detective": False,
    }
    game = Mafia(players, settings, random.Random(0))
    mafia_seats = sorted(s for s in game.alive if game.role[s] == "mafia")
    assert len(mafia_seats) == 5
    town_targets = sorted(s for s in game.alive if game.role[s] != "mafia")
    target_a, target_b = town_targets[0], town_targets[1]

    scripted: list[tuple[int, str, dict]] = []
    for i, seat in enumerate(mafia_seats):
        target = target_a if i < 3 else target_b
        scripted.append((seat, "kill", {"target": str(target)}))

    ctx = MockContext(
        rng=random.Random(0),
        players=players,
        settings=settings,
        emoji=SimpleNamespace(get=lambda *a, **k: ""),
        scripted_moves=scripted,
    )
    game.day = 1
    await game._night(ctx)

    assert target_a not in game.alive
    assert target_b in game.alive
    night_events = [m for m in ctx.recorded if m.source == "night_outcome"]
    assert len(night_events) == 1
    assert night_events[0].args["victim"] == target_a


class _ForfeitDuringNightContext:
    """Simulates a mid-phase forfeit while night inputs are collected."""

    def __init__(self, game: Mafia, *, forfeit_seat: int, night_moves: dict[int, Move]) -> None:
        self.game = game
        self.forfeit_seat = forfeit_seat
        self.night_moves = night_moves
        self.players = game.players
        self.rng = game.rng
        self.emoji = SimpleNamespace(get=lambda *a, **k: "")
        self.recorded: list[Move] = []

    async def update(self, view: LayoutView) -> None:
        del view

    async def send_private(self, seat: int, view: LayoutView) -> None:
        del seat, view

    async def request_inputs(self, view, **kwargs):
        del view, kwargs
        self.game.remove_player(self.forfeit_seat)
        return dict(self.night_moves)

    async def record_event(self, source: str, arguments: dict) -> None:
        self.recorded.append(
            Move(
                actor_seat=None,
                source=source,
                args=arguments,
                kind=LogEntryKind.GAME,
            )
        )


async def test_mid_phase_forfeit_skips_night_kill() -> None:
    players = _players(6)
    settings = {
        "mafia_count": 2,
        "enable_doctor": False,
        "enable_detective": False,
    }
    game = Mafia(players, settings, random.Random(0))
    mafia_seats = [s for s in game.alive if game.role[s] == "mafia"]
    town_seats = [s for s in game.alive if game.role[s] != "mafia"]
    assert len(mafia_seats) == 2 and len(town_seats) == 4

    last_town = town_seats[-1]
    forfeit_town = town_seats[0]
    # Two town forfeits during night bring mafia to parity before the kill resolves.
    night_moves = {
        seat: Move(actor_seat=seat, source="kill", args={"target": str(last_town)})
        for seat in mafia_seats
    }

    class Ctx(_ForfeitDuringNightContext):
        def __init__(self) -> None:
            super().__init__(game, forfeit_seat=forfeit_town, night_moves=night_moves)
            self._second_forfeit = town_seats[1]

        async def request_inputs(self, view, **kwargs):
            del view, kwargs
            self.game.remove_player(self.forfeit_seat)
            self.game.remove_player(self._second_forfeit)
            return dict(self.night_moves)

    ctx = Ctx()
    game.day = 1
    await game._night(ctx)

    assert game._winner() == "mafia"
    assert last_town in game.alive
    night_events = [m for m in ctx.recorded if m.source == "night_outcome"]
    assert night_events[0].args["victim"] is None


def test_town_forfeit_town_wins() -> None:
    """A town player forfeits, town later wins via _finish("town") → forfeiter is "loss"/"Forfeited"."""
    players = _players(6)
    settings = {
        "mafia_count": 2,
        "enable_doctor": False,
        "enable_detective": False,
    }
    game = Mafia(players, settings, random.Random(0))
    
    # Pick a town player to forfeit
    town_forfeiters = [s for s in game.alive if game.role[s] != "mafia"]
    forfeit_seat = town_forfeiters[0]
    
    # Remove the forfeiter via remove_player
    game.remove_player(forfeit_seat)
    
    # Verify forfeiter is in the forfeited set
    assert forfeit_seat in game.forfeited
    
    # Simulate town winning
    outcome = game._finish("town")
    
    # The forfeited player should have "loss" and "Forfeited" description
    assert outcome.results[forfeit_seat] == "loss"
    assert outcome.player_descriptions[forfeit_seat] == "Forfeited"
    
    # Other town players should have "win"
    for seat in town_forfeiters[1:]:
        if seat in game.alive:
            assert outcome.results[seat] == "win"


def test_town_forfeit_mafia_wins() -> None:
    """A town player forfeits, mafia wins → forfeiter is "loss"/"Forfeited" even though town lost."""
    players = _players(6)
    settings = {
        "mafia_count": 2,
        "enable_doctor": False,
        "enable_detective": False,
    }
    game = Mafia(players, settings, random.Random(0))
    
    # Pick a town player to forfeit
    town_forfeiters = [s for s in game.alive if game.role[s] != "mafia"]
    forfeit_seat = town_forfeiters[0]
    
    # Remove the forfeiter via remove_player
    game.remove_player(forfeit_seat)
    
    # Simulate mafia winning
    outcome = game._finish("mafia")
    
    # The forfeited player should have "loss" and "Forfeited" description, not "win"
    assert outcome.results[forfeit_seat] == "loss"
    assert outcome.player_descriptions[forfeit_seat] == "Forfeited"
    
    # Mafia players should still have "win"
    for seat in game.alive:
        if game.role[seat] == "mafia":
            assert outcome.results[seat] == "win"


def test_town_lynched_town_wins() -> None:
    """A town player is lynched (day kill), town wins → lynched player gets "win"."""
    players = _players(6)
    settings = {
        "mafia_count": 2,
        "enable_doctor": False,
        "enable_detective": False,
    }
    game = Mafia(players, settings, random.Random(0))
    
    # Pick a town player to "lynch"
    town_seats = [s for s in game.alive if game.role[s] != "mafia"]
    lynched_seat = town_seats[0]
    
    # Simulate a lynch (day kill) using _eliminate_player
    game._eliminate_player(lynched_seat)
    game.death_reason[lynched_seat] = "day"
    
    # Simulate town winning
    outcome = game._finish("town")
    
    # The lynched player should still get "win" since town won
    assert outcome.results[lynched_seat] == "win"
    assert outcome.player_descriptions[lynched_seat] == "Lynched by Town"


def test_forfeit_end_outcome_faction_wins() -> None:
    """forfeit_end_outcome where forfeiter's faction wins → forfeiter still "loss"."""
    players = _players(8)
    settings = {
        "mafia_count": 3,
        "enable_doctor": False,
        "enable_detective": False,
    }
    game = Mafia(players, settings, random.Random(0))
    
    # Pick a mafia member to forfeit
    mafia_seats = [s for s in game.alive if game.role[s] == "mafia"]
    forfeit_seat = mafia_seats[0]
    
    # Make mafia reach parity by removing town players until they win
    town_seats = [s for s in game.alive if game.role[s] != "mafia"]
    for seat in town_seats:
        if len(game.alive - {s for s in game.alive if game.role[s] == "mafia"}) <= len({s for s in game.alive if game.role[s] == "mafia"}):
            break
        game._eliminate_player(seat)
    
    # Now call forfeit_end_outcome on a mafia member
    outcome = game.forfeit_end_outcome(forfeit_seat)
    
    # The forfeited mafia should have "loss" despite mafia winning
    assert outcome.results[forfeit_seat] == "loss"
    assert outcome.player_descriptions[forfeit_seat] == "Forfeited"
    
    # Other mafia should have "win"
    for seat in mafia_seats:
        if seat != forfeit_seat and seat in game.alive:
            assert outcome.results[seat] == "win"
