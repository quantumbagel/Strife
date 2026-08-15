from __future__ import annotations

from strife.engine.players import GameOutcome, Player


def forfeit_outcome(
    players: list[Player],
    forfeiter_seat: int,
    *,
    alive_seats: set[int] | None = None,
    min_players: int = 1,
) -> GameOutcome | None:
    """Build a forfeit ``GameOutcome`` when the game should end after a forfeit move.

    Returns ``None`` when play should continue (e.g. enough humans remain).
    """
    alive = alive_seats if alive_seats is not None else {p.seat for p in players}
    alive.discard(forfeiter_seat)

    if alive:
        alive_humans = [p for p in players if p.seat in alive and not p.is_bot]
        if alive_humans and len(alive) >= min_players:
            return None

    results: dict[int, str] = {}
    player_descriptions: dict[int, str] = {}
    for player in players:
        if player.seat == forfeiter_seat:
            results[player.seat] = "loss"
            player_descriptions[player.seat] = "Forfeited"
        elif player.seat in alive:
            results[player.seat] = "win"
            player_descriptions[player.seat] = "Opponent forfeited"
        else:
            results[player.seat] = "loss"
            player_descriptions[player.seat] = "Removed from play"

    forfeiter_mention = str(players[forfeiter_seat])
    return GameOutcome(
        results=results,
        summary={"reason": "forfeit", "forfeiter": forfeiter_seat},
        description=f"{forfeiter_mention} forfeited",
        player_descriptions=player_descriptions,
    )
