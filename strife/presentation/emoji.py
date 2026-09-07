from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path

import discord

from strife.config.emoji import EmojiConfig, EmojiEntry, save_emoji_config
from strife.logging import get_logger
from strife.presentation.base_emojis import BASE_EMOJIS

log = get_logger("presentation.emoji")

EMOJI_SUFFIXES = {".png", ".gif", ".jpg", ".jpeg", ".webp"}
_DISCORD_NAME = re.compile(r"^[a-zA-Z0-9_]{2,32}$")


def plugin_emoji_name(game_key: str, stem: str) -> str:
    """Discord application-emoji name for a plugin file stem: ``{game_key}_{stem}``.

    If *stem* is already namespaced for this game, it is returned unchanged.
    """
    prefix = f"{game_key}_"
    name = stem if stem.startswith(prefix) else prefix + stem
    return name


def is_base_emoji(name: str) -> bool:
    return name in BASE_EMOJIS


class EmojiResolver:
    def __init__(self, config: EmojiConfig, *, game_key: str | None = None) -> None:
        self._config = config
        self._game_key = game_key

    @property
    def config(self) -> EmojiConfig:
        return self._config

    @property
    def game_key(self) -> str | None:
        return self._game_key

    def reload(self, config: EmojiConfig) -> None:
        self._config = config

    def bind_game(self, game_key: str | None) -> EmojiResolver:
        if game_key == self._game_key:
            return self
        return EmojiResolver(self._config, game_key=game_key)

    def get(self, name: str, *, base: bool = False) -> str:
        if not name:
            return self._unknown("(empty)")
        if base:
            if name not in BASE_EMOJIS:
                log.warning("Unknown base emoji %r (not in the platform base set)", name)
                return self._unknown(name)
            return self._render(name)
        if self._game_key:
            plugin_name = plugin_emoji_name(self._game_key, name)
            if plugin_name in self._config.entries:
                return self._render(plugin_name)
            if name in BASE_EMOJIS:
                return self._render(name)
            log.warning("Unknown emoji %r for plugin %s", name, self._game_key)
            return self._unknown(name)
        return self._render(name)

    def game(self, key: str, name: str) -> str:
        return self.bind_game(key).get(name)

    def get_game_emoji(self, game_key: str) -> str:
        plugin_name = plugin_emoji_name(game_key, "game")
        if plugin_name in self._config.entries:
            return self._render(plugin_name)
        return self.get("game", base=True)

    def _render(self, name: str) -> str:
        entry = self._config.entries.get(name)
        if entry is None:
            log.warning("Unknown emoji: %s", name)
            fallback = self._config.entries.get("error")
            return fallback.fallback if (fallback and fallback.fallback) else "❓"
        if entry.id is not None:
            prefix = "a" if entry.animated else ""
            return f"<{prefix}:{name}:{entry.id}>"
        return entry.fallback if entry.fallback is not None else "❓"

    def _unknown(self, name: str) -> str:
        fallback = self._config.entries.get("error")
        return fallback.fallback if (fallback and fallback.fallback) else "❓"

    async def sync(self, bot: discord.Client) -> EmojiConfig:
        emojis = await bot.fetch_application_emojis()
        by_name = {emoji.name: emoji for emoji in emojis}

        seen_names = set()
        for name, entry in self._config.entries.items():
            emoji = by_name.get(name)
            if emoji:
                entry.id = emoji.id
                entry.animated = emoji.animated
            else:
                entry.id = None
            seen_names.add(name)

        for name, emoji in by_name.items():
            if name not in seen_names:
                self._config.entries[name] = EmojiEntry(
                    fallback=None, id=emoji.id, animated=emoji.animated
                )

        if self._config.path:
            save_emoji_config(self._config)

        return self._config

    async def reupload(
        self,
        bot: discord.Client,
        assets_dir: Path,
        *,
        plugin_dirs: Sequence[tuple[str, Path]] = (),
    ) -> int:
        existing = await bot.fetch_application_emojis()
        for emoji in existing:
            await emoji.delete()

        uploaded = 0
        uploaded += await self._upload_dir(bot, assets_dir, prefix=None, allow=BASE_EMOJIS)
        for game_key, folder in plugin_dirs:
            uploaded += await self._upload_dir(bot, folder, prefix=game_key, allow=None)

        if self._config.path:
            save_emoji_config(self._config)
        return uploaded

    async def _upload_dir(
        self,
        bot: discord.Client,
        folder: Path,
        *,
        prefix: str | None,
        allow: frozenset[str] | None,
    ) -> int:
        if not folder.exists():
            return 0
        uploaded = 0
        for path in sorted(folder.iterdir()):
            if not path.is_file() or path.suffix.lower() not in EMOJI_SUFFIXES:
                continue
            stem = path.stem
            if allow is not None and stem not in allow:
                log.warning("Skipping %s; not in the platform base emoji set", path.name)
                continue
            name = plugin_emoji_name(prefix, stem) if prefix else stem
            if not _DISCORD_NAME.fullmatch(name):
                log.error("Cannot upload emoji %s as %r (Discord name must be 2-32 [A-Za-z0-9_])", path, name)
                continue
            with path.open("rb") as fh:
                created = await bot.create_application_emoji(name=name, image=fh.read())
            if name in self._config.entries:
                self._config.entries[name].id = created.id
                self._config.entries[name].animated = created.animated
            else:
                self._config.entries[name] = EmojiEntry(
                    fallback=None, id=created.id, animated=created.animated
                )
            uploaded += 1
        return uploaded


def get_game_emoji(resolver: EmojiResolver, game_key: str) -> str:
    return resolver.get_game_emoji(game_key)
