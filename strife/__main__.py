from __future__ import annotations

import signal

from strife.bot import StrifeBot
from strife.logging import configure_logging
from strife.settings import get_settings


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    bot = StrifeBot(settings)

    def _handle_signal(_signum: int, _frame: object) -> None:
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, _handle_signal)
    bot.run(settings.discord_token, log_handler=None)


if __name__ == "__main__":
    main()
