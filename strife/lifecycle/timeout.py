from __future__ import annotations

from enum import StrEnum


class TimeoutConsequence(StrEnum):
    BOT_TAKEOVER = "bot_takeover"
    REMOVED = "removed"
    GAME_ENDS = "game_ends"
    SKIP = "skip"
    AUTO_PASS = "auto_pass"
    STRIKE = "strike"


def will_removal_end_game(session: object, seat: int) -> bool:
    """Check if removing ``seat`` from the game will cause it to end."""
    game = session.game  # type: ignore[attr-defined]
    after = game.active_seats() - {seat}
    remaining_humans = [
        player
        for player in session.players  # type: ignore[attr-defined]
        if player.seat in after and not player.is_bot
    ]
    if not after or not remaining_humans:
        return True
    return len(after) < game.metadata.player_count.min_players


def determine_consequence(
    session: object, seat: int, reason: str = "timeout"
) -> TimeoutConsequence:
    """Determine what consequence applies when ``seat`` fails to make a move (timeout or forfeit)."""
    # Forfeits skip interactive options and bot takeover, going straight to removal or end game.
    if reason == "forfeit":
        humans = [p for p in getattr(session, "players", []) if not p.is_bot]
        if len(humans) == 2:
            return TimeoutConsequence.GAME_ENDS

        meta = session.game.metadata  # type: ignore[attr-defined]
        if meta.supports_player_removal:
            if will_removal_end_game(session, seat):
                return TimeoutConsequence.GAME_ENDS
            return TimeoutConsequence.REMOVED
        return TimeoutConsequence.GAME_ENDS

    # Default timeout consequence logic
    pending = getattr(session, "pending", {}).get(seat)

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


