from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class TextSize(StrEnum):
    HEADER = "header"
    SUBHEADER = "subheader"
    BODY = "body"


class Align(StrEnum):
    LEFT = "left"
    CENTER = "center"
    RIGHT = "right"


class ButtonStyle(StrEnum):
    PRIMARY = "primary"
    SECONDARY = "secondary"
    SUCCESS = "success"
    DANGER = "danger"
    LINK = "link"


@dataclass
class TextDisplay:
    markdown_content: str
    size_style: TextSize = TextSize.BODY
    alignment: Align = Align.LEFT


@dataclass
class Separator:
    visible: bool = True


@dataclass
class MediaGalleryItem:
    media_url: str
    description: str | None = None


@dataclass
class MediaGallery:
    items: list[MediaGalleryItem] = field(default_factory=list)

    def add_item(self, item: MediaGalleryItem) -> MediaGallery:
        self.items.append(item)
        return self


@dataclass
class Button:
    source: str = ""
    label: str = ""
    style: ButtonStyle = ButtonStyle.SECONDARY
    emoji: str | None = None
    url: str | None = None
    disabled: bool = False
    payload: dict | None = None
    route_prefix: str | None = None
    resource_id: int | None = None


@dataclass
class SelectChoice:
    label: str
    value: str
    description: str | None = None
    emoji: str | None = None
    default: bool = False


@dataclass
class Select:
    source: str
    choices: list[SelectChoice] = field(default_factory=list)
    placeholder: str | None = None
    min_values: int = 1
    max_values: int = 1
    disabled: bool = False
    payload: dict | None = None
    route_prefix: str | None = None
    resource_id: int | None = None


@dataclass
class ActionRow:
    items: list[Button | Select] = field(default_factory=list)

    def add_button(self, button: Button) -> ActionRow:
        self.items.append(button)
        return self

    def add_select(self, select: Select) -> ActionRow:
        self.items.append(select)
        return self


@dataclass
class Container:
    children: list[TextDisplay | Separator | MediaGallery | ActionRow] = field(default_factory=list)

    def add_text(self, text: TextDisplay) -> Container:
        self.children.append(text)
        return self

    def add_separator(self, separator: Separator | None = None) -> Container:
        self.children.append(separator or Separator())
        return self

    def set_gallery(self, gallery: MediaGallery) -> Container:
        self.children.append(gallery)
        return self

    def add_action_row(self, row: ActionRow) -> Container:
        self.children.append(row)
        return self


@dataclass
class LayoutView:
    children: list[Container | ActionRow | TextDisplay | Separator | MediaGallery] = field(
        default_factory=list
    )

    def add_container(self, container: Container) -> LayoutView:
        self.children.append(container)
        return self

    def add_action_row(self, row: ActionRow) -> LayoutView:
        self.children.append(row)
        return self

    def header(self, emoji_name: str, title: str, *, emoji_resolver: object | None = None) -> LayoutView:
        emoji = emoji_name
        if emoji_resolver is not None and hasattr(emoji_resolver, "general"):
            emoji = emoji_resolver.general(emoji_name)  # type: ignore[union-attr]
        self.children.append(
            TextDisplay(
                markdown_content=f"## {emoji} {title}",
                size_style=TextSize.HEADER,
            )
        )
        return self


def walk_interactive(view: LayoutView) -> list[Button | Select]:
    items: list[Button | Select] = []

    def visit(node: object) -> None:
        if isinstance(node, Button | Select):
            items.append(node)
        elif isinstance(node, ActionRow):
            for child in node.items:
                visit(child)
        elif isinstance(node, Container):
            for child in node.children:
                visit(child)
        elif isinstance(node, LayoutView):
            for child in node.children:
                visit(child)

    visit(view)
    return items


def disable_all(view: LayoutView) -> None:
    for item in walk_interactive(view):
        item.disabled = True
