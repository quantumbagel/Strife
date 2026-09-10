from __future__ import annotations

from typing import TYPE_CHECKING
from strife.engine.players import Move

if TYPE_CHECKING:
    from strife.games.coup.game import Coup


def _level(difficulty: str) -> int:
    return {"easy": 0, "medium": 1, "hard": 2}.get(difficulty, 1)


def _opponents(game: Coup, seat: int) -> list[int]:
    return [s for s in game.alive if s != seat]


def _richest(game: Coup, seats: list[int]) -> int:
    return max(seats, key=lambda s: (game.coins.get(s, 0), -s))


def _threats(game: Coup, seats: list[int]) -> list[int]:
    return [s for s in seats if len(game.hands.get(s, [])) >= 2 or game.coins.get(s, 0) >= 7]


def choose_move(game: Coup, difficulty: str, seat: int) -> Move:
    level = _level(difficulty)
    my_cards = game.hands.get(seat, [])
    my_coins = game.coins.get(seat, 0)
    others = _opponents(game, seat)

    if game.state_phase == "lose_influence":
        if my_cards:
            return Move(actor_seat=seat, source="lose_card", args={"card": my_cards[0]})
        return Move(actor_seat=seat, source="lose_card", args={})

    if game.state_phase == "exchange":
        options = game.exchange_options.get(seat, [])
        if len(options) >= len(my_cards):
            keep = options[: len(my_cards)]
            return Move(actor_seat=seat, source="exchange_keep", args={"keep": keep})
        return Move(actor_seat=seat, source="exchange_keep", args={})

    if game.state_phase in ("challenge_window", "block_window", "block_challenge_window"):
        return _reaction(game, level, seat, my_cards)

    if not others:
        return Move(actor_seat=seat, source="action", args={"type": "income"})

    if my_coins >= 10:
        return Move(
            actor_seat=seat,
            source="action",
            args={"type": "coup", "target": _pick_target(game, others, level)},
        )

    if level >= 2 and my_coins >= 7:
        threats = _threats(game, others)
        if threats:
            return Move(
                actor_seat=seat,
                source="action",
                args={"type": "coup", "target": _pick_target(game, threats, level)},
            )

    assassin_p = (0.15, 0.7, 0.9)[level]
    if my_coins >= 3 and ("assassin" in my_cards or (level >= 1 and game.rng.random() < assassin_p * 0.25)):
        if "assassin" in my_cards or level >= 1:
            if "assassin" in my_cards or game.rng.random() < assassin_p:
                return Move(
                    actor_seat=seat,
                    source="action",
                    args={"type": "assassinate", "target": _pick_target(game, others, level)},
                )

    steal_p = (0.2, 0.8, 0.9)[level]
    if "captain" in my_cards or (level >= 1 and game.rng.random() < steal_p * 0.35):
        if "captain" in my_cards or game.rng.random() < steal_p:
            targets = [s for s in others if game.coins.get(s, 0) > 0]
            if targets:
                return Move(
                    actor_seat=seat,
                    source="action",
                    args={"type": "steal", "target": _pick_target(game, targets, level)},
                )

    tax_p = (0.15, 0.5, 0.7)[level]
    if "duke" in my_cards or game.rng.random() < tax_p:
        return Move(actor_seat=seat, source="action", args={"type": "tax"})

    if level == 0 or game.rng.random() < 0.55:
        return Move(actor_seat=seat, source="action", args={"type": "income"})
    return Move(actor_seat=seat, source="action", args={"type": "foreign_aid"})


def _pick_target(game: Coup, seats: list[int], level: int) -> int:
    if level >= 2:
        threats = _threats(game, seats)
        pool = threats or seats
        return _richest(game, pool)
    return game.rng.choice(seats)


def _reaction(game: Coup, level: int, seat: int, my_cards: list[str]) -> Move:
    action_type = game.current_action
    target = game.current_target
    targeted = target == seat

    if game.state_phase == "challenge_window":
        if action_type == "assassinate" and targeted:
            if "contessa" in my_cards or game.rng.random() < (0.15, 0.7, 0.85)[level]:
                return Move(actor_seat=seat, source="block_contessa", args={})
        elif action_type == "steal" and targeted:
            if "captain" in my_cards or "ambassador" in my_cards or game.rng.random() < (0.15, 0.7, 0.85)[level]:
                if "captain" in my_cards:
                    claim = "captain"
                elif "ambassador" in my_cards:
                    claim = "ambassador"
                else:
                    claim = game.rng.choice(["captain", "ambassador"])
                return Move(actor_seat=seat, source=f"block_{claim}", args={})
        challenge_p = (0.04, 0.12, 0.28)[level]
        if targeted:
            challenge_p += (0.0, 0.08, 0.15)[level]
        if game.rng.random() < challenge_p and game.current_actor != seat:
            return Move(actor_seat=seat, source="challenge", args={})
        return Move(actor_seat=seat, source="pass", args={})

    if game.state_phase == "block_window":
        if action_type == "foreign_aid" and seat != game.current_actor:
            if "duke" in my_cards or game.rng.random() < (0.05, 0.2, 0.45)[level]:
                return Move(actor_seat=seat, source="block_duke", args={})
        return Move(actor_seat=seat, source="pass", args={})

    if game.state_phase == "block_challenge_window":
        if game.rng.random() < (0.05, 0.2, 0.35)[level]:
            return Move(actor_seat=seat, source="challenge", args={})
        return Move(actor_seat=seat, source="pass", args={})

    return Move(actor_seat=seat, source="pass", args={})
