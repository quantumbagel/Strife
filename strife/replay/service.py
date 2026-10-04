from __future__ import annotations

import time
import asyncio
from collections import OrderedDict
from dataclasses import dataclass

import discord

from strife.config.text import TextConfig
from strife.logging import get_logger
from strife.persistence.repositories import (
    MatchDetail,
    MatchRepository,
    MoveRepository,
)
from strife.presentation.compiler import Compiler
from strife.presentation.emoji import EmojiResolver
from strife.presentation.user_error import UserErrorPresenter
from strife.presentation.modals import PageJumpModal, edit_pager_message
from strife.engine.log import LOG_FORMAT
from strife.engine.players import Player
from strife.engine.registry import GameRegistry
from strife.engine.replay import ReplayDivergence, run_replay
from strife.replay.view import build_replay_view
from strife.routing import prefixes as P

log = get_logger("replay.service")

_REBUILD_TIMEOUT = 15.0
_CACHE_MAX_ENTRIES = 64
_CACHE_MAX_FRAMES = 4000


class ReplayFormatTooOld(Exception):
    """Stored match log predates the current replay format."""

    def __init__(self, match_id: int) -> None:
        self.match_id = match_id
        super().__init__(f"Match {match_id} log format too old for replay")


class ReplayLoadError(Exception):
    """Replay reconstruction failed for a stored match."""

    def __init__(
        self,
        match_id: int,
        game_key: str,
        *,
        move_count: int,
        cause: BaseException,
    ) -> None:
        self.match_id = match_id
        self.game_key = game_key
        self.move_count = move_count
        self.cause = cause
        super().__init__(
            f"Replay load failed for match {match_id} ({game_key}, {move_count} moves)"
        )


@dataclass
class _ReplayCacheEntry:
    detail: MatchDetail
    frames: list


def _players_from_detail(detail: MatchDetail) -> list[Player]:
    return [
        Player(
            seat=p.seat_index,
            user_id=p.user_id,
            display_name=p.display_name,
            is_bot=False if p.user_id is not None else p.is_bot,
            bot_difficulty=None if p.user_id is not None else p.bot_difficulty,
            role_key=p.role_key,
        )
        for p in detail.players
    ]


def _rebuild_frames(
    game_registry: GameRegistry,
    emoji: EmojiResolver,
    detail: MatchDetail,
    move_records: list,
) -> list:
    """Construct the game and run_replay. Must run in a worker thread."""

    async def _run() -> list:
        from strife.presentation.emoji_context import bind_emoji, reset_emoji

        game = game_registry.create(
            detail.game_key,
            _players_from_detail(detail),
            detail.settings,
            detail.seed,
        )
        game_emoji = emoji.bind_game(detail.game_key)
        token = bind_emoji(game_emoji)
        try:
            async with asyncio.timeout(_REBUILD_TIMEOUT):
                return await run_replay(
                    game,
                    move_records,
                    emoji=game_emoji,
                    started_at=detail.started_at,
                )
        except TimeoutError as exc:
            raise ReplayLoadError(
                detail.id,
                detail.game_key,
                move_count=len(move_records),
                cause=exc,
            ) from exc
        finally:
            reset_emoji(token)

    return asyncio.run(_run())


