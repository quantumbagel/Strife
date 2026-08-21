from __future__ import annotations

import discord

from strife.config.text import TextConfig
from strife.engine.metadata import (
    GameMetadata,
    OptionType,
    SettingOption,
    choice_emoji_for,
    int_setting_bounds,
    int_setting_fits_select,
)
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
    TextDisplay,
    TextSize,
    UserSelect,
    small_text,
    walk_interactive,
)
from strife.presentation.emoji import EmojiResolver, get_game_emoji
from strife.presentation.roster import bot_label, member_line
from strife.routing import prefixes as P
from strife.settings import get_settings

SETTINGS_TABS = ("general", "access", "rules")


def normalize_settings_tab(tab: str | None) -> str:
    if tab in SETTINGS_TABS:
        return tab
    return "general"


def get_option_emoji(resolver: EmojiResolver, option: SettingOption) -> str:
    if option.emoji:
        return resolver.get(option.emoji)
    if option.key in resolver.config.entries:
        return resolver.get(option.key)
    return resolver.get("settings")


def _add_title(
    container: Container,
    *,
    lobby: Lobby,
    meta: GameMetadata,
    emoji: EmojiResolver,
    text: TextConfig,
    tab: str,
) -> None:
    game_emoji = get_game_emoji(emoji, meta.key)
    forward_emoji = emoji.get("forward")
    settings_emoji = emoji.get("settings")
    tab_label = text.get(f"lobby.settings_tab_{tab}")
    container.add_text(
        TextDisplay(
            markdown_content=text.get(
                "lobby.settings_title",
                game_emoji=game_emoji,
                game_name=meta.name,
                forward_emoji=forward_emoji,
                settings_emoji=settings_emoji,
                tab=tab_label,
            ),
            size_style=TextSize.HEADER,
        )
    )
    container.add_separator()


