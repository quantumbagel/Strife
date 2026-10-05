from __future__ import annotations

import asyncio
import shlex
from pathlib import Path

import discord
from discord.ext import commands

from strife.commands.game_commands import (
    register_slash_group_for_game,
    remove_slash_group_for_game,
)
from strife.logging import get_logger
from strife.persistence.migrator import Migrator
from strife.persistence.repositories import MatchRepository, UserRepository
from strife.plugins.deps import missing_dependencies
from strife.plugins.errors import PluginError
from strife.plugins.games_yaml import ensure_game_entry
from strife.plugins.manager import PluginUpdate, normalize_source
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
        try:
            parts = shlex.split(message.content.removeprefix("strife/"))
            if not parts:
                return
            cmd, *args = parts
        except ValueError:
            await message.reply(
                "Invalid command quoting. Usage: strife/<command> [args...]"
            )
            return
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
                except Exception:  # noqa: BLE001, S110
                    pass
        except Exception:  # noqa: BLE001, S110
            pass
        return None

    async def _dispatch(
        self, cmd: str, args: list[str], message: discord.Message
    ) -> None:
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
                except Exception:  # noqa: BLE001, S110
                    pass
            await self._add_reaction_with_fallback(message, "success", "✅")
        except PluginError as exc:
            if added_loading:
                try:
                    await message.remove_reaction(added_loading, self.bot.user)
                except Exception:  # noqa: BLE001, S110
                    pass
            await self._add_reaction_with_fallback(message, "error", "❌")
            await message.reply(str(exc)[:1900])
        except Exception as exc:
            log.exception("strife/%s %s failed", cmd, " ".join(args))
            if added_loading:
                try:
                    await message.remove_reaction(added_loading, self.bot.user)
                except Exception:  # noqa: BLE001, S110
                    pass
            await self._add_reaction_with_fallback(message, "error", "❌")
            try:
                await message.reply(
                    f"`strife/{cmd}` failed: {type(exc).__name__}. "
                    "See the bot log for details."
                )
            except discord.HTTPException:
                log.exception("Could not report strife/%s failure", cmd)

    def _guild_target(
        self, args: list[str], message: discord.Message, usage: str
    ) -> tuple[discord.abc.Snowflake | None, str]:
        """Parse ``[] | local | <guild_id>`` into ``(guild or None for global, label)``."""
        if len(args) > 1:
            raise PluginError(usage)
        if not args:
            return None, "global"
        target = args[0]
        if target == "local":
            if message.guild is None:
                raise PluginError(f"`local` needs a server channel, not a DM.\n{usage}")
            return message.guild, "this guild"
        try:
            guild_id = int(target)
        except ValueError:
            raise PluginError(f"Invalid target `{target}`.\n{usage}") from None
        return discord.Object(id=guild_id), f"guild {guild_id}"

    async def _sync(self, args: list[str], message: discord.Message) -> None:
        guild, label = self._guild_target(args, message, _SYNC_USAGE)
        if guild is None:
            synced = await _sync_global_tree(self.bot)
            await message.reply(f"Globally synced {len(synced)} commands.")
            return
        self.bot.tree.copy_global_to(guild=guild)
        synced = await self.bot.tree.sync(guild=guild)
        reply = f"Synced {len(synced)} commands to {label}."
        clear_cmd = (
            "strife/clear local" if args[0] == "local" else f"strife/clear {args[0]}"
        )
        try:
            global_count = len(await self.bot.tree.fetch_commands())
        except discord.HTTPException:
            global_count = None
        if global_count:
            reply += (
                f"\nWarning: {global_count} command(s) are also synced globally, so they show "
                f"twice in {label}. Run `{clear_cmd}` to drop the guild copy."
            )
        elif global_count is None:
            reply += (
                f"\nIf these are also synced globally they will show twice in {label}; "
                f"`{clear_cmd}` drops the guild copy."
            )
        await message.reply(reply)

    async def _clear(self, args: list[str], message: discord.Message) -> None:
        guild, label = self._guild_target(args, message, _CLEAR_USAGE)
        if guild is None:
            self.bot.tree.clear_commands(guild=None)
            await self.bot.tree.sync()
            await message.reply(
                "Cleared global commands. Restart the bot before `strife/sync` "
                "to register them again."
            )
            return
        self.bot.tree.clear_commands(guild=guild)
        await self.bot.tree.sync(guild=guild)
        await message.reply(f"Cleared the guild-only command copy for {label}.")

    async def _treediff(self, args: list[str], message: discord.Message) -> None:
        guild = message.guild
        remote = await self.bot.tree.fetch_commands(guild=guild)
        local = {c.name: c for c in self.bot.tree.get_commands(guild=guild)}
        lines = [
            f"Remote: {len(remote)} commands",
            f"Local top-level: {len(local)} commands",
        ]
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
        # Resume may still be reading and writing live rows right after boot.
        await self.bot.wait_until_live_resumed()  # type: ignore[attr-defined]
        sessions = getattr(self.bot, "sessions", None)
        n_games = len(sessions.active_games) if sessions is not None else 0
        n_lobbies = len(sessions.lobbies) if sessions is not None else 0
        if n_games or n_lobbies:
            raise PluginError(
                f"Not resetting: {n_games} live game(s) and {n_lobbies} open lobby(ies). "
                "Their final saves would fail against the wiped tables. "
                "Let the games finish and end the lobbies, then retry."
            )
        migrator = Migrator(self.bot.pool, self.settings.migrations_dir)  # type: ignore[attr-defined]
        await migrator.reset()
        _clear_replay_caches(getattr(self.bot, "replay", None))
        await message.reply(
            "Database reset and migrations re-applied. Replay cache cleared."
        )

    async def _emoji(self, args: list[str], message: discord.Message) -> None:
        resolver = self.bot.emoji  # type: ignore[attr-defined]
        manager = self.bot.plugin_manager  # type: ignore[attr-defined]
        assets = Path("assets/emoji")
        plugin_dirs = manager.emoji_sources() if manager is not None else []
        result = await resolver.reupload(self.bot, assets, plugin_dirs=plugin_dirs)
        try:
            await resolver.sync(self.bot)
        except discord.HTTPException:
            log.exception("Emoji sync after re-upload failed")
        reply = (
            f"Uploaded {result.uploaded} application emoji(s) and updated emoji.yaml."
        )
        if result.failed:
            shown = "\n".join(f"- {name}: {why}" for name, why in result.failed[:15])
            more = len(result.failed) - 15
            reply += (
                f"\n{len(result.failed)} failed (those keep their Unicode fallback):\n{shown}"
                + (f"\n…and {more} more (see log)" if more > 0 else "")
                + "\nRun `strife/emoji` again to retry."
            )
        await message.reply(reply[:1900])

    async def _plugins(self, args: list[str], message: discord.Message) -> None:
        manager = self.bot.plugin_manager  # type: ignore[attr-defined]
        registry = getattr(self.bot, "game_registry", None)
        loaded = {meta.key for meta in registry.all()} if registry is not None else None
        games = self.bot.config.games  # type: ignore[attr-defined]
        hidden = {key for key in loaded or () if not games.for_game(key).enabled}
        lines = manager.status_lines(loaded=loaded, hidden=hidden)
        body = "Plugins:\n" + ("\n".join(lines) if lines else "(none)")
        await message.reply(body[:1900])

    async def _install(self, args: list[str], message: discord.Message) -> None:
        if not args or len(args) > 2:
            raise PluginError("Usage: strife/install <builtin-key | git-url> [ref]")
        await self.bot.wait_until_live_resumed()  # type: ignore[attr-defined]
        target = args[0]
        ref = args[1] if len(args) > 1 else None
        manager = self.bot.plugin_manager  # type: ignore[attr-defined]
        registry = self.bot.game_registry  # type: ignore[attr-defined]

        source = normalize_source(target)
        if source is not None:
            manifest = await asyncio.to_thread(manager.install_from_git, source, ref)
            kind = "git"
        else:
            if ref is not None:
                raise PluginError("Builtin restore does not take a git ref")
            manifest = await asyncio.to_thread(manager.restore_builtin, target)
            kind = "builtin"

        key = manifest.key
        try:
            extra = _load_or_advise_restart(self.bot, registry, key)
            extra += _register_slash(self.bot, registry, key)
        except Exception as exc:
            log.exception("Plugin %s failed to load after install; rolling back", key)
            registry.unregister(key)
            remove_slash_group_for_game(self.bot.tree, key)
            try:
                await asyncio.to_thread(manager.rollback_install, key)
            except Exception as rb_exc:  # noqa: BLE001
                raise PluginError(
                    f"**{key}** was installed but failed to load: {exc}\n"
                    f"Rolling back also failed ({rb_exc}); run `strife/uninstall {key} confirm`."
                ) from exc
            raise PluginError(
                f"**{key}** failed to load, so the install was rolled back:\n{exc}"
            ) from exc
        manager.confirm_install(key)
        enabled = _sync_game_overlay(self.bot, key)
        _refresh_changelog(self.bot, key)
        extra += await _sync_tree_after_plugin_change(self.bot)
        if kind == "git":
            extra += " Run `strife/emoji` if the plugin shipped an emoji/ folder."
        if not enabled:
            extra += " Disabled in games.yaml — set enabled: true to list it in /play."
        await message.reply(f"Installed **{key}** v{manifest.version}.{extra}")

    async def _update(self, args: list[str], message: discord.Message) -> None:
        if not args or len(args) > 2:
            raise PluginError("Usage: strife/update <key> [ref]")
        await self.bot.wait_until_live_resumed()  # type: ignore[attr-defined]
        key = args[0]
        ref = args[1] if len(args) > 1 else None
        await _require_no_live(self.bot, key, action="update")
        manager = self.bot.plugin_manager  # type: ignore[attr-defined]
        registry = self.bot.game_registry  # type: ignore[attr-defined]
        update = await asyncio.to_thread(manager.update_from_git, key, ref)
        try:
            extra = _load_or_advise_restart(self.bot, registry, key, reload=True)
            if not registry.contains(key):
                raise PluginError(f"Plugin '{key}' did not register after update")
        except Exception as exc:
            log.exception(
                "Plugin %s failed to reload after update; restoring old files", key
            )
            try:
                missing = missing_dependencies(update.manifest.dependencies)
            except PluginError:
                missing = []
            try:
                await asyncio.to_thread(manager.revert_update, update)
                try:
                    manager.reload_one(registry, key)
                except Exception:
                    log.exception("Failed to re-import restored plugin %s", key)
            except Exception as rb_exc:
                log.exception("Could not restore %s after a failed update", key)
                raise PluginError(
                    f"**{key}** v{update.manifest.version} failed to load: {exc}\n"
                    f"Restoring the previous files also failed ({rb_exc}). The old version keeps "
                    f"running until restart; the backup is at `{update.backup}`."
                ) from exc
            if missing:
                raise PluginError(
                    f"**{key}** v{update.manifest.version} was not applied; these packages must "
                    f"be installed first: {', '.join(missing)}. "
                    f"Run `python -m strife.plugins sync-deps` or restart with "
                    f"`STRIFE_SYNC_PLUGIN_DEPS`, then retry `strife/update {key}`."
                ) from exc
            raise PluginError(
                f"**{key}** v{update.manifest.version} failed to load, so the previous files "
                f"and ref were restored (the running version was not replaced):\n{exc}"
            ) from exc
        await asyncio.to_thread(manager.finish_update, update)
        extra += _register_slash(self.bot, registry, key)
        extra += await _sync_tree_after_plugin_change(self.bot)
        _refresh_changelog(self.bot, key)
        extra += " Run `strife/emoji` if the plugin shipped an emoji/ folder."
        await message.reply(
            f"Updated **{key}** to v{update.manifest.version}."
            f"{_describe_ref(key, update)} Match history was kept.{extra}"
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

        await self.bot.wait_until_live_resumed()  # type: ignore[attr-defined]

        manager = self.bot.plugin_manager  # type: ignore[attr-defined]
        registry = self.bot.game_registry  # type: ignore[attr-defined]
        present = registry.contains(key) or manager.record_for(key) is not None

        n_sessions = n_lobbies = 0
        result = None
        lobby_svc = getattr(self.bot, "lobby", None)
        try:
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
                            log.exception(
                                "Failed to reload %s after uninstall error", key
                            )
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
                f"Deleted {n_matches} match(es) and {n_stats} stat row(s)."
                f"{await _sync_tree_after_plugin_change(self.bot)}"
            )
        finally:
            end = getattr(lobby_svc, "end_closing_game", None)
            if callable(end):
                end(key)


