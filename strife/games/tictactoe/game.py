from __future__ import annotations

import asyncio

from strife.engine.metadata import (
    BotSpec,
    OptionType,
    PlayerCount,
    PlayerOrder,
    SettingOption,
    game_metadata_from,
)
from strife.engine.context import GameContext
from strife.engine.players import GameOutcome, Move
from strife.engine.turn_based import TurnBasedGame
from strife.persistence.repositories import MoveRecord
from strife.games.tictactoe.bot import choose_move
from strife.presentation.components import (
    ActionRow,
    Button,
    ButtonStyle,
    Container,
    LayoutView,
)
from strife.presentation.game_ui import message_lead


@game_metadata_from(
    key="tictactoe",
    name="Tic-Tac-Toe",
    summary="Classic 3x3. Get three in a row.",
    description="Two players alternate placing X and O on a 3x3 grid; first to align three wins.",
    tags=("classic", "strategy", "2p"),
    author="Strife",
    version="1.0.0",
    author_link=None,
    source_link=None,
    how_to_play_link="https://en.wikipedia.org/wiki/Tic-tac-toe",
    time_estimate="2m",
    difficulty=2,
    player_count=PlayerCount(fixed=2),
    player_order=PlayerOrder.RANDOM,
    bots=(
        BotSpec("easy", "Random legal move"),
        BotSpec("medium", "Win/block heuristic"),
        BotSpec("hard", "Optimal minimax (never loses)"),
    ),
    settings=(
        SettingOption(
            key="first_move",
            title="First Move",
            description="Who plays X (moves first)",
            type=OptionType.CHOICE,
            default="random",
            choices=("random", "creator"),
            emoji="first_move",
            choice_emojis=(("random", "restart"), ("creator", "creator")),
        ),
    ),
)
class TicTacToe(TurnBasedGame):
    def __init__(self, players, settings, rng):
        super().__init__(players, settings, rng)
        self.marks: dict[int, str] = {}
        self.reset()

    def reset(self) -> None:
        self.board: list[int | None] = [None] * 9
        first_mover = self._starting_seat()
        self.current = first_mover
        other = 1 - first_mover
        self.marks = {first_mover: "tictactoe_x", other: "tictactoe_o"}

    def _idx(self, col: int, row: int) -> int:
        return row * 3 + col

    def _starting_seat(self) -> int:
        mode = self.setting("first_move", "random")
        if mode == "creator":
            creator_id = self.settings.get("creator_id")
            if creator_id is not None:
                for player in self.players:
                    if player.user_id == creator_id:
                        return player.seat
            return 0
        return self.rng.randint(0, 1)

    def _turn_number(self) -> int:
        return sum(1 for v in self.board if v is not None) + 1

    def replay_action_status(self, ctx: GameContext, next_actor: int) -> str:
        return self._action_status(ctx, next_actor)

    def _action_status(self, ctx: GameContext, seat: int) -> str:
        player = self.players[seat]
        mark = ctx.emoji.get(self.marks[seat])
        return f"{mark} {player.mention} to act"

    def _final_status(self, winner_seat: int | None) -> tuple[str, str]:
        if winner_seat is not None:
            winner_label = self.players[winner_seat].mention
            return f"{winner_label} won!", "success"
        if all(v is not None for v in self.board):
            return "Draw — the board is full.", "hmm"
        return "Game over.", "error"

    def apply_move(self, move: MoveRecord) -> None:
        if move.source.startswith("tile_") and move.actor_seat is not None:
            col, row = int(move.source[5]), int(move.source[6])
            self.board[self._idx(col, row)] = move.actor_seat

    async def play(self, ctx: GameContext) -> GameOutcome:
        while True:
            seat = self.current
            view = self.render(ctx)
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
        return self._board_view(
            ctx,
            lead=lead,
            prefix_emoji=status_emoji,
        )

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
        return self._board_view(
            ctx,
            lead=lead,
            prefix_emoji=status_emoji,
            controls=False,
        )

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
                            disabled=not controls,
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
