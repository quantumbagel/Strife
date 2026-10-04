from __future__ import annotations

from strife.engine.game import Game
from strife.engine.players import Move


def is_seat_event(move: Move) -> bool:
    """True for host rows that change occupancy (takeover or mid-match removal)."""
    if not move.is_system:
        return False
    if move.source in ("bot_takeover", "timeout_strike"):
        return True
    return move.source == "forfeit" and bool(move.args.get("removed"))


def apply_seat_event(game: Game, move: Move) -> None:
    """Apply a seat event. The only engine/host call of ``remove_player``."""
    if move.source == "timeout_strike":
        return
    if move.source == "bot_takeover":
        seat = move.args.get("seat")
        if seat is None:
            return
        player = game.players[int(seat)]
        player.is_bot = True
        diff = move.args.get("bot_difficulty")
        if diff is not None:
            player.bot_difficulty = str(diff)
        return
    if move.source == "forfeit" and move.args.get("removed"):
        seat = move.actor_seat
        if seat is not None:
            game.remove_player(int(seat))