_SYNC_USAGE = (
    "Usage: `strife/sync` (global), `strife/sync local` (this guild), "
    "or `strife/sync <guild_id>`."
)
_CLEAR_USAGE = (
    "Usage: `strife/clear` (global), `strife/clear local` (this guild's copy), "
    "or `strife/clear <guild_id>`."
)


def _describe_ref(key: str, update: PluginUpdate) -> str:
    commit = f" (commit `{update.commit[:10]}`)" if update.commit else ""
    if update.requested_ref is not None:
        return f" Ref: `{update.ref}`{commit}."
    if update.reused_ref:
        return (
            f" Reused the pinned ref `{update.ref}`{commit} from the last install/update. "
            f"To follow a branch instead, run `strife/update {key} <branch>` (e.g. `main`)."
        )
    return f" Ref: default branch{commit}."


def _clear_replay_caches(replay) -> None:
    if replay is None:
        return
    clear = getattr(replay, "clear_cache", None)
    if callable(clear):
        clear()
        return
    for attr in ("_cache", "_autocomplete_cache"):
        cache = getattr(replay, attr, None)
        if cache is not None and hasattr(cache, "clear"):
            cache.clear()


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
    """Load the plugin now, or tell the operator to restart so extras can install at boot.

    Does not register slash commands; call ``_register_slash`` after.
    Reload after an update must not keep the new files when extras are missing.
    """
    manager = bot.plugin_manager
    record = manager.record_for(key)
    try:
        missing = (
            missing_dependencies(record.dependencies) if record is not None else []
        )
    except PluginError:
        missing = []
    try:
        if reload:
            manager.reload_one(registry, key)
        else:
            manager.load_one(registry, key)
    except PluginError:
        if missing and not reload:
            return (
                f" Restart the bot to install extras ({', '.join(missing)}) "
                "and load the plugin."
            )
        raise
    return ""


