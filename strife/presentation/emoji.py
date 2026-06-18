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

    def _lookup(self, bucket: dict, name: str) -> str:
        entry = bucket.get(name)
        if entry is None:
            log.warning("Unknown emoji: %s", name)
            fallback = self._config.general.get("error_cross")
            return fallback.fallback if (fallback and fallback.fallback) else "❓"
        if entry.id is not None:
            return f"<:{name}:{entry.id}>"
        return entry.fallback if entry.fallback is not None else "❓"

    def resolve(self, name: str) -> str:
        for bucket in (self._config.general, self._config.button, self._config.game):
            if name in bucket:
                return self._lookup(bucket, name)
        return self._lookup(self._config.general, "error_cross")

    def general(self, name: str) -> str:
        return self._lookup(self._config.general, name)

    def button(self, name: str) -> str:
        return self._lookup(self._config.button, name)

    def game(self, key: str, name: str) -> str:
        return self._lookup(self._config.game, name if name in self._config.game else key)

    async def sync(self, bot: discord.Client) -> EmojiConfig:
        emojis = await bot.fetch_application_emojis()
        by_name = {emoji.name: emoji.id for emoji in emojis}
        
        seen_names = set()
        for bucket in (self._config.general, self._config.button, self._config.game):
            for name, entry in bucket.items():
                entry.id = by_name.get(name)
                seen_names.add(name)
                
        for name, emoji_id in by_name.items():
            if name not in seen_names:
                self._config.general[name] = EmojiEntry(fallback=None, id=emoji_id)
                
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
            for bucket in (self._config.general, self._config.button, self._config.game):
                if name in bucket:
                    bucket[name].id = created.id
            uploaded += 1

        if self._config.path:
            save_emoji_config(self._config)
        return uploaded
