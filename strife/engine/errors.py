from __future__ import annotations


class SessionError(Exception):
    """User-facing session/lobby failure identified by a stable ``code``."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)
