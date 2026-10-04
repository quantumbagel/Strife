from __future__ import annotations

from strife.engine.players import GameOutcome, Player, Result


def forfeit_outcome(
    players: list[Player],
    forfeiter_seat: int,
    *,
    alive_seats: set[int] | None = None,
    min_players: int = 1,
    reason: str = "forfeit",
    must_end: bool = False,
) -> GameOutcome | None:
    """Build a forfeit ``GameOutcome`` when the game should end after a forfeit move.

    Returns ``None`` when play should continue (e.g. enough humans remain),
    unless ``must_end`` is true.
    """
    alive = set(alive_seats) if alive_seats is not None else {p.seat for p in players}
    alive.discard(forfeiter_seat)

    if not must_end and alive:
        alive_humans = [p for p in players if p.seat in alive and not p.is_bot]
        if alive_humans and len(alive) >= min_players:
            return None

    timed_out = reason == "timeout"
    forfeiter_label = "Timed out" if timed_out else "Forfeited"
    opponent_label = "Opponent timed out" if timed_out else "Opponent forfeited"
    action_str = "timed out" if timed_out else "forfeited"

    results: dict[int, Result] = {}
    player_descriptions: dict[int, str] = {}
    for player in players:
        if player.seat == forfeiter_seat:
            results[player.seat] = Result.LOSS
            player_descriptions[player.seat] = forfeiter_label
        elif player.seat in alive:
            results[player.seat] = Result.WIN
            player_descriptions[player.seat] = opponent_label
        else:
            results[player.seat] = Result.LOSS
            player_descriptions[player.seat] = "Removed from play"

    forfeiter_mention = str(players[forfeiter_seat])
    summary: dict = {"reason": reason, "forfeiter": forfeiter_seat}
    remaining = [p.seat for p in players if p.seat in alive]
    if len(remaining) == 1:
        summary["winner"] = remaining[0]
    return GameOutcome(
        results=results,
        summary=summary,
        description=f"{forfeiter_mention} {action_str}",
        player_descriptions=player_descriptions,
    )
