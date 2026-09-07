from __future__ import annotations

from strife.engine.metadata import BotSpec, GameMetadata, PlayerCount, PlayerOrder
from strife.games.spyfall.game import Spyfall

META = GameMetadata(
    key="spyfall",
    name="Spyfall",
    summary="Uncover the spy or blend in to guess the location.",
    description="Everyone knows the secret location except the spy. Ask questions to find the spy.",
    tags=("party", "social-deduction"),
    author="Strife",
    author_link=None,
    source_link=None,
    how_to_play_link="https://en.wikipedia.org/wiki/Spyfall",
    time_estimate="8m",
    difficulty=4,
    player_count=PlayerCount(minimum=3, maximum=8),
    player_order=PlayerOrder.RANDOM,
    bots=(
        BotSpec("easy", "Mostly passes, rare accusations"),
        BotSpec("medium", "Accuses more as time runs out"),
        BotSpec("hard", "Presses accusations and guesses locations"),
    ),
    bot_takeover_difficulty="medium",
)

Spyfall.metadata = META
GAME = Spyfall
