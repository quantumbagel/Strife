from __future__ import annotations

from typing import TYPE_CHECKING

from strife.engine.players import Move

if TYPE_CHECKING:
    from strife.engine import BotRequest
    from strife.games.spyfall.game import Spyfall


def _allowed(request: BotRequest, source: str) -> bool:
    return request.sources is None or source in request.sources


def _form_choices(request: BotRequest, key: str) -> list[str] | None:
    if request.form is not None and key in request.form:
        return list(request.form[key])
    return None


def _pick_target(game: Spyfall, request: BotRequest, seat: int) -> str | None:
    choices = _form_choices(request, "target")
    if choices is not None:
        others = [value for value in choices if value not in {str(seat), "_"}]
    else:
        others = [str(s) for s in game.alive if s != seat]
    if not others:
        return None
    return str(game.bot_rng.choice(others))


def _pick_location(game: Spyfall, request: BotRequest) -> str | None:
    choices = _form_choices(request, "location")
    if choices is not None:
        locations = choices
    else:
        locations = list(game.LOCATIONS)
    if not locations:
        return None
    return str(game.bot_rng.choice(locations))


def choose_move(game: Spyfall, request: BotRequest) -> Move:
    seat = request.seat
    difficulty = request.difficulty

    if game.accused_player is not None:
        if seat == game.accused_player:
            source = "vote_innocent"
        elif seat == game.spy:
            source = "vote_guilty"
        else:
            source = "vote_guilty" if game.bot_rng.random() < 0.6 else "vote_innocent"
        if not _allowed(request, source):
            if _allowed(request, "vote_innocent"):
                source = "vote_innocent"
            elif _allowed(request, "vote_guilty"):
                source = "vote_guilty"
            elif request.sources:
                source = game.bot_rng.choice(sorted(request.sources))
        return Move(actor_seat=seat, source=source, args={})

    is_spy = seat == game.spy
    pressure = 0.2 if difficulty == "easy" else 0.35 if difficulty == "medium" else 0.5
    pressure += 0.12 * max(0, game.turn - 1)

    if (
        not is_spy
        and seat not in game._accused_seats
        and game.bot_rng.random() < pressure
        and _allowed(request, "accuse")
    ):
        target = _pick_target(game, request, seat)
        if target is not None:
            return Move(actor_seat=seat, source="accuse", args={"target": target})

    if _allowed(request, "pass"):
        return Move(actor_seat=seat, source="pass", args={})

    if _allowed(request, "accuse"):
        target = _pick_target(game, request, seat)
        if target is not None:
            return Move(actor_seat=seat, source="accuse", args={"target": target})

    if _allowed(request, "guess_location"):
        location = _pick_location(game, request)
        if location is not None:
            return Move(
                actor_seat=seat, source="guess_location", args={"location": location}
            )

    if request.sources:
        source = game.bot_rng.choice(sorted(request.sources))
        args: dict[str, str] = {}
        if source == "accuse":
            target = _pick_target(game, request, seat)
            if target is not None:
                args["target"] = target
        elif source == "guess_location":
            location = _pick_location(game, request)
            if location is not None:
                args["location"] = location
        return Move(actor_seat=seat, source=source, args=args)
    return Move(actor_seat=seat, source="pass", args={})
