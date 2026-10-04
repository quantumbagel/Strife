"""What game plugins import.

Use ``strife.engine`` and ``strife.presentation``. Not persistence, the
compiler, routing, or ``strife.session`` (that is the live Discord host).

Typical imports::

    from strife.engine import (
        Game,
        TurnBasedGame,
        GameContext,
        Move,
        Interrupt,
        GameOutcome,
        Result,
        Player,
        SeatPrompt,
        game_metadata_from,
        PlayerCount,
        PlayerOrder,
        PLATFORM_VERSION,
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
from strife.engine.requests import BotRequest, FormSpec, SeatPrompt, TimeoutConsequence
from strife.engine.players import (
    GameOutcome,
    Interrupt,
    Move,
    Player,
    Result,
    select_value,
)
from strife.engine.replay import (
    ReplayDivergence,
    freeze_view,
    run_replay,
    system_replay_info,
)
from strife.engine.turn_based import TurnBasedGame
from strife.engine.workers import run_cpu

__all__ = [
    "BotRequest",
    "BotSpec",
    "FormSpec",
    "Game",
    "GameContext",
    "GameMetadata",
    "GameOutcome",
    "Interrupt",
    "Move",
    "MoveParam",
    "OptionType",
    "PLATFORM_VERSION",
    "ParamType",
    "Player",
    "PlayerCount",
    "PlayerOrder",
    "ReplayDivergence",
    "ReplayFrame",
    "Result",
    "RoleSpec",
    "SeatPrompt",
    "SettingOption",
    "SlashMove",
    "TimeoutConsequence",
    "TurnBasedGame",
    "forfeit_outcome",
    "freeze_view",
    "game_metadata",
    "game_metadata_from",
    "parse_version",
    "platform_satisfies",
    "run_cpu",
    "run_replay",
    "select_value",
    "system_replay_info",
]
