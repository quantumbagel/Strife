from __future__ import annotations

import asyncio
from typing import Any

from strife.engine.context import GameContext
from strife.engine.game import Game
from strife.engine.players import GameOutcome, Move
from strife.games.tictactoe.bot import choose_move
from strife.presentation.components import (
    ActionRow,
    Button,
    ButtonStyle,
    Container,
    LayoutView,
    TextDisplay,
    TextSize,
)


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

    async def play(self, ctx: GameContext) -> GameOutcome:
        while True:
            seat = self.current
            view = self._board_view(ctx, prompt=f"{self.marks[seat]}'s turn")
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
                await ctx.update(self._board_view(ctx, prompt="Winner!", highlight=line))
                return GameOutcome(results={seat: "win", 1 - seat: "loss"}, summary={"winner": seat, "line": line})
            if all(v is not None for v in self.board):
                await ctx.update(self._board_view(ctx, prompt="Draw"))
                return GameOutcome(results={0: "draw", 1: "draw"}, summary={"winner": None})
            self.current = 1 - seat

    def _board_view(self, ctx: GameContext, *, prompt: str, highlight: list[int] | None = None) -> LayoutView:
        p0, p1 = self.players[0], self.players[1]
        header = LayoutView()
        container = Container()
        container.add_text(TextDisplay(markdown_content="### Tic-Tac-Toe", size_style=TextSize.HEADER))
        container.add_text(
            TextDisplay(
                markdown_content=f"**{p0.display_name}** vs **{p1.display_name}**",
                size_style=TextSize.SUBHEADER,
            )
        )
        container.add_text(TextDisplay(markdown_content=prompt, size_style=TextSize.BODY))
        header.add_container(container)
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
                    style = ButtonStyle.SUCCESS if highlight and idx in highlight else ButtonStyle.PRIMARY
                    action.add_button(
                        Button(
                            source=f"tile_{col}{row}",
                            label="\u200b",
                            emoji=self.marks[occupant],
                            style=style,
                            disabled=True,
                        )
                    )
            header.add_action_row(action)
        return header

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