def _register_slash(bot, registry, key: str) -> str:
    extra = ""
    if not registry.contains(key):
        return extra
    remove_slash_group_for_game(bot.tree, key)
    games = getattr(getattr(bot, "config", None), "games", None)
    if games is not None and not games.for_game(key).enabled:
        return extra
    meta = registry.metadata(key)
    lobby = getattr(bot, "lobby", None)
    if lobby is not None:
        register_slash_group_for_game(
            bot.tree,
            meta,
            bot.sessions,
            lobby.user_errors,
            lobby.user_success,
        )
    return extra


async def _sync_global_tree(bot) -> list:
    return await bot.tree.sync()


async def _sync_tree_after_plugin_change(bot) -> str:
    """Publish the local command tree when the bot syncs globally at boot."""
    settings = getattr(bot, "settings", None)
    if settings is None or not settings.sync_on_start:
        return " Run `strife/sync` so slash commands go live."
    try:
        synced = await _sync_global_tree(bot)
    except Exception:
        log.exception("Command tree sync after plugin change failed")
        return " Run `strife/sync` so slash commands go live."
    return f" Synced {len(synced)} command(s)."


_STOP_LIVE_WAIT_SECONDS = 15.0
_STOP_LIVE_MAX_PASSES = 5


async def _require_no_live(bot, game_key: str, *, action: str) -> None:
    n_sessions, n_lobbies = _count_live(bot, game_key)
    n_db = await _count_live_db(bot, game_key)
    if not n_sessions and not n_lobbies and not n_db:
        return
    db_note = f" ({n_db} live match(es) stored)" if n_db else ""
    suffix = " Finish them first, or uninstall."
    raise PluginError(
        f"Cannot {action} `{game_key}` while {n_sessions} match(es) and {n_lobbies} "
        f"lobby(ies) are live{db_note}.{suffix}"
    )


