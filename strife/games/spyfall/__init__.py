from __future__ import annotations

from strife.engine.metadata import GameMetadata, PlayerCount, PlayerOrder
from strife.games.spyfall.game import Spyfall

META = GameMetadata(
    key="spyfall",
    name="Spyfall",
    summary="Uncover the spy or blend in to guess the location.",
    description="Everyone knows the secret location except the spy. Ask questions to find the spy.",
    tags=("party", "social-deduction"),
    author="Strife",
    version="1.0.0",
    author_link=None,
    source_link=None,
    time_estimate="8m",
    difficulty=4,
    player_count=PlayerCount(minimum=3, maximum=8),
    player_order=PlayerOrder.RANDOM,
)

Spyfall.metadata = META
