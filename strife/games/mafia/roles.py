from __future__ import annotations

from strife.engine.metadata import GameMetadata


def compose_roles(meta: GameMetadata, player_count: int, settings: dict, rng) -> list[str]:
    mafia_count = int(settings.get("mafia_count", 2))
    max_mafia = max(1, (player_count - 1) // 2)
    mafia_count = min(mafia_count, max_mafia)
    roles: list[str] = ["mafia"] * mafia_count
    if settings.get("enable_doctor", True) and player_count >= 5:
        roles.append("doctor")
    if settings.get("enable_detective", True) and player_count >= 6:
        roles.append("detective")
    while len(roles) < player_count:
        roles.append("villager")
    roles = roles[:player_count]
    rng.shuffle(roles)
    return roles
