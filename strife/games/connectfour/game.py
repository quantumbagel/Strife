from __future__ import annotations

from strife.engine.context import GameContext
from strife.engine.players import GameOutcome, Move
from strife.engine.workers import run_cpu
from strife.engine.turn_based import TurnBasedGame
from strife.persistence.repositories import MoveRecord
from strife.games.connectfour.bot import choose_move
from strife.presentation.components import (
    ActionRow,
    Button,
    ButtonStyle,
    Container,
    LayoutView,
)
from strife.presentation.game_ui import message_lead
from strife.presentation.style import add_body


class ConnectFour(TurnBasedGame):
    def __init__(self, players, settings, rng):
        super().__init__(players, settings, rng)
        self.board: list[int | None] = [None] * 42
        self.current = self._starting_seat()

    def reset(self) -> None:
        self.board = [None] * 42
        self.current = self._starting_seat()

    def _idx(self, col: int, row: int) -> int:
        return row * 7 + col

    def _starting_seat(self) -> int:
        return self.rng.randint(0, 1)

    def _turn_number(self) -> int:
        return sum(1 for v in self.board if v is not None) + 1

    def replay_action_status(self, ctx: GameContext, next_actor: int) -> str:
        return self._action_status(ctx, next_actor)

    def _action_status(self, ctx: GameContext, seat: int) -> str:
        player = self.players[seat]
        color = "connect_four_red" if seat == 0 else "connect_four_yellow"
        mark = ctx.emoji.get(color)
        return f"{mark} {player.mention} to act"

    def _final_status(self, winner_seat: int | None) -> tuple[str, str]:
        if winner_seat is not None:
            winner_label = self.players[winner_seat].mention
            return f"{winner_label} won!", "success"
        if all(v is not None for v in self.board):
            return "Draw — the board is full.", "hmm"
        return "Game over.", "error"

    def get_valid_moves(self) -> list[int]:
        return [c for c in range(7) if self.board[c] is None]

    def get_next_open_row(self, col: int) -> int | None:
        for r in range(5, -1, -1):
            if self.board[r * 7 + col] is None:
                return r
        return None

    def _winning_line(self, seat: int) -> list[int] | None:
        for r in range(6):
            for c in range(4):
                indices = [r * 7 + c + i for i in range(4)]
                if all(self.board[idx] == seat for idx in indices):
                    return indices

        for c in range(7):
            for r in range(3):
                indices = [(r + i) * 7 + c for i in range(4)]
                if all(self.board[idx] == seat for idx in indices):
                    return indices

        for r in range(3):
            for c in range(4):
                indices = [(r + i) * 7 + (c + i) for i in range(4)]
                if all(self.board[idx] == seat for idx in indices):
                    return indices

        for r in range(3, 6):
            for c in range(4):
                indices = [(r - i) * 7 + (c + i) for i in range(4)]
                if all(self.board[idx] == seat for idx in indices):
                    return indices

        return None

    def _check_win_at(self, idx: int, seat: int) -> list[int] | None:
        self.board[idx] = seat
        line = self._winning_line(seat)
        self.board[idx] = None
        return line

    def _check_win_for_player(self, seat: int) -> list[int] | None:
        return self._winning_line(seat)

    def apply_move(self, move: MoveRecord) -> None:
        if move.source.startswith("col_") and move.actor_seat is not None:
            col = int(move.source.split("_")[1])
            row = self.get_next_open_row(col)
            if row is not None:
                self.board[row * 7 + col] = move.actor_seat

    async def play(self, ctx: GameContext) -> GameOutcome:
        while True:
            seat = self.current
            view = self.render(ctx)
            valid_cols = self.get_valid_moves()
            sources = {f"col_{c}" for c in valid_cols}

            move = await ctx.request_input(view, actor=seat, sources=sources)
            col = int(move.source.split("_")[1])
            row = self.get_next_open_row(col)
            if row is None:
                continue

            self.board[row * 7 + col] = seat

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

    def render_final(self, ctx: GameContext) -> LayoutView:
        winner_seat = None
        winning_line = None
        for seat in (0, 1):
            line = self._winning_line(seat)
            if line is not None:
                winner_seat = seat
                winning_line = line
                break
        status, status_emoji = self._final_status(winner_seat)
        return self._board_view(
            ctx,
            lead=status,
            prefix_emoji=status_emoji,
            highlight=winning_line,
            controls=False,
        )

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
            lead=status,
            prefix_emoji=status_emoji,
            highlight=highlight,
            controls=False,
        )

    def render(
        self,
        ctx: GameContext,
        *,
        lead: str | None = None,
        status: str | None = None,
        status_emoji: str | None = None,
        title: str | None = None,
    ) -> LayoutView:
        if lead is None:
            if status is not None:
                lead = status
            elif title is not None:
                lead = title
            else:
                lead = self._action_status(ctx, self.current)
        return self._board_view(ctx, lead=lead, prefix_emoji=status_emoji)

    def render_replay(
        self,
        ctx: GameContext,
        *,
        lead: str | None = None,
        status: str | None = None,
        status_emoji: str | None = None,
        title: str | None = None,
    ) -> LayoutView:
        if lead is None:
            if status is not None:
                lead = status
            elif title is not None:
                lead = title
        return self._board_view(ctx, lead=lead, prefix_emoji=status_emoji, controls=False)

    def _board_view(
        self,
        ctx: GameContext,
        *,
        lead: str | None = None,
        prefix_emoji: str | None = None,
        highlight: list[int] | None = None,
        controls: bool = True,
    ) -> LayoutView:
        view = LayoutView()
        container = Container()
        message_lead(container, lead, emoji=ctx.emoji, prefix_emoji=prefix_emoji)

        board_text = ""
        for r in range(6):
            row_emojis = []
            for c in range(7):
                idx = r * 7 + c
                occupant = self.board[idx]
                if highlight and idx in highlight:
                    if occupant == 0:
                        row_emojis.append(ctx.emoji.get("connect_four_red"))
                    elif occupant == 1:
                        row_emojis.append(ctx.emoji.get("connect_four_yellow"))
                    else:
                        row_emojis.append(ctx.emoji.get("connect_four_empty"))
                elif occupant == 0:
                    row_emojis.append(ctx.emoji.get("connect_four_red"))
                elif occupant == 1:
                    row_emojis.append(ctx.emoji.get("connect_four_yellow"))
                else:
                    row_emojis.append(ctx.emoji.get("connect_four_empty"))
            board_text += " ".join(row_emojis) + "\n"

        add_body(container, board_text)

        if controls:
            valid_cols = self.get_valid_moves()

            row1 = ActionRow()
            for c in range(4):
                row1.add_button(
                    Button(
                        source=f"col_{c}",
                        label=f"{c + 1}",
                        style=ButtonStyle.SECONDARY,
                        disabled=c not in valid_cols,
                    )
                )
            container.add_action_row(row1)

            row2 = ActionRow()
            for c in range(4, 7):
                row2.add_button(
                    Button(
                        source=f"col_{c}",
                        label=f"{c + 1}",
                        style=ButtonStyle.SECONDARY,
                        disabled=c not in valid_cols,
                    )
                )
            container.add_action_row(row2)

        view.add_container(container)
        return view

    async def bot_move(self, difficulty: str, seat: int) -> Move:
        col = await run_cpu(choose_move, self, difficulty, seat)
        return Move(actor_seat=seat, source=f"col_{col}", args={})
