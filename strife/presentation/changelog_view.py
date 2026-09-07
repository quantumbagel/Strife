"""About → Changes: bot, platform, and per-game changelog.toml notes."""

from __future__ import annotations

from collections.abc import Sequence

from strife.changelog import Changelog, ChangelogCatalog, Release
from strife.config.text import TextConfig
from strife.engine.metadata import GameMetadata
from strife.presentation.components import (
    ActionRow,
    Button,
    ButtonStyle,
    Container,
    LayoutView,
    Section,
    Separator,
    TextDisplay,
    TextSize,
)
from strife.presentation.emoji import EmojiResolver
from strife.routing import prefixes as P

RELEASES_PER_PAGE = 3
GAMES_PER_PAGE = 3
HUB_GAME_PREVIEWS = 3


def _payload(
    *,
    scope: str = "hub",
    game: str | None = None,
    page: int = 0,
) -> dict:
    data: dict = {"tab": "changes", "scope": scope, "page": page}
    if game:
        data["game"] = game
    return data


def _scope_button(
    text: TextConfig,
    *,
    source: str,
    label_key: str,
    scope: str,
    current: str,
    emoji: str,
    game: str | None = None,
) -> Button:
    active = current == scope and (scope != "game" or game is None)
    return Button(
        source=source,
        label=text.get(label_key),
        style=ButtonStyle.PRIMARY if active else ButtonStyle.SECONDARY,
        emoji=emoji,
        disabled=active,
        route_prefix=P.ABOUT_NAV,
        payload=_payload(scope=scope),
    )


def _back_row(text: TextConfig, *, to_hub: bool = False) -> ActionRow:
    row = ActionRow()
    row.add_button(
        Button(
            source="back",
            label=text.get("about.back_btn"),
            style=ButtonStyle.SECONDARY,
            emoji="previous",
            route_prefix=P.ABOUT_NAV,
            payload=_payload() if to_hub else {"tab": "main"},
        )
    )
    return row


def _page_row(
    text: TextConfig,
    *,
    page: int,
    pages: int,
    scope: str,
    game: str | None = None,
) -> ActionRow | None:
    if pages <= 1:
        return None
    row = ActionRow()
    row.add_button(
        Button(
            source="prev",
            label=text.get("common.prev"),
            style=ButtonStyle.SECONDARY,
            emoji="previous",
            disabled=page <= 0,
            route_prefix=P.ABOUT_NAV,
            payload=_payload(scope=scope, game=game, page=max(0, page - 1)),
        )
    )
    row.add_button(
        Button(
            source="page",
            label=text.get("changes.page", page=page + 1, pages=pages),
            style=ButtonStyle.SECONDARY,
            disabled=True,
            route_prefix=P.ABOUT_NAV,
            payload=_payload(scope=scope, game=game, page=page),
        )
    )
    row.add_button(
        Button(
            source="next",
            label=text.get("common.next"),
            style=ButtonStyle.SECONDARY,
            emoji="next",
            disabled=page >= pages - 1,
            route_prefix=P.ABOUT_NAV,
            payload=_payload(scope=scope, game=game, page=min(pages - 1, page + 1)),
        )
    )
    return row


def _scope_row(text: TextConfig, current: str) -> ActionRow:
    row = ActionRow()
    row.add_button(
        _scope_button(
            text,
            source="bot",
            label_key="changes.bot_btn",
            scope="bot",
            current=current,
            emoji="logo",
        )
    )
    row.add_button(
        _scope_button(
            text,
            source="platform",
            label_key="changes.platform_btn",
            scope="platform",
            current=current,
            emoji="configure",
        )
    )
    row.add_button(
        _scope_button(
            text,
            source="games",
            label_key="changes.games_btn",
            scope="games",
            current=current,
            emoji="game",
        )
    )
    return row


