from __future__ import annotations

from strife.engine.metadata import (
    BotSpec,
    GameMetadata,
    PlayerCount,
    PlayerOrder,
)
from strife.games.connectfour.game import ConnectFour

META = GameMetadata(
    key="connect_four",
    name="Connect Four",
    summary="Classic 7x6 alignment game. Align 4 in a row.",
    description="Drop discs into columns; first to connect 4 horizontally, vertically, or diagonally wins.",
    tags=("classic", "strategy", "2p"),
    author="Strife",
    version="1.0.0",
    platform_version="1.0.0",
    author_link=None,
    source_link=None,
    how_to_play_link="https://en.wikipedia.org/wiki/Connect_Four",
    time_estimate="5m",
    difficulty=3,
    player_count=PlayerCount(fixed=2),
    player_order=PlayerOrder.RANDOM,
    bots=(
        BotSpec("easy", "Random legal moves"),
        BotSpec("medium", "Win/block heuristics"),
        BotSpec("hard", "Alpha-beta minimax"),
    ),
    settings=(),
    slash_moves=(),
    supports_player_removal=False,
)

ConnectFour.metadata = META
