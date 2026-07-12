from __future__ import annotations

from strife.engine.metadata import (
    BotSpec,
    GameMetadata,
    OptionType,
    PlayerCount,
    PlayerOrder,
    RoleFlow,
    RoleMode,
    RoleSpec,
    SettingOption,
)
from strife.games.test.game import TestGame

META = GameMetadata(
    key="test",
    name="API Test",
    summary="A test game to exercise and verify all framework API features.",
    description="This game guides players through interactive layouts, multiple inputs, private messaging, and settings display.",
    tags=("test", "dev", "utility"),
    author="Strife",
    version="1.0.0",
    author_link=None,
    source_link=None,
    how_to_play_link=None,
    time_estimate="1m",
    difficulty=1,
    player_count=PlayerCount(minimum=1, maximum=4),
    player_order=PlayerOrder.RANDOM,
    bots=(
        BotSpec("easy", "Plays the API Test game automatically"),
    ),
    settings=(
        SettingOption(
            key="test_mode",
            title="Test Mode",
            description="Choose which features to focus on",
            type=OptionType.CHOICE,
            default="all",
            choices=("all", "interactive", "static"),
            emoji="settings",
            choice_emojis=(
                ("all", "play"),
                ("interactive", "pointing"),
                ("static", "spectate"),
            ),
        ),
        SettingOption(
            key="test_int",
            title="Test Int",
            description="An integer setting option",
            type=OptionType.INT,
            default=42,
            minimum=1,
            maximum=100,
            emoji="hmm",
        ),
        SettingOption(
            key="test_bool",
            title="Test Bool",
            description="A boolean setting option",
            type=OptionType.BOOL,
            default=True,
            emoji="ready",
        ),
    ),
    slash_moves=(),
    role_mode=RoleMode.SECRET,
    role_flow=RoleFlow.RANDOM,
    roles=(
        RoleSpec("tester", "Tester", "You are a tester. Interact with everything to verify features."),
        RoleSpec("observer", "Observer", "You observe and verify that the system runs smoothly."),
    ),
    supports_player_removal=True,
)

TestGame.metadata = META
