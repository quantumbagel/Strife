from __future__ import annotations

import discord

from strife.config.text import TextConfig
from strife.engine.metadata import GameMetadata, OptionType, SettingOption
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
            markdown_content=f"### {game_emoji} {meta.name} {forward_emoji} {settings_emoji} Settings Configuration",
            size_style=TextSize.HEADER,
        )
    )

    # Privacy Status display
    privacy_status = (
        f"{emoji.get('private')} **Private** — Only whitelisted players can join this lobby."
        if lobby.private
        else f"{emoji.get('public')} **Public** — Anyone in the server can view and join."
    )
    container.add_text(
        TextDisplay(
            markdown_content=f"**Lobby Privacy:**\n{privacy_status}",
            size_style=TextSize.BODY,
        )
    )

    # Privacy configuration selector
    priv_row = ActionRow()
    priv_row.add_select(
        Select(
            source="priv",
            placeholder="Adjust Privacy Mode...",
            choices=[
                SelectChoice(
                    label="Public",
                    value="public",
                    default=not lobby.private,
                    description="Open lobby. Anyone in the server can join.",
                    emoji="public",
                ),
                SelectChoice(
                    label="Private",
                    value="private",
                    default=lobby.private,
                    description="Closed lobby. Restricted to whitelisted users.",
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
            label="Reset Privacy & Access Lists",
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
                    markdown_content=f"### {emoji.get('user')} Access Control Lists\n"
                                     f"**Whitelisted:** {wl_str}",
                    size_style=TextSize.SUBHEADER,
                )
            )

            # Whitelist Add Selector
            wl_add_candidates = [m for m in candidates if m.id not in lobby.whitelist]
            if wl_add_candidates:
                container.add_action_row(
                    ActionRow().add_select(
                        Select(
                            source="add_whitelist",
                            placeholder="➕ Add player to whitelist...",
                            choices=[
                                SelectChoice(
                                    label=m.display_name,
                                    value=str(m.id),
                                    description=f"Allow {m.display_name} to join",
                                    emoji="success",
                                )
                                for m in wl_add_candidates[:25]
                            ],
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
                            placeholder="➖ Remove player from whitelist...",
                            choices=[
                                SelectChoice(
                                    label=get_member_name(uid),
                                    value=str(uid),
                                    description=f"Revoke join access for this player",
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
                    markdown_content=f"### {emoji.get('user')} Access Control Lists\n"
                                     f"**Blacklisted:** {bl_str}",
                    size_style=TextSize.SUBHEADER,
                )
            )

            # Blacklist Add Selector
            bl_add_candidates = [m for m in candidates if m.id not in lobby.blacklist]
            if bl_add_candidates:
                container.add_action_row(
                    ActionRow().add_select(
                        Select(
                            source="add_blacklist",
                            placeholder="🚫 Blacklist player (restrict access)...",
                            choices=[
                                SelectChoice(
                                    label=m.display_name,
                                    value=str(m.id),
                                    description=f"Prevent {m.display_name} from joining",
                                    emoji="error",
                                )
                                for m in bl_add_candidates[:25]
                            ],
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
                            placeholder="🔓 Remove player from blacklist...",
                            choices=[
                                SelectChoice(
                                    label=get_member_name(uid),
                                    value=str(uid),
                                    description=f"Allow this player to join again",
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
                    markdown_content=f"**Access Lists:** whitelist {len(lobby.whitelist)}"
                )
            )
        else:
            container.add_text(
                TextDisplay(
                    markdown_content=f"**Access Lists:** blacklist {len(lobby.blacklist)}"
                )
            )

    # Game Settings Section
    if meta.settings:
        container.add_separator()
        container.add_text(
            TextDisplay(
                markdown_content=f"### {settings_emoji} Game-Specific Options",
                size_style=TextSize.SUBHEADER,
            )
        )

        for option in meta.settings:
            opt_emoji = get_option_emoji(emoji, option)
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
                        label="On",
                        value="true",
                        default=current,
                        description=f"Enable {option.title.lower()}",
                        emoji="success",
                    ),
                    SelectChoice(
                        label="Off",
                        value="false",
                        default=not current,
                        description=f"Disable {option.title.lower()}",
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
                        description=f"Set {option.title.lower()} to {value}",
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
                        description=f"Set {option.title.lower()} value to {v}",
                        emoji="user",
                    )
                    for v in range(minimum, min(maximum, minimum + 10) + 1)
                ]

            opt_row = ActionRow()
            opt_row.add_select(
                Select(
                    source="opt",
                    placeholder=f"Configure {option.title}...",
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
            label="Reset Game Rules",
            style=ButtonStyle.SECONDARY,
            emoji="rematch",
            route_prefix=P.LOBBY_RESET_RULES,
            resource_id=lobby.thread_id,
        )
    )
    rules.add_button(
        Button(
            source="end",
            label="End Lobby",
            style=ButtonStyle.DANGER,
            emoji="error",
            route_prefix=P.LOBBY_END,
            resource_id=lobby.thread_id,
        )
    )
    container.add_action_row(rules)

    # Add the single, styled container to the LayoutView
    view.add_container(container)
    return view
