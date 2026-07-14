from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import discord

from strife.presentation.components import LayoutView
from strife.presentation.compiler import Compiler

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


class ViewSurface:
    def __init__(self, compiler: Compiler, *, prefix: str, resource_id: int) -> None:
        self.compiler = compiler
        self._prefix = prefix
        self._resource_id = resource_id
        self._message: discord.Message | None = None

    @property
    def prefix(self) -> str:
        return self._prefix

    @property
    def resource_id(self) -> int:
        return self._resource_id

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
        compiled = self.compiler.compile(view, resource_id=self._resource_id, prefix=self._prefix)
        files = getattr(view, "files", [])
        if isinstance(target, discord.Interaction):
            kwargs = {"view": compiled}
            if ephemeral:
                kwargs["ephemeral"] = True
            if files:
                kwargs["files"] = files
            await target.response.send_message(**kwargs)
            self._message = await target.original_response()
        else:
            kwargs = {"view": compiled}
            if files:
                kwargs["files"] = files
            self._message = await target.send(**kwargs)
        return self._message

    async def send_to_thread(self, thread: discord.Thread, view: LayoutView) -> discord.Message:
        compiled = self.compiler.compile(view, resource_id=self._resource_id, prefix=self._prefix)
        files = getattr(view, "files", [])
        kwargs = {"view": compiled}
        if files:
            kwargs["files"] = files
        self._message = await thread.send(**kwargs)
        return self._message

    async def update(self, view: LayoutView) -> None:
        if self._message is None:
            raise RuntimeError("No message bound to surface")
        compiled = self.compiler.compile(view, resource_id=self._resource_id, prefix=self._prefix)
        files = getattr(view, "files", [])
        await self._message.edit(content=None, embeds=[], view=compiled, attachments=files)


    async def replace(self, view: LayoutView) -> None:
        await self.update(view)

    async def disable_all(self) -> None:
        if self._message is not None:
            try:
                view = discord.ui.LayoutView.from_message(self._message)
                for item in view.walk_children():
                    if hasattr(item, "disabled"):
                        item.disabled = True
                await self._message.edit(view=view)
            except discord.HTTPException:
                pass

    async def send_private(
        self,
        interaction_factory: Callable[[], Awaitable[discord.Interaction | None]],
        user: discord.abc.User,
        view: LayoutView,
    ) -> None:
        compiled = self.compiler.compile(view, resource_id=self._resource_id, prefix=self._prefix)
        try:
            dm = user.dm_channel or await user.create_dm()
            await dm.send(view=compiled)
        except discord.HTTPException:
            interaction = await interaction_factory()
            if interaction and not interaction.response.is_done():
                await interaction.response.send_message(view=compiled, ephemeral=True)

    async def delete(self) -> None:
        if self._message is not None:
            try:
                await self._message.delete()
            except discord.HTTPException:
                pass
            self._message = None


_BACKGROUND_TASKS: set[asyncio.Task] = set()


def _track_task(task: asyncio.Task) -> None:
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)
