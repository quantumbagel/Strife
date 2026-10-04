from __future__ import annotations

PLATFORM_VERSION = "3.0.0"


def parse_version(value: str) -> tuple[int, int, int] | None:
    """Parse a ``major.minor.patch`` (minor/patch optional) version string."""
    if not value or not isinstance(value, str):
        return None
    parts = value.strip().split(".")
    if not 1 <= len(parts) <= 3:
        return None
    numbers: list[int] = []
    for part in parts:
        if not part.isdigit():
            return None
        numbers.append(int(part))
    while len(numbers) < 3:
        numbers.append(0)
    return numbers[0], numbers[1], numbers[2]


def platform_satisfies(required: str, host: str = PLATFORM_VERSION) -> bool:
    """Return True when *host* can run a plugin that targets *required*.

    Same major, and host is greater than or equal to the required minor/patch.
    A 3.0.0 game runs on 3.2.0; a 3.2.0 game does not run on 3.0.0; a 4.0.0
    game does not run on 3.x.
    """
    need = parse_version(required)
    have = parse_version(host)
    if need is None or have is None:
        return False
    if have[0] != need[0]:
        return False
    return have[1:] >= need[1:]
