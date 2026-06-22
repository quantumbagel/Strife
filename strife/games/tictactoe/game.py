from __future__ import annotations

import asyncio

from strife.engine.context import GameContext, ReplayFrame
from strife.engine.game import Game
from strife.engine.players import GameOutcome, Move, Player
from strife.persistence.repositories import MoveRecord
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

    async def play(self, ctx: GameContext) -> GameOutcome:
        while True:
            seat = self.current
            display_seat = seat
            player = self.players[display_seat]
            mark = ctx.emoji.get(self.marks[display_seat])
            turn_label = player_mention(
                user_id=player.user_id,
                display_name=player.display_name,
                is_bot=player.is_bot,
            )
            view = self._board_view(ctx, prompt=f"{mark} {turn_label}'s turn")
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
                winner_name = self.players[seat].display_name
                return GameOutcome(
                    results={seat: "win", 1 - seat: "loss"},
                    summary={"winner": seat, "line": line},
                    description=f"{winner_name} won",
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
            player = self.players[first_actor]
            mark = ctx.emoji.get(self.marks[first_actor])
            turn_label = player_mention(
                user_id=player.user_id,
                display_name=player.display_name,
                is_bot=player.is_bot,
            )
            prompt = f"{mark} {turn_label}'s turn"
        else:
            prompt = "Game Start"
            
        initial_view = self._board_view(ctx, prompt=prompt)
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
                            "display_name": p.display_name,
                            "type": "bot_takeover",
                            "reason": move.arguments.get("replace_reason", "timeout"),
                        }
            elif move.source == "forfeit" or move.source == "game_end":
                if move.source == "forfeit":
                    for p in self.players:
                        if p.seat == actor:
                            takeover_info = {
                                "display_name": p.display_name,
                                "type": "removal",
                                "reason": move.arguments.get("reason", "forfeit"),
                            }
                # If game was forfeited/ended here, we just show final frame
                is_last = True
            
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
                
                if winner_seat is not None:
                    prompt = "Winner!"
                elif all(v is not None for v in self.board):
                    prompt = "Draw"
                else:
                    prompt = "Game Over"
                
                final_view = self._board_view(ctx, prompt=prompt, highlight=winning_line)
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
                player = self.players[next_actor]
                mark = ctx.emoji.get(self.marks[next_actor])
                turn_label = player_mention(
                    user_id=player.user_id,
                    display_name=player.display_name,
                    is_bot=player.is_bot,
                )
                prompt = f"{mark} {turn_label}'s turn"
                
                view = self._board_view(ctx, prompt=prompt)
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
        if winner_seat is not None:
            prompt = "Winner!"
        elif winner_seat is None and all(v is not None for v in self.board):
            prompt = "Draw"
        else:
            prompt = "Game Over"
        highlight = line if isinstance(line, list) else None
        return self._board_view(ctx, prompt=prompt, highlight=highlight)

    def _board_view(
        self, ctx: GameContext, *, prompt: str, highlight: list[int] | None = None
    ) -> LayoutView:
        view = LayoutView()
        container = Container()
        container.add_text(TextDisplay(markdown_content=prompt, size_style=TextSize.BODY))
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
