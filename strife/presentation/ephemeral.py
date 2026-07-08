from __future__ import annotations

import asyncio
import discord
from strife.logging import get_logger

log = get_logger("presentation.ephemeral")


def setup_ephemeral_timeouts(timeout_seconds: float = 5.0) -> None:
    """Monkeypatches discord.py response methods to automatically delete
    ephemeral plain-text messages (error/confirmation messages) after a timeout.
    """
    _original_send_message = discord.InteractionResponse.send_message
    _original_webhook_send = discord.Webhook.send

    async def patched_send_message(self, *args, **kwargs) -> None:
        await _original_send_message(self, *args, **kwargs)

        ephemeral = kwargs.get("ephemeral", False)
        content = kwargs.get("content")
        if not content and len(args) > 0:
            content = args[0]

        view = kwargs.get("view")
        embed = kwargs.get("embed")
        embeds = kwargs.get("embeds")

        if ephemeral and content and not (view or embed or embeds):
            async def delete_after_delay():
                await asyncio.sleep(timeout_seconds)
                try:
                    await self._parent.delete_original_response()
                except Exception as exc:
                    log.debug("Failed to delete ephemeral original response: %s", exc)
            asyncio.create_task(delete_after_delay())

    async def patched_webhook_send(self, *args, **kwargs) -> discord.WebhookMessage | None:
        msg = await _original_webhook_send(self, *args, **kwargs)

        ephemeral = kwargs.get("ephemeral", False)
        content = kwargs.get("content")
        if not content and len(args) > 0:
            content = args[0]

        view = kwargs.get("view")
        embed = kwargs.get("embed")
        embeds = kwargs.get("embeds")

        if ephemeral and content and not (view or embed or embeds) and msg:
            async def delete_after_delay():
                await asyncio.sleep(timeout_seconds)
                try:
                    await msg.delete()
                except Exception as exc:
                    log.debug("Failed to delete ephemeral followup response: %s", exc)
            asyncio.create_task(delete_after_delay())

        return msg

    discord.InteractionResponse.send_message = patched_send_message
    discord.Webhook.send = patched_webhook_send
    log.info("Monkeypatched discord.py response methods with ephemeral timeouts (%ss)", timeout_seconds)
