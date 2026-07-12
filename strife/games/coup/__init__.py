from __future__ import annotations

from strife.engine.metadata import BotSpec, GameMetadata, PlayerCount, PlayerOrder
from strife.games.coup.game import Coup

META = GameMetadata(
    key="coup",
    name="Coup",
    summary="Bluff and challenge your way to total political control.",
    description="Each player holds secret roles. Claim actions, block others, and challenge their claims.",
    tags=("strategy", "bluffing", "card"),
    author="Strife",
    version="1.0.0",
    author_link=None,
    source_link=None,
    time_estimate="15m",
    difficulty=5,
    player_count=PlayerCount(minimum=3, maximum=6),
    player_order=PlayerOrder.RANDOM,
    bots=(
        BotSpec("easy", "Safe income and basic blocks"),
        BotSpec("medium", "Uses owned roles and targets threats"),
        BotSpec("hard", "Aggressive coups, steals, and challenges"),
    ),
)

Coup.metadata = META