async def _count_live_db(bot, game_key: str) -> int:
    matches = getattr(bot, "matches", None)
    if matches is not None:
        return await matches.count_live(game_key)
    pool = getattr(bot, "pool", None)
    if pool is None:
        return 0
    return await MatchRepository(pool).count_live(game_key)


def _count_live(bot, game_key: str) -> tuple[int, int]:
    sessions = getattr(bot, "sessions", None)
    if sessions is None:
        return 0, 0
    n_sessions = sum(
        1
        for session in sessions.active_games.values()
        if getattr(session, "game_key", None) == game_key
    )
    n_lobbies = sum(
        1 for lobby in sessions.lobbies.values() if lobby.game_key == game_key
    )
    return n_sessions, n_lobbies


async def _stop_live(bot, game_key: str) -> tuple[int, int]:
    lobby_svc = getattr(bot, "lobby", None)
    if lobby_svc is not None:
        begin = getattr(lobby_svc, "begin_closing_game", None)
        if callable(begin):
            begin(game_key)
    sessions = getattr(bot, "sessions", None)
    if sessions is None:
        return 0, 0
    n_sessions = 0
    n_lobbies = 0
    seen_sessions: set[int] = set()
    for _pass in range(_STOP_LIVE_MAX_PASSES):
        for session in list(sessions.active_games.values()):
            if getattr(session, "game_key", None) != game_key:
                continue
            thread_id = session.thread_id
            is_new = thread_id not in seen_sessions
            seen_sessions.add(thread_id)
            try:
                await session.cancel("uninstalled")
            except Exception:
                log.exception(
                    "Failed to cancel session %s while uninstalling %s",
                    session.id,
                    game_key,
                )
            # A match already ending may still be saving; let it finish before its rows go.
            task = getattr(session, "task", None)
            if task is not None and not task.done():
                try:
                    await asyncio.wait_for(
                        asyncio.shield(task), timeout=_STOP_LIVE_WAIT_SECONDS
                    )
                except Exception:  # noqa: BLE001
                    log.warning(
                        "Session %s still finishing while uninstalling %s",
                        session.id,
                        game_key,
                    )
            if is_new:
                n_sessions += 1
        close = getattr(lobby_svc, "close_lobbies_for_game", None)
        if close is not None:
            n_lobbies += await close(game_key)
        n_left, n_lobby_left = _count_live(bot, game_key)
        if not n_left and not n_lobby_left:
            break
    else:
        n_left, n_lobby_left = _count_live(bot, game_key)
        if n_left or n_lobby_left:
            log.warning(
                "Uninstall of %s still has %s live match(es) and %s lobby(ies) after %s passes",
                game_key,
                n_left,
                n_lobby_left,
                _STOP_LIVE_MAX_PASSES,
            )
    return n_sessions, n_lobbies
