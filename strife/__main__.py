from __future__ import annotations

from strife.bot import StrifeBot
from strife.settings import get_settings


def main() -> None:
    settings = get_settings()
    bot = StrifeBot(settings)
    bot.run(settings.discord_token, log_handler=None)


if __name__ == "__main__":
    main()