def _add_general_tab(
    container: Container,
    *,
    lobby: Lobby,
    meta: GameMetadata,
    emoji: EmojiResolver,
    text: TextConfig,
) -> None:
    container.add_text(
        TextDisplay(
            markdown_content=text.get("lobby.privacy_title", private_emoji=emoji.get("private")),
            size_style=TextSize.SUBHEADER,
        )
    )
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
            f"• {member_line(
                emoji,
                user_id=None,
                display_name=bot.name,
                is_bot=True,
                bot_difficulty=bot.difficulty,
            )}"
            for bot in lobby.bots
        ]
        roster_body = "\n".join(bot_lines) if bot_lines else ""
        container.add_text(
            TextDisplay(
                markdown_content=(
                    f"{text.get('lobby.bots_title', bot_emoji=bot_emoji)}\n"
                    f"{text.get('lobby.bots_summary') + '\n' if len(lobby.bots) else '\n'}"
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
                                label=bot_label(emoji, bot.name, difficulty=bot.difficulty),
                                value=bot.name,
                                description=text.get(
                                    "lobby.remove_bot_desc",
                                    name=bot_label(emoji, bot.name),
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

    container.add_separator()
    admin = ActionRow()
    if lobby.ready:
        admin.add_button(
            Button(
                source="clear_ready",
                label=text.get("lobby.clear_ready_label"),
                style=ButtonStyle.SECONDARY,
                emoji="restart",
                route_prefix=P.LOBBY_CLEAR_READY,
                resource_id=lobby.thread_id,
            )
        )
    admin.add_button(
        Button(
            source="reset_priv",
            label=text.get("lobby.reset_privacy_label"),
            style=ButtonStyle.SECONDARY,
            emoji="previous",
            route_prefix=P.LOBBY_RESET_PRIV,
            resource_id=lobby.thread_id,
        )
    )
    admin.add_button(
        Button(
            source="end",
            label=text.get("lobby.end_lobby_label"),
            style=ButtonStyle.DANGER,
            emoji="leave",
            route_prefix=P.LOBBY_END,
            resource_id=lobby.thread_id,
        )
    )
    container.add_action_row(admin)


def _add_access_tab(
    container: Container,
    *,
    lobby: Lobby,
    emoji: EmojiResolver,
    text: TextConfig,
    interaction: discord.Interaction | None,
) -> None:
    settings_tab_payload = {"settings_tab": "access"}
    owner_ids = frozenset(get_settings().owner_ids)

    if interaction and interaction.guild:
        guild = interaction.guild

        seated_names = {
            member.user_id: member.display_name
            for member in lobby.members
            if member.user_id is not None
        }

        def get_member_name(uid: int) -> str:
            if uid in seated_names:
                return seated_names[uid]
            member = guild.get_member(uid)
            return member.display_name if member else f"User ID: {uid}"

        if lobby.private:
            summary = text.get(
                "lobby.private_access_summary",
                approved=len(lobby.approved),
                pending=len(lobby.pending_requests),
            )
            if lobby.pending_requests:
                summary += f"\n{text.get('lobby.pending_requests_hint', count=len(lobby.pending_requests))}"
            container.add_text(
                TextDisplay(
                    markdown_content=summary,
                    size_style=TextSize.SUBHEADER,
                )
            )
            container.add_separator()

        seated_lines = [
            member_line(
                emoji,
                user_id=member.user_id,
                display_name=member.display_name,
                owner_ids=owner_ids,
                creator_id=lobby.creator_id,
                suffix=(
                    f"({text.get('lobby.ready')})"
                    if member.user_id in lobby.ready
                    else f"({text.get('lobby.not_ready')})"
                ),
            )
            for member in lobby.members
        ]
        container.add_text(
            TextDisplay(
                markdown_content=(
                    f"{text.get('lobby.seated_players_title', user_emoji=emoji.get('user'))}\n"
                    + ("\n".join(seated_lines) if seated_lines else text.get("lobby.empty_roster"))
                ),
                size_style=TextSize.SUBHEADER,
            )
        )

        kickable = [m for m in lobby.members if m.user_id != lobby.creator_id]
        if kickable:
            container.add_described_select(
                DescribedSelect(
                    description=text.get("lobby.kick_player_select_desc"),
                    select=Select(
                        source="kick",
                        placeholder=text.get("lobby.kick_player_placeholder"),
                        choices=[
                            SelectChoice(
                                label=member.display_name,
                                value=str(member.user_id),
                                description=text.get(
                                    "lobby.kick_player_desc", name=member.display_name
                                ),
                                emoji="leave",
                            )
                            for member in kickable
                        ],
                        route_prefix=P.LOBBY_KICK,
                        resource_id=lobby.thread_id,
                        payload=settings_tab_payload,
                    ),
                )
            )

        if lobby.private:
            seated_ids = {member.user_id for member in lobby.members}
            preapproved = [uid for uid in lobby.approved if uid not in seated_ids]
            container.add_separator()
            pre_names = [get_member_name(uid) for uid in preapproved]
            pre_str = ", ".join(pre_names) if pre_names else "_None_"
            container.add_text(
                TextDisplay(
                    markdown_content=(
                        f"{text.get('lobby.preapprove_title', success_emoji=emoji.get('success'))}\n"
                        f"{text.get('lobby.preapprove_summary', names=pre_str)}"
                    ),
                    size_style=TextSize.SUBHEADER,
                )
            )
            container.add_described_select(
                DescribedSelect(
                    description=text.get("lobby.preapprove_select_desc"),
                    select=UserSelect(
                        source="preapprove",
                        placeholder=text.get("lobby.preapprove_placeholder"),
                        route_prefix=P.LOBBY_PRE_APPROVE,
                        resource_id=lobby.thread_id,
                        payload=settings_tab_payload,
                    ),
                )
            )
            if preapproved:
                container.add_described_select(
                    DescribedSelect(
                        description=text.get("lobby.revoke_approval_select_desc"),
                        select=Select(
                            source="revoke_approval",
                            placeholder=text.get("lobby.revoke_approval_placeholder"),
                            choices=[
                                SelectChoice(
                                    label=get_member_name(uid),
                                    value=str(uid),
                                    description=text.get(
                                        "lobby.revoke_approval_desc",
                                        name=get_member_name(uid),
                                    ),
                                    emoji="error",
                                )
                                for uid in preapproved
                            ],
                            route_prefix=P.LOBBY_REVOKE_APPROVAL,
                            resource_id=lobby.thread_id,
                            payload=settings_tab_payload,
                        ),
                    )
                )

        container.add_separator()
        bl_ids = list(lobby.blacklist)
        bl_names = [get_member_name(uid) for uid in bl_ids]
        bl_str = ", ".join(bl_names) if bl_names else "_None_"
        container.add_text(
            TextDisplay(
                markdown_content=(
                    f"{text.get('lobby.access_control_title', ban_emoji=emoji.get('ban'))}\n"
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
                    payload=settings_tab_payload,
                ),
            )
        )
        if bl_ids:
            container.add_described_select(
                DescribedSelect(
                    description=text.get("lobby.remove_blacklist_select_desc"),
                    select=Select(
                        source="remove_blacklist",
                        placeholder=text.get("lobby.remove_blacklist_placeholder"),
                        choices=[
                            SelectChoice(
                                label=get_member_name(uid),
                                value=str(uid),
                                description=text.get("lobby.allow_join_again_desc"),
                                emoji="join",
                            )
                            for uid in bl_ids
                        ],
                        route_prefix=P.LOBBY_REMOVE_BLACKLIST,
                        resource_id=lobby.thread_id,
                        payload=settings_tab_payload,
                    ),
                )
            )
    else:
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


def _add_rules_tab(
    container: Container,
    *,
    lobby: Lobby,
    meta: GameMetadata,
    emoji: EmojiResolver,
    text: TextConfig,
) -> None:
    settings_emoji = emoji.get("settings")
    if not meta.settings:
        container.add_text(
            TextDisplay(
                markdown_content=text.get("lobby.no_game_options"),
                size_style=TextSize.BODY,
            )
        )
        return

    container.add_text(
        TextDisplay(
            markdown_content=text.get("lobby.game_options_title", settings_emoji=settings_emoji),
            size_style=TextSize.SUBHEADER,
        )
    )

    for option in meta.settings:
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
                    description=text.get(
                        "lobby.set_choice_option_desc", title=option.title.lower(), value=value
                    ),
                    emoji=choice_emoji_for(option, value),
                )
                for value in (option.choices or ())
            ]
        elif option.type == OptionType.INT:
            _add_int_setting(
                container,
                lobby=lobby,
                option=option,
                emoji=emoji,
                text=text,
                opt_emoji=opt_emoji,
            )
            continue

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

    container.add_separator()
    reset = ActionRow()
    reset.add_button(
        Button(
            source="reset_rules",
            label=text.get("lobby.reset_rules_label"),
            style=ButtonStyle.SECONDARY,
            emoji="restart",
            route_prefix=P.LOBBY_RESET_RULES,
            resource_id=lobby.thread_id,
        )
    )
    container.add_action_row(reset)


def _add_int_setting(
    container: Container,
    *,
    lobby: Lobby,
    option: SettingOption,
    emoji: EmojiResolver,
    text: TextConfig,
    opt_emoji: str,
) -> None:
    minimum, maximum = int_setting_bounds(option)
    current = int(lobby.settings.get(option.key, option.default))

    if int_setting_fits_select(option):
        choices = [
            SelectChoice(
                label=str(value),
                value=str(value),
                default=value == current,
                description=text.get(
                    "lobby.set_int_option_desc", title=option.title.lower(), value=value
                ),
                emoji="user",
            )
            for value in range(minimum, maximum + 1)
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
        return

    container.add_text(
        TextDisplay(
            markdown_content=(
                f"**{opt_emoji} {option.title}**\n"
                f"{small_text(option.description)}\n"
                f"{text.get('lobby.int_option_current', value=current, minimum=minimum, maximum=maximum)}"
            ),
            size_style=TextSize.BODY,
        )
    )
    container.add_separator()
    row = ActionRow()
    row.add_button(
        Button(
            source="opt_modal",
            label=text.get("lobby.set_int_option_button", title=option.title),
            style=ButtonStyle.SECONDARY,
            emoji="settings",
            route_prefix=P.LOBBY_OPT_MODAL,
            resource_id=lobby.thread_id,
            payload={"option_key": option.key},
        )
    )
    container.add_action_row(row)


def _add_tab_nav(
    container: Container,
    *,
    lobby: Lobby,
    meta: GameMetadata,
    text: TextConfig,
    active_tab: str,
) -> None:
    container.add_separator()
    nav = ActionRow()
    tabs: list[tuple[str, str, str]] = [
        ("general", "lobby.settings_tab_general", "settings"),
        ("access", "lobby.settings_tab_access", "user"),
    ]
    if meta.settings:
        tabs.append(("rules", "lobby.settings_tab_rules", "restart"))

    for tab_key, label_key, tab_emoji in tabs:
        nav.add_button(
            Button(
                source="tab",
                label=text.get(label_key),
                style=ButtonStyle.PRIMARY if tab_key == active_tab else ButtonStyle.SECONDARY,
                emoji=tab_emoji,
                route_prefix=P.LOBBY_SETTINGS,
                resource_id=lobby.thread_id,
                payload={"tab": tab_key},
                disabled=tab_key == active_tab,
            )
        )
    container.add_action_row(nav)


def _apply_readonly(view: LayoutView) -> None:
    for item in walk_interactive(view):
        if isinstance(item, Button) and item.source == "tab":
            continue
        item.disabled = True


def build_settings_view(
    lobby: Lobby,
    meta: GameMetadata,
    emoji: EmojiResolver,
    text: TextConfig,
    interaction: discord.Interaction | None = None,
    *,
    tab: str = "general",
    readonly: bool = False,
) -> LayoutView:
    active_tab = normalize_settings_tab(tab)
    if active_tab == "rules" and not meta.settings:
        active_tab = "general"

    view = LayoutView()
    container = Container()
    _add_title(container, lobby=lobby, meta=meta, emoji=emoji, text=text, tab=active_tab)

    if readonly:
        container.add_text(
            TextDisplay(
                markdown_content=text.get(
                    "lobby.settings_readonly_hint",
                    creator=f"<@{lobby.creator_id}>",
                ),
                size_style=TextSize.BODY,
            )
        )
        container.add_separator()

    if active_tab == "general":
        _add_general_tab(container, lobby=lobby, meta=meta, emoji=emoji, text=text)
    elif active_tab == "access":
        _add_access_tab(
            container,
            lobby=lobby,
            emoji=emoji,
            text=text,
            interaction=interaction,
        )
    else:
        _add_rules_tab(container, lobby=lobby, meta=meta, emoji=emoji, text=text)

    _add_tab_nav(container, lobby=lobby, meta=meta, text=text, active_tab=active_tab)
    view.add_container(container)
    if readonly:
        _apply_readonly(view)
    return view
