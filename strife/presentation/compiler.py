from __future__ import annotations

import discord
from discord import ui

from strife.presentation.components import (
    ActionRow,
    Button,
    ButtonStyle,
    ChannelSelect,
    Container,
    DescribedSelect,
    LayoutView,
    MediaGallery,
    Section,
    Select,
    Separator,
    TextDisplay,
    TextSize,
    UserSelect,
    small_text,
)
from strife.presentation.emoji import EmojiResolver
from strife.routing.custom_id import CustomIdEncoder


class LayoutError(Exception):
    pass


_STYLE_MAP = {
    ButtonStyle.PRIMARY: discord.ButtonStyle.primary,
    ButtonStyle.SECONDARY: discord.ButtonStyle.secondary,
    ButtonStyle.SUCCESS: discord.ButtonStyle.success,
    ButtonStyle.DANGER: discord.ButtonStyle.danger,
    ButtonStyle.LINK: discord.ButtonStyle.link,
}


class Compiler:
    def __init__(self, emoji: EmojiResolver, encoder: CustomIdEncoder) -> None:
        self._emoji = emoji
        self._encoder = encoder
        self._component_count = 0
        self._text_chars = 0

    @property
    def emoji(self) -> EmojiResolver:
        return self._emoji

    def compile(self, view: LayoutView, *, resource_id: int, prefix: str) -> ui.LayoutView:
        self._component_count = 0
        self._text_chars = 0
        layout = ui.LayoutView(timeout=None)
        for child in view.children:
            layout.add_item(self._compile_top(child, resource_id=resource_id, prefix=prefix))
        if self._component_count > 40:
            raise LayoutError("Layout exceeds 40 components")
        if self._text_chars > 4000:
            raise LayoutError("Layout exceeds 4000 total text characters")
        return layout

    def _count(self) -> None:
        self._component_count += 1

    def _prefix_text(self, text: TextDisplay) -> str:
        content = text.markdown_content
        if text.size_style == TextSize.HEADER and not content.startswith("#"):
            content = f"### {content}"
        elif text.size_style == TextSize.SUBHEADER and not content.startswith(("#", "-#", "**")):
            content = f"**{content}**"
        if len(content) > 4000:
            raise LayoutError("TextDisplay exceeds 4000 characters")
        self._text_chars += len(content)
        return content

    def _resolve_emoji(self, name: str | None) -> str | discord.PartialEmoji | None:
        if not name:
            return None
        resolved = self._emoji.get(name)
        if resolved.startswith("<:") and resolved.endswith(">"):
            inner = resolved[2:-1]
            ename, _, eid = inner.partition(":")
            return discord.PartialEmoji(name=ename, id=int(eid))
        return resolved

    def _compile_button(self, button: Button, *, resource_id: int, prefix: str) -> ui.Button:
        self._count()
        if button.style == ButtonStyle.LINK:
            if not button.url:
                raise LayoutError("LINK button requires url")
            return ui.Button(
                style=discord.ButtonStyle.link,
                label=button.label or None,
                url=button.url,
                emoji=self._resolve_emoji(button.emoji),
                disabled=button.disabled,
            )
        if not button.source:
            return ui.Button(
                style=_STYLE_MAP[button.style],
                label=button.label or None,
                emoji=self._resolve_emoji(button.emoji),
                disabled=True,
            )
        custom_id = self._encoder.encode(
            button.route_prefix or prefix,
            button.resource_id if button.resource_id is not None else resource_id,
            button.source,
            button.payload,
        )
        if len(custom_id) > 100:
            raise LayoutError("custom_id exceeds 100 characters")
        return ui.Button(
            style=_STYLE_MAP[button.style],
            label=button.label or None,
            emoji=self._resolve_emoji(button.emoji),
            disabled=button.disabled,
            custom_id=custom_id,
        )

    def _compile_select(self, select: Select, *, resource_id: int, prefix: str) -> ui.Select:
        self._count()
        custom_id = self._encoder.encode(
            select.route_prefix or prefix,
            select.resource_id if select.resource_id is not None else resource_id,
            select.source,
            select.payload,
        )
        if len(custom_id) > 100:
            raise LayoutError("custom_id exceeds 100 characters")
        options = [
            discord.SelectOption(
                label=choice.label[:100],
                value=choice.value[:100],
                description=(choice.description[:100] if choice.description else None),
                emoji=self._resolve_emoji(choice.emoji),
                default=choice.default,
            )
            for choice in select.choices
        ]
        return ui.Select(
            custom_id=custom_id,
            placeholder=select.placeholder,
            min_values=select.min_values,
            max_values=select.max_values,
            options=options or [discord.SelectOption(label="—", value="_")],
            disabled=select.disabled,
        )

    _CHANNEL_TYPE_MAP = {
        "text": discord.ChannelType.text,
        "voice": discord.ChannelType.voice,
        "category": discord.ChannelType.category,
        "news": discord.ChannelType.news,
        "stage": discord.ChannelType.stage_voice,
        "forum": discord.ChannelType.forum,
    }

    def _compile_channel_select(
        self, channel_select: ChannelSelect, *, resource_id: int, prefix: str
    ) -> ui.ChannelSelect:
        self._count()
        custom_id = self._encoder.encode(
            channel_select.route_prefix or prefix,
            channel_select.resource_id if channel_select.resource_id is not None else resource_id,
            channel_select.source,
            channel_select.payload,
        )
        if len(custom_id) > 100:
            raise LayoutError("custom_id exceeds 100 characters")
        channel_types = [
            self._CHANNEL_TYPE_MAP[t]
            for t in channel_select.channel_types
            if t in self._CHANNEL_TYPE_MAP
        ] or [discord.ChannelType.text]
        default_values = (
            [discord.Object(id=channel_select.default_id)]
            if channel_select.default_id is not None
            else []
        )
        return ui.ChannelSelect(
            custom_id=custom_id,
            placeholder=channel_select.placeholder,
            min_values=channel_select.min_values,
            max_values=channel_select.max_values,
            channel_types=channel_types,
            default_values=default_values,
            disabled=channel_select.disabled,
        )

    def _compile_user_select(
        self, user_select: UserSelect, *, resource_id: int, prefix: str
    ) -> ui.UserSelect:
        self._count()
        custom_id = self._encoder.encode(
            user_select.route_prefix or prefix,
            user_select.resource_id if user_select.resource_id is not None else resource_id,
            user_select.source,
            user_select.payload,
        )
        if len(custom_id) > 100:
            raise LayoutError("custom_id exceeds 100 characters")
        return ui.UserSelect(
            custom_id=custom_id,
            placeholder=user_select.placeholder,
            min_values=user_select.min_values,
            max_values=user_select.max_values,
            disabled=user_select.disabled,
        )

    def _compile_described_select(
        self, described: DescribedSelect, *, resource_id: int, prefix: str
    ) -> list[ui.Item]:
        self._count()
        content = small_text(described.description)
        if described.label:
            content = f"{described.label}\n{content}"
        text = ui.TextDisplay(
            content=self._prefix_text(
                TextDisplay(
                    markdown_content=content,
                    size_style=TextSize.BODY,
                )
            )
        )
        self._count()
        separator = ui.Separator(visible=False)
        row = ActionRow()
        if isinstance(described.select, ChannelSelect):
            row.add_channel_select(described.select)
        elif isinstance(described.select, UserSelect):
            row.add_user_select(described.select)
        else:
            row.add_select(described.select)
        return [text, separator, self._compile_action_row(row, resource_id=resource_id, prefix=prefix)]

    def _compile_action_row(self, row: ActionRow, *, resource_id: int, prefix: str) -> ui.ActionRow:
        buttons = [item for item in row.items if isinstance(item, Button)]
        selects = [item for item in row.items if isinstance(item, Select)]
        channel_selects = [item for item in row.items if isinstance(item, ChannelSelect)]
        user_selects = [item for item in row.items if isinstance(item, UserSelect)]
        interactive = buttons + selects + channel_selects + user_selects
        if len(interactive) != len(row.items):
            raise LayoutError("ActionRow contains unsupported items")
        if len(buttons) > 0 and (len(selects) > 0 or len(channel_selects) > 0 or len(user_selects) > 0):
            raise LayoutError("ActionRow cannot mix buttons and selects")
        if len(buttons) > 5:
            raise LayoutError("ActionRow cannot have more than 5 buttons")
        if len(selects) > 1 or len(channel_selects) > 1 or len(user_selects) > 1:
            raise LayoutError("ActionRow cannot have more than 1 select")
        if sum(bool(x) for x in (selects, channel_selects, user_selects)) > 1:
            raise LayoutError("ActionRow cannot mix select types")
        compiled = ui.ActionRow()
        for item in row.items:
            if isinstance(item, Button):
                compiled.add_item(self._compile_button(item, resource_id=resource_id, prefix=prefix))
            elif isinstance(item, ChannelSelect):
                compiled.add_item(
                    self._compile_channel_select(item, resource_id=resource_id, prefix=prefix)
                )
            elif isinstance(item, UserSelect):
                compiled.add_item(
                    self._compile_user_select(item, resource_id=resource_id, prefix=prefix)
                )
            else:
                compiled.add_item(self._compile_select(item, resource_id=resource_id, prefix=prefix))
        return compiled

    def _compile_section(self, section: Section, *, resource_id: int, prefix: str) -> ui.Section:
        self._count()
        compiled_children = []
        for child in section.children:
            self._count()
            compiled_children.append(ui.TextDisplay(content=self._prefix_text(child)))
        if not section.accessory:
            raise LayoutError("Section requires an accessory")
        compiled_accessory = self._compile_button(section.accessory, resource_id=resource_id, prefix=prefix)
        return ui.Section(*compiled_children, accessory=compiled_accessory)

    def _compile_container(self, container: Container, *, resource_id: int, prefix: str) -> ui.Container:
        compiled = ui.Container()
        for child in container.children:
            if isinstance(child, TextDisplay):
                self._count()
                compiled.add_item(ui.TextDisplay(content=self._prefix_text(child)))
            elif isinstance(child, Separator):
                self._count()
                compiled.add_item(ui.Separator(visible=child.visible))
            elif isinstance(child, MediaGallery):
                self._count()
                gallery = ui.MediaGallery(
                    *[
                        discord.MediaGalleryItem(media=item.media_url, description=item.description)
                        for item in child.items
                    ]
                )
                compiled.add_item(gallery)
            elif isinstance(child, ActionRow):
                compiled.add_item(self._compile_action_row(child, resource_id=resource_id, prefix=prefix))
            elif isinstance(child, DescribedSelect):
                for item in self._compile_described_select(child, resource_id=resource_id, prefix=prefix):
                    compiled.add_item(item)
            elif isinstance(child, Section):
                compiled.add_item(self._compile_section(child, resource_id=resource_id, prefix=prefix))
        return compiled

    def _compile_top(self, node: object, *, resource_id: int, prefix: str) -> ui.Item:
        if isinstance(node, Container):
            return self._compile_container(node, resource_id=resource_id, prefix=prefix)
        if isinstance(node, ActionRow):
            return self._compile_action_row(node, resource_id=resource_id, prefix=prefix)
        if isinstance(node, TextDisplay):
            self._count()
            return ui.TextDisplay(content=self._prefix_text(node))
        if isinstance(node, Separator):
            self._count()
            return ui.Separator(visible=node.visible)
        if isinstance(node, MediaGallery):
            self._count()
            return ui.MediaGallery(
                *[
                    discord.MediaGalleryItem(media=item.media_url, description=item.description)
                    for item in node.items
                ]
            )
        if isinstance(node, Section):
            return self._compile_section(node, resource_id=resource_id, prefix=prefix)
        raise LayoutError(f"Unsupported node type: {type(node)}")



