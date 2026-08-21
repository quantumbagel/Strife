from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import time

import discord

from strife.engine.log import LogEntryKind, SYSTEM_SOURCES
from strife.engine.players import GameOutcome, Move
from strife.lifecycle.results import build_results_view
from strife.persistence.repositories import FinishedMatch, MatchPlayer
from strife.presentation.game_ui import build_game_thread_header_view
from strife.session.types import log


class SessionLifecycleMixin:
    async def cancel(self, reason: str, forfeiter_seat: int | None = None) -> bool:
        if self._finalized:
            return False
        task = self.task
        if task is not None and not task.done() and task is not asyncio.current_task():
            task.cancel()
        self._append_log_entry("game_end", {"reason": reason, "cancelled": True}, kind=LogEntryKind.SYSTEM)
        results = {}
        summary = {"reason": reason}
        player_descriptions = {}
        if forfeiter_seat is not None:
            for player in self.players:
                if player.seat == forfeiter_seat:
                    results[player.seat] = "loss"
                    player_descriptions[player.seat] = "Timed out" if reason == "timeout" else "Forfeited"
                else:
                    results[player.seat] = "win"
                    player_descriptions[player.seat] = "Opponent timed out" if reason == "timeout" else "Opponent forfeited"
            opponents = [p.seat for p in self.players if p.seat != forfeiter_seat]
            if len(opponents) == 1:
                summary["winner"] = opponents[0]
            forfeiter_mention = str(self.players[forfeiter_seat])
            action_str = "timed out" if reason == "timeout" else "forfeited"
            description = f"{forfeiter_mention} {action_str}"
        elif reason == "restart":
            description = self.text.get("match.session_restarted_description")
            for player in self.players:
                player_descriptions[player.seat] = "Abandoned (bot restart)"
            await self._notify_thread(self.text.get("match.session_restarted"))
        else:
            description = reason.capitalize()
            for player in self.players:
                player_descriptions[player.seat] = "Abandoned"

        await self._finalize(
            GameOutcome(
                results=results,
                summary=summary,
                description=description,
                player_descriptions=player_descriptions,
            ),
            status="abandoned",
        )
        return True


    def _record_move(self, move: Move, *, kind: LogEntryKind | None = None) -> None:
        entry_kind = kind or (
            LogEntryKind.SYSTEM
            if move.kind == LogEntryKind.SYSTEM or move.source in SYSTEM_SOURCES
            else LogEntryKind.GAME
        )
        self.recorded_moves.append(
            Move(
                actor_seat=move.actor_seat,
                source=move.source,
                args=move.args,
                kind=entry_kind,
                turn_index=self._turn_index,
                created_at=datetime.now(timezone.utc),
            )
        )
        self._turn_index += 1
        self.last_move_at = time.monotonic()


    def _append_log_entry(
        self,
        source: str,
        arguments: dict,
        *,
        actor_seat: int | None = None,
        kind: LogEntryKind = LogEntryKind.GAME,
    ) -> None:
        self.recorded_moves.append(
            Move(
                actor_seat=actor_seat,
                source=source,
                args=arguments,
                kind=kind,
                turn_index=self._turn_index,
                created_at=datetime.now(timezone.utc),
            )
        )
        self._turn_index += 1
        self.last_move_at = time.monotonic()


    def _record_system(self, source: str, arguments: dict, *, actor_seat: int | None = None) -> None:
        self._append_log_entry(source, arguments, actor_seat=actor_seat, kind=LogEntryKind.SYSTEM)


    async def _finalize(self, outcome: GameOutcome, *, status: str) -> None:
        async with self.lock:
            if self._finalized:
                return
            self._finalized = True
        match_id = 0
        try:
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
                total_turns=sum(1 for m in self.recorded_moves if m.kind == LogEntryKind.GAME),
                started_at=self._started_at,
                ended_at=datetime.now(timezone.utc),
                players=[
                    MatchPlayer(
                        seat_index=p.seat,
                        user_id=p.user_id,
                        is_bot=p.is_bot,
                        bot_difficulty=p.bot_difficulty,
                        display_name=p.display_name,
                        role_key=p.role_key,
                        result=outcome.results.get(p.seat),
                    )
                    for p in self.players
                ],
                moves=list(self.recorded_moves),
            )
            match_id, code = await self._finalizer.persist(finished, outcome)
            try:
                self._finalizer.notify_match_end(self.thread_id, match_id, outcome, self.players)
            except Exception:
                log.exception("Failed to register rematch offer for thread %s", self.thread_id)
            self._match_id = match_id
            self._match_code = code

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
                )
                if hasattr(self, "lobby_surface") and self.lobby_surface is not None:
                    await self.lobby_surface.update(results_view)
            except Exception:
                log.exception("Failed to update results view on finalize")

            if self._bot:
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
            await self._finalizer.session_complete(self)
