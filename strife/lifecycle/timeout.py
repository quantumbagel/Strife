from __future__ import annotations

from enum import StrEnum


class TimeoutConsequence(StrEnum):
    BOT_TAKEOVER = "bot_takeover"
    REMOVED = "removed"
    GAME_ENDS = "game_ends"
    SKIP = "skip"
    AUTO_PASS = "auto_pass"
    STRIKE = "strike"
    # until="any" window closed with nobody acting: no seat is blamed.
    PHASE_ENDS = "phase_ends"


def will_removal_end_game(session: object, seat: int) -> bool:
    """Check if removing ``seat`` from the game will cause it to end.

    Lobby ``min_players`` is a start constraint, not a mid-game floor.
    Play continues while any active seats and at least one human remain;
    the game itself decides when a faction or seat count has lost.
    """
    game = session.game  # type: ignore[attr-defined]
    after = game.active_seats() - {seat}
    remaining_humans = [
        player
        for player in session.players  # type: ignore[attr-defined]
        if player.seat in after and not player.is_bot
    ]
    return not after or not remaining_humans


def determine_consequence(
    session: object, seat: int, reason: str = "timeout"
) -> TimeoutConsequence:
    """Determine what consequence applies when ``seat`` fails to make a move (timeout or forfeit)."""
    # Forfeits skip interactive options and bot takeover, going straight to removal or end game.
    if reason == "forfeit":
        meta = session.game.metadata  # type: ignore[attr-defined]
        if meta.supports_player_removal:
            if will_removal_end_game(session, seat):
                return TimeoutConsequence.GAME_ENDS
            return TimeoutConsequence.REMOVED
        return TimeoutConsequence.GAME_ENDS

    # Default timeout consequence logic
    pending = getattr(session, "pending", {}).get(seat)

    # A shared "first to act" window isn't any one seat's turn, so expiry
    # closes the window instead of punishing whoever is listed first.
    if pending is not None and getattr(pending, "until", "all") == "any":
        return TimeoutConsequence.PHASE_ENDS

    consequence_type = "abandon"
    if pending and getattr(pending, "timeout_consequence", None) is not None:
        consequence_type = pending.timeout_consequence
    elif hasattr(session, "turn_timeout_consequence"):
        consequence_type = session.turn_timeout_consequence

    if consequence_type == "skip":
        return TimeoutConsequence.SKIP
    if consequence_type == "auto_pass":
        return TimeoutConsequence.AUTO_PASS
    if consequence_type in ("game_ends", "game_end"):
        return TimeoutConsequence.GAME_ENDS

    if consequence_type == "strike":
        players = getattr(session, "players", [])
        if 0 <= seat < len(players):
            player = players[seat]
            max_strikes = getattr(session, "turn_timeout_max_strikes", 3)
            if player.timeout_strikes + 1 < max_strikes:
                return TimeoutConsequence.STRIKE

    meta = session.game.metadata  # type: ignore[attr-defined]

    if meta.supports_bots:
        return TimeoutConsequence.BOT_TAKEOVER

    if meta.supports_player_removal:
        if will_removal_end_game(session, seat):
            return TimeoutConsequence.GAME_ENDS
        return TimeoutConsequence.REMOVED

    return TimeoutConsequence.GAME_ENDS


def timeout_consequence(session: object, seat: int) -> TimeoutConsequence:
    """Predict what happens when ``seat`` times out (mirrors lifecycle abandon logic)."""
    return determine_consequence(session, seat, reason="timeout")


