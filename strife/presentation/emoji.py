from __future__ import annotations

from pathlib import Path

import discord

from strife.config.emoji import EmojiConfig, EmojiEntry, load_emoji_config, save_emoji_config
from strife.logging import get_logger

log = get_logger("presentation.emoji")


class EmojiResolver:
    def __init__(self, config: EmojiConfig) -> None:
        self._config = config

    @property
    def config(self) -> EmojiConfig:
        return self._config

    def reload(self, config: EmojiConfig) -> None:
        self._config = config

    def get(self, name: str) -> str:
        entry = self._config.entries.get(name)
        if entry is None:
            log.warning("Unknown emoji: %s", name)
            fallback = self._config.entries.get("error_cross")
            return fallback.fallback if (fallback and fallback.fallback) else "❓"
        if entry.id is not None:
            prefix = "a" if entry.animated else ""
            return f"<{prefix}:{name}:{entry.id}>"
        return entry.fallback if entry.fallback is not None else "❓"

    def resolve(self, name: str) -> str:
        return self.get(name)

    def general(self, name: str) -> str:
        return self.get(name)

    def button(self, name: str) -> str:
        return self.get(name)

    def game(self, key: str, name: str) -> str:
        if name in self._config.entries:
            return self.get(name)
        return self.get(key)

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
                self._config.entries[name] = EmojiEntry(fallback=None, id=emoji.id, animated=emoji.animated)
                
        if self._config.path:
            save_emoji_config(self._config)
            
        return self._config

    async def reupload(self, bot: discord.Client, assets_dir: Path) -> int:
        existing = await bot.fetch_application_emojis()
        for emoji in existing:
            await emoji.delete()

        uploaded = 0
        if not assets_dir.exists():
            return uploaded

        for path in sorted(assets_dir.iterdir()):
            if not path.is_file() or path.suffix.lower() not in {".png", ".gif", ".jpg", ".jpeg", ".webp"}:
                continue
            name = path.stem
            with path.open("rb") as fh:
                created = await bot.create_application_emoji(name=name, image=fh.read())
            if name in self._config.entries:
                self._config.entries[name].id = created.id
                self._config.entries[name].animated = created.animated
            uploaded += 1

        if self._config.path:
            save_emoji_config(self._config)
        return uploaded
