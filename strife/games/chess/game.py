from __future__ import annotations

import asyncio
import io
import random
from typing import Any

import chess
import chess.svg
import discord
import resvg_py

from strife.engine.context import GameContext
from strife.engine.metadata import (
    BotSpec,
    PlayerCount,
    PlayerOrder,
    game_metadata_from,
    SlashMove,
    MoveParam,
    ParamType,
    SettingOption,
    OptionType,
)
from strife.engine.players import GameOutcome, Move, Player
from strife.engine.turn_based import TurnBasedGame
from strife.persistence.repositories import MoveRecord
from strife.engine.replay import ReplayBuilder, is_terminal_replay_move, system_replay_info
import time
from datetime import datetime, timezone
from strife.presentation.components import (
    LayoutView,
    MediaGallery,
    MediaGalleryItem,
)
from strife.presentation.game_ui import game_container
from strife.presentation.style import add_meta


def parse_user_move(board: chess.Board, text: str) -> chess.Move | None:
    text = text.strip()
    if not text:
        return None
    # 1. Try UCI directly (case insensitive)
    try:
        m = board.parse_uci(text.lower())
        if m in board.legal_moves:
            return m
    except ValueError:
        pass

    # 2. Try SAN directly as is
    try:
        m = board.parse_san(text)
        if m in board.legal_moves:
            return m
    except ValueError:
        pass

    # 3. Try SAN with capitalized first letter (e.g. nf3 -> Nf3, qe2 -> Qe2)
    if len(text) > 1 and text[0].lower() in ("n", "b", "r", "q", "k"):
        capitalized = text[0].upper() + text[1:]
        try:
            m = board.parse_san(capitalized)
            if m in board.legal_moves:
                return m
        except ValueError:
            pass

    # 4. Try lowercase SAN (e.g., e4, d5)
    try:
        m = board.parse_san(text.lower())
        if m in board.legal_moves:
            return m
    except ValueError:
        pass

    return None


