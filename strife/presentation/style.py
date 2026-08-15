"""Shared Components V2 layout language.

Platform commands (catalog, about, settings, profile, errors/success) established
this look. Games and remaining command surfaces should use the same helpers.

Layout
------
One ``Container`` per message.

1. Header — ``### {emoji} Title``. Breadcrumbs use the ``forward`` emoji:
   ``Strife {forward} Catalog``.
2. Subtitle — one ``-#`` line for counts, status, or a short hint.
3. Divider — a visible separator between major sections. Use an invisible
   spacer when you only need air, not a rule.
4. Body — sentence-case copy, ``**Section Title**`` headings, custom-emoji
   bullets. Metadata and hints stay on ``-#`` lines.
5. Actions — ``SECONDARY`` by default, ``PRIMARY`` for the main CTA,
   ``SUCCESS`` / ``DANGER`` only when the action itself confirms or destroys.

Rules
-----
- Custom application emoji only. Do not decorate chrome with unicode
  (⚠️ 🕵️ 📍 ⏱️ 💥 👑). Game-piece glyphs (dice faces, X/O, role keys) are
  fine when they come from the emoji catalog.
- Title Case button labels. Short, calm copy — no ALL CAPS, no ornamental
  titles like ``C O U P``.
- Feedback (success / error / query notice) is header + optional body, never
  a bare ``send_message("...")``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from strife.presentation.components import (
    Container,
    LayoutView,
    Separator,
    TextDisplay,
    TextSize,
    small_text,
)

if TYPE_CHECKING:
    from strife.presentation.emoji import EmojiResolver


def add_header(container: Container, title: str, *, emoji: str | None = None) -> Container:
    prefix = f"{emoji} " if emoji else ""
    container.add_text(
        TextDisplay(
            markdown_content=f"### {prefix}{title}",
            size_style=TextSize.HEADER,
        )
    )
    return container


def add_subtitle(container: Container, text: str, *, emoji: str | None = None) -> Container:
    prefix = f"{emoji} " if emoji else ""
    container.add_text(
        TextDisplay(
            markdown_content=small_text(f"{prefix}{text}"),
            size_style=TextSize.BODY,
        )
    )
    return container


def add_body(container: Container, text: str) -> Container:
    container.add_text(
        TextDisplay(
            markdown_content=text,
            size_style=TextSize.BODY,
        )
    )
    return container


def add_section(container: Container, title: str, body: str | None = None) -> Container:
    content = f"**{title}**"
    if body:
        content = f"{content}\n{body}"
    return add_body(container, content)


def add_meta(container: Container, text: str) -> Container:
    return add_subtitle(container, text)


def add_divider(container: Container) -> Container:
    container.add_separator(Separator(visible=True))
    return container


def add_spacer(container: Container) -> Container:
    container.add_separator(Separator(visible=False))
    return container


def bullet_lines(
    items: list[str],
    *,
    emoji: EmojiResolver | None = None,
    bullet: str | None = None,
) -> str:
    mark = bullet
    if mark is None and emoji is not None:
        mark = emoji.get("bullet")
    if mark is None:
        mark = "•"
    return "\n".join(f"{mark} {item}" for item in items)


def history_block(
    entries: list[str],
    emoji: EmojiResolver,
    *,
    title: str = "Recent Events",
    limit: int = 5,
) -> str | None:
    if not entries:
        return None
    shown = entries[-limit:]
    return f"**{title}**\n{bullet_lines(shown, emoji=emoji)}"


def notice_view(
    *,
    title: str,
    emoji: str | None = None,
    body: str | None = None,
    sections: list[tuple[str, str]] | None = None,
) -> LayoutView:
    """Compact ephemeral panel used for peeks, notices, and query errors."""
    container = Container()
    add_header(container, title, emoji=emoji)
    if body:
        add_spacer(container)
        add_body(container, body)
    if sections:
        for section_title, section_body in sections:
            add_divider(container)
            add_section(container, section_title, section_body)
    view = LayoutView()
    view.add_container(container)
    return view
