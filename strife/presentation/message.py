from __future__ import annotations

import io
from typing import TYPE_CHECKING

import discord

from strife.presentation.components import LayoutView, ViewFile
from strife.presentation.compiler import Compiler

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


def to_discord_files(files: list[ViewFile] | None) -> list[discord.File]:
    converted: list[discord.File] = []
    for item in files or []:
        if not isinstance(item, ViewFile):
            raise TypeError(
                f"LayoutView.files entries must be ViewFile, got {type(item).__name__}"
            )
        kwargs: dict = {
            "fp": io.BytesIO(item.data),
            "filename": item.filename,
        }
        if item.description:
            kwargs["description"] = item.description
        converted.append(discord.File(**kwargs))
    return converted


class ViewSurface:
    def __init__(self, compiler: Compiler, *, prefix: str, resource_id: int) -> None:
        self.compiler = compiler
        self._prefix = prefix
        self._resource_id = resource_id
        self._message: discord.Message | None = None
        self._mirrors: list[discord.Message] = []

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
        files = to_discord_files(view.files)
        if isinstance(target, discord.Interaction):
            kwargs: dict = {"view": compiled}
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

    async def edit_interaction(
        self,
        interaction: discord.Interaction,
        view: LayoutView,
    ) -> discord.Message:
        compiled = self.compiler.compile(view, resource_id=self._resource_id, prefix=self._prefix)
        files = to_discord_files(view.files)
        kwargs: dict = {"view": compiled}
        if files:
            kwargs["attachments"] = files
        if not interaction.response.is_done():
            await interaction.response.edit_message(**kwargs)
            self._message = await interaction.original_response()
        elif interaction.message is not None:
            await interaction.message.edit(**kwargs)
            self._message = interaction.message
        else:
            await interaction.edit_original_response(**kwargs)
            self._message = await interaction.original_response()
        return self._message

    def add_mirror(self, message: discord.Message | None) -> None:
        if message is None:
            return
        if self._message is not None and message.id == self._message.id:
            return
        if any(existing.id == message.id for existing in self._mirrors):
            return
        self._mirrors.append(message)

    async def _edit_bound_message(self, message: discord.Message, view: LayoutView) -> None:
        compiled = self.compiler.compile(view, resource_id=self._resource_id, prefix=self._prefix)
        files = to_discord_files(view.files)
        kwargs: dict = {"content": None, "embeds": [], "view": compiled}
        if files:
            kwargs["attachments"] = files
        await message.edit(**kwargs)

    async def send_to_thread(self, thread: discord.Thread, view: LayoutView) -> discord.Message:
        compiled = self.compiler.compile(view, resource_id=self._resource_id, prefix=self._prefix)
        files = to_discord_files(view.files)
        kwargs: dict = {"view": compiled}
        if files:
            kwargs["files"] = files
        self._message = await thread.send(**kwargs)
        return self._message

    async def update(self, view: LayoutView) -> None:
        if self._message is None:
            raise RuntimeError("No message bound to surface")
        await self._edit_bound_message(self._message, view)
        remaining: list[discord.Message] = []
        for mirror in self._mirrors:
            try:
                await self._edit_bound_message(mirror, view)
            except discord.HTTPException:
                continue
            remaining.append(mirror)
        self._mirrors = remaining


    async def replace(self, view: LayoutView) -> None:
        await self.update(view)

    async def disable_all(self) -> None:
        messages = [self._message, *self._mirrors]
        for message in messages:
            if message is None:
                continue
            try:
                view = discord.ui.LayoutView.from_message(message)
                for item in view.walk_children():
                    if hasattr(item, "disabled"):
                        item.disabled = True
                await message.edit(view=view)
            except discord.HTTPException:
                pass

    async def send_private(
        self,
        interaction_factory: Callable[[], Awaitable[discord.Interaction | None]],
        user: discord.abc.User,
        view: LayoutView,
    ) -> None:
        compiled = self.compiler.compile(view, resource_id=self._resource_id, prefix=self._prefix)
        files = to_discord_files(view.files)
        try:
            dm = user.dm_channel or await user.create_dm()
            kwargs: dict = {"view": compiled}
            if files:
                kwargs["files"] = files
            await dm.send(**kwargs)
        except discord.HTTPException:
            interaction = await interaction_factory()
            if interaction and not interaction.response.is_done():
                await interaction.response.send_message(view=compiled, ephemeral=True)

    async def delete(self) -> None:
        messages = [self._message, *self._mirrors]
        self._message = None
        self._mirrors = []
        for message in messages:
            if message is None:
                continue
            try:
                await message.delete()
            except discord.HTTPException:
                pass
