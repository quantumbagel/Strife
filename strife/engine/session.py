from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

import discord

from strife.config.text import TextConfig
from strife.engine.context import LiveContext
from strife.engine.game import Game
from strife.engine.players import GameOutcome, Move, Player
from strife.lifecycle.results import build_results_view
from strife.logging import get_logger
from strife.persistence.repositories import (
    FinishedMatch,
    MatchPlayer,
    MoveRecord,
)
from strife.presentation.components import (
    Container,
    LayoutView,
    Separator,
    TextDisplay,
    TextSize,
)
from strife.presentation.message import ViewSurface
from strife.presentation.roster import member_line
from strife.routing.router import InteractionInput
from strife.settings import get_settings

log = get_logger("engine.session")


@dataclass
class PendingInput:
    allowed_actors: set[int]
    allowed_sources: set[str] | None
    future: asyncio.Future[Move]


@dataclass
class RecordedMove:
    turn_index: int
    actor_seat: int | None
    source: str
    arguments: dict
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
        self._finalize_cb = finalize_cb
        self.game_key = game_key
        self.ctx = LiveContext(self)
        self.lock = asyncio.Lock()
        self.pending: dict[int, PendingInput] = {}
        self.recorded_moves: list[RecordedMove] = []
        self.last_move_at = time.monotonic()
        self._warned = False
        self.task: asyncio.Task | None = None
        self._turn_index = 0
        self._started_at = datetime.now(timezone.utc)
        self._match_id: int | None = None
        self._match_code: str | None = None
        self._bot = None
        self._finalized = False

    def set_bot(self, bot) -> None:
        self._bot = bot

    async def start(self) -> None:
        self.task = asyncio.create_task(self._run())

    async def _run(self) -> None:
        try:
            outcome = await self.game.play(self.ctx)
            await self._finalize(outcome, status="completed")
        except asyncio.CancelledError:
            return
        except Exception:
            log.exception("Game session crashed", extra={"match_id": self.id})
            if not self._finalized:
                await self._finalize(
                    GameOutcome(
                        results={},
                        summary={"error": True},
                        description="Game session crashed",
                        player_descriptions={},
                    ),
                    status="abandoned",
                )

    async def submit(self, inp: InteractionInput) -> None:
        async with self.lock:
            seat = self._seat_for_user(inp.actor.id)
            if seat is None:
                raise RuntimeError("not_a_player")
            pending = self.pending.get(seat)
            if pending is None or seat not in pending.allowed_actors:
                raise RuntimeError("not_your_turn")
            if pending.allowed_sources is not None and inp.source not in pending.allowed_sources:
                raise RuntimeError("invalid_action")
            move = Move(actor_seat=seat, source=inp.source, args=inp.args)
            if not pending.future.done():
                pending.future.set_result(move)
            self.pending.pop(seat, None)

    async def force_move(self, seat: int, move: Move) -> None:
        async with self.lock:
            pending = self.pending.get(seat)
            if pending and not pending.future.done():
                pending.future.set_result(move)
                self.pending.pop(seat, None)

    async def cancel(self, reason: str, forfeiter_seat: int | None = None) -> None:
        if self._finalized:
            return
        if self.task and not self.task.done():
            self.task.cancel()
        self._record_action("game_end", {"reason": reason, "cancelled": True})
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

    def _seat_for_user(self, user_id: int) -> int | None:
        for player in self.players:
            if player.user_id == user_id and not player.is_bot:
                return player.seat
        return None

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

    async def _request_input(
        self, view: LayoutView, *, actor: int, sources: set[str] | None
    ) -> Move:
        if self.players[actor].is_bot:
            difficulty = self.players[actor].bot_difficulty or "medium"
            move = await self.game.bot_move(difficulty, actor)
            await self._update_surface(view)
            self._record_move(move)
            return move

        loop = asyncio.get_running_loop()
        future: asyncio.Future[Move] = loop.create_future()
        self.pending[actor] = PendingInput({actor}, sources, future)
        self.last_move_at = time.monotonic()
        self._warned = False
        await self._update_surface(view)
        move = await future
        self._record_move(move)
        return move

    async def _request_inputs(
        self,
        view: LayoutView,
        *,
        actors: set[int],
        sources: set[str] | None,
        until: Literal["all", "any"],
        per_seat_sources: dict[int, set[str]] | None = None,
    ) -> dict[int, Move]:
        results: dict[int, Move] = {}
        humans = {seat for seat in actors if not self.players[seat].is_bot}
        for seat in actors:
            if self.players[seat].is_bot:
                difficulty = self.players[seat].bot_difficulty or "medium"
                move = await self.game.bot_move(difficulty, seat)
                results[seat] = move
                self._record_move(move)

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
            self.pending[seat] = PendingInput({seat}, seat_sources, future)
        self.last_move_at = time.monotonic()
        self._warned = False
        await self._update_surface(view)

        if until == "any":
            done, pending_futures = await asyncio.wait(
                futures.values(), return_when=asyncio.FIRST_COMPLETED
            )
            for seat, future in futures.items():
                if future in done:
                    move = future.result()
                    results[seat] = move
                    self._record_move(move)
                elif not future.done():
                    future.cancel()
                self.pending.pop(seat, None)
            return results

        for seat, future in futures.items():
            move = await future
            results[seat] = move
            self._record_move(move)
            self.pending.pop(seat, None)
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

    def _record_move(self, move: Move) -> None:
        self.recorded_moves.append(
            RecordedMove(
                turn_index=self._turn_index,
                actor_seat=move.actor_seat,
                source=move.source,
                arguments=move.args,
                created_at=datetime.now(timezone.utc),
            )
        )
        self._turn_index += 1
        self.last_move_at = time.monotonic()

    def _record_action(self, source: str, arguments: dict) -> None:
        self.recorded_moves.append(
            RecordedMove(
                turn_index=self._turn_index,
                actor_seat=None,
                source=source,
                arguments=arguments,
                created_at=datetime.now(timezone.utc),
            )
        )
        self._turn_index += 1
        self.last_move_at = time.monotonic()

    async def _finalize(self, outcome: GameOutcome, *, status: str) -> None:
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
                total_turns=len(self.recorded_moves),
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
                        created_at=m.created_at,
                    )
                    for m in self.recorded_moves
                ],
            )
            match_id, code = await self._finalize_cb(finished, outcome)
            self._match_id = match_id
            self._match_code = code

            try:
                final = await self.game.final_view(self.ctx, outcome)
                if final is not None:
                    await self._update_surface(final)
                else:
                    await self.surface.disable_all()
            except Exception:
                log.exception("Failed to update game surface on finalize")

            if self.header_surface is not None:
                try:
                    emoji = self.header_surface.compiler.emoji
                    game_emoji = emoji.get_game_emoji(self.game_key)
                    forward = emoji.get("forward")
                    settings = get_settings()
                    finished_view = LayoutView()
                    container = Container()
                    container.add_text(
                        TextDisplay(
                            markdown_content=f"### {game_emoji} {self.game.metadata.name} {forward} Match Finished",
                            size_style=TextSize.HEADER,
                        )
                    )
                    container.add_separator()

                    roster_lines = [
                        member_line(
                            emoji,
                            user_id=p.user_id,
                            display_name=p.display_name,
                            is_bot=p.is_bot,
                            bot_difficulty=p.bot_difficulty,
                            owner_ids=frozenset(settings.owner_ids),
                        )
                        for p in self.players
                    ]
                    container.add_text(
                        TextDisplay(
                            markdown_content=f"{self.text.get('lobby.players_title')}\n"
                            + ("\n".join(roster_lines) or self.text.get("lobby.empty_roster")),
                            size_style=TextSize.BODY,
                        )
                    )
                    container.add_separator(Separator(visible=False))
                    container.add_text(
                        TextDisplay(
                            markdown_content=f"-# {emoji.get('success')} {self.text.get('lobby.game_finished')}",
                            size_style=TextSize.BODY,
                        )
                    )
                    finished_view.add_container(container)
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
