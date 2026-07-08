from __future__ import annotations

from collections.abc import Awaitable, Callable

import discord
from discord import ui


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
    ) -> None:
        super().__init__(title=title)
        self._on_submit_cb = on_submit_cb
        self._total = max(1, total)
        self._error_message = error_message
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
            await interaction.response.send_message(self._error_message, ephemeral=True)
            return
        page = max(1, min(page, self._total))
        await self._on_submit_cb(interaction, page - 1)
