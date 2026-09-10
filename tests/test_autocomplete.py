from __future__ import annotations

from discord import app_commands

from pathlib import Path

from strife.commands.autocomplete import (
    bot_add_difficulty_choices,
    bot_remove_name_choices,
    notice_choices,
    option_key_choices,
    option_value_choices,
    replay_match_choices_or_notice,
)
from strife.config.text import TextConfig, load_text_config
from strife.engine.metadata import BotSpec, GameMetadata, OptionType, PlayerCount, SettingOption
from strife.games.coup import META as COUP_META
from strife.matchmaking.lobby import Lobby, LobbyMember, QueuedBot


def _text() -> TextConfig:
    return TextConfig(
        {
            "autocomplete": {
                "not_creator_add_bots": (
                    "You are not in a lobby where you are the creator, so you cannot add bots"
                ),
                "not_creator_remove_bots": (
                    "You are not in a lobby where you are the creator, so you cannot remove bots"
                ),
                "not_creator_change_options": (
                    "You are not in a lobby where you are the creator, so you cannot change options"
                ),
                "game_has_no_bots": "This game does not support bots",
                "lobby_full": "This lobby is full, so you cannot add bots",
                "no_bots_to_remove": "There are no bots in this lobby to remove",
                "game_has_no_options": "This game has no configurable rules",
                "choose_option_key_first": "Choose an option key first",
                "unknown_option": "That option is not available in this lobby",
                "no_completed_matches": "You have no completed matches to replay",
            }
        }
    )


def _lobby(**kwargs) -> Lobby:
    defaults = dict(
        thread_id=1,
        guild_id=2,
        channel_id=3,
        game_key="coup",
        creator_id=10,
        private=False,
        members=[LobbyMember(10, "Host")],
    )
    defaults.update(kwargs)
    return Lobby(**defaults)


def test_notice_choices_are_one_message() -> None:
    message = "You are not in a lobby where you are the creator, so you cannot add bots"
    choices = notice_choices(message)
    assert len(choices) == 1
    assert choices[0].name == message
    assert choices[0].value == message
    assert len(choices[0].name) <= 100


def test_coup_bot_add_lists_difficulties() -> None:
    choices = bot_add_difficulty_choices(_lobby(), COUP_META, _text(), "")
    assert [choice.value for choice in choices] == ["easy", "medium", "hard"]
    assert choices[0].name.startswith("Easy")


def test_coup_bot_add_filters_difficulty() -> None:
    choices = bot_add_difficulty_choices(_lobby(), COUP_META, _text(), "hard")
    assert [choice.value for choice in choices] == ["hard"]


def test_bot_add_without_creator_lobby_explains() -> None:
    choices = bot_add_difficulty_choices(None, None, _text(), "easy")
    assert len(choices) == 1
    assert "creator" in choices[0].name.lower()
    assert "add bots" in choices[0].name.lower()


def test_bot_add_when_lobby_full_explains() -> None:
    meta = GameMetadata(
        key="full",
        name="Full",
        player_count=PlayerCount(fixed=2),
        bots=(BotSpec("easy", "Random"),),
    )
    lobby = _lobby(
        game_key="full",
        members=[LobbyMember(10, "Host"), LobbyMember(11, "Guest")],
    )
    choices = bot_add_difficulty_choices(lobby, meta, _text(), "")
    assert len(choices) == 1
    assert "full" in choices[0].name.lower()


def test_bot_add_when_game_has_no_bots_explains() -> None:
    meta = GameMetadata(key="nobots", name="No Bots", player_count=PlayerCount(fixed=2))
    choices = bot_add_difficulty_choices(_lobby(game_key="nobots"), meta, _text(), "")
    assert len(choices) == 1
    assert "does not support bots" in choices[0].name.lower()


def test_bot_remove_without_creator_lobby_explains() -> None:
    choices = bot_remove_name_choices(None, _text(), "")
    assert len(choices) == 1
    assert "remove bots" in choices[0].name.lower()


def test_bot_remove_with_no_bots_explains() -> None:
    choices = bot_remove_name_choices(_lobby(), _text(), "")
    assert len(choices) == 1
    assert "no bots" in choices[0].name.lower()


def test_bot_remove_lists_queued_bots() -> None:
    lobby = _lobby(bots=[QueuedBot(name="Bot-easy-1", difficulty="easy")])
    choices = bot_remove_name_choices(lobby, _text(), "")
    assert [choice.value for choice in choices] == ["Bot-easy-1"]


def test_option_key_without_creator_lobby_explains() -> None:
    choices = option_key_choices(None, None, _text(), "")
    assert len(choices) == 1
    assert "change options" in choices[0].name.lower()


def test_option_key_for_coup_explains_no_rules() -> None:
    choices = option_key_choices(_lobby(), COUP_META, _text(), "")
    assert len(choices) == 1
    assert "no configurable rules" in choices[0].name.lower()


def test_option_value_requires_key() -> None:
    meta = GameMetadata(
        key="test",
        name="Test",
        settings=(
            SettingOption(
                key="mode",
                title="Mode",
                description="",
                type=OptionType.CHOICE,
                default="all",
                choices=("all", "static"),
            ),
        ),
    )
    choices = option_value_choices(_lobby(game_key="test"), meta, None, _text(), "")
    assert len(choices) == 1
    assert "option key" in choices[0].name.lower()


def test_replay_without_completed_matches_explains() -> None:
    choices = replay_match_choices_or_notice(
        [],
        has_completed=False,
        current="",
        text=_text(),
    )
    assert len(choices) == 1
    assert "completed matches" in choices[0].name.lower()


def test_shipped_autocomplete_copy_fits_discord() -> None:
    text = load_text_config(Path("config/text.toml"))
    for key in (
        "autocomplete.not_creator_add_bots",
        "autocomplete.not_creator_remove_bots",
        "autocomplete.not_creator_change_options",
        "autocomplete.game_has_no_bots",
        "autocomplete.lobby_full",
        "autocomplete.no_bots_to_remove",
        "autocomplete.game_has_no_options",
        "autocomplete.choose_option_key_first",
        "autocomplete.unknown_option",
        "autocomplete.no_completed_matches",
    ):
        choices = notice_choices(text.get(key))
        assert len(choices) == 1
        assert choices[0].name == text.get(key)
        assert len(choices[0].name) <= 100


def test_replay_filter_miss_stays_empty() -> None:
    existing = [app_commands.Choice(name="#abc123 · Coup", value="abc123")]
    filtered = replay_match_choices_or_notice(
        [],
        has_completed=True,
        current="zzz",
        text=_text(),
    )
    assert filtered == []
    kept = replay_match_choices_or_notice(
        existing,
        has_completed=True,
        current="",
        text=_text(),
    )
    assert kept == existing
