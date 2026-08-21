from __future__ import annotations

from strife.engine.players import Player
from strife.matchmaking.lobby import Lobby, LobbyMember
from strife.matchmaking.registries import SessionRegistries, UserLocation


class _FakeSession:
    def __init__(self, thread_id: int, guild_id: int, players: list[Player]) -> None:
        self.thread_id = thread_id
        self.guild_id = guild_id
        self.players = players


async def test_promote_updates_location_under_lock() -> None:
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
    session = _FakeSession(
        99,
        22,
        [Player(seat=0, user_id=1, display_name="A")],
    )
    await regs.promote(11, session)  # type: ignore[arg-type]
    loc = regs.location_of(1)
    assert loc is not None
    assert loc.kind == "game"
    assert loc.thread_id == 99
    assert regs.get_lobby(11) is None
    assert regs.get_game(99) is session



