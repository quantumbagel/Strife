from __future__ import annotations

import discord

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
    UserSelect,
)
from strife.presentation.emoji import EmojiResolver, get_game_emoji
from strife.routing import prefixes as P


def get_option_emoji(resolver: EmojiResolver, key: str) -> str:
    if key in resolver.config.entries:
        return resolver.get(key)
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

    # Privacy Status display
    privacy_status = (
        text.get("lobby.private_status_body", private_emoji=emoji.get("private"))
        if lobby.private
        else text.get("lobby.public_status_body", public_emoji=emoji.get("public"))
    )
    container.add_text(
        TextDisplay(
            markdown_content=text.get("lobby.privacy_status", status=privacy_status),
            size_style=TextSize.BODY,
        )
    )

    # Privacy configuration selector
    priv_row = ActionRow()
    priv_row.add_select(
        Select(
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
        )
    )
    container.add_action_row(priv_row)

    # Reset Privacy action row
    reset_priv = ActionRow()
    reset_priv.add_button(
        Button(
            source="reset_priv",
            label=text.get("lobby.reset_privacy_label"),
            style=ButtonStyle.SECONDARY,
            emoji="previous",
            route_prefix=P.LOBBY_RESET_PRIV,
            resource_id=lobby.thread_id,
        )
    )
    container.add_action_row(reset_priv)

    # Access Lists (Whitelist & Blacklist) Management
    if interaction and interaction.guild:
        guild = interaction.guild
        members = guild.members
        
        # Exclude bot itself and the lobby creator from candidates
        bot_user_id = guild.me.id if guild.me else None
        candidates = [
            m for m in members 
            if not m.bot and m.id != lobby.creator_id and m.id != bot_user_id
        ]
        candidates.sort(key=lambda m: m.display_name.lower())

        # Whitelist & Blacklist Display Names
        def get_member_name(uid: int) -> str:
            member = guild.get_member(uid)
            return member.display_name if member else f"User ID: {uid}"

        container.add_separator()
        if lobby.private:
            wl_names = [get_member_name(uid) for uid in lobby.whitelist]
            wl_str = ", ".join(wl_names) if wl_names else "_None_"
            container.add_text(
                TextDisplay(
                    markdown_content=(
                        f"{text.get('lobby.access_control_title', user_emoji=emoji.get('user'))}\n"
                        f"{text.get('lobby.whitelisted_label', whitelist=wl_str)}"
                    ),
                    size_style=TextSize.SUBHEADER,
                )
            )

            # Whitelist Add Selector
            container.add_action_row(
                ActionRow().add_user_select(
                    UserSelect(
                        source="add_whitelist",
                        placeholder=text.get("lobby.add_whitelist_placeholder"),
                        route_prefix=P.LOBBY_ADD_WHITELIST,
                        resource_id=lobby.thread_id,
                    )
                )
            )

            # Whitelist Remove Selector
            if lobby.whitelist:
                container.add_action_row(
                    ActionRow().add_select(
                        Select(
                            source="remove_whitelist",
                            placeholder=text.get("lobby.remove_whitelist_placeholder"),
                            choices=[
                                SelectChoice(
                                    label=get_member_name(uid),
                                    value=str(uid),
                                    description=text.get("lobby.revoke_join_desc"),
                                    emoji="error",
                                )
                                for uid in lobby.whitelist
                            ][:25],
                            route_prefix=P.LOBBY_REMOVE_WHITELIST,
                            resource_id=lobby.thread_id,
                        )
                    )
                )
        else:
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

            # Blacklist Add Selector
            container.add_action_row(
                ActionRow().add_user_select(
                    UserSelect(
                        source="add_blacklist",
                        placeholder=text.get("lobby.add_blacklist_placeholder"),
                        route_prefix=P.LOBBY_ADD_BLACKLIST,
                        resource_id=lobby.thread_id,
                    )
                )
            )

            # Blacklist Remove Selector
            if lobby.blacklist:
                container.add_action_row(
                    ActionRow().add_select(
                        Select(
                            source="remove_blacklist",
                            placeholder=text.get("lobby.remove_blacklist_placeholder"),
                            choices=[
                                SelectChoice(
                                    label=get_member_name(uid),
                                    value=str(uid),
                                    description=text.get("lobby.allow_join_again_desc"),
                                    emoji="success",
                                )
                                for uid in lobby.blacklist
                            ][:25],
                            route_prefix=P.LOBBY_REMOVE_BLACKLIST,
                            resource_id=lobby.thread_id,
                        )
                    )
                )
    else:
        container.add_separator()
        if lobby.private:
            container.add_text(
                TextDisplay(
                    markdown_content=text.get("lobby.whitelist_count", count=len(lobby.whitelist))
                )
            )
        else:
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
            opt_emoji = get_option_emoji(emoji, option.key)
            container.add_text(
                TextDisplay(
                    markdown_content=f"**{opt_emoji} {option.title}**",
                    size_style=TextSize.BODY,
                )
            )

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
                        emoji="pointing",
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

            opt_row = ActionRow()
            opt_row.add_select(
                Select(
                    source="opt",
                    placeholder=text.get("lobby.configure_option_placeholder", title=option.title),
                    choices=choices,
                    payload={"option_key": option.key, "option_type": option.type.value},
                    route_prefix=P.LOBBY_OPT,
                    resource_id=lobby.thread_id,
                )
            )
            container.add_action_row(opt_row)

            container.add_text(
                TextDisplay(
                    markdown_content=f"-# {option.description}",
                    size_style=TextSize.BODY,
                )
            )

    # General / Admin Reset & Teardown Buttons
    container.add_separator()
    rules = ActionRow()
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