@game_metadata_from(
    key="chess",
    dependencies=["chess>=1.11.2", "resvg-py>=0.3.3"],
    name="Chess",
    summary="The classic game of chess.",
    description="Play Chess against another player or a bot. Enter moves using standard text notation like e4 or Nf3.",
    tags=("classic", "strategy", "2p"),
    author="Strife",
    version="1.0.0",
    author_link=None,
    source_link=None,
    how_to_play_link="https://en.wikipedia.org/wiki/Rules_of_chess",
    time_estimate="15m",
    difficulty=4,
    player_count=PlayerCount(fixed=2),
    player_order=PlayerOrder.RANDOM,
    bots=(
        BotSpec("random", "Random legal move"),
        BotSpec("capture-priority", "Prioritizes captures, then random"),
    ),
    bot_takeover_difficulty="capture-priority",
    settings=(
        SettingOption(
            key="time_control",
            title="Time Control",
            description="Move clock time control (e.g. 5+5 is 5 mins base + 5 secs increment per move)",
            type=OptionType.CHOICE,
            default="none",
            choices=("none", "1+0", "3+0", "3+2", "5+0", "5+5", "10+0", "15+10", "30+0"),
            emoji="timer",
        ),
    ),
    slash_moves=(
        SlashMove(
            name="move",
            description="Make a chess move (e.g. e4, Nf3, e2e4)",
            params=(
                MoveParam(
                    name="move",
                    description="The move to play in SAN or UCI notation",
                    type=ParamType.STRING,
                    required=True,
                ),
            ),
        ),
    ),
)
class Chess(TurnBasedGame):
    def __init__(self, players: list[Player], settings, rng: random.Random):
        super().__init__(players, settings, rng)
        self.reset()

    def reset(self) -> None:
        self.board = chess.Board()
        self.current = 0
        
        # Parse time control setting
        time_control = self.setting("time_control", "none")
        self.time_control_active = (time_control != "none")
        self.clocks = [0.0, 0.0]
        self.increment = 0.0
        
        if self.time_control_active:
            try:
                base_str, inc_str = time_control.split("+")
                base_minutes = float(base_str)
                increment_seconds = float(inc_str)
                self.clocks = [base_minutes * 60.0, base_minutes * 60.0]
                self.increment = increment_seconds
            except ValueError:
                self.time_control_active = False
                
        self.last_move_time = None

    def _format_time(self, seconds: float) -> str:
        if seconds < 0:
            seconds = 0
        mins = int(seconds // 60)
        secs = int(seconds % 60)
        return f"{mins:02d}:{secs:02d}"

    def _format_clocks(self) -> str:
        return f"White: `{self._format_time(self.clocks[0])}` | Black: `{self._format_time(self.clocks[1])}`"

    def apply_move(self, move: MoveRecord) -> None:
        if self.time_control_active and move.actor_seat is not None:
            if self.last_move_time is not None and move.created_at is not None:
                t1 = self.last_move_time
                t2 = move.created_at
                if t1.tzinfo is not None:
                    t1 = t1.astimezone(timezone.utc).replace(tzinfo=None)
                if t2.tzinfo is not None:
                    t2 = t2.astimezone(timezone.utc).replace(tzinfo=None)
                elapsed = (t2 - t1).total_seconds()
                
                actor = move.actor_seat
                self.clocks[actor] = max(0.0, self.clocks[actor] - elapsed) + self.increment
            
            if move.created_at is not None:
                self.last_move_time = move.created_at

        if move.source == "move":
            move_text = move.arguments.get("move") or ""
        else:
            move_text = move.source
        m = parse_user_move(self.board, move_text)
        if m is not None:
            self.board.push(m)
        self.current = 1 - self.current

    def _apply_system_timeout(self, move: MoveRecord) -> None:
        if self.time_control_active and move.source == "game_end" and move.arguments.get("reason") == "timeout":
            if self.last_move_time is not None and move.created_at is not None:
                t1 = self.last_move_time
                t2 = move.created_at
                if t1.tzinfo is not None:
                    t1 = t1.astimezone(timezone.utc).replace(tzinfo=None)
                if t2.tzinfo is not None:
                    t2 = t2.astimezone(timezone.utc).replace(tzinfo=None)
                elapsed = (t2 - t1).total_seconds()
                
                actor = self.current
                self.clocks[actor] = max(0.0, self.clocks[actor] - elapsed)

    def replay_action_status(self, ctx: GameContext, next_actor: int) -> str:
        player = self.players[next_actor]
        color = "White" if next_actor == 0 else "Black"
        status = f"{color} ({player.mention}) to act"
        return status

    def _action_status(self, ctx: GameContext, seat: int) -> str:
        player = self.players[seat]
        color = "White" if seat == 0 else "Black"
        check_str = " (in check)" if self.board.is_check() else ""
        return f"{color} ({player.mention}) to act{check_str}"

    def _outcome(self) -> GameOutcome | None:
        if not self.board.is_game_over():
            return None

        if self.board.is_checkmate():
            winner = 1 - self.current
            loser = self.current
            winner_mention = str(self.players[winner])
            return GameOutcome(
                results={winner: "win", loser: "loss"},
                summary={"winner": winner},
                description=f"{winner_mention} won by checkmate",
                player_descriptions={winner: "Won by checkmate", loser: "Lost"},
            )

        # Otherwise it's a draw
        reason = "draw"
        if self.board.is_stalemate():
            reason = "stalemate"
        elif self.board.is_insufficient_material():
            reason = "insufficient material"
        elif self.board.can_claim_threefold_repetition():
            reason = "threefold repetition"
        elif self.board.can_claim_fifty_moves():
            reason = "fifty-move rule"

        return GameOutcome(
            results={0: "draw", 1: "draw"},
            summary={"winner": None},
            description=f"Draw by {reason}",
            player_descriptions={0: f"Draw ({reason})", 1: f"Draw ({reason})"},
        )

    async def play(self, ctx: GameContext) -> GameOutcome:
        error_msg = None
        if self.time_control_active and self.last_move_time is None:
            self.last_move_time = datetime.now(timezone.utc).replace(tzinfo=None)

        while True:
            outcome = self._outcome()
            if outcome is not None:
                return outcome

            seat = self.current
            if self.time_control_active and self.clocks[seat] <= 0:
                winner = 1 - seat
                loser = seat
                winner_mention = str(self.players[winner])
                return GameOutcome(
                    results={winner: "win", loser: "loss"},
                    summary={"winner": winner, "reason": "timeout"},
                    description=f"{winner_mention} won on time",
                    player_descriptions={winner: "Won on time", loser: "Lost on time"},
                )

            lead = self._action_status(ctx, seat)
            if error_msg:
                lead = f"**{error_msg}**\n{lead}"

            view = self.render(
                ctx,
                lead=lead,
                status_emoji="loading",
            )

            # Accept both UCI and SAN notation, and the slash command "move"
            sources = {m.uci() for m in self.board.legal_moves} | {
                self.board.san(m) for m in self.board.legal_moves
            }
            sources.add("move")
            
            start_time = time.monotonic()
            timeout_seconds = self.clocks[seat] if self.time_control_active else None
            timeout_consequence = "game_ends" if self.time_control_active else None

            move = await ctx.request_input(
                view,
                actor=seat,
                sources=sources,
                timeout_seconds=timeout_seconds,
                timeout_consequence=timeout_consequence,
            )

            if self.time_control_active:
                elapsed = time.monotonic() - start_time
                self.clocks[seat] = max(0.0, self.clocks[seat] - elapsed) + self.increment

            if move.source == "move":
                move_text = move.args.get("move", "")
            else:
                move_text = move.source

            m = parse_user_move(self.board, move_text)
            if m is not None:
                self.board.push(m)
                self.current = 1 - seat
                error_msg = None
            else:
                error_msg = f"Invalid or illegal move: '{move_text}'. Try again."

    async def parse_replay(self, moves: list[MoveRecord], ctx: GameContext) -> list[ReplayFrame]:
        self.reset()
        if ctx.started_at:
            t = ctx.started_at
            if t.tzinfo is not None:
                t = t.astimezone(timezone.utc).replace(tzinfo=None)
            self.last_move_time = t
            
        builder = ReplayBuilder(ctx)
        builder.initial_frame(
            self.render_replay(
                ctx,
                title="Start",
                status=self.replay_initial_status(ctx, moves),
                status_emoji="loading",
            ),
            label="Start",
        )

        for index, move in enumerate(moves):
            system_info = system_replay_info(self.players, move)
            if move.is_game:
                self.apply_move(move)
            else:
                self._apply_system_timeout(move)

            if is_terminal_replay_move(move, index, len(moves)):
                builder.after_move(
                    move,
                    self.render_final_replay(ctx),
                    label="Final",
                    actor_seat=move.actor_seat,
                    takeover_info=system_info,
                )
                break

            action = index + 1
            builder.after_move(
                move,
                self.render_replay(ctx, title=f"Action {action}"),
                label=f"Action {action}",
                actor_seat=move.actor_seat,
                takeover_info=system_info,
            )

        return builder.build()

    def _render_base(
        self,
        ctx: GameContext,
        *,
        title: str | None = None,
        status: str | None = None,
        status_emoji: str | None = None,
        lead: str | None = None,
    ) -> tuple[LayoutView, Container]:
        view = LayoutView()

        # Render SVG board
        svg_data = chess.svg.board(self.board)
        # Convert to PNG using resvg-py
        png_data = resvg_py.svg_to_bytes(svg_string=svg_data, width=450, height=450)

        # Attach the board as a file attachment
        move_index = len(self.board.move_stack)
        filename = f"board_{move_index}.png"
        file = discord.File(io.BytesIO(png_data), filename=filename)
        view.files = [file]

        container = game_container(ctx, lead=lead or status or title, prefix_emoji=status_emoji or "loading")

        if self.time_control_active:
            add_meta(container, f"{ctx.emoji.get('timer')} Clocks: {self._format_clocks()}")

        gallery = MediaGallery()
        gallery.add_item(MediaGalleryItem(media_url=f"attachment://{filename}", description="Chess board"))
        container.set_gallery(gallery)

        return view, container

    def render(
        self,
        ctx: GameContext,
        *,
        title: str | None = None,
        status: str | None = None,
        status_emoji: str | None = None,
        lead: str | None = None,
    ) -> LayoutView:
        view, container = self._render_base(
            ctx, title=title, status=status, status_emoji=status_emoji, lead=lead
        )
        add_meta(container, "Use `/chess move` with SAN or UCI — `e4`, `Nf3`, or `e2e4`.")
        view.add_container(container)
        return view

    def render_replay(
        self,
        ctx: GameContext,
        *,
        title: str | None = None,
        status: str | None = None,
        status_emoji: str | None = None,
        lead: str | None = None,
    ) -> LayoutView:
        view, container = self._render_base(
            ctx, title=title, status=status, status_emoji=status_emoji, lead=lead
        )
        view.add_container(container)
        return view

    def render_final(self, ctx: GameContext) -> LayoutView:
        outcome = self._outcome()
        status = outcome.description if outcome else "Game over."
        return self.render(ctx, title="Final", status=status, status_emoji="error")

    def render_final_replay(self, ctx: GameContext) -> LayoutView:
        outcome = self._outcome()
        status = outcome.description if outcome else "Game over."
        return self.render_replay(ctx, title="Final", status=status, status_emoji="error")



    async def bot_move(self, difficulty: str, seat: int) -> Move:
        await asyncio.sleep(0.5)
        legal = list(self.board.legal_moves)
        if not legal:
            return Move(actor_seat=seat, source="resign", args={})

        if difficulty == "capture-priority":
            captures = [m for m in legal if self.board.is_capture(m)]
            if captures:
                chosen = self.rng.choice(captures)
            else:
                chosen = self.rng.choice(legal)
        else:
            chosen = self.rng.choice(legal)

        return Move(actor_seat=seat, source=chosen.uci(), args={})
