from __future__ import annotations


from strife.engine.metadata import (
    BotSpec,
    GameMetadata,
    OptionType,
    PlayerCount,
    PlayerOrder,
    RoleSpec,
    SettingOption,
)
from strife.games.mafia.game import Mafia

META = GameMetadata(
    key="mafia",
    name="Mafia",
    summary="One of the most popular social deduction games. Can you find the mafia before they eliminate you?",
    description=(
        "Mafia secretly eliminate town members each night; town debates and lynches by day. "
        "Town wins by eliminating all mafia; mafia win at parity."
    ),
    tags=("social", "deduction", "party"),
    author="Strife",
    version="1.0.0",
    platform_version="1.0.0",
    author_link=None,
    source_link=None,
    how_to_play_link="https://en.wikipedia.org/wiki/Mafia_(party_game)",
    time_estimate="20m",
    difficulty=5,
    player_count=PlayerCount(minimum=4, maximum=12),
    player_order=PlayerOrder.RANDOM,
    bots=(
        BotSpec("easy", "Plays semi-randomly"),
        BotSpec("medium", "Tracks claims and votes plausibly"),
        BotSpec("hard", "Uses night results + voting history"),
    ),
    settings=(
        SettingOption("mafia_count", "Mafia Count", "Number of mafia", OptionType.INT, default=2, minimum=1, maximum=4, emoji="user"),
        SettingOption("enable_doctor", "Doctor", "Include a Doctor", OptionType.BOOL, default=True, emoji="ready"),
        SettingOption("enable_detective", "Detective", "Include a Detective", OptionType.BOOL, default=True, emoji="hmm"),
    ),
    slash_moves=(),
    roles=(
        RoleSpec("villager", "Villager", "No night action. Find and lynch the mafia."),
        RoleSpec("mafia", "Mafia", "Each night, agree on one victim to eliminate."),
        RoleSpec("doctor", "Doctor", "Each night, protect one player from elimination."),
        RoleSpec("detective", "Detective", "Each night, learn one player's alignment."),
    ),
    supports_player_removal=True
)

Mafia.metadata = META
