from __future__ import annotations

from typing import Any

from strife.engine.metadata import OptionType, SettingOption

INT_SETTING_SELECT_LIMIT = 25


def int_setting_bounds(option: SettingOption) -> tuple[int, int]:
    minimum = option.minimum if option.minimum is not None else int(option.default)
    maximum = option.maximum if option.maximum is not None else minimum + 5
    return minimum, maximum


def int_setting_fits_select(option: SettingOption) -> bool:
    minimum, maximum = int_setting_bounds(option)
    return maximum - minimum + 1 <= INT_SETTING_SELECT_LIMIT


def choice_emoji_for(
    option: SettingOption, value: str, *, default: str = "pointing"
) -> str:
    if option.choice_emojis:
        for choice_value, emoji_key in option.choice_emojis:
            if choice_value == value:
                return emoji_key
    if option.emoji:
        return option.emoji
    return default


def format_setting_display(
    option: SettingOption,
    value: Any,
    *,
    on_label: str = "On",
    off_label: str = "Off",
) -> str:
    if option.type == OptionType.BOOL:
        display_value = on_label if bool(value) else off_label
    elif option.type == OptionType.CHOICE:
        display_value = str(value).capitalize()
    else:
        display_value = str(value)
    return f"{option.title}: {display_value}"


def format_settings_rules(
    options: tuple[SettingOption, ...],
    settings: dict[str, Any],
    *,
    on_label: str,
    off_label: str,
) -> list[str]:
    return [
        format_setting_display(
            option,
            settings.get(option.key, option.default),
            on_label=on_label,
            off_label=off_label,
        )
        for option in options
    ]
