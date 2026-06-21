from __future__ import annotations

import shlex
from pathlib import Path

import discord
from discord.ext import commands

from strife.logging import get_logger
from strife.persistence.migrator import Migrator
from strife.settings import Settings

log = get_logger("commands.admin")


class AdminCommands(commands.Cog):
    def __init__(self, bot: commands.Bot, settings: Settings) -> None:
        self.bot = bot
        self.settings = settings

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot:
            return
        if message.author.id not in self.settings.owner_ids:
            return
        if not message.content.startswith("strife/"):
            return
        cmd, *args = shlex.split(message.content.removeprefix("strife/"))
        await self._dispatch(cmd, args, message)

    async def _add_reaction_with_fallback(
        self, message: discord.Message, emoji_key: str, default_fallback: str
    ) -> str | None:
        emoji_resolver = getattr(self.bot, "emoji", None)
        emoji = emoji_resolver.get(emoji_key) if emoji_resolver else default_fallback
        if emoji == "❓":
            emoji = default_fallback

        try:
            await message.add_reaction(emoji)
            return emoji
        except discord.HTTPException:
            if emoji != default_fallback:
                try:
                    await message.add_reaction(default_fallback)
                    return default_fallback
                except Exception:
                    pass
        except Exception:
            pass
        return None

    async def _dispatch(self, cmd: str, args: list[str], message: discord.Message) -> None:
        handlers = {
            "sync": self._sync,
            "clear": self._clear,
            "treediff": self._treediff,
            "dbreset": self._dbreset,
            "emoji": self._emoji,
        }
        handler = handlers.get(cmd)
        if handler is None:
            await message.reply(f"Unknown admin command: {cmd}")
            return

        added_loading = await self._add_reaction_with_fallback(message, "loading", "⏳")

        try:
            await handler(args, message)
            if added_loading:
                try:
                    await message.remove_reaction(added_loading, self.bot.user)
                except Exception:
                    pass
            await self._add_reaction_with_fallback(message, "success", "✅")
        except Exception as e:
            if added_loading:
                try:
                    await message.remove_reaction(added_loading, self.bot.user)
                except Exception:
                    pass
            await self._add_reaction_with_fallback(message, "error", "❌")
            raise e

    async def _sync(self, args: list[str], message: discord.Message) -> None:
        if not args:
            synced = await self.bot.tree.sync()
            await message.reply(f"Globally synced {len(synced)} commands.")
            return
        target = args[0]
        if target == "local":
            if message.guild is None:
                await message.reply("local sync requires a guild context")
                return
            self.bot.tree.copy_global_to(guild=message.guild)
            synced = await self.bot.tree.sync(guild=message.guild)
            await message.reply(f"Synced {len(synced)} commands to this guild.")
            return
        guild = discord.Object(id=int(target))
        self.bot.tree.copy_global_to(guild=guild)
        synced = await self.bot.tree.sync(guild=guild)
        await message.reply(f"Synced {len(synced)} commands to guild {target}.")

    async def _clear(self, args: list[str], message: discord.Message) -> None:
        if not args:
            self.bot.tree.clear_commands(guild=None)
            await self.bot.tree.sync()
            await message.reply("Cleared global commands.")
            return
        target = args[0]
        guild = message.guild if target == "local" else discord.Object(id=int(target))
        self.bot.tree.clear_commands(guild=guild)
        await self.bot.tree.sync(guild=guild)
        await message.reply("Cleared guild commands.")

    async def _treediff(self, args: list[str], message: discord.Message) -> None:
        guild = message.guild
        remote = await self.bot.tree.fetch_commands(guild=guild)
        local = {c.name: c for c in self.bot.tree.get_commands(guild=guild)}
        lines = [f"Remote: {len(remote)} commands", f"Local top-level: {len(local)} commands"]
        remote_names = {c.name for c in remote}
        local_names = set(local)
        added = local_names - remote_names
        removed = remote_names - local_names
        if added:
            lines.append("Local only: " + ", ".join(sorted(added)))
        if removed:
            lines.append("Remote only: " + ", ".join(sorted(removed)))
        await message.reply("\n".join(lines)[:1900])

    async def _dbreset(self, args: list[str], message: discord.Message) -> None:
        if args != ["confirm"]:
            await message.reply("Run `strife/dbreset confirm` to wipe the database.")
            return
        migrator = Migrator(self.bot.pool, self.settings.migrations_dir)  # type: ignore[attr-defined]
        await migrator.reset()
        await message.reply("Database reset and migrations re-applied.")

    async def _emoji(self, args: list[str], message: discord.Message) -> None:
        resolver = self.bot.emoji  # type: ignore[attr-defined]
        assets = Path("assets/emoji")
        count = await resolver.reupload(self.bot, assets)
        await resolver.sync(self.bot)
        await message.reply(f"Uploaded {count} application emoji(s) and updated emoji.yaml.")
