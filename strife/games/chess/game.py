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
)
from strife.engine.players import GameOutcome, Move, Player
from strife.engine.turn_based import TurnBasedGame
from strife.persistence.repositories import MoveRecord
from strife.presentation.components import (
    LayoutView,
    MediaGallery,
    MediaGalleryItem,
    TextDisplay,
)
from strife.presentation.game_ui import game_container


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
    time_estimate="15m",
    difficulty=4,
    player_count=PlayerCount(fixed=2),
    player_order=PlayerOrder.RANDOM,
    bots=(
        BotSpec("easy", "Random legal move"),
        BotSpec("medium", "Captures-only priority"),
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

    def apply_move(self, move: MoveRecord) -> None:
        if move.source == "move":
            move_text = move.args.get("move") or ""
        else:
            move_text = move.source
        m = parse_user_move(self.board, move_text)
        if m is not None:
            self.board.push(m)
        self.current = 1 - self.current

    def replay_action_status(self, ctx: GameContext, next_actor: int) -> str:
        player = self.players[next_actor]
        color = "White" if next_actor == 0 else "Black"
        return f"{color} ({player.mention}) to act"

    def _action_status(self, ctx: GameContext, seat: int) -> str:
        player = self.players[seat]
        color = "White" if seat == 0 else "Black"
        check_str = " (in check!)" if self.board.is_check() else ""
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
        while True:
            outcome = self._outcome()
            if outcome is not None:
                return outcome

            seat = self.current
            lead = self._action_status(ctx, seat)
            if error_msg:
                lead = f"⚠️ **{error_msg}**\n{lead}"

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
            move = await ctx.request_input(view, actor=seat, sources=sources)

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
                error_msg = f"Invalid or illegal move: '{move_text}'"

    def render(
        self,
        ctx: GameContext,
        *,
        title: str | None = None,
        status: str | None = None,
        status_emoji: str | None = None,
        lead: str | None = None,
    ) -> LayoutView:
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

        # Media gallery containing the board
        gallery = MediaGallery()
        gallery.add_item(MediaGalleryItem(media_url=f"attachment://{filename}", description="Chess Board"))
        container.set_gallery(gallery)

        # Show list of legal moves (SAN)
        legal_moves_str = ", ".join(self.board.san(m) for m in self.board.legal_moves)
        container.add_text(TextDisplay(f"**Legal moves:** {legal_moves_str}"))

        view.add_container(container)
        return view

    def render_final(self, ctx: GameContext) -> LayoutView:
        outcome = self._outcome()
        status = outcome.description if outcome else "Game over."
        return self.render(ctx, title="Final", status=status, status_emoji="error")



    async def bot_move(self, difficulty: str, seat: int) -> Move:
        await asyncio.sleep(0.5)
        legal = list(self.board.legal_moves)
        if not legal:
            return Move(actor_seat=seat, source="resign", args={})

        if difficulty in ("medium", "hard"):
            captures = [m for m in legal if self.board.is_capture(m)]
            if captures:
                chosen = self.rng.choice(captures)
            else:
                chosen = self.rng.choice(legal)
        else:
            chosen = self.rng.choice(legal)

        return Move(actor_seat=seat, source=chosen.uci(), args={})
