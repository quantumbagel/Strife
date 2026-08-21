from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

import discord
from discord import ui

from strife.presentation.feedback import build_feedback_view, send_ephemeral_feedback
from strife.routing import prefixes as P

if TYPE_CHECKING:
    from strife.config.text import TextConfig
    from strife.presentation.compiler import Compiler
    from strife.presentation.emoji import EmojiResolver


async def send_modal_error(
    interaction: discord.Interaction,
    message: str,
    *,
    compiler: Compiler | None,
    emoji: EmojiResolver | None,
    text: TextConfig | None,
) -> None:
    if compiler is not None and emoji is not None and text is not None:
        view = build_feedback_view(
            icon=emoji.get("error"),
            title=message,
            text=text,
        )
        await send_ephemeral_feedback(
            interaction,
            view,
            compiler=compiler,
            prefix=P.REPLAY_NOOP,
            resource_id=interaction.user.id,
        )
        return
    await interaction.response.send_message(message, ephemeral=True)


class PageJumpModal(ui.Modal):
    def __init__(
        self,
        *,
        title: str,
        label: str,
        placeholder: str,
        current: int,
        total: int,
        on_submit_cb: Callable[[discord.Interaction, int], Awaitable[None]],
        error_message: str = "Enter a valid page number.",
        compiler: Compiler | None = None,
        emoji: EmojiResolver | None = None,
        text: TextConfig | None = None,
    ) -> None:
        super().__init__(title=title)
        self._on_submit_cb = on_submit_cb
        self._total = max(1, total)
        self._error_message = error_message
        self._compiler = compiler
        self._emoji = emoji
        self._text = text
        self.page_input = ui.TextInput(
            label=label,
            placeholder=placeholder,
            default=str(current),
            min_length=1,
            max_length=6,
            required=True,
        )
        self.add_item(self.page_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        raw = self.page_input.value.strip()
        try:
            page = int(raw)
        except ValueError:
            await send_modal_error(
                interaction,
                self._error_message,
                compiler=self._compiler,
                emoji=self._emoji,
                text=self._text,
            )
            return
        page = max(1, min(page, self._total))
        await self._on_submit_cb(interaction, page - 1)


class IntRangeModal(ui.Modal):
    def __init__(
        self,
        *,
        title: str,
        label: str,
        placeholder: str,
        default: int,
        minimum: int,
        maximum: int,
        on_submit_cb: Callable[[discord.Interaction, int], Awaitable[None]],
        error_message: str = "Enter a valid number.",
        range_error_message: str = "Enter a number within the allowed range.",
        compiler: Compiler | None = None,
        emoji: EmojiResolver | None = None,
        text: TextConfig | None = None,
    ) -> None:
        super().__init__(title=title[:45])
        self._on_submit_cb = on_submit_cb
        self._minimum = minimum
        self._maximum = maximum
        self._error_message = error_message
        self._range_error_message = range_error_message
        self._compiler = compiler
        self._emoji = emoji
        self._text = text
        self.value_input = ui.TextInput(
            label=label[:45],
            placeholder=placeholder[:100],
            default=str(default),
            min_length=1,
            max_length=max(len(str(maximum)), len(str(minimum)), 1),
            required=True,
        )
        self.add_item(self.value_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        raw = self.value_input.value.strip()
        try:
            value = int(raw)
        except ValueError:
            await send_modal_error(
                interaction,
                self._error_message,
                compiler=self._compiler,
                emoji=self._emoji,
                text=self._text,
            )
            return
        if value < self._minimum or value > self._maximum:
            await send_modal_error(
                interaction,
                self._range_error_message,
                compiler=self._compiler,
                emoji=self._emoji,
                text=self._text,
            )
            return
        await self._on_submit_cb(interaction, value)
