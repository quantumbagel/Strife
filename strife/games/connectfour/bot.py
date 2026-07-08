from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from strife.games.connectfour.game import ConnectFour


def choose_move(game: ConnectFour, difficulty: str, seat: int) -> int:
    valid_moves = game.get_valid_moves()
    if not valid_moves:
        # Fallback (should not happen if game loop checks for draw)
        return 0

    if difficulty == "easy":
        return game.rng.choice(valid_moves)

    if difficulty == "medium":
        # 1. Can we win in one move?
        for col in valid_moves:
            row = game.get_next_open_row(col)
            if row is not None:
                idx = row * 7 + col
                game.board[idx] = seat
                is_win = game._check_win_at(idx, seat) is not None
                game.board[idx] = None
                if is_win:
                    return col

        # 2. Can the opponent win in one move? Block them.
        opponent_seat = 1 - seat
        for col in valid_moves:
            row = game.get_next_open_row(col)
            if row is not None:
                idx = row * 7 + col
                game.board[idx] = opponent_seat
                is_win = game._check_win_at(idx, opponent_seat) is not None
                game.board[idx] = None
                if is_win:
                    return col

        # 3. Prefer center column if open
        if 3 in valid_moves:
            return 3

        return game.rng.choice(valid_moves)

    # Hard: Minimax with Alpha-Beta pruning
    return _find_best_move_minimax(game, seat)


def _find_best_move_minimax(game: ConnectFour, seat: int) -> int:
    valid_moves = game.get_valid_moves()
    best_score = -9999999
    best_col = valid_moves[0] if valid_moves else 3
    depth = 4  # Depth 4 is fast and performs well

    # Move ordering: evaluate center columns first to optimize alpha-beta pruning
    ordered_moves = sorted(valid_moves, key=lambda c: abs(c - 3))

    for col in ordered_moves:
        row = game.get_next_open_row(col)
        if row is None:
            continue
        idx = row * 7 + col
        game.board[idx] = seat
        score = _alphabeta(game, depth - 1, -9999999, 9999999, False, 1 - seat, seat)
        game.board[idx] = None

        if score > best_score:
            best_score = score
            best_col = col

    return best_col


def _alphabeta(
    game: ConnectFour,
    depth: int,
    alpha: float,
    beta: float,
    maximizing: bool,
    current_seat: int,
    bot_seat: int,
) -> float:
    # Check terminal states
    if game._check_win_for_player(bot_seat):
        return 100000.0 + depth
    if game._check_win_for_player(1 - bot_seat):
        return -100000.0 - depth
    if not game.get_valid_moves():
        return 0.0
    if depth == 0:
        return _evaluate_board(game, bot_seat)

    valid_moves = game.get_valid_moves()
    ordered_moves = sorted(valid_moves, key=lambda c: abs(c - 3))

    if maximizing:
        max_eval = -9999999.0
        for col in ordered_moves:
            row = game.get_next_open_row(col)
            if row is None:
                continue
            idx = row * 7 + col
            game.board[idx] = bot_seat
            eval_val = _alphabeta(
                game, depth - 1, alpha, beta, False, 1 - bot_seat, bot_seat
            )
            game.board[idx] = None
            max_eval = max(max_eval, eval_val)
            alpha = max(alpha, eval_val)
            if beta <= alpha:
                break
        return max_eval
    else:
        min_eval = 9999999.0
        for col in ordered_moves:
            row = game.get_next_open_row(col)
            if row is None:
                continue
            idx = row * 7 + col
            game.board[idx] = 1 - bot_seat
            eval_val = _alphabeta(
                game, depth - 1, alpha, beta, True, bot_seat, bot_seat
            )
            game.board[idx] = None
            min_eval = min(min_eval, eval_val)
            beta = min(beta, eval_val)
            if beta <= alpha:
                break
        return min_eval


def _evaluate_board(game: ConnectFour, bot_seat: int) -> float:
    score = 0.0
    opponent_seat = 1 - bot_seat

    # Center column preference
    center_array = [game.board[r * 7 + 3] for r in range(6)]
    center_count = center_array.count(bot_seat)
    score += center_count * 3.0

    # Horizontal windows of 4
    for r in range(6):
        row_array = [game.board[r * 7 + c] for c in range(7)]
        for c in range(4):
            window = row_array[c : c + 4]
            score += _evaluate_window(window, bot_seat, opponent_seat)

    # Vertical windows of 4
    for c in range(7):
        col_array = [game.board[r * 7 + c] for r in range(6)]
        for r in range(3):
            window = col_array[r : r + 4]
            score += _evaluate_window(window, bot_seat, opponent_seat)

    # Positive diagonal
    for r in range(3):
        for c in range(4):
            window = [game.board[(r + i) * 7 + (c + i)] for i in range(4)]
            score += _evaluate_window(window, bot_seat, opponent_seat)

    # Negative diagonal
    for r in range(3, 6):
        for c in range(4):
            window = [game.board[(r - i) * 7 + (c + i)] for i in range(4)]
            score += _evaluate_window(window, bot_seat, opponent_seat)

    return score


def _evaluate_window(window: list[int | None], bot_seat: int, opponent_seat: int) -> float:
    score = 0.0
    bot_count = window.count(bot_seat)
    opp_count = window.count(opponent_seat)
    empty_count = window.count(None)

    if bot_count == 4:
        score += 1000.0
    elif bot_count == 3 and empty_count == 1:
        score += 50.0
    elif bot_count == 2 and empty_count == 2:
        score += 10.0

    if opp_count == 3 and empty_count == 1:
        score -= 80.0  # Heavily penalize letting opponent get 3-in-a-row
    elif opp_count == 2 and empty_count == 2:
        score -= 10.0

    return score
