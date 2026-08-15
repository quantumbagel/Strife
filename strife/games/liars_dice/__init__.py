from __future__ import annotations

from strife.engine.metadata import (
    BotSpec,
    GameMetadata,
    OptionType,
    PlayerCount,
    PlayerOrder,
    SettingOption,
)
from strife.games.liars_dice.game import LiarsDice

META = GameMetadata(
    key="liars_dice",
    name="Liar's Dice",
    summary="A turn-based game of secret dice rolls, bidding, and bluffing.",
    description="Roll 5 dice. Bid on total dice across the table. Accuse the prior bidder of lying to win.",
    tags=("party", "bluffing", "dice"),
    author="Strife",
    version="1.0.0",
    author_link=None,
    source_link=None,
    how_to_play_link="https://en.wikipedia.org/wiki/Liar%27s_dice",
    time_estimate="10m",
    difficulty=3,
    player_count=PlayerCount(minimum=2, maximum=6),
    player_order=PlayerOrder.RANDOM,
    bots=(
        BotSpec("easy", "Safe opening bids, rarely calls"),
        BotSpec("medium", "Bids from its own dice, calls obvious lies"),
        BotSpec("hard", "Tracks table totals and pressures bids"),
    ),
    bot_takeover_difficulty="medium",
    settings=(
        SettingOption("dice_count", "Dice Count", "Starting dice per player", OptionType.INT, default=5, minimum=2, maximum=6),
        SettingOption("wild_ones", "Wild Ones", "Whether 1s count as wildcards", OptionType.BOOL, default=True),
    ),
)

LiarsDice.metadata = META
