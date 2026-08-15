from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal

import discord

from strife.config.text import TextConfig
from strife.engine.context import LiveContext
from strife.engine.game import Game
from strife.engine.log import LogEntryKind, SYSTEM_SOURCES
from strife.engine.players import GameOutcome, Move, Player
from strife.lifecycle.results import build_results_view
from strife.logging import get_logger
from strife.persistence.repositories import (
    FinishedMatch,
    MatchPlayer,
    MoveRecord,
)
from strife.presentation.components import LayoutView
from strife.presentation.feedback import build_feedback_view, send_ephemeral_feedback
from strife.presentation.game_ui import build_game_thread_header_view
from strife.presentation.message import ViewSurface
from strife.lifecycle.timeout import timeout_consequence
from strife.routing.router import InteractionInput

log = get_logger("engine.session")


@dataclass
class PendingInput:
    allowed_actors: set[int]
    allowed_sources: set[str] | None
    future: asyncio.Future[Move]
    description: str | None = None
    line_description: str | None = None
    timeout_seconds: float | None = None
    timeout_consequence: str | None = None


@dataclass
class RecordedMove:
    turn_index: int
    actor_seat: int | None
    source: str
    arguments: dict
    kind: LogEntryKind
    created_at: datetime


class GameSession:
    def __init__(
        self,
        *,
        thread_id: int,
        guild_id: int,
        game: Game,
        players: list[Player],
        settings: dict,
        seed: int,
        surface: ViewSurface,
        text: TextConfig,
        finalize_cb,
        game_key: str,
        header_surface: ViewSurface | None = None,
        turn_timeout_seconds: int = 90,
        turn_timeout_max_strikes: int = 3,
        turn_timeout_consequence: str = "abandon",
    ) -> None:
        self.id = thread_id
        self.thread_id = thread_id
        self.guild_id = guild_id
        self.game = game
        self.players = players
        self.settings = settings
        self.seed = seed
        self.surface = surface
        self.header_surface = header_surface
        self.text = text
        self.turn_timeout_seconds = turn_timeout_seconds
        self.turn_timeout_max_strikes = turn_timeout_max_strikes
        self.turn_timeout_consequence = turn_timeout_consequence
        self._finalize_cb = finalize_cb
        self.game_key = game_key
        self.ctx = LiveContext(self)
        self.lock = asyncio.Lock()
        self.pending: dict[int, PendingInput] = {}
        self.recorded_moves: list[RecordedMove] = []
        self.last_move_at = time.monotonic()
        self.task: asyncio.Task | None = None
        self._turn_index = 0
        self._started_at = datetime.now(timezone.utc)
        self._match_id: int | None = None
        self._match_code: str | None = None
        self._bot = None
        self._finalized = False
        self._timeout_warned: dict[int, float] = {}

    def set_bot(self, bot) -> None:
        self._bot = bot

    async def start(self) -> None:
        self.task = asyncio.create_task(self._run())

    async def _run(self) -> None:
        from strife.presentation.emoji_context import bind_emoji, reset_emoji

        token = bind_emoji(self.surface.compiler.emoji)
        try:
            try:
                outcome = await self.game.play(self.ctx)
                await self._finalize(outcome, status="completed")
            except asyncio.CancelledError:
                if not self._finalized:
                    self._append_log_entry(
                        "game_end",
                        {"reason": "cancelled", "cancelled": True},
                        kind=LogEntryKind.SYSTEM,
                    )
                    await self._finalize(
                        GameOutcome(
                            results={},
                            summary={"reason": "cancelled"},
                            description=self.text.get("match.session_cancelled_description"),
                            player_descriptions={
                                player.seat: "Abandoned" for player in self.players
                            },
                        ),
                        status="abandoned",
                    )
                return
            except Exception:
                log.exception("Game session crashed", extra={"match_id": self.id})
                await self._notify_thread(self.text.get("match.session_crashed"))
                if not self._finalized:
                    await self._finalize(
                        GameOutcome(
                            results={},
                            summary={"error": True},
                            description=self.text.get("match.session_crashed_description"),
                            player_descriptions={
                                player.seat: "Abandoned" for player in self.players
                            },
                        ),
                        status="abandoned",
                    )
        finally:
            reset_emoji(token)

    async def submit(self, inp: InteractionInput) -> None:
        async with self.lock:
            seat = self._seat_for_user(inp.actor.id)
            if seat is None:
                raise RuntimeError("not_a_player")
            pending = self.pending.get(seat)
            if pending is None or seat not in pending.allowed_actors:
                raise RuntimeError("cannot_act")
            if pending.allowed_sources is not None and inp.source not in pending.allowed_sources:
                raise RuntimeError("invalid_action")
            move = Move(actor_seat=seat, source=inp.source, args=inp.args)
            if not pending.future.done():
                pending.future.set_result(move)
            self.pending.pop(seat, None)

    async def handle_slash_command(self, user_id: int, command_name: str, args: dict[str, Any]) -> None:
        async with self.lock:
            seat = self._seat_for_user(user_id)
            if seat is None:
                raise RuntimeError("not_a_player")
            pending = self.pending.get(seat)
            if pending is None or seat not in pending.allowed_actors:
                raise RuntimeError("cannot_act")

            source = command_name
            move_args = args

            if pending.allowed_sources is not None and source not in pending.allowed_sources:
                raise RuntimeError("invalid_action")

            move = Move(actor_seat=seat, source=source, args=move_args)
            if not pending.future.done():
                pending.future.set_result(move)
            self.pending.pop(seat, None)



    async def handle_query(self, source: str, interaction: discord.Interaction) -> bool:
        seat = self._seat_for_user(interaction.user.id)
        if seat is None:
            view = build_feedback_view(
                icon=self.surface.compiler.emoji.get("error"),
                title=self.text.get("errors.not_a_player"),
                body=self.text.get("errors_help.not_a_player"),
                body_heading=self.text.get("common.error_fix"),
                text=self.text,
            )
            await send_ephemeral_feedback(
                interaction,
                view,
                compiler=self.surface.compiler,
                prefix=self.surface.prefix,
                resource_id=self.thread_id,
            )
            return True

        try:
            return await asyncio.wait_for(
                self.game.handle_query(seat, source, interaction, self.ctx, self.surface),
                timeout=5.0,
            )
        except Exception:
            log.exception(
                "Error or timeout in handle_query for game %s (match_id: %s)",
                self.game_key,
                self.id,
            )
            raise RuntimeError("query_failed") from None

    async def force_move(self, seat: int, move: Move) -> None:
        async with self.lock:
            pending = self.pending.get(seat)
            if pending and not pending.future.done():
                pending.future.set_result(move)
                self.pending.pop(seat, None)
        await self.refresh_header()

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

    async def _request_input(
        self,
        view: LayoutView,
        *,
        actor: int,
        sources: set[str] | None,
        record: bool = True,
        description: str | None = None,
        timeout_seconds: float | None = None,
        timeout_consequence: str | None = None,
    ) -> Move:
        if self.players[actor].is_bot:
            difficulty = self.players[actor].bot_difficulty or "medium"
            try:
                move = await asyncio.wait_for(
                    self.game.bot_move(difficulty, actor),
                    timeout=10.0,
                )
            except Exception as e:
                log.exception("Bot move crashed or timed out for seat %s in match %s", actor, self.id)
                raise RuntimeError(f"Bot failed to make a move: {e}") from e
            await self._update_surface(view)
            if record:
                self._record_move(move)
            return move

        loop = asyncio.get_running_loop()
        future: asyncio.Future[Move] = loop.create_future()
        self.pending[actor] = PendingInput(
            {actor},
            sources,
            future,
            description=description,
            timeout_seconds=timeout_seconds,
            timeout_consequence=timeout_consequence,
        )
        self.last_move_at = time.monotonic()
        self._timeout_warned.pop(actor, None)
        await self._update_surface(view)
        await self.refresh_header()
        move = await future
        if record:
            self._record_move(move)
        await self.refresh_header()
        return move

    async def _request_inputs(
        self,
        view: LayoutView,
        *,
        actors: set[int],
        sources: set[str] | None,
        until: Literal["all", "any"],
        per_seat_sources: dict[int, set[str]] | None = None,
        record: bool = True,
        description: str | None = None,
        descriptions: dict[int, str] | None = None,
        timeout_seconds: float | None = None,
        timeout_consequence: str | None = None,
    ) -> dict[int, Move]:
        results: dict[int, Move] = {}
        humans = {seat for seat in actors if not self.players[seat].is_bot}
        bots = actors - humans

        if until == "any" and not humans:
            # All actors are bots: pick one at random and return only that move.
            bot_seat = self.game.rng.choice(sorted(bots))
            difficulty = self.players[bot_seat].bot_difficulty or "medium"
            try:
                move = await asyncio.wait_for(
                    self.game.bot_move(difficulty, bot_seat),
                    timeout=10.0,
                )
            except Exception as e:
                log.exception("Bot move crashed or timed out for seat %s in match %s", bot_seat, self.id)
                raise RuntimeError(f"Bot failed to make a move: {e}") from e
            if record:
                self._record_move(move)
            return {bot_seat: move}

        loop = asyncio.get_running_loop()
        futures: dict[int, asyncio.Future[Move]] = {}
        for seat in humans:
            future: asyncio.Future[Move] = loop.create_future()
            futures[seat] = future
            seat_sources = (
                per_seat_sources.get(seat, sources)
                if per_seat_sources is not None
                else sources
            )
            self.pending[seat] = PendingInput(
                {seat},
                seat_sources,
                future,
                description=description,
                line_description=(
                    descriptions.get(seat) if descriptions is not None else None
                ),
                timeout_seconds=timeout_seconds,
                timeout_consequence=timeout_consequence,
            )
        self.last_move_at = time.monotonic()
        for seat in humans:
            self._timeout_warned.pop(seat, None)
        await self._update_surface(view)
        await self.refresh_header()

        if until == "any":
            if not futures:
                return results
            done, pending_futures = await asyncio.wait(
                futures.values(), return_when=asyncio.FIRST_COMPLETED
            )
            for seat, future in futures.items():
                if future in done:
                    move = future.result()
                    results[seat] = move
                    if record:
                        self._record_move(move)
                elif not future.done():
                    future.cancel()
                self.pending.pop(seat, None)
            await self.refresh_header()
            return results

        # until == "all": collect bot moves alongside human futures.
        for seat in bots:
            difficulty = self.players[seat].bot_difficulty or "medium"
            try:
                move = await asyncio.wait_for(
                    self.game.bot_move(difficulty, seat),
                    timeout=10.0,
                )
            except Exception as e:
                log.exception("Bot move crashed or timed out for seat %s in match %s", seat, self.id)
                raise RuntimeError(f"Bot failed to make a move: {e}") from e
            results[seat] = move
            if record:
                self._record_move(move)

        for seat, future in futures.items():
            move = await future
            results[seat] = move
            if record:
                self._record_move(move)
            self.pending.pop(seat, None)
        await self.refresh_header()
        return results

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

    def _record_move(self, move: Move, *, kind: LogEntryKind | None = None) -> None:
        entry_kind = kind or (
            LogEntryKind.SYSTEM
            if move.source in SYSTEM_SOURCES
            else LogEntryKind.GAME
        )
        self.recorded_moves.append(
            RecordedMove(
                turn_index=self._turn_index,
                actor_seat=move.actor_seat,
                source=move.source,
                arguments=move.args,
                kind=entry_kind,
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
            RecordedMove(
                turn_index=self._turn_index,
                actor_seat=actor_seat,
                source=source,
                arguments=arguments,
                kind=kind,
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
                moves=[
                    MoveRecord(
                        turn_index=m.turn_index,
                        actor_seat=m.actor_seat,
                        source=m.source,
                        arguments=m.arguments,
                        kind=m.kind,
                        created_at=m.created_at,
                    )
                    for m in self.recorded_moves
                ],
            )
            match_id, code = await self._finalize_cb(finished, outcome)
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
            await self._finalize_cb.session_complete(self)  # type: ignore[attr-defined]
