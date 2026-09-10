from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from strife.games.mafia.game import Mafia


def choose_mafia_move(game: Mafia, difficulty: str, seat: int) -> tuple[str, dict]:
    role = game.role[seat]
    alive = sorted(game.alive)
    if game._phase == "night":
        if role == "mafia":
            targets = [s for s in alive if game.role[s] != "mafia"]
            if not targets:
                targets = alive
            if difficulty == "easy":
                target = game.rng.choice(targets)
            else:
                detectives = [s for s in targets if game.role[s] == "detective"]
                doctors = [s for s in targets if game.role[s] == "doctor"]
                if detectives:
                    target = detectives[0]
                elif difficulty == "hard" and doctors:
                    target = doctors[0]
                else:
                    target = targets[0]
            return "kill", {"target": target}
        if role == "doctor":
            target = seat if difficulty != "easy" else game.rng.choice(alive)
            return "protect", {"target": target}
        if role == "detective":
            candidates = [s for s in alive if s != seat]
            target = game.rng.choice(candidates) if candidates else seat
            return "investigate", {"target": target}
    candidates = [s for s in alive if s != seat]
    if role == "mafia" and difficulty != "easy":
        candidates = [s for s in candidates if game.role[s] != "mafia"] or candidates
    if not candidates:
        return "vote", {"target": "skip"}
    if difficulty == "easy":
        target = game.rng.choice(candidates)
    else:
        target = candidates[0]
    return "vote", {"target": target}
