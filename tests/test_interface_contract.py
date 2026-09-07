from __future__ import annotations

import asyncio
import random

from strife.engine.players import Move, Player
from strife.matchmaking.lobby import Lobby, LobbyMember
from strife.matchmaking.registries import SessionRegistries, UserLocation
from strife.presentation.components import ActionRow, Button, Container, LayoutView, query_sources
from strife.routing.cache import InMemoryPayloadCache
from strife.session.context import LiveContext
from strife.session.types import PendingInput

from tests.test_registries import _FakeSession


async def test_create_lobby_rollback_removes_lobby() -> None:
    """Registries must not keep a lobby after occupancy is released."""
    regs = SessionRegistries()
    lobby = Lobby(
        thread_id=11,
        guild_id=22,
        channel_id=33,
        game_key="tictactoe",
        creator_id=1,
        private=False,
        members=[LobbyMember(1, "A")],
    )
    await regs.reserve_user(1, UserLocation("lobby", 11, 22))
    regs.add_lobby(lobby)
    regs.remove_lobby(11)
    await regs.release_user(1)
    assert regs.get_lobby(11) is None
    assert regs.location_of(1) is None


async def test_rollback_promote_restores_lobby_occupancy() -> None:
    regs = SessionRegistries()
    lobby = Lobby(
        thread_id=11,
        guild_id=22,
        channel_id=33,
        game_key="tictactoe",
        creator_id=1,
        private=False,
        members=[LobbyMember(1, "A")],
    )
    regs.add_lobby(lobby)
    await regs.reserve_user(1, UserLocation("lobby", 11, 22))
    session = _FakeSession(99, 22, [Player(seat=0, user_id=1, display_name="A")])
    await regs.promote(11, session)  # type: ignore[arg-type]
    await regs.rollback_promote(lobby, session)  # type: ignore[arg-type]
    loc = regs.location_of(1)
    assert loc is not None
    assert loc.kind == "lobby"
    assert loc.thread_id == 11
    assert regs.get_lobby(11) is lobby
    assert regs.get_game(99) is None


def test_live_context_hides_session() -> None:
    ctx = LiveContext(object())  # type: ignore[arg-type]
    assert not hasattr(ctx, "_session")
    assert ctx._query_interaction is None


def test_query_sources_stripped() -> None:
    view = LayoutView()
    container = Container()
    row = ActionRow()
    row.add_button(Button(source="pass", label="Pass"))
    row.add_button(Button(source="peek", label="Peek", query=True))
    container.add_action_row(row)
    view.add_container(container)
    assert "peek" in query_sources(view)
    assert "pass" not in query_sources(view)


def test_payload_cache_invalidate_and_bound() -> None:
    cache = InMemoryPayloadCache(max_entries=4)
    tokens = [cache.put(b"x" + bytes([i]), resource_id=10) for i in range(6)]
    assert len(cache._store) <= 4
    cache.put(b"keep", resource_id=11)
    cache.invalidate(11)
    assert all(entry[2] != 11 for entry in cache._store.values())
    cache.invalidate(10)
    assert all(entry[2] != 10 for entry in cache._store.values())
    assert tokens  # used


async def test_mock_context_honors_record_false() -> None:
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "scripts" / "run_game.py"
    spec = importlib.util.spec_from_file_location("run_game", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    MockContext = mod.MockContext

    players = [
        Player(seat=0, user_id=1, display_name="A"),
        Player(seat=1, user_id=2, display_name="B"),
    ]
    ctx = MockContext(
        rng=random.Random(0),
        players=players,
        settings={},
        emoji=object(),
        scripted_moves=[
            (0, "vote_a", {}),
            (1, "vote_b", {}),
        ],
    )
    view = LayoutView()
    moves = await ctx.request_inputs(
        view,
        actors={0, 1},
        sources={"vote_a", "vote_b"},
        until="all",
        record=False,
    )
    assert len(moves) == 2
    assert ctx.recorded == []
    await ctx.record_event("day_outcome", {"lynched": 0})
    assert len(ctx.recorded) == 1
    assert ctx.recorded[0].source == "day_outcome"


def test_pending_input_has_per_seat_deadline() -> None:
    loop = asyncio.new_event_loop()
    future: asyncio.Future[Move] = loop.create_future()
    pending = PendingInput(
        {0},
        {"pass"},
        future,
        timeout_seconds=30,
        deadline_at=100.0,
        timeout_generation=3,
    )
    assert pending.deadline_at == 100.0
    assert pending.timeout_generation == 3
    loop.close()
