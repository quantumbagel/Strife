from __future__ import annotations

import time
from typing import Any

import discord

from strife.lifecycle.timeout import timeout_consequence
from strife.presentation.components import LayoutView
from strife.presentation.message import ViewSurface, to_discord_files
from strife.session.header import build_game_thread_header_view
from strife.session.types import log

_THREAD_ARCHIVED_CODE = 50083


class SessionIOMixin:
    def set_bot(self, bot) -> None:
        self._bot = bot

    async def _respond_query(
        self, interaction: discord.Interaction, view: LayoutView
    ) -> None:
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
        thread = await self._game_thread()
        if thread is None:
            return
        try:
            await thread.send(content)
        except discord.HTTPException:
            log.exception("Failed to post notice in thread %s", self.thread_id)

    async def _game_thread(self) -> discord.Thread | None:
        if self._bot is None:
            return None
        thread = self._bot.get_channel(self.thread_id)
        if thread is None:
            try:
                thread = await self._bot.fetch_channel(self.thread_id)
            except Exception:  # noqa: BLE001
                return None
        return thread if isinstance(thread, discord.Thread) else None

    def _is_archived_thread_error(self, exc: BaseException) -> bool:
        return (
            isinstance(exc, discord.HTTPException)
            and getattr(exc, "code", None) == _THREAD_ARCHIVED_CODE
        )

    async def _unarchive_game_thread(self) -> bool:
        thread = await self._game_thread()
        if thread is None:
            return False
        try:
            await thread.edit(archived=False, locked=False)
            return True
        except discord.Forbidden:
            log.warning("Couldn't unarchive game thread %s", self.thread_id)
            return False
        except discord.HTTPException:
            log.exception("Failed to unarchive game thread %s", self.thread_id)
            return False

    async def _send_board(self, view: LayoutView) -> None:
        thread = await self._game_thread()
        if thread is None:
            return
        await self.surface.send(thread, view)
        await self._persist_board_message()

    async def _update_surface(self, view: LayoutView) -> None:
        retried_unarchive = False
        while True:
            try:
                if self.surface.message is None:
                    await self._send_board(view)
                else:
                    await self.surface.update(view)
                    await self._persist_board_message()
                return
            except discord.NotFound:
                self._board_message_saved = False
                try:
                    await self._send_board(view)
                    return
                except discord.HTTPException as send_exc:
                    if (
                        not retried_unarchive
                        and self._is_archived_thread_error(send_exc)
                        and await self._unarchive_game_thread()
                    ):
                        retried_unarchive = True
                        continue
                    log.exception(
                        "Failed to send replacement board in thread %s",
                        self.thread_id,
                    )
                    return
            except discord.HTTPException as exc:
                if (
                    not retried_unarchive
                    and self._is_archived_thread_error(exc)
                    and await self._unarchive_game_thread()
                ):
                    retried_unarchive = True
                    continue
                log.exception(
                    "Failed to update game surface in thread %s",
                    self.thread_id,
                )
                return

    async def _persist_board_message(self) -> None:
        if self._board_message_saved or self._match_id is None:
            return
        message_id = self.surface.message_id
        if message_id is None:
            return
        try:
            await self._finalizer.set_board_message(self._match_id, message_id)
            self._board_message_saved = True
        except Exception:  # noqa: BLE001
            log.exception(
                "Failed to persist board message for thread %s", self.thread_id
            )

    async def refresh_header(self) -> None:
        if self.header_surface is None or self._finalized:
            return
        self._header_dirty = True
        if self._header_refreshing:
            return
        self._header_refreshing = True
        while True:
            self._header_dirty = False
            if self.header_surface is None or self._finalized:
                self._header_refreshing = False
                return
            try:
                await self._refresh_header_once()
            except BaseException:
                # This caller owns the loop; don't leave the flag stuck on cancellation.
                self._header_refreshing = False
                raise
            if not self._header_dirty:
                self._header_refreshing = False
                return

    async def _refresh_header_once(self) -> None:
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
                now = time.monotonic()
                deadlines = [
                    self.pending[seat].deadline_at
                    for seat in human_pending
                    if self.pending[seat].deadline_at is not None
                ]
                if deadlines:
                    remaining = min(deadlines) - now
                else:
                    timeout_val = self.turn_timeout_seconds
                    for seat in human_pending:
                        pending_input = self.pending.get(seat)
                        if pending_input and pending_input.timeout_seconds is not None:
                            timeout_val = min(
                                timeout_val, pending_input.timeout_seconds
                            )
                    remaining = timeout_val - (now - self.last_move_at)
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
                timeout_consequence_text = self.text.get(
                    f"lobby.timeout_consequence_{key}"
                )
            view = build_game_thread_header_view(
                players=self.players,
                text=self.text,
                emoji=self.header_surface.compiler.emoji,
                owner_ids=self._owner_ids(),
                pending_seats=human_pending or None,
                deadline_unix=deadline_unix,
                wait_description=wait_description,
                line_descriptions=line_descriptions or None,
                timeout_consequence=timeout_consequence_text,
            )
            await self.header_surface.update(view)
        except Exception:  # noqa: BLE001
            log.exception("Failed to update game thread header message")

    async def _send_private(self, seat: int, view: LayoutView) -> None:
        player = self.players[seat]
        # A taken-over seat keeps its user id, but that person has left the match.
        if player.user_id is None or player.is_bot or self._bot is None:
            return
        compiled_surface = ViewSurface(
            self.surface.compiler,
            prefix=self.surface.prefix,
            resource_id=self.surface.resource_id,
        )
        try:
            user = self._bot.get_user(player.user_id) or await self._bot.fetch_user(
                player.user_id
            )
            dm = user.dm_channel or await user.create_dm()
            await compiled_surface.send(dm, view)
        except discord.HTTPException:
            log.warning("Failed to DM player %s (seat %s)", player.display_name, seat)
            if player.user_id in self._dm_failure_notified:
                return
            self._dm_failure_notified.add(player.user_id)
            thread = await self._game_thread()
            if isinstance(thread, discord.Thread):
                try:
                    await thread.send(
                        self.text.get(
                            "match.dm_failed",
                            player=player.display_name,
                        )
                    )
                except discord.HTTPException:
                    log.exception(
                        "Failed to post DM failure notice in thread %s", self.thread_id
                    )
