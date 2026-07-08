from __future__ import annotations

import asyncio

from strife.engine.context import GameContext, ReplayFrame
from strife.engine.game import Game
from strife.engine.players import GameOutcome, Move
from strife.persistence.repositories import MoveRecord
from strife.games.connectfour.bot import choose_move
from strife.presentation.components import (
    ActionRow,
    Button,
    ButtonStyle,
    Container,
    LayoutView,
    TextDisplay,
)
from strife.presentation.game_frame import add_game_header
from strife.presentation.roster import player_mention


class ConnectFour(Game):
    def __init__(self, players, settings, rng):
        super().__init__(players, settings, rng)
        self.board: list[int | None] = [None] * 42
        self.current = self._starting_seat()

    def _idx(self, col: int, row: int) -> int:
        return row * 7 + col

    def _starting_seat(self) -> int:
        return self.rng.randint(0, 1)

    def _turn_number(self) -> int:
        return sum(1 for v in self.board if v is not None) + 1

    def _turn_status(self, ctx: GameContext, seat: int) -> str:
        player = self.players[seat]
        color = "connect_four_red" if seat == 0 else "connect_four_yellow"
        mark = ctx.emoji.get(color)
        turn_label = player_mention(
            user_id=player.user_id,
            display_name=player.display_name,
            is_bot=player.is_bot,
        )
        return f"{mark} {turn_label}'s turn"

    def _final_status(self, winner_seat: int | None) -> tuple[str, str]:
        if winner_seat is not None:
            winner_label = player_mention(
                user_id=self.players[winner_seat].user_id,
                display_name=self.players[winner_seat].display_name,
                is_bot=self.players[winner_seat].is_bot,
            )
            return f"{winner_label} won!", "success"
        if all(v is not None for v in self.board):
            return "Draw — the board is full.", "hmm"
        return "Game over.", "error"

    def get_valid_moves(self) -> list[int]:
        # Columns 0-6 where the top row is empty
        return [c for c in range(7) if self.board[c] is None]

    def get_next_open_row(self, col: int) -> int | None:
        # Search from bottom (5) to top (0)
        for r in range(5, -1, -1):
            if self.board[r * 7 + col] is None:
                return r
        return None

    def _winning_line(self, seat: int) -> list[int] | None:
        # Horizontal
        for r in range(6):
            for c in range(4):
                indices = [r * 7 + c + i for i in range(4)]
                if all(self.board[idx] == seat for idx in indices):
                    return indices

        # Vertical
        for c in range(7):
            for r in range(3):
                indices = [(r + i) * 7 + c for i in range(4)]
                if all(self.board[idx] == seat for idx in indices):
                    return indices

        # Diagonal Down-Right
        for r in range(3):
            for c in range(4):
                indices = [(r + i) * 7 + (c + i) for i in range(4)]
                if all(self.board[idx] == seat for idx in indices):
                    return indices

        # Diagonal Up-Right
        for r in range(3, 6):
            for c in range(4):
                indices = [(r - i) * 7 + (c + i) for i in range(4)]
                if all(self.board[idx] == seat for idx in indices):
                    return indices

        return None

    def _check_win_for_player(self, seat: int) -> bool:
        return self._winning_line(seat) is not None

    def _check_win_at(self, idx: int, seat: int) -> list[int] | None:
        # Simple winning check after a move
        return self._winning_line(seat)

    async def play(self, ctx: GameContext) -> GameOutcome:
        while True:
            seat = self.current
            view = self._board_view(
                ctx,
                title=f"Turn {self._turn_number()}",
                status=self._turn_status(ctx, seat),
                status_emoji="loading",
            )
            valid_cols = self.get_valid_moves()
            sources = {f"col_{c}" for c in valid_cols}

            move = await ctx.request_input(view, actor=seat, sources=sources)
            col = int(move.source.split("_")[1])
            row = self.get_next_open_row(col)
            if row is None:
                continue

            self.board[row * 7 + col] = seat
            await ctx.record_action(move.source, move.args)

            line = self._winning_line(seat)
            if line is not None:
                winner_mention = str(self.players[seat])
                return GameOutcome(
                    results={seat: "win", 1 - seat: "loss"},
                    summary={"winner": seat, "line": line},
                    description=f"{winner_mention} won!",
                    player_descriptions={seat: "Won", 1 - seat: "Lost"},
                )

            if all(v is not None for v in self.board):
                return GameOutcome(
                    results={0: "draw", 1: "draw"},
                    summary={"winner": None},
                    description="Draw",
                    player_descriptions={0: "Draw", 1: "Draw"},
                )

            self.current = 1 - seat

    async def parse_replay(self, moves: list[MoveRecord], ctx: GameContext) -> list[ReplayFrame]:
        self.board = [None] * 42
        frames: list[ReplayFrame] = []
        from strife.presentation.compiler import clone_and_disable

        if moves:
            first_actor = moves[0].actor_seat if moves[0].actor_seat is not None else 0
            status = self._turn_status(ctx, first_actor)
        else:
            status = "Game start"

        initial_view = self._board_view(
            ctx, title="Turn 1", status=status, status_emoji="loading"
        )
        frames.append(
            ReplayFrame(
                index=len(frames),
                turn_label="Turn 1",
                actor_seat=None,
                view=clone_and_disable(initial_view),
                timestamp=ctx.started_at,
            )
        )

        for i, move in enumerate(moves):
            actor = move.actor_seat
            takeover_info = None
            if move.arguments.get("replaced_by_bot"):
                for p in self.players:
                    if p.seat == actor:
                        p.is_bot = True
                        p.bot_difficulty = "hard"
                        takeover_info = {
                            "user_id": p.user_id,
                            "display_name": p.display_name,
                            "is_bot": p.is_bot,
                            "type": "bot_takeover",
                            "reason": move.arguments.get("replace_reason", "timeout"),
                        }

            if move.source.startswith("col_") and actor is not None:
                col = int(move.source.split("_")[1])
                row = self.get_next_open_row(col)
                if row is not None:
                    self.board[row * 7 + col] = actor

            is_last = (i == len(moves) - 1)
            if is_last:
                winner_seat = None
                winning_line = None
                for seat in (0, 1):
                    line = self._winning_line(seat)
                    if line is not None:
                        winner_seat = seat
                        winning_line = line
                        break

                status, status_emoji = self._final_status(winner_seat)
                final_view = self._board_view(
                    ctx,
                    title="Final",
                    status=status,
                    status_emoji=status_emoji,
                    highlight=winning_line,
                )
                frames.append(
                    ReplayFrame(
                        index=len(frames),
                        turn_label="Final",
                        actor_seat=None,
                        view=clone_and_disable(final_view),
                        takeover_info=takeover_info,
                        timestamp=move.created_at,
                    )
                )
                break
            else:
                next_move = moves[i + 1]
                next_actor = next_move.actor_seat if next_move.actor_seat is not None else 0
                view = self._board_view(
                    ctx,
                    title=f"Turn {self._turn_number()}",
                    status=self._turn_status(ctx, next_actor),
                    status_emoji="loading",
                )
                frames.append(
                    ReplayFrame(
                        index=len(frames),
                        turn_label=f"Turn {i + 2}",
                        actor_seat=actor,
                        view=clone_and_disable(view),
                        takeover_info=takeover_info,
                        timestamp=move.created_at,
                    )
                )
        return frames

    async def final_view(self, ctx: GameContext, outcome: GameOutcome) -> LayoutView | None:
        summary = outcome.summary or {}
        winner_seat = summary.get("winner")
        line = summary.get("line")
        status, status_emoji = self._final_status(
            winner_seat if isinstance(winner_seat, int) else None
        )
        highlight = line if isinstance(line, list) else None
        return self._board_view(
            ctx,
            title="Final",
            status=status,
            status_emoji=status_emoji,
            highlight=highlight,
        )

    def _board_view(
        self,
        ctx: GameContext,
        *,
        title: str,
        status: str | None = None,
        status_emoji: str | None = None,
        highlight: list[int] | None = None,
    ) -> LayoutView:
        view = LayoutView()
        container = Container()
        add_game_header(
            container,
            ctx.emoji,
            game_key=self.metadata.key,
            game_name=self.metadata.name,
            title=title,
            status=status,
            status_emoji=status_emoji,
            is_replay=ctx.is_replay,
        )

        # Build board text representation
        board_text = ""
        for r in range(6):
            row_emojis = []
            for c in range(7):
                idx = r * 7 + c
                occupant = self.board[idx]
                if occupant == 0:
                    row_emojis.append(ctx.emoji.get("connect_four_red"))
                elif occupant == 1:
                    row_emojis.append(ctx.emoji.get("connect_four_yellow"))
                else:
                    row_emojis.append(ctx.emoji.get("connect_four_empty"))
            board_text += " ".join(row_emojis) + "\n"

        container.add_text(TextDisplay(board_text))

        # Add column buttons (Row 1: 1-4, Row 2: 5-7)
        valid_cols = self.get_valid_moves()

        row1 = ActionRow()
        for c in range(4):
            is_disabled = (c not in valid_cols) or ctx.is_replay
            row1.add_button(
                Button(
                    source=f"col_{c}",
                    label=f"{c + 1}",
                    style=ButtonStyle.SECONDARY,
                    disabled=is_disabled,
                )
            )
        container.add_action_row(row1)

        row2 = ActionRow()
        for c in range(4, 7):
            is_disabled = (c not in valid_cols) or ctx.is_replay
            row2.add_button(
                Button(
                    source=f"col_{c}",
                    label=f"{c + 1}",
                    style=ButtonStyle.SECONDARY,
                    disabled=is_disabled,
                )
            )
        container.add_action_row(row2)

        view.add_container(container)
        return view

    async def bot_move(self, difficulty: str, seat: int) -> Move:
        col = await asyncio.to_thread(choose_move, self, difficulty, seat)
        return Move(actor_seat=seat, source=f"col_{col}", args={})
