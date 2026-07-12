from __future__ import annotations

import discord

from strife.config.text import TextConfig
from strife.engine.metadata import GameMetadata, OptionType, SettingOption, choice_emoji_for
from strife.matchmaking.lobby import Lobby
from strife.presentation.components import (
    ActionRow,
    Button,
    ButtonStyle,
    Container,
    DescribedSelect,
    LayoutView,
    Select,
    SelectChoice,
    Separator, TextDisplay,
    TextSize,
    UserSelect,
)
from strife.presentation.emoji import EmojiResolver, get_game_emoji
from strife.routing import prefixes as P


def get_option_emoji(resolver: EmojiResolver, option: SettingOption) -> str:
    if option.emoji:
        return resolver.get(option.emoji)
    if option.key in resolver.config.entries:
        return resolver.get(option.key)
    return resolver.get("settings")


def build_settings_view(
    lobby: Lobby,
    meta: GameMetadata,
    emoji: EmojiResolver,
    text: TextConfig,
    interaction: discord.Interaction | None = None,
) -> LayoutView:
    game_emoji = get_game_emoji(emoji, meta.key)
    forward_emoji = emoji.get("forward")
    settings_emoji = emoji.get("settings")
    view = LayoutView()
    container = Container()

    # Title Section in the Container
    container.add_text(
        TextDisplay(
            markdown_content=text.get(
                "lobby.settings_title",
                game_emoji=game_emoji,
                game_name=meta.name,
                forward_emoji=forward_emoji,
                settings_emoji=settings_emoji,
            ),
            size_style=TextSize.HEADER,
        )
    )

    container.add_separator()


    container.add_text(
        TextDisplay(
            markdown_content=text.get("lobby.privacy_title", private_emoji=emoji.get("private")),
            size_style=TextSize.SUBHEADER,
        )
    )

    # Privacy configuration selector
    container.add_described_select(
        DescribedSelect(
            description=text.get("lobby.privacy_select_desc"),
            select=Select(
                source="priv",
                placeholder=text.get("lobby.privacy_placeholder"),
                choices=[
                    SelectChoice(
                        label=text.get("lobby.public_label"),
                        value="public",
                        default=not lobby.private,
                        description=text.get("lobby.public_desc"),
                        emoji="public",
                    ),
                    SelectChoice(
                        label=text.get("lobby.private_label"),
                        value="private",
                        default=lobby.private,
                        description=text.get("lobby.private_desc"),
                        emoji="private",
                    ),
                ],
                route_prefix=P.LOBBY_PRIV,
                resource_id=lobby.thread_id,
            ),
        )
    )

    if meta.supports_bots:
        container.add_separator()
        bot_emoji = emoji.get("bot")
        bot_lines = [
            f"• **{bot.name}** ({bot.difficulty})"
            for bot in lobby.bots
        ]
        roster_body = "\n".join(bot_lines) if bot_lines else ""
        container.add_text(
            TextDisplay(
                markdown_content=(
                    f"{text.get('lobby.bots_title', bot_emoji=bot_emoji)}\n"
                    f"{text.get('lobby.bots_summary') + "\n" if len(lobby.bots) else "\n"}"
                    f"{text.get('lobby.bots_roster', roster=roster_body)}"
                ),
                size_style=TextSize.SUBHEADER,
            )
        )

        can_add_bot = not lobby.is_full(meta)
        if can_add_bot:
            container.add_described_select(
                DescribedSelect(
                    description=text.get("lobby.add_bot_select_desc"),
                    select=Select(
                        source="bot_add",
                        placeholder=text.get("lobby.add_bot_placeholder"),
                        choices=[
                            SelectChoice(
                                label=spec.display_label(),
                                value=spec.difficulty,
                                emoji="bot",
                            )
                            for spec in meta.bots
                        ],
                        route_prefix=P.LOBBY_BOT_ADD,
                        resource_id=lobby.thread_id,
                    ),
                )
            )
        else:
            container.add_text(
                TextDisplay(
                    markdown_content=text.get("lobby.bots_full_hint"),
                    size_style=TextSize.BODY,
                )
            )

        if lobby.bots:
            container.add_described_select(
                DescribedSelect(
                    description=text.get("lobby.remove_bot_select_desc"),
                    select=Select(
                        source="bot_remove",
                        placeholder=text.get("lobby.remove_bot_placeholder"),
                        choices=[
                            SelectChoice(
                                label=bot.name,
                                value=bot.name,
                                description=text.get(
                                    "lobby.remove_bot_desc",
                                    name=bot.name,
                                ),
                                emoji="leave",
                            )
                            for bot in lobby.bots
                        ],
                        route_prefix=P.LOBBY_BOT_REMOVE,
                        resource_id=lobby.thread_id,
                    ),
                )
            )

    # Access Lists (Blacklist) Management
    if interaction and interaction.guild:
        guild = interaction.guild

        def get_member_name(uid: int) -> str:
            member = guild.get_member(uid)
            return member.display_name if member else f"User ID: {uid}"

        container.add_separator()
        if lobby.private:
            container.add_text(
                TextDisplay(
                    markdown_content=text.get(
                        "lobby.private_access_summary",
                        approved=len(lobby.approved),
                        pending=len(lobby.pending_requests),
                    ),
                    size_style=TextSize.SUBHEADER,
                )
            )

        bl_names = [get_member_name(uid) for uid in lobby.blacklist]
        bl_str = ", ".join(bl_names) if bl_names else "_None_"
        container.add_text(
            TextDisplay(
                markdown_content=(
                    f"{text.get('lobby.access_control_title', user_emoji=emoji.get('user'))}\n"
                    f"{text.get('lobby.blacklisted_label', blacklist=bl_str)}"
                ),
                size_style=TextSize.SUBHEADER,
            )
        )

        container.add_described_select(
            DescribedSelect(
                description=text.get("lobby.add_blacklist_select_desc"),
                select=UserSelect(
                    source="add_blacklist",
                    placeholder=text.get("lobby.add_blacklist_placeholder"),
                    route_prefix=P.LOBBY_ADD_BLACKLIST,
                    resource_id=lobby.thread_id,
                ),
            )
        )

        if lobby.blacklist:
            container.add_described_select(
                DescribedSelect(
                    description=text.get("lobby.remove_blacklist_select_desc"),
                    select=UserSelect(
                        source="remove_blacklist",
                        placeholder=text.get("lobby.remove_blacklist_placeholder"),
                        route_prefix=P.LOBBY_REMOVE_BLACKLIST,
                        resource_id=lobby.thread_id,
                    ),
                )
            )
    else:
        container.add_separator()
        if lobby.private:
            container.add_text(
                TextDisplay(
                    markdown_content=text.get(
                        "lobby.private_access_summary",
                        approved=len(lobby.approved),
                        pending=len(lobby.pending_requests),
                    )
                )
            )
        container.add_text(
            TextDisplay(
                markdown_content=text.get("lobby.blacklist_count", count=len(lobby.blacklist))
            )
        )

    # Game Settings Section
    if meta.settings:
        container.add_separator()
        container.add_text(
            TextDisplay(
                markdown_content=text.get("lobby.game_options_title", settings_emoji=settings_emoji),
                size_style=TextSize.SUBHEADER,
            )
        )

        for option in meta.settings[:6]:
            opt_emoji = get_option_emoji(emoji, option)
            if option.type == OptionType.BOOL:
                current = bool(lobby.settings.get(option.key, option.default))
                choices = [
                    SelectChoice(
                        label=text.get("lobby.on_label"),
                        value="true",
                        default=current,
                        description=text.get("lobby.enable_option_desc", title=option.title.lower()),
                        emoji="success",
                    ),
                    SelectChoice(
                        label=text.get("lobby.off_label"),
                        value="false",
                        default=not current,
                        description=text.get("lobby.disable_option_desc", title=option.title.lower()),
                        emoji="error",
                    ),
                ]
            elif option.type == OptionType.CHOICE:
                current = str(lobby.settings.get(option.key, option.default))
                choices = [
                    SelectChoice(
                        label=value.capitalize(),
                        value=value,
                        default=value == current,
                        description=text.get("lobby.set_choice_option_desc", title=option.title.lower(), value=value),
                        emoji=choice_emoji_for(option, value),
                    )
                    for value in (option.choices or ())
                ]
            else:
                minimum = option.minimum if option.minimum is not None else int(option.default)
                maximum = option.maximum if option.maximum is not None else minimum + 5
                current = int(lobby.settings.get(option.key, option.default))
                choices = [
                    SelectChoice(
                        label=str(v),
                        value=str(v),
                        default=v == current,
                        description=text.get("lobby.set_int_option_desc", title=option.title.lower(), value=v),
                        emoji="user",
                    )
                    for v in range(minimum, min(maximum, minimum + 10) + 1)
                ]

            container.add_described_select(
                DescribedSelect(
                    label=f"**{opt_emoji} {option.title}**",
                    description=option.description,
                    select=Select(
                        source="opt",
                        placeholder=text.get("lobby.configure_option_placeholder", title=option.title),
                        choices=choices,
                        payload={"option_key": option.key, "option_type": option.type.value},
                        route_prefix=P.LOBBY_OPT,
                        resource_id=lobby.thread_id,
                    ),
                )
            )

    # General / Admin Reset & Teardown Buttons
    container.add_separator()
    rules = ActionRow()
    rules.add_button(
        Button(
            source="reset_priv",
            label=text.get("lobby.reset_privacy_label"),
            style=ButtonStyle.SECONDARY,
            emoji="previous",
            route_prefix=P.LOBBY_RESET_PRIV,
            resource_id=lobby.thread_id,
        )
    )
    rules.add_button(
        Button(
            source="reset_rules",
            label=text.get("lobby.reset_rules_label"),
            style=ButtonStyle.SECONDARY,
            emoji="restart",
            route_prefix=P.LOBBY_RESET_RULES,
            resource_id=lobby.thread_id,
        )
    )
    rules.add_button(
        Button(
            source="end",
            label=text.get("lobby.end_lobby_label"),
            style=ButtonStyle.DANGER,
            emoji="leave",
            route_prefix=P.LOBBY_END,
            resource_id=lobby.thread_id,
        )
    )
    container.add_action_row(rules)

    # Add the single, styled container to the LayoutView
    view.add_container(container)
    return view
