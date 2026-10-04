from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import discord

from strife.engine.log import LogEntryKind
from strife.engine.players import GameOutcome, Result
from strife.lifecycle.results import build_results_view
from strife.persistence.repositories import FinishedMatch, MatchPlayer
from strife.session.header import build_game_thread_header_view
from strife.session.types import log


class SessionLifecycleMixin:
    async def cancel(self, reason: str, forfeiter_seat: int | None = None) -> bool:
        async with self.lock:
            if self._finalized or self._ending or self._pausing:
                return False
            self._ending = True
        task = self.task
        if task is not None and not task.done() and task is not asyncio.current_task():
            task.cancel()
            try:
                await asyncio.wait_for(task, timeout=10.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass
        async with self.lock:
            self.log.system("game_end", {"reason": reason, "cancelled": True})
        if forfeiter_seat is not None:
            outcome = self.game.forfeit_end_outcome(forfeiter_seat, reason)
        elif reason == "restart":
            await self._notify_thread(self.text.get("match.session_restarted"))
            outcome = GameOutcome(
                results={},
                summary={"reason": reason},
                description=self.text.get("match.session_restarted_description"),
                player_descriptions={
                    player.seat: "Abandoned (bot restart)" for player in self.players
                },
            )
        else:
            outcome = GameOutcome(
                results={},
                summary={"reason": reason},
                description=reason.capitalize(),
                player_descriptions={player.seat: "Abandoned" for player in self.players},
            )

        await self._finalize(outcome, status="abandoned")
        return True

    async def pause(self) -> bool:
        """Stop play without ending the match so the next boot can resume it."""
        async with self.lock:
            if self._finalized or self._ending or self._pausing:
                return False
            self._pausing = True
        task = self.task
        if task is not None and not task.done() and task is not asyncio.current_task():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        await self._stop_writer()
        match_id = self._match_id
        if match_id is not None:
            try:
                await self._finalizer.append_moves(
                    match_id, list(self.log.entries)
                )
            except Exception:
                log.exception(
                    "Failed to re-append in-memory log for thread %s on pause",
                    self.thread_id,
                )
        await self._notify_thread(self.text.get("match.session_paused"))
        return True


    async def _finalize(self, outcome: GameOutcome, *, status: str) -> None:
        async with self.lock:
            if self._finalized:
                return
            self._ending = True
            self._finalized = True
            moves = list(self.recorded_moves)
        match_id = 0
        persist_ok = False
        released = False
        try:
            for seat, value in outcome.results.items():
                try:
                    Result(value)
                except (ValueError, TypeError):
                    log.error(
                        "Invalid GameOutcome result %r for seat %s (thread %s)",
                        value,
                        seat,
                        self.thread_id,
                    )
            finished = FinishedMatch(
                code=self._match_code,
                game_key=self.game_key,
                guild_id=self.guild_id,
                thread_id=self.thread_id,
                seed=self.seed,
                settings=self.settings,
                status=status,
                outcome={
                    "summary": outcome.summary,
                    "description": outcome.description,
                    "player_descriptions": {
                        str(k): v for k, v in outcome.player_descriptions.items()
                    }
                    if outcome.player_descriptions
                    else {},
                },
                total_turns=sum(
                    1
                    for m in moves
                    if m.kind == LogEntryKind.GAME and m.actor_seat is not None
                ),
                started_at=self._started_at,
                ended_at=datetime.now(timezone.utc),
                match_id=self._match_id,
                game_version=self.game_version,
                board_message_id=self.surface.message_id,
                header_message_id=(
                    self.header_surface.message_id if self.header_surface is not None else None
                ),
                lobby_channel_id=self.lobby_channel_id,
                lobby_message_id=self.lobby_message_id,
                turn_timeout_seconds=self.turn_timeout_seconds,
                turn_timeout_max_strikes=self.turn_timeout_max_strikes,
                turn_timeout_consequence=self.turn_timeout_consequence.value,
                lobby_private=self.lobby_private,
                lobby_creator_id=self.lobby_creator_id,
                players=[
                    MatchPlayer(
                        seat_index=p.seat,
                        user_id=p.user_id,
                        is_bot=p.is_bot and p.seat not in self.taken_over,
                        bot_difficulty=p.bot_difficulty,
                        display_name=p.display_name,
                        role_key=self.game.players[p.seat].role_key,
                        # A bot finished an AFK player's seat; its result isn't theirs.
                        result=(
                            Result.LOSS
                            if p.seat in self.taken_over and outcome.results.get(p.seat)
                            else outcome.results.get(p.seat)
                        ),
                    )
                    for p in self.players
                ],
                moves=moves,
            )

            try:
                await self._stop_writer()
            except Exception:
                log.exception("Failed to flush moves for thread %s", self.thread_id)
            match_id, code = 0, self._match_code or ""
            delays = (1.0, 3.0, 9.0)
            for attempt, delay in enumerate(delays):
                try:
                    match_id, code = await self._finalizer.finish(finished, outcome)
                    persist_ok = True
                    break
                except Exception:
                    log.exception(
                        "Failed to persist match for thread %s (attempt %s/3)",
                        self.thread_id,
                        attempt + 1,
                    )
                    if attempt + 1 < len(delays):
                        await asyncio.sleep(delay)
            if not persist_ok:
                await self._notify_thread(self.text.get("match.save_failed"))
            if persist_ok:
                try:
                    self._finalizer.notify_match_end(self.thread_id, match_id, outcome, self.players)
                except Exception:
                    log.exception("Failed to register rematch offer for thread %s", self.thread_id)
            self._match_id = match_id
            self._match_code = code

            await self._finalizer.session_complete(self)
            released = True

            try:
                final = await asyncio.wait_for(
                    self.game.final_view(self.ctx, outcome),
                    timeout=5.0,
                )
                if final is not None:
                    await self._update_surface(final)
                else:
                    await self.surface.disable_all()
            except Exception:
                log.exception("Failed to update game surface on finalize")

            if self.header_surface is not None:
                try:
                    finished_view = build_game_thread_header_view(
                        players=self.players,
                        text=self.text,
                        emoji=self.header_surface.compiler.emoji,
                        finished=True,
                        owner_ids=self._owner_ids(),
                    )
                    await self.header_surface.update(finished_view)
                except Exception:
                    log.exception("Failed to update game thread header message to finished")

            try:
                results_view = build_results_view(
                    game_name=self.game.metadata.name,
                    game_key=self.game_key,
                    outcome=outcome,
                    players=self.players,
                    thread_id=self.thread_id,
                    match_id=match_id,
                    text=self.text,
                    emoji=self.surface.compiler.emoji,
                    rematch_disabled=not persist_ok,
                    replay_disabled=not persist_ok,
                    match_status=status,
                    removed_seats=frozenset(getattr(self, "_removed_seats", ())),
                    taken_over_seats=frozenset(self.taken_over),
                    role_keys={
                        p.seat: self.game.players[p.seat].role_key for p in self.players
                    },
                )
                if hasattr(self, "lobby_surface") and self.lobby_surface is not None:
                    await self.lobby_surface.update(results_view)
            except Exception:
                log.exception("Failed to update results view on finalize")

            if persist_ok and self._bot:
                thread = self._bot.get_channel(self.thread_id)
                if not thread:
                    try:
                        thread = await self._bot.fetch_channel(self.thread_id)
                    except Exception:
                        pass
                if isinstance(thread, discord.Thread):
                    try:
                        await thread.edit(locked=True)
                    except Exception:
                        log.exception("Failed to lock game thread %s", self.thread_id)
        finally:
            encoder = getattr(self.surface.compiler, "encoder", None)
            if encoder is not None:
                encoder.invalidate_resource(self.thread_id)
            if not released:
                await self._finalizer.session_complete(self)
