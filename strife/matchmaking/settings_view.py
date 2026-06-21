from __future__ import annotations

from strife.config.text import TextConfig
from strife.engine.metadata import GameMetadata, OptionType
from strife.matchmaking.lobby import Lobby
from strife.presentation.components import (
    ActionRow,
    Button,
    ButtonStyle,
    Container,
    LayoutView,
    Select,
    SelectChoice,
    TextDisplay,
    TextSize,
)
from strife.presentation.emoji import EmojiResolver, get_game_emoji
from strife.routing import prefixes as P


def build_settings_view(
    lobby: Lobby,
    meta: GameMetadata,
    emoji: EmojiResolver,
    text: TextConfig,
) -> LayoutView:
    game_emoji = get_game_emoji(emoji, meta.key)
    forward_emoji = emoji.get("forward")
    view = LayoutView()
    container = Container()
    container.add_text(
        TextDisplay(
            markdown_content=f"### {game_emoji} {meta.name} {forward_emoji} Configuration",
            size_style=TextSize.HEADER,
        )
    )
    privacy = "Private" if lobby.private else "Public"
    container.add_text(TextDisplay(markdown_content=f"**Privacy:** {privacy}"))
    container.add_text(
        TextDisplay(
            markdown_content=f"**Access Lists:** whitelist {len(lobby.whitelist)} / blacklist {len(lobby.blacklist)}"
        )
    )
    container.add_separator()
    view.add_container(container)

    row = ActionRow()
    row.add_select(
        Select(
            source="priv",
            placeholder="Privacy",
            choices=[
                SelectChoice(label="Public", value="public", default=not lobby.private),
                SelectChoice(label="Private", value="private", default=lobby.private),
            ],
            route_prefix=P.LOBBY_PRIV,
            resource_id=lobby.thread_id,
        )
    )
    view.add_action_row(row)

    reset_priv = ActionRow()
    reset_priv.add_button(
        Button(
            source="reset_priv",
            label="Reset Privacy",
            style=ButtonStyle.SECONDARY,
            route_prefix=P.LOBBY_RESET_PRIV,
            resource_id=lobby.thread_id,
        )
    )
    view.add_action_row(reset_priv)

    for option in meta.settings:
        opt_row = ActionRow()
        if option.type == OptionType.BOOL:
            current = bool(lobby.settings.get(option.key, option.default))
            choices = [
                SelectChoice(label="On", value="true", default=current),
                SelectChoice(label="Off", value="false", default=not current),
            ]
        elif option.type == OptionType.CHOICE:
            current = str(lobby.settings.get(option.key, option.default))
            choices = [
                SelectChoice(label=value, value=value, default=value == current)
                for value in (option.choices or ())
            ]
        else:
            minimum = option.minimum if option.minimum is not None else int(option.default)
            maximum = option.maximum if option.maximum is not None else minimum + 5
            current = int(lobby.settings.get(option.key, option.default))
            choices = [
                SelectChoice(label=str(v), value=str(v), default=v == current)
                for v in range(minimum, min(maximum, minimum + 10) + 1)
            ]
        opt_row.add_select(
            Select(
                source="opt",
                placeholder=option.title,
                choices=choices,
                payload={"option_key": option.key, "option_type": option.type.value},
                route_prefix=P.LOBBY_OPT,
                resource_id=lobby.thread_id,
            )
        )
        view.add_action_row(opt_row)

    rules = ActionRow()
    rules.add_button(
        Button(
            source="reset_rules",
            label="Reset Game Rules",
            style=ButtonStyle.SECONDARY,
            route_prefix=P.LOBBY_RESET_RULES,
            resource_id=lobby.thread_id,
        )
    )
    rules.add_button(
        Button(
            source="end",
            label="End Lobby",
            style=ButtonStyle.DANGER,
            route_prefix=P.LOBBY_END,
            resource_id=lobby.thread_id,
        )
    )
    view.add_action_row(rules)
    return view
