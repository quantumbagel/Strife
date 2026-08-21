from __future__ import annotations

from typing import Any
import time

import discord

from strife.lifecycle.timeout import timeout_consequence
from strife.presentation.components import LayoutView
from strife.presentation.game_ui import build_game_thread_header_view
from strife.presentation.message import ViewSurface, to_discord_files
from strife.session.types import log


class SessionIOMixin:
    def set_bot(self, bot) -> None:
        self._bot = bot


    async def _respond_query(self, interaction: discord.Interaction, view: LayoutView) -> None:
        compiled = self.surface.compiler.compile(
            view,
            resource_id=self.surface.resource_id,
            prefix=self.surface.prefix,
        )
        files = to_discord_files(view.files)
        kwargs: dict[str, Any] = {"view": compiled, "ephemeral": True}
        if files:
            kwargs["files"] = files
        if not interaction.response.is_done():
            await interaction.response.send_message(**kwargs)
        else:
            await interaction.followup.send(**kwargs)


    def _seat_for_user(self, user_id: int) -> int | None:
        for player in self.players:
            if player.user_id == user_id and not player.is_bot:
                return player.seat
        return None


    async def _notify_thread(self, content: str) -> None:
        if self._bot is None or not content:
            return
        thread = self._bot.get_channel(self.thread_id)
        if thread is None:
            try:
                thread = await self._bot.fetch_channel(self.thread_id)
            except Exception:
                return
        if not isinstance(thread, discord.Thread):
            return
        try:
            await thread.send(content)
        except discord.HTTPException:
            log.exception("Failed to post notice in thread %s", self.thread_id)


    async def _update_surface(self, view: LayoutView) -> None:
        if self.surface.message is None:
            if self._bot is not None:
                thread = self._bot.get_channel(self.thread_id)
                if not thread:
                    try:
                        thread = await self._bot.fetch_channel(self.thread_id)
                    except Exception:
                        pass
                if thread is not None:
                    await self.surface.send(thread, view)
        else:
            await self.surface.update(view)


    async def refresh_header(self) -> None:
        if self.header_surface is None or self._finalized:
            return
        try:
            human_pending = sorted(
                seat for seat in self.pending if not self.players[seat].is_bot
            )
            deadline_unix = None
            wait_description = None
            line_descriptions: dict[int, str] = {}
            if human_pending:
                timeout_val = self.turn_timeout_seconds
                for seat in human_pending:
                    pending_input = self.pending.get(seat)
                    if pending_input and pending_input.timeout_seconds is not None:
                        timeout_val = min(timeout_val, pending_input.timeout_seconds)
                remaining = timeout_val - (time.monotonic() - self.last_move_at)
                deadline_unix = int(time.time() + max(0, remaining))
                header_descriptions = {
                    self.pending[seat].description
                    for seat in human_pending
                    if self.pending[seat].description
                }
                if len(header_descriptions) == 1:
                    wait_description = header_descriptions.pop()
                for seat in human_pending:
                    line_desc = self.pending[seat].line_description
                    if line_desc:
                        line_descriptions[seat] = line_desc
            timeout_consequence_text = None
            if human_pending:
                key = timeout_consequence(self, human_pending[0]).value
                timeout_consequence_text = self.text.get(f"lobby.timeout_consequence_{key}")
            view = build_game_thread_header_view(
                players=self.players,
                text=self.text,
                emoji=self.header_surface.compiler.emoji,
                pending_seats=human_pending or None,
                deadline_unix=deadline_unix,
                wait_description=wait_description,
                line_descriptions=line_descriptions or None,
                timeout_consequence=timeout_consequence_text,
            )
            await self.header_surface.update(view)
        except Exception:
            log.exception("Failed to update game thread header message")


    async def _send_private(self, seat: int, view: LayoutView) -> None:
        player = self.players[seat]
        if player.user_id is None or self._bot is None:
            return
        user = self._bot.get_user(player.user_id) or await self._bot.fetch_user(player.user_id)
        compiled_surface = ViewSurface(
            self.surface.compiler, prefix=self.surface.prefix, resource_id=self.surface.resource_id
        )
        try:
            dm = user.dm_channel or await user.create_dm()
            await compiled_surface.send(dm, view)
        except discord.HTTPException:
            log.warning("Failed to DM player %s (seat %s)", player.display_name, seat)
            thread = self._bot.get_channel(self.thread_id)
            if thread is None:
                try:
                    thread = await self._bot.fetch_channel(self.thread_id)
                except Exception:
                    thread = None
            if isinstance(thread, discord.Thread):
                try:
                    await thread.send(
                        self.text.get(
                            "match.dm_failed",
                            player=player.display_name,
                        )
                    )
                except discord.HTTPException:
                    log.exception("Failed to post DM failure notice in thread %s", self.thread_id)
