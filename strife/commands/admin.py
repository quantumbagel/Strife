from __future__ import annotations

import asyncio
import shlex
from pathlib import Path

import discord
from discord.ext import commands

from strife.commands.game_commands import register_slash_group_for_game, remove_slash_group_for_game
from strife.logging import get_logger
from strife.persistence.migrator import Migrator
from strife.persistence.repositories import MatchRepository, UserRepository
from strife.plugins.deps import missing_dependencies
from strife.plugins.errors import PluginError
from strife.plugins.games_yaml import ensure_game_entry
from strife.plugins.manager import looks_like_source
from strife.settings import Settings

log = get_logger("commands.admin")


def _refresh_changelog(bot, key: str, *, drop: bool = False) -> None:
    changelogs = getattr(bot, "changelogs", None)
    if changelogs is None:
        return
    if drop:
        changelogs.drop_plugin(key)
        return
    manager = getattr(bot, "plugin_manager", None)
    if manager is None:
        return
    record = manager.record_for(key)
    if record is None:
        changelogs.drop_plugin(key)
        return
    changelogs.load_plugin(key, record.root, version=record.manifest.version)


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
            "plugins": self._plugins,
            "install": self._install,
            "update": self._update,
            "uninstall": self._uninstall,
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
        except PluginError as exc:
            if added_loading:
                try:
                    await message.remove_reaction(added_loading, self.bot.user)
                except Exception:
                    pass
            await self._add_reaction_with_fallback(message, "error", "❌")
            await message.reply(str(exc))
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
        manager = self.bot.plugin_manager  # type: ignore[attr-defined]
        assets = Path("assets/emoji")
        plugin_dirs = manager.emoji_sources() if manager is not None else []
        count = await resolver.reupload(self.bot, assets, plugin_dirs=plugin_dirs)
        await resolver.sync(self.bot)
        await message.reply(f"Uploaded {count} application emoji(s) and updated emoji.yaml.")

    async def _plugins(self, args: list[str], message: discord.Message) -> None:
        manager = self.bot.plugin_manager  # type: ignore[attr-defined]
        lines = manager.status_lines()
        body = "Plugins:\n" + ("\n".join(lines) if lines else "(none)")
        await message.reply(body[:1900])

    async def _install(self, args: list[str], message: discord.Message) -> None:
        if not args:
            raise PluginError("Usage: strife/install <builtin-key | git-url> [ref]")
        target = args[0]
        ref = args[1] if len(args) > 1 else None
        manager = self.bot.plugin_manager  # type: ignore[attr-defined]
        registry = self.bot.game_registry  # type: ignore[attr-defined]

        if looks_like_source(target):
            manifest = await asyncio.to_thread(manager.install_from_git, target, ref)
            kind = "git"
        else:
            if ref is not None:
                raise PluginError("Builtin restore does not take a git ref")
            manifest = await asyncio.to_thread(manager.restore_builtin, target)
            kind = "builtin"

        enabled = _sync_game_overlay(self.bot, manifest.key)
        extra = _load_or_advise_restart(self.bot, registry, manifest.key)
        _refresh_changelog(self.bot, manifest.key)
        if kind == "git":
            extra += " Run `strife/emoji` if the plugin shipped an emoji/ folder."
        if not enabled:
            extra += " Disabled in games.yaml — set enabled: true to list it in /play."
        await message.reply(f"Installed **{manifest.key}** v{manifest.version}.{extra}")

    async def _update(self, args: list[str], message: discord.Message) -> None:
        if not args:
            raise PluginError("Usage: strife/update <key> [ref]")
        key = args[0]
        ref = args[1] if len(args) > 1 else None
        n_sessions, n_lobbies = _count_live(self.bot, key)
        if n_sessions or n_lobbies:
            raise PluginError(
                f"Cannot update `{key}` while {n_sessions} match(es) and {n_lobbies} "
                f"lobby(ies) are live. Finish them first, or uninstall."
            )
        manager = self.bot.plugin_manager  # type: ignore[attr-defined]
        registry = self.bot.game_registry  # type: ignore[attr-defined]
        manifest = await asyncio.to_thread(manager.update_from_git, key, ref)
        extra = _load_or_advise_restart(self.bot, registry, manifest.key, reload=True)
        _refresh_changelog(self.bot, manifest.key)
        extra += " Run `strife/emoji` if the plugin shipped an emoji/ folder."
        await message.reply(
            f"Updated **{manifest.key}** to v{manifest.version}. Match history was kept.{extra}"
        )

    async def _uninstall(self, args: list[str], message: discord.Message) -> None:
        if not args:
            raise PluginError("Usage: strife/uninstall <key> confirm")
        key = args[0]
        if args[1:] != ["confirm"]:
            await message.reply(
                f"This deletes all matches, replays, and stats for `{key}`, "
                f"abandons live games, and removes the plugin.\n"
                f"Run `strife/uninstall {key} confirm` to proceed."
            )
            return

        manager = self.bot.plugin_manager  # type: ignore[attr-defined]
        registry = self.bot.game_registry  # type: ignore[attr-defined]
        present = registry.contains(key) or manager.record_for(key) is not None

        n_sessions = n_lobbies = 0
        result = None
        if present:
            n_sessions, n_lobbies = await _stop_live(self.bot, key)
            registry.unregister(key)
            remove_slash_group_for_game(self.bot.tree, key)
            try:
                result = await asyncio.to_thread(manager.uninstall, key)
            except Exception:
                if manager.record_for(key) is not None:
                    try:
                        manager.load_one(registry, key)
                    except PluginError:
                        log.exception("Failed to reload %s after uninstall error", key)
                    else:
                        _register_slash(self.bot, registry, key)
                        _sync_game_overlay(self.bot, key)
                        _refresh_changelog(self.bot, key)
                raise
            _refresh_changelog(self.bot, key, drop=True)

        try:
            n_matches = await MatchRepository(self.bot.pool).delete_for_game(key)  # type: ignore[attr-defined]
            n_stats = await UserRepository(self.bot.pool).delete_stats_for_game(key)  # type: ignore[attr-defined]
        except Exception as exc:
            raise PluginError(
                f"Could not delete match history for `{key}` ({exc}). "
                f"Re-run `strife/uninstall {key} confirm` to wipe remaining data."
            ) from exc

        if result is None:
            if n_matches == 0 and n_stats == 0:
                raise PluginError(f"Unknown plugin '{key}'")
            await message.reply(
                f"Wiped leftover history for `{key}`: "
                f"{n_matches} match(es) and {n_stats} stat row(s)."
            )
            return

        await message.reply(
            f"Uninstalled **{key}** ({result.origin}). "
            f"Stopped {n_sessions} live match(es) and {n_lobbies} lobby(ies). "
            f"Deleted {n_matches} match(es) and {n_stats} stat row(s). "
            f"Run `strife/sync` if it had slash commands."
        )


