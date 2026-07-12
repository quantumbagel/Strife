from __future__ import annotations

import logging
import time
from collections import OrderedDict
from dataclasses import dataclass

import discord

from strife.config.text import TextConfig
from strife.persistence.repositories import MatchDetail, MatchRepository, MoveRepository
from strife.presentation.compiler import Compiler
from strife.presentation.user_error import UserErrorPresenter
from strife.presentation.modals import PageJumpModal
from strife.engine.context import ReplayContext
from strife.engine.players import Player
from strife.engine.registry import GameRegistry
from strife.replay.view import build_replay_view
from strife.routing import prefixes as P

log = logging.getLogger(__name__)


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
        self._cache_size = 64
        self._autocomplete_cache: dict[int, tuple[float, list]] = {}

    async def autocomplete_matches(
        self, user_id: int, *, limit: int = 25
    ) -> list:
        now = time.monotonic()
        cached = self._autocomplete_cache.get(user_id)
        if cached and now - cached[0] < 15:
            return cached[1]
        matches = await self.matches.list_for_user(user_id, None, limit=limit)
        self._autocomplete_cache[user_id] = (now, matches)
        if len(self._autocomplete_cache) > 256:
            oldest = min(self._autocomplete_cache, key=lambda k: self._autocomplete_cache[k][0])
            self._autocomplete_cache.pop(oldest, None)
        return matches

    async def _load_entry(self, match_id: int) -> _ReplayCacheEntry | None:
        if match_id in self._cache:
            self._cache.move_to_end(match_id)
            return self._cache[match_id]
        detail = await self.matches.get(match_id)
        if detail is None:
            return None
        move_records = await self.moves.list_for_match(match_id)
        players = [
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
        try:
            game = self.game_registry.create(
                detail.game_key, players, detail.settings, detail.seed
            )
            ctx = ReplayContext(
                rng=game.rng,
                players=players,
                settings=detail.settings,
                emoji=self.compiler.emoji,
                started_at=detail.started_at,
            )
            from strife.presentation.emoji_context import bind_emoji, reset_emoji

            token = bind_emoji(self.compiler.emoji)
            try:
                frames = await game.parse_replay(move_records, ctx)
            finally:
                reset_emoji(token)
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
        self._cache[match_id] = entry
        if len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)
        return entry

    async def open(self, interaction: discord.Interaction, match: str | int) -> None:
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)
        detail = await self.matches.get(match)
        if detail is None:
            await self.user_errors.send(interaction, "common.match_not_found")
            return
        try:
            entry = await self._load_entry(detail.id)
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
            emoji=self.compiler.emoji,
        )
        compiled = self.compiler.compile(view, resource_id=entry.detail.id, prefix=P.R_NAV)
        if interaction.response.is_done():
            await interaction.followup.send(view=compiled, ephemeral=True)
        else:
            await interaction.response.send_message(view=compiled, ephemeral=True)

    async def render_frame(
        self,
        match_id: int,
        frame: int,
        interaction: discord.Interaction,
        *,
        owner_id: int,
    ) -> None:
        try:
            entry = await self._load_entry(match_id)
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
            emoji=self.compiler.emoji,
        )
        compiled = self.compiler.compile(view, resource_id=match_id, prefix=P.R_NAV)
        if interaction.response.is_done():
            await interaction.edit_original_response(view=compiled)
        else:
            await interaction.response.edit_message(view=compiled)

    async def open_jump_modal(
        self,
        interaction: discord.Interaction,
        match_id: int,
        *,
        owner_id: int,
        total: int,
        frame: int = 0,
    ) -> None:
        async def on_submit(modal_interaction: discord.Interaction, new_frame: int) -> None:
            await modal_interaction.response.defer()
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
        )
        await interaction.response.send_modal(modal)
