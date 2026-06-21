from __future__ import annotations

from strife.presentation.components import (
    ActionRow,
    Button,
    ButtonStyle,
    LayoutView,
    Separator,
    TextDisplay,
    TextSize,
)


def header_text(emoji: str, title: str) -> TextDisplay:
    return TextDisplay(markdown_content=f"### {emoji} {title}", size_style=TextSize.HEADER)


def divider() -> Separator:
    return Separator()


def mention_list(title: str, mentions: list[str]) -> TextDisplay:
  body = "\n".join(f"- {m}" for m in mentions)
  return TextDisplay(markdown_content=f"**{title}**\n{body}", size_style=TextSize.BODY)


def paginator_row(
    *,
    prev_src: str,
    next_src: str,
    page: int,
    total: int,
    prev_payload: dict | None = None,
    next_payload: dict | None = None,
) -> ActionRow:
    row = ActionRow()
    row.add_button(
        Button(
            source=prev_src,
            label="Previous",
            style=ButtonStyle.SECONDARY,
            disabled=page <= 0,
            payload=prev_payload,
        )
    )
    row.add_button(
        Button(
            source="",
            label=f"Page {page + 1}/{max(total, 1)}",
            style=ButtonStyle.SECONDARY,
            disabled=True,
        )
    )
    row.add_button(
        Button(
            source=next_src,
            label="Next",
            style=ButtonStyle.SECONDARY,
            disabled=page >= total - 1,
            payload=next_payload,
        )
    )
    return row
