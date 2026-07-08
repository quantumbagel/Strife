from __future__ import annotations

import asyncio

from strife.engine.context import GameContext, ReplayFrame
from strife.engine.game import Game
from strife.engine.players import GameOutcome, Move
from strife.persistence.repositories import MoveRecord
from strife.games.tictactoe.bot import choose_move
from strife.presentation.components import (
    ActionRow,
    Button,
    ButtonStyle,
    Container,
    LayoutView,
)
from strife.presentation.game_frame import add_game_header
from strife.presentation.roster import player_mention


class TicTacToe(Game):
    def __init__(self, players, settings, rng):
        super().__init__(players, settings, rng)
        self.board: list[int | None] = [None] * 9
        self.current = self._starting_seat()
        self.marks = {0: "tictactoe_x", 1: "tictactoe_o"}

    def _idx(self, col: int, row: int) -> int:
        return row * 3 + col

    def _starting_seat(self) -> int:
        mode = self.settings.get("first_move", "random")
        if mode == "creator":
            return 0
        return self.rng.randint(0, 1)

    def _turn_number(self) -> int:
        return sum(1 for v in self.board if v is not None) + 1

    def _turn_status(self, ctx: GameContext, seat: int) -> str:
        player = self.players[seat]
        mark = ctx.emoji.get(self.marks[seat])
        turn_label = player_mention(
            user_id=player.user_id,
            display_name=player.display_name,
            is_bot=player.is_bot,
        )
        return f"{mark} {turn_label}'s turn"

    def _final_status(self, winner_seat: int | None) -> tuple[str, str]:
        """Return (status text, status emoji name) for a finished board."""
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

    async def play(self, ctx: GameContext) -> GameOutcome:
        while True:
            seat = self.current
            view = self._board_view(
                ctx,
                title=f"Turn {self._turn_number()}",
                status=self._turn_status(ctx, seat),
                status_emoji="loading",
            )
            empties = {
                f"tile_{c}{r}"
                for r in range(3)
                for c in range(3)
                if self.board[self._idx(c, r)] is None
            }
            move = await ctx.request_input(view, actor=seat, sources=empties)
            col, row = int(move.source[5]), int(move.source[6])
            self.board[self._idx(col, row)] = seat
            line = self._winning_line(seat)
            if line is not None:
                winner_mention = str(self.players[seat])
                return GameOutcome(
                    results={seat: "win", 1 - seat: "loss"},
                    summary={"winner": seat, "line": line},
                    description=f"{winner_mention} won",
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
        self.board = [None] * 9
        frames: list[ReplayFrame] = []
        from strife.presentation.compiler import clone_and_disable

        # Show initial empty board frame (Turn 1 / Initial state)
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

        # Now apply each move
        for i, move in enumerate(moves):
            actor = move.actor_seat
            # Check for bot takeover / forfeit
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
            elif move.source == "forfeit" or move.source == "game_end":
                if move.source == "forfeit":
                    for p in self.players:
                        if p.seat == actor:
                            takeover_info = {
                                "user_id": p.user_id,
                                "display_name": p.display_name,
                                "is_bot": p.is_bot,
                                "type": "removal",
                                "reason": move.arguments.get("reason", "forfeit"),
                            }

            # Apply tile move if source is a tile
            if move.source.startswith("tile_") and actor is not None:
                col, row = int(move.source[5]), int(move.source[6])
                self.board[row * 3 + col] = actor

            is_last = (i == len(moves) - 1) or move.source in ("forfeit", "game_end")
            if is_last:
                # Calculate final state view
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
                # Next move is at i + 1
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
        for row in range(3):
            action = ActionRow()
            for col in range(3):
                idx = self._idx(col, row)
                occupant = self.board[idx]
                if occupant is None:
                    action.add_button(
                        Button(
                            source=f"tile_{col}{row}",
                            label="\u200b",
                            style=ButtonStyle.SECONDARY,
                        )
                    )
                else:
                    style = (
                        ButtonStyle.SUCCESS
                        if highlight and idx in highlight
                        else ButtonStyle.PRIMARY
                    )
                    action.add_button(
                        Button(
                            source=f"tile_{col}{row}",
                            label="\u200b",
                            emoji=self.marks[occupant],
                            style=style,
                            disabled=True,
                        )
                    )
            container.add_action_row(action)
        view.add_container(container)
        return view

    def _winning_line(self, seat: int) -> list[int] | None:
        lines = [
            [0, 1, 2],
            [3, 4, 5],
            [6, 7, 8],
            [0, 3, 6],
            [1, 4, 7],
            [2, 5, 8],
            [0, 4, 8],
            [2, 4, 6],
        ]
        for line in lines:
            if all(self.board[i] == seat for i in line):
                return line
        return None

    async def bot_move(self, difficulty: str, seat: int) -> Move:
        col, row = await asyncio.to_thread(choose_move, self, difficulty, seat)
        return Move(actor_seat=seat, source=f"tile_{col}{row}", args={})