def format_release(release: Release, emoji: EmojiResolver, *, heading: bool = True) -> str:
    bullet = emoji.get("bullet")
    date = f" · {release.date}" if release.date else ""
    lines: list[str] = []
    if heading:
        lines.append(f"**{release.version}**{date}")
        if release.summary:
            lines.append(release.summary)
    elif release.summary:
        lines.append(release.summary)
    labels = (
        ("added", release.added),
        ("changed", release.changed),
        ("fixed", release.fixed),
        ("removed", release.removed),
    )
    for kind, items in labels:
        title = kind.capitalize()
        for item in items:
            lines.append(f"{bullet} **{title}** {item}")
    return "\n".join(lines)


def _latest_line(release: Release | None, *, fallback: str) -> str:
    if release is None:
        return fallback
    date = f" · {release.date}" if release.date else ""
    summary = release.summary or fallback
    return f"**{release.version}**{date}\n{summary}"


def _add_header(
    container: Container,
    emoji: EmojiResolver,
    text: TextConfig,
    *,
    title_key: str,
    subtitle: str,
    **title_kwargs: object,
) -> None:
    logo = emoji.get("logo")
    forward = emoji.get("forward")
    container.add_text(
        TextDisplay(
            markdown_content=text.get(title_key, logo=logo, forward=forward, **title_kwargs),
            size_style=TextSize.HEADER,
        )
    )
    container.add_text(TextDisplay(markdown_content=subtitle))
    container.add_separator(Separator(visible=False))
    container.add_separator()


def _hub(
    emoji: EmojiResolver,
    text: TextConfig,
    changelogs: ChangelogCatalog,
    games: Sequence[GameMetadata],
) -> LayoutView:
    view = LayoutView()
    container = Container()
    time_emoji = emoji.get("time")
    _add_header(
        container,
        emoji,
        text,
        title_key="changes.title",
        subtitle=text.get("changes.subtitle", time=time_emoji),
    )

    bot_latest = changelogs.bot.latest
    platform_latest = changelogs.platform.latest
    container.add_text(
        TextDisplay(
            markdown_content=(
                f"{emoji.get('logo')} **{text.get('changes.bot_heading')}**\n"
                f"{_latest_line(bot_latest, fallback=text.get('changes.empty'))}"
            )
        )
    )
    container.add_separator()
    container.add_text(
        TextDisplay(
            markdown_content=(
                f"{emoji.get('configure')} **{text.get('changes.platform_heading')}**\n"
                f"{_latest_line(platform_latest, fallback=text.get('changes.empty'))}"
            )
        )
    )

    previews = [meta for meta in games if changelogs.latest_for_game(meta.key)][:HUB_GAME_PREVIEWS]
    if previews:
        container.add_separator()
        blocks: list[str] = []
        for meta in previews:
            latest = changelogs.latest_for_game(meta.key)
            assert latest is not None
            date = f" · {latest.date}" if latest.date else ""
            blocks.append(
                f"{emoji.get_game_emoji(meta.key)} **{meta.name}** {latest.version}{date}\n"
                f"{latest.summary}"
            )
        container.add_text(TextDisplay(markdown_content="\n\n".join(blocks)))

    container.add_action_row(_scope_row(text, "hub"))
    container.add_action_row(_back_row(text))
    view.add_container(container)
    return view