class ReplayService:
    def __init__(
        self,
        matches: MatchRepository,
        moves: MoveRepository,
        game_registry: GameRegistry,
        compiler: Compiler,
        text: TextConfig,
        user_errors: UserErrorPresenter,
    ) -> None:
        self.matches = matches
        self.moves = moves
        self.game_registry = game_registry
        self.compiler = compiler
        self.text = text
        self.user_errors = user_errors
        self._cache: OrderedDict[int, _ReplayCacheEntry] = OrderedDict()
        self._cache_size = _CACHE_MAX_ENTRIES
        self._cache_max_frames = _CACHE_MAX_FRAMES
        self._inflight: dict[int, asyncio.Task[_ReplayCacheEntry | None]] = {}
        self._inflight_guard = asyncio.Lock()
        self._autocomplete_cache: dict[tuple[int, int], tuple[float, list]] = {}

    async def autocomplete_matches(
        self, user_id: int, guild_id: int, *, limit: int = 25
    ) -> list:
        now = time.monotonic()
        cache_key = (user_id, guild_id)
        cached = self._autocomplete_cache.get(cache_key)
        if cached and now - cached[0] < 15:
            return cached[1]
        matches = await self.matches.list_for_user(
            user_id, None, guild_id=guild_id, limit=limit
        )
        self._autocomplete_cache[cache_key] = (now, matches)
        if len(self._autocomplete_cache) > 256:
            oldest = min(
                self._autocomplete_cache, key=lambda k: self._autocomplete_cache[k][0]
            )
            self._autocomplete_cache.pop(oldest, None)
        return matches

    def _cached_entry(
        self, match_id: int, *, guild_id: int | None
    ) -> _ReplayCacheEntry | None:
        entry = self._cache.get(match_id)
        if entry is None:
            return None
        if guild_id is not None and entry.detail.guild_id != guild_id:
            return None
        self._cache.move_to_end(match_id)
        return entry

    def _remember(self, match_id: int, entry: _ReplayCacheEntry) -> None:
        if match_id in self._cache:
            self._cache.pop(match_id)
        self._cache[match_id] = entry
        while True:
            over_entries = len(self._cache) > self._cache_size
            over_frames = (
                sum(len(item.frames) for item in self._cache.values())
                > self._cache_max_frames
            )
            if not over_entries and not over_frames:
                return
            if len(self._cache) <= 1:
                return
            self._cache.popitem(last=False)

    async def _load_entry(
        self,
        match_id: int,
        *,
        guild_id: int | None = None,
    ) -> _ReplayCacheEntry | None:
        cached = self._cached_entry(match_id, guild_id=guild_id)
        if cached is not None:
            return cached
        async with self._inflight_guard:
            cached = self._cached_entry(match_id, guild_id=guild_id)
            if cached is not None:
                return cached
            inflight = self._inflight.get(match_id)
            if inflight is None:
                inflight = asyncio.create_task(
                    self._load_uncached(match_id, guild_id=guild_id)
                )
                self._inflight[match_id] = inflight
        entry = await asyncio.shield(inflight)
        # A rebuild started for another server's request must not leak here.
        if entry is not None and guild_id is not None and entry.detail.guild_id != guild_id:
            return None
        return entry

    async def _load_uncached(
        self,
        match_id: int,
        *,
        guild_id: int | None,
    ) -> _ReplayCacheEntry | None:
        try:
            cached = self._cached_entry(match_id, guild_id=guild_id)
            if cached is not None:
                return cached
            detail = await self.matches.get(match_id, guild_id=guild_id)
            if detail is None:
                return None
            if detail.log_format < LOG_FORMAT:
                raise ReplayFormatTooOld(match_id)
            move_records = await self.moves.list_for_match(match_id)
            try:
                frames = await asyncio.to_thread(
                    _rebuild_frames,
                    self.game_registry,
                    self.compiler.emoji,
                    detail,
                    move_records,
                )
            except ReplayLoadError:
                log.exception(
                    "Replay load failed for match %s (game=%s, moves=%d)",
                    match_id,
                    detail.game_key,
                    len(move_records),
                )
                raise
            except ReplayDivergence as exc:
                log.exception(
                    "Replay divergence for match %s (game=%s, moves=%d): %s",
                    match_id,
                    detail.game_key,
                    len(move_records),
                    exc,
                )
                raise ReplayLoadError(
                    match_id,
                    detail.game_key,
                    move_count=len(move_records),
                    cause=exc,
                ) from exc
            except Exception as exc:
                log.exception(
                    "Replay load failed for match %s (game=%s, moves=%d)",
                    match_id,
                    detail.game_key,
                    len(move_records),
                )
                raise ReplayLoadError(
                    match_id,
                    detail.game_key,
                    move_count=len(move_records),
                    cause=exc,
                ) from exc
            entry = _ReplayCacheEntry(detail=detail, frames=frames)
            self._remember(match_id, entry)
            return entry
        finally:
            current = self._inflight.get(match_id)
            if current is not None and current is asyncio.current_task():
                self._inflight.pop(match_id, None)

    async def open(self, interaction: discord.Interaction, match: str | int) -> None:
        """Open a replay by code (user input) or id (signed buttons/selects).

        Only matches played in the server the interaction comes from are found.
        """
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)
        if interaction.guild_id is None:
            await self.user_errors.send(interaction, "common.match_not_found")
            return
        detail = await self.matches.get(
            match, guild_id=interaction.guild_id, include_live=True
        )
        if detail is not None and detail.status == "live":
            await self.user_errors.send(interaction, "common.replay_still_live")
            return
        if detail is None:
            await self.user_errors.send(interaction, "common.match_not_found")
            return
        try:
            entry = await self._load_entry(detail.id, guild_id=interaction.guild_id)
        except ReplayFormatTooOld:
            await self.user_errors.send(interaction, "common.replay_old_format")
            return
        except ReplayLoadError:
            await self.user_errors.send(interaction, "common.replay_load_failed")
            return
        if entry is None or not entry.frames:
            if entry is not None and not entry.frames:
                log.warning(
                    "Replay for match %s (%s) produced no frames from stored moves",
                    detail.id,
                    detail.game_key,
                )
            await self.user_errors.send(interaction, "common.replay_unavailable")
            return
        game_name = self.game_registry.metadata(detail.game_key).name
        game_compiler = self.compiler.for_game(detail.game_key)
        view = build_replay_view(
            entry.detail,
            0,
            len(entry.frames),
            owner_id=interaction.user.id,
            frame_view=entry.frames[0].view,
            takeover_info=entry.frames[0].takeover_info,
            timestamp=entry.frames[0].timestamp,
            turn_label=entry.frames[0].turn_label,
            text=self.text,
            game_name=game_name,
            emoji=game_compiler.emoji,
        )
        compiled = game_compiler.compile(
            view, resource_id=entry.detail.id, prefix=P.R_NAV
        )
        from strife.presentation.message import to_discord_files

        files = to_discord_files(getattr(view, "files", []))
        if interaction.response.is_done():
            await interaction.followup.send(view=compiled, files=files, ephemeral=True)
        else:
            await interaction.response.send_message(
                view=compiled, files=files, ephemeral=True
            )

    async def render_frame(
        self,
        match_id: int,
        frame: int,
        interaction: discord.Interaction,
        *,
        owner_id: int,
    ) -> None:
        if not interaction.response.is_done():
            await interaction.response.defer()
        try:
            entry = await self._load_entry(
                match_id, guild_id=interaction.guild_id
            )
        except ReplayFormatTooOld:
            await self.user_errors.send(interaction, "common.replay_old_format")
            return
        except ReplayLoadError:
            await self.user_errors.send(interaction, "common.replay_load_failed")
            return
        if entry is None:
            await self.user_errors.send(interaction, "common.match_not_found")
            return
        if not entry.frames:
            log.warning(
                "Replay for match %s (%s) produced no frames from stored moves",
                match_id,
                entry.detail.game_key,
            )
            await self.user_errors.send(interaction, "common.replay_unavailable")
            return
        frame = max(0, min(frame, len(entry.frames) - 1))
        game_name = self.game_registry.metadata(entry.detail.game_key).name
        game_compiler = self.compiler.for_game(entry.detail.game_key)
        view = build_replay_view(
            entry.detail,
            frame,
            len(entry.frames),
            owner_id=owner_id,
            frame_view=entry.frames[frame].view,
            takeover_info=entry.frames[frame].takeover_info,
            timestamp=entry.frames[frame].timestamp,
            turn_label=entry.frames[frame].turn_label,
            text=self.text,
            game_name=game_name,
            emoji=game_compiler.emoji,
        )
        compiled = game_compiler.compile(view, resource_id=match_id, prefix=P.R_NAV)
        from strife.presentation.message import to_discord_files

        files = to_discord_files(getattr(view, "files", []))
        await edit_pager_message(interaction, view=compiled, attachments=files)

    async def open_jump_modal(
        self,
        interaction: discord.Interaction,
        match_id: int,
        *,
        owner_id: int,
        total: int,
        frame: int = 0,
    ) -> None:
        async def on_submit(
            modal_interaction: discord.Interaction, new_frame: int
        ) -> None:
            await self.render_frame(
                match_id,
                new_frame,
                modal_interaction,
                owner_id=owner_id,
            )

        modal = PageJumpModal(
            title=self.text.get("replay.jump_modal_title"),
            label=self.text.get("replay.jump_modal_label"),
            placeholder=self.text.get("replay.jump_modal_placeholder"),
            current=frame + 1,
            total=total,
            on_submit_cb=on_submit,
            compiler=self.compiler,
            emoji=self.compiler.emoji,
            text=self.text,
        )
        await interaction.response.send_modal(modal)
