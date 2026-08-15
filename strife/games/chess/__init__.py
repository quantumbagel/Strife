from strife.engine.registry import check_dependencies

dependencies = ["chess>=1.11.2", "resvg-py>=0.3.3"]
if not check_dependencies(dependencies):
    raise ImportError(
        "Chess requires chess>=1.11.2 and resvg-py>=0.3.3. "
        "Install the project dependencies (or the [chess] extra) at deploy time."
    )

from strife.games.chess.game import Chess

__all__ = ["Chess"]
