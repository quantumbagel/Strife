from __future__ import annotations

from enum import StrEnum

from strife.engine.requests import TimeoutConsequence as InputTimeoutConsequence
from strife.logging import get_logger

log = get_logger("lifecycle.timeout")


class ResolvedTimeoutConsequence(StrEnum):
    BOT_TAKEOVER = "bot_takeover"
    REMOVED = "removed"
    GAME_ENDS = "game_ends"
    SKIP = "skip"
    AUTO_PASS = "auto_pass"
    STRIKE = "strike"
    # until="any" window closed with nobody acting: no seat is blamed.
    PHASE_ENDS = "phase_ends"


def _input_consequence(session: object, seat: int) -> InputTimeoutConsequence:
    pending = getattr(session, "pending", {}).get(seat)
    if (
        pending is not None
        and getattr(pending, "timeout_consequence", None) is not None
    ):
        value = pending.timeout_consequence
    elif hasattr(session, "turn_timeout_consequence"):
        value = session.turn_timeout_consequence
    else:
        return InputTimeoutConsequence.ABANDON
    if isinstance(value, InputTimeoutConsequence):
        return value
    if isinstance(value, str):
        try:
            return InputTimeoutConsequence(value)
        except ValueError:
            log.error("Unknown timeout_consequence '%s'; using abandon", value)
            return InputTimeoutConsequence.ABANDON
    return InputTimeoutConsequence.ABANDON


def will_removal_end_game(session: object, seat: int) -> bool:
    """Check if removing ``seat`` from the game will cause it to end.

    Lobby ``min_players`` is a start constraint, not a mid-game floor.
    Play continues while any active seats and at least one human remain;
    the game itself decides when a faction or seat count has lost.

    Callers must hold the session lock. In-progress removals belong in
    ``session._removed_seats`` before this runs so two concurrent forfeits
    cannot both choose REMOVED and leave only bots.
    """
    game = session.game  # type: ignore[attr-defined]
    removed = getattr(session, "_removed_seats", set())
    after = game.active_seats() - removed - {seat}
    remaining_humans = [
        player
        for player in session.players  # type: ignore[attr-defined]
        if player.seat in after and not player.is_bot
    ]
    return not after or not remaining_humans


def determine_consequence(
    session: object, seat: int, reason: str = "timeout"
) -> ResolvedTimeoutConsequence:
    """Determine what consequence applies when ``seat`` fails to make a move (timeout or forfeit)."""
    # Forfeits skip interactive options and bot takeover, going straight to removal or end game.
    if reason == "forfeit":
        meta = session.game.metadata  # type: ignore[attr-defined]
        if meta.supports_player_removal:
            if will_removal_end_game(session, seat):
                return ResolvedTimeoutConsequence.GAME_ENDS
            return ResolvedTimeoutConsequence.REMOVED
        return ResolvedTimeoutConsequence.GAME_ENDS

    # Default timeout consequence logic
    pending = getattr(session, "pending", {}).get(seat)

    # A shared "first to act" window isn't any one seat's turn, so expiry
    # closes the window instead of punishing whoever is listed first.
    if pending is not None and getattr(pending, "until", "all") == "any":
        return ResolvedTimeoutConsequence.PHASE_ENDS

    consequence_type = _input_consequence(session, seat)

    if consequence_type == InputTimeoutConsequence.SKIP:
        return ResolvedTimeoutConsequence.SKIP
    if consequence_type == InputTimeoutConsequence.AUTO_PASS:
        return ResolvedTimeoutConsequence.AUTO_PASS
    if consequence_type == InputTimeoutConsequence.GAME_ENDS:
        return ResolvedTimeoutConsequence.GAME_ENDS

    if consequence_type == InputTimeoutConsequence.STRIKE:
        strikes = getattr(session, "timeout_strikes", {}).get(seat, 0)
        max_strikes = getattr(session, "turn_timeout_max_strikes", 3)
        if strikes + 1 < max_strikes:
            return ResolvedTimeoutConsequence.STRIKE

    meta = session.game.metadata  # type: ignore[attr-defined]

    if meta.supports_bots:
        return ResolvedTimeoutConsequence.BOT_TAKEOVER

    if meta.supports_player_removal:
        if will_removal_end_game(session, seat):
            return ResolvedTimeoutConsequence.GAME_ENDS
        return ResolvedTimeoutConsequence.REMOVED

    return ResolvedTimeoutConsequence.GAME_ENDS


def timeout_consequence(session: object, seat: int) -> ResolvedTimeoutConsequence:
    """Predict what happens when ``seat`` times out (mirrors lifecycle abandon logic)."""
    return determine_consequence(session, seat, reason="timeout")
