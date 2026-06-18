from __future__ import annotations

from typing import TYPE_CHECKING

import discord

from strife.presentation.components import LayoutView, disable_all
from strife.presentation.compiler import Compiler

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


class ViewSurface:
    def __init__(self, compiler: Compiler, *, prefix: str, resource_id: int) -> None:
        self._compiler = compiler
        self._prefix = prefix
        self._resource_id = resource_id
        self._message: discord.Message | None = None

    @property
    def message(self) -> discord.Message | None:
        return self._message

    @property
    def message_id(self) -> int | None:
        return self._message.id if self._message else None

    def set_prefix(self, prefix: str) -> None:
        self._prefix = prefix

    def set_resource_id(self, resource_id: int) -> None:
        self._resource_id = resource_id

    async def send(
        self,
        target: discord.Interaction | discord.abc.Messageable,
        view: LayoutView,
        *,
        ephemeral: bool = False,
    ) -> discord.Message:
        compiled = self._compiler.compile(view, resource_id=self._resource_id, prefix=self._prefix)
        if isinstance(target, discord.Interaction):
            if ephemeral:
                await target.response.send_message(view=compiled, ephemeral=True)
                self._message = await target.original_response()
            else:
                await target.response.send_message(view=compiled)
                self._message = await target.original_response()
        else:
            self._message = await target.send(view=compiled)
        return self._message

    async def send_to_thread(self, thread: discord.Thread, view: LayoutView) -> discord.Message:
        compiled = self._compiler.compile(view, resource_id=self._resource_id, prefix=self._prefix)
        self._message = await thread.send(view=compiled)
        return self._message

    async def update(self, view: LayoutView) -> None:
        if self._message is None:
            raise RuntimeError("No message bound to surface")
        compiled = self._compiler.compile(view, resource_id=self._resource_id, prefix=self._prefix)
        await self._message.edit(content=None, embeds=[], view=compiled)

    async def replace(self, view: LayoutView) -> None:
        if self._message is None:
            raise RuntimeError("No message bound to surface")
        compiled = self._compiler.compile(view, resource_id=self._resource_id, prefix=self._prefix)
        await self._message.edit(content=None, embeds=[], view=compiled)

    async def disable_all(self, view: LayoutView) -> None:
        disable_all(view)
        await self.update(view)

    async def send_private(
        self,
        interaction_factory: Callable[[], Awaitable[discord.Interaction | None]],
        user: discord.abc.User,
        view: LayoutView,
    ) -> None:
        compiled = self._compiler.compile(view, resource_id=self._resource_id, prefix=self._prefix)
        try:
            dm = user.dm_channel or await user.create_dm()
            await dm.send(view=compiled)
        except discord.HTTPException:
            interaction = await interaction_factory()
            if interaction and not interaction.response.is_done():
                await interaction.response.send_message(view=compiled, ephemeral=True)
