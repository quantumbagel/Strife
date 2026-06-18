from __future__ import annotations

from strife.engine.metadata import (
    BotSpec,
    GameMetadata,
    OptionType,
    PlayerCount,
    PlayerOrder,
    RoleFlow,
    RoleMode,
    SettingOption,
)
from strife.games.tictactoe.game import TicTacToe

META = GameMetadata(
    key="tictactoe",
    name="Tic-Tac-Toe",
    summary="Classic 3x3. Get three in a row.",
    description="Two players alternate placing X and O on a 3x3 grid; first to align three wins.",
    tags=("classic", "strategy", "2p"),
    author="Strife",
    version="1.0.0",
    author_link=None,
    source_link=None,
    time_estimate="2m",
    difficulty="easy",
    player_count=PlayerCount(fixed=2),
    player_order=PlayerOrder.RANDOM,
    bots=(
        BotSpec("easy", "Random legal move"),
        BotSpec("medium", "Win/block heuristic"),
        BotSpec("hard", "Optimal minimax (never loses)"),
    ),
    settings=(
        SettingOption(
            key="first_move",
            title="First Move",
            description="Who plays X (moves first)",
            type=OptionType.CHOICE,
            default="random",
            choices=("random", "creator"),
        ),
    ),
    slash_moves=(),
    supports_player_removal=False,
)

TicTacToe.metadata = META
