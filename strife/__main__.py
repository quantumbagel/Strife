from __future__ import annotations

import asyncio
import signal

from strife.bot import StrifeBot
from strife.logging import configure_logging
from strife.settings import get_settings


async def _run() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    bot = StrifeBot(settings)

    async with bot:
        loop = asyncio.get_running_loop()
        shutdown_tasks: set[asyncio.Task] = set()

        def _shutdown() -> None:
            if any(not task.done() for task in shutdown_tasks):
                return
            task = asyncio.create_task(bot.close())
            shutdown_tasks.add(task)

        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, _shutdown)
        await bot.start(settings.discord_token)


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