def _sync_game_overlay(bot, key: str) -> bool:
    """Align in-memory catalog enablement with games.yaml. Does not flip an existing row."""
    manager = bot.plugin_manager
    enabled = True
    path = getattr(manager, "games_yaml_path", None)
    if path is not None:
        try:
            enabled = ensure_game_entry(path, key)
        except PluginError:
            enabled = True
    bot.config.games.note_game(key, enabled=enabled)
    return enabled


def _load_or_advise_restart(bot, registry, key: str, *, reload: bool = False) -> str:
    """Load the plugin now, or tell the operator to restart so extras can install at boot."""
    manager = bot.plugin_manager
    record = manager.record_for(key)
    missing = missing_dependencies(record.dependencies) if record is not None else []
    try:
        if reload:
            manager.reload_one(registry, key)
        else:
            manager.load_one(registry, key)
    except PluginError:
        if missing:
            return (
                f" Restart the bot to install extras ({', '.join(missing)}) "
                "and load the plugin."
            )
        raise
    return _register_slash(bot, registry, key)


def _register_slash(bot, registry, key: str) -> str:
    extra = ""
    if not registry.contains(key):
        return extra
    meta = registry.metadata(key)
    lobby = getattr(bot, "lobby", None)
    remove_slash_group_for_game(bot.tree, key)
    if lobby is not None and register_slash_group_for_game(
        bot.tree,
        meta,
        bot.sessions,
        lobby.user_errors,
        lobby.user_success,
    ):
        extra = " Run `strife/sync` so slash commands go live."
    return extra


def _count_live(bot, game_key: str) -> tuple[int, int]:
    sessions = getattr(bot, "sessions", None)
    if sessions is None:
        return 0, 0
    n_sessions = sum(
        1 for session in sessions.active_games.values() if getattr(session, "game_key", None) == game_key
    )
    n_lobbies = sum(1 for lobby in sessions.lobbies.values() if lobby.game_key == game_key)
    return n_sessions, n_lobbies


async def _stop_live(bot, game_key: str) -> tuple[int, int]:
    sessions = getattr(bot, "sessions", None)
    if sessions is None:
        return 0, 0
    n_sessions = 0
    n_lobbies = 0
    for session in list(sessions.active_games.values()):
        if getattr(session, "game_key", None) != game_key:
            continue
        try:
            await session.cancel("uninstalled")
        except Exception:
            log.exception("Failed to cancel session %s while uninstalling %s", session.id, game_key)
        n_sessions += 1
    for lobby in list(sessions.lobbies.values()):
        if lobby.game_key != game_key:
            continue
        for member in lobby.members:
            await sessions.release_user(member.user_id)
        sessions.remove_lobby(lobby.thread_id)
        if lobby.surface:
            try:
                await lobby.surface.delete()
            except Exception:
                log.exception("Failed to delete lobby surface while uninstalling %s", game_key)
        n_lobbies += 1
    return n_sessions, n_lobbies
