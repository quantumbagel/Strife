"""Player-facing changelog files for the bot, the game API, and each plugin.

Format (TOML, newest first)::

    [[release]]
    version = "1.0.0"
    date = "2026-09-07"
    summary = "Initial release."
    added = ["A thing"]
    changed = []
    fixed = []
    removed = []

Host files live in ``changelog/bot.toml`` and ``changelog/platform.toml``.
Each plugin ships ``changelog.toml`` next to ``plugin.toml``. Missing or
invalid files are empty — they never skip plugin registration.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from strife.engine.platform import parse_version
from strife.logging import get_logger

log = get_logger("changelog")

PLUGIN_CHANGELOG = "changelog.toml"
Kind = Literal["bot", "platform", "game"]

_DATE_LEN = 10


@dataclass(frozen=True)
class Release:
    version: str
    date: str
    summary: str
    added: tuple[str, ...] = ()
    changed: tuple[str, ...] = ()
    fixed: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()

    def has_notes(self) -> bool:
        return bool(self.summary or self.added or self.changed or self.fixed or self.removed)


@dataclass(frozen=True)
class Changelog:
    kind: Kind
    key: str | None
    releases: tuple[Release, ...] = ()

    @property
    def latest(self) -> Release | None:
        return self.releases[0] if self.releases else None


def _string_list(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        text = value.strip()
        return (text,) if text else ()
    if not isinstance(value, list):
        return ()
    items: list[str] = []
    for raw in value:
        text = str(raw).strip()
        if text:
            items.append(text)
    return tuple(items)


def _valid_date(value: str) -> bool:
    if len(value) != _DATE_LEN or value[4] != "-" or value[7] != "-":
        return False
    year, month, day = value.split("-")
    return year.isdigit() and month.isdigit() and day.isdigit()


def _parse_release(raw: object, *, path: Path, index: int) -> Release | None:
    if not isinstance(raw, dict):
        log.error("%s release %s is not a table", path, index)
        return None
    version = str(raw.get("version") or "").strip()
    if parse_version(version) is None:
        log.error("%s release %s has invalid version %r", path, index, version)
        return None
    date = str(raw.get("date") or "").strip()
    if date and not _valid_date(date):
        log.error("%s release %s has invalid date %r (use YYYY-MM-DD)", path, index, date)
        date = ""
    summary = str(raw.get("summary") or "").strip()
    release = Release(
        version=version,
        date=date,
        summary=summary,
        added=_string_list(raw.get("added")),
        changed=_string_list(raw.get("changed")),
        fixed=_string_list(raw.get("fixed")),
        removed=_string_list(raw.get("removed")),
    )
    if not release.has_notes():
        log.error("%s release %s (%s) has no summary or notes", path, index, version)
        return None
    return release


def _sort_key(release: Release) -> tuple[tuple[int, int, int], str]:
    parsed = parse_version(release.version) or (0, 0, 0)
    return parsed, release.date


def load_changelog(path: Path, *, kind: Kind, key: str | None = None) -> Changelog:
    """Load a changelog file. Missing files yield an empty changelog."""
    empty = Changelog(kind=kind, key=key, releases=())
    if not path.is_file():
        return empty
    try:
        data: dict[str, Any] = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        log.error("Cannot read changelog %s: %s", path, exc)
        return empty
    raw_releases = data.get("release") or ()
    if isinstance(raw_releases, dict):
        raw_releases = [raw_releases]
    if not isinstance(raw_releases, list):
        log.error("%s: 'release' must be an array of tables", path)
        return empty
    parsed: list[Release] = []
    for index, raw in enumerate(raw_releases):
        release = _parse_release(raw, path=path, index=index)
        if release is not None:
            parsed.append(release)
    parsed.sort(key=_sort_key, reverse=True)
    return Changelog(kind=kind, key=key, releases=tuple(parsed))


class ChangelogCatalog:
    """In-memory index of host + plugin changelogs."""

    def __init__(self) -> None:
        self.bot = Changelog(kind="bot", key=None)
        self.platform = Changelog(kind="platform", key=None)
        self.games: dict[str, Changelog] = {}

    def load_host(self, directory: Path) -> None:
        self.bot = load_changelog(directory / "bot.toml", kind="bot")
        self.platform = load_changelog(directory / "platform.toml", kind="platform")

    def load_plugin(self, key: str, root: Path, *, version: str | None = None) -> None:
        changelog = load_changelog(root / PLUGIN_CHANGELOG, kind="game", key=key)
        self.games[key] = changelog
        latest = changelog.latest
        if version and latest is not None and latest.version != version:
            log.warning(
                "Plugin %s is v%s but changelog.toml latest is %s",
                key,
                version,
                latest.version,
            )

    def drop_plugin(self, key: str) -> None:
        self.games.pop(key, None)

    def load_plugins(self, records: list) -> None:
        self.games = {}
        for record in records:
            self.load_plugin(record.key, record.root, version=record.manifest.version)

    def for_game(self, key: str) -> Changelog:
        found = self.games.get(key)
        if found is not None:
            return found
        return Changelog(kind="game", key=key)

    def latest_for_game(self, key: str) -> Release | None:
        return self.for_game(key).latest
