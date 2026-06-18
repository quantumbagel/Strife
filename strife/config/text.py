from __future__ import annotations

import tomllib
from pathlib import Path

from strife.logging import get_logger

log = get_logger("config.text")


class TextConfig:
    def __init__(self, data: dict[str, dict[str, str]]) -> None:
        self._data = data

    def get(self, key: str, **fmt: object) -> str:
        section, _, name = key.partition(".")
        try:
            text = self._data[section][name]
        except KeyError:
            log.warning("Missing text key: %s", key)
            return key
        if fmt:
            try:
                return text.format(**fmt)
            except (KeyError, ValueError) as exc:
                log.warning("Text format error for %s: %s", key, exc)
                return text
        return text


def load_text_config(path: Path) -> TextConfig:
    with path.open("rb") as fh:
        data = tomllib.load(fh)
    return TextConfig(data)
