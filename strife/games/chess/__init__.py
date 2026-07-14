from strife.engine.registry import check_dependencies

dependencies = ["chess>=1.11.2", "resvg-py>=0.3.3"]
check_dependencies(dependencies)

from strife.games.chess.game import Chess

__all__ = ["Chess"]
