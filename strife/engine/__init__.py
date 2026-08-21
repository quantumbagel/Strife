"""Public game-plugin API.

Authors should import from ``strife.engine`` and ``strife.presentation`` —
not persistence, compiler, routing, or the live host (``strife.session``).

The live Discord session (``GameSession``, ``LiveContext``) lives in
``strife.session``. This package is the contract games type against.

Typical imports::

    from strife.engine import (
        Game,
        TurnBasedGame,
        GameContext,
        Move,
        GameOutcome,
        Player,
        game_metadata_from,
        PlayerCount,
        PlayerOrder,
        PLATFORM_VERSION,
        ReplayBuilder,
        iter_replay,
        select_value,
        run_cpu,
    )
    from strife.presentation.components import ActionRow, Button, ButtonStyle, LayoutView
    from strife.presentation.game_ui import add_controls, game_container, query_panel
"""

from strife.engine.context import GameContext, ReplayFrame
from strife.engine.game import Game
from strife.engine.metadata import (
    BotSpec,
    GameMetadata,
    MoveParam,
    OptionType,
    ParamType,
    PlayerCount,
    PlayerOrder,
    RoleSpec,
    SettingOption,
    SlashMove,
    game_metadata,
    game_metadata_from,
)
from strife.engine.outcomes import forfeit_outcome
from strife.engine.platform import PLATFORM_VERSION, parse_version, platform_satisfies
from strife.engine.players import GameOutcome, Move, Player, select_value
from strife.engine.replay import (
    ReplayBuilder,
    ReplayStep,
    freeze_view,
    iter_replay,
    is_terminal_replay_move,
    system_replay_info,
)
from strife.engine.turn_based import TurnBasedGame
from strife.engine.workers import run_cpu

# Compatibility alias: recorded log rows are ``Move`` values.
MoveRecord = Move

__all__ = [
    "BotSpec",
    "Game",
    "GameContext",
    "GameMetadata",
    "GameOutcome",
    "Move",
    "MoveParam",
    "MoveRecord",
    "OptionType",
    "PLATFORM_VERSION",
    "ParamType",
    "Player",
    "PlayerCount",
    "PlayerOrder",
    "ReplayBuilder",
    "ReplayFrame",
    "ReplayStep",
    "RoleSpec",
    "SettingOption",
    "SlashMove",
    "TurnBasedGame",
    "forfeit_outcome",
    "freeze_view",
    "game_metadata",
    "game_metadata_from",
    "is_terminal_replay_move",
    "iter_replay",
    "parse_version",
    "platform_satisfies",
    "run_cpu",
    "select_value",
    "system_replay_info",
]
