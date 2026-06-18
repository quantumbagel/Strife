from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from strife.games.tictactoe.game import TicTacToe


def choose_move(game: TicTacToe, difficulty: str, seat: int) -> tuple[int, int]:
    empties = [(c, r) for r in range(3) for c in range(3) if game.board[game._idx(c, r)] is None]
    if difficulty == "easy":
        return game.rng.choice(empties)
    if difficulty == "medium":
        move = _find_win(game, seat) or _find_win(game, 1 - seat)
        if move:
            return move
        if game.board[4] is None:
            return (1, 1)
        corners = [(c, r) for c, r in empties if c in {0, 2} and r in {0, 2}]
        if corners:
            return game.rng.choice(corners)
        return game.rng.choice(empties)
    return _minimax_move(game, seat)


def _find_win(game: TicTacToe, seat: int) -> tuple[int, int] | None:
    for col, row in [(c, r) for r in range(3) for c in range(3) if game.board[game._idx(c, r)] is None]:
        idx = game._idx(col, row)
        game.board[idx] = seat
        won = game._winning_line(seat) is not None
        game.board[idx] = None
        if won:
            return (col, row)
    return None


def _minimax_move(game: TicTacToe, seat: int) -> tuple[int, int]:
    best_score = -2
    best_moves: list[tuple[int, int]] = []
    for col, row in [(c, r) for r in range(3) for c in range(3) if game.board[game._idx(c, r)] is None]:
        idx = game._idx(col, row)
        game.board[idx] = seat
        score = _minimax(game, 1 - seat, seat, False)
        game.board[idx] = None
        if score > best_score:
            best_score = score
            best_moves = [(col, row)]
        elif score == best_score:
            best_moves.append((col, row))
    return game.rng.choice(best_moves)


def _minimax(game: TicTacToe, seat: int, bot: int, maximizing: bool) -> int:
    if game._winning_line(bot) is not None:
        return 1
    if game._winning_line(1 - bot) is not None:
        return -1
    if all(v is not None for v in game.board):
        return 0
    scores = []
    for col, row in [(c, r) for r in range(3) for c in range(3) if game.board[game._idx(c, r)] is None]:
        idx = game._idx(col, row)
        game.board[idx] = seat
        scores.append(_minimax(game, 1 - seat, bot, not maximizing))
        game.board[idx] = None
    return max(scores) if maximizing else min(scores)