def _detail(
    emoji: EmojiResolver,
    text: TextConfig,
    changelog: Changelog,
    *,
    title_key: str,
    subtitle: str,
    scope: str,
    page: int,
    title_kwargs: dict | None = None,
) -> LayoutView:
    view = LayoutView()
    container = Container()
    _add_header(
        container,
        emoji,
        text,
        title_key=title_key,
        subtitle=subtitle,
        **(title_kwargs or {}),
    )
    releases = changelog.releases
    pages = max(1, (len(releases) + RELEASES_PER_PAGE - 1) // RELEASES_PER_PAGE)
    page = max(0, min(page, pages - 1))
    chunk = releases[page * RELEASES_PER_PAGE : (page + 1) * RELEASES_PER_PAGE]
    if not chunk:
        container.add_text(TextDisplay(markdown_content=text.get("changes.empty")))
    else:
        for index, release in enumerate(chunk):
            if index > 0:
                container.add_separator()
            container.add_text(TextDisplay(markdown_content=format_release(release, emoji)))
    nav = _page_row(text, page=page, pages=pages, scope=scope, game=changelog.key)
    if nav is not None:
        container.add_action_row(nav)
    container.add_action_row(_scope_row(text, scope))
    container.add_action_row(_back_row(text, to_hub=True))
    view.add_container(container)
    return view


def _games_list(
    emoji: EmojiResolver,
    text: TextConfig,
    changelogs: ChangelogCatalog,
    games: Sequence[GameMetadata],
    *,
    page: int,
) -> LayoutView:
    view = LayoutView()
    container = Container()
    game_emoji = emoji.get("game")
    _add_header(
        container,
        emoji,
        text,
        title_key="changes.games_title",
        subtitle=text.get("changes.games_subtitle", game=game_emoji, count=len(games)),
    )
    if not games:
        container.add_text(TextDisplay(markdown_content=text.get("changes.no_games")))
    else:
        pages = max(1, (len(games) + GAMES_PER_PAGE - 1) // GAMES_PER_PAGE)
        page = max(0, min(page, pages - 1))
        chunk = games[page * GAMES_PER_PAGE : (page + 1) * GAMES_PER_PAGE]
        for index, meta in enumerate(chunk):
            if index > 0:
                container.add_separator()
            latest = changelogs.latest_for_game(meta.key)
            date = f" · {latest.date}" if latest and latest.date else ""
            summary = latest.summary if latest else text.get("changes.empty")
            version = latest.version if latest else meta.version
            section = Section(
                accessory=Button(
                    source="game",
                    label=text.get("changes.view_btn"),
                    style=ButtonStyle.SECONDARY,
                    emoji="learn",
                    route_prefix=P.ABOUT_NAV,
                    payload=_payload(scope="game", game=meta.key),
                )
            )
            section.add_text(
                TextDisplay(
                    markdown_content=(
                        f"{emoji.get_game_emoji(meta.key)} **{meta.name}**\n"
                        f"{summary}\n"
                        f"-# v{version}{date}"
                    )
                )
            )
            container.add_section(section)
        nav = _page_row(text, page=page, pages=pages, scope="games")
        if nav is not None:
            container.add_action_row(nav)
    container.add_action_row(_scope_row(text, "games"))
    container.add_action_row(_back_row(text, to_hub=True))
    view.add_container(container)
    return view


def build_changes_view(
    emoji: EmojiResolver,
    text: TextConfig,
    changelogs: ChangelogCatalog,
    games: Sequence[GameMetadata],
    *,
    scope: str = "hub",
    game_key: str | None = None,
    page: int = 0,
) -> LayoutView:
    if scope == "bot":
        latest = changelogs.bot.latest
        version = latest.version if latest else "—"
        return _detail(
            emoji,
            text,
            changelogs.bot,
            title_key="changes.bot_title",
            subtitle=text.get("changes.bot_subtitle", version=version),
            scope="bot",
            page=page,
        )
    if scope == "platform":
        latest = changelogs.platform.latest
        version = latest.version if latest else "—"
        return _detail(
            emoji,
            text,
            changelogs.platform,
            title_key="changes.platform_title",
            subtitle=text.get("changes.platform_subtitle", version=version),
            scope="platform",
            page=page,
        )
    if scope == "game" and game_key:
        meta = next((item for item in games if item.key == game_key), None)
        name = meta.name if meta else game_key
        changelog = changelogs.for_game(game_key)
        latest = changelog.latest
        version = latest.version if latest else (meta.version if meta else "—")
        game_emoji = emoji.get_game_emoji(game_key)
        return _detail(
            emoji,
            text,
            changelog,
            title_key="changes.game_title",
            subtitle=text.get("changes.game_subtitle", version=version),
            scope="game",
            page=page,
            title_kwargs={"game": name, "game_emoji": game_emoji},
        )
    if scope == "games":
        return _games_list(emoji, text, changelogs, games, page=page)
    return _hub(emoji, text, changelogs, games)
