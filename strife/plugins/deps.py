from __future__ import annotations

import importlib.metadata
import re
import subprocess
import sys
from collections.abc import Iterable, Sequence
from functools import lru_cache
from pathlib import Path

from packaging.requirements import InvalidRequirement, Requirement
from packaging.version import InvalidVersion, Version

from strife.logging import get_logger
from strife.plugins.errors import PluginError
from strife.plugins.manifest import load_manifest, scan_plugin_toml

log = get_logger("plugins.deps")

# Seed set. host_protected_distributions() also walks requires of these + strife.
PLATFORM_DISTRIBUTIONS = frozenset(
    {
        "discord-py",
        "asyncpg",
        "msgpack",
        "pyyaml",
        "pydantic",
        "pydantic-settings",
        "packaging",
        "pip",
        "setuptools",
        "wheel",
        "strife",
    }
)


def distribution_name(requirement: str) -> str:
    """Return the distribution name from a PEP 508 requirement string."""
    return _parse_requirement(requirement).name


def normalize_dist(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _parse_requirement(requirement: str) -> Requirement:
    text = requirement.strip()
    if not text or text.startswith("-"):
        raise PluginError(f"Invalid requirement {requirement!r}")
    try:
        return Requirement(text)
    except InvalidRequirement as exc:
        raise PluginError(f"Invalid requirement {requirement!r}") from exc


def _requirement_installed(requirement: str) -> bool:
    req = _parse_requirement(requirement)
    if req.marker is not None and not req.marker.evaluate():
        return True
    try:
        dist = importlib.metadata.distribution(req.name)
    except importlib.metadata.PackageNotFoundError:
        return False
    if not req.specifier:
        return True
    try:
        version = Version(dist.version)
    except InvalidVersion:
        return False
    return version in req.specifier


def check_dependencies(dependencies: Sequence[str]) -> bool:
    """Return True when every requirement is already installed. Never installs packages."""
    missing = missing_dependencies(dependencies)
    if missing:
        log.error(
            "Missing plugin dependencies (install via plugin sync / strife/install, not from game code): %s",
            ", ".join(missing),
        )
        return False
    return True


def missing_dependencies(dependencies: Sequence[str]) -> list[str]:
    if not dependencies:
        return []
    return [dep for dep in dependencies if not _requirement_installed(dep)]


def collect_requirements(roots: Iterable[Path]) -> list[str]:
    seen: set[str] = set()
    reqs: list[str] = []
    for root in roots:
        for toml_path in scan_plugin_toml(root):
            try:
                manifest = load_manifest(toml_path)
            except PluginError as exc:
                log.error("%s", exc)
                continue
            for dep in manifest.dependencies:
                key = dep.strip()
                if key and key not in seen:
                    seen.add(key)
                    reqs.append(key)
    return reqs


def pip_install(requirements: Sequence[str]) -> None:
    if not requirements:
        return
    log.info("pip install %s", " ".join(requirements))
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", *requirements],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise PluginError(f"pip install failed for {list(requirements)}: {detail[-800:]}")


def pip_uninstall(distributions: Sequence[str]) -> None:
    protected = host_protected_distributions()
    names = [name for name in distributions if normalize_dist(name) not in protected]
    if not names:
        return
    log.info("pip uninstall %s", " ".join(names))
    result = subprocess.run(
        [sys.executable, "-m", "pip", "uninstall", "-y", *names],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        log.warning("pip uninstall failed for %s: %s", names, detail[-800:])


def _requires_of(dist_name: str) -> list[str]:
    try:
        dist = importlib.metadata.distribution(dist_name)
    except importlib.metadata.PackageNotFoundError:
        return []
    names: list[str] = []
    for raw in dist.requires or []:
        try:
            req = Requirement(raw)
        except InvalidRequirement:
            continue
        if req.marker is not None and not req.marker.evaluate():
            continue
        names.append(normalize_dist(req.name))
    return names


@lru_cache(maxsize=1)
def host_protected_distributions() -> frozenset[str]:
    """Distributions the host (and its recursive requires) needs. Never pip-uninstall these."""
    seed = {normalize_dist(name) for name in PLATFORM_DISTRIBUTIONS}
    seen: set[str] = set()
    stack = list(seed)
    while stack:
        name = stack.pop()
        if name in seen:
            continue
        seen.add(name)
        for dep in _requires_of(name):
            if dep not in seen:
                stack.append(dep)
    return frozenset(seen)


def orphan_distributions(
    removing: Sequence[str],
    remaining: Sequence[str],
) -> list[str]:
    still_needed = {normalize_dist(distribution_name(req)) for req in remaining}
    still_needed |= host_protected_distributions()
    orphans: list[str] = []
    seen: set[str] = set()
    for req in removing:
        name = distribution_name(req)
        normalized = normalize_dist(name)
        if normalized in still_needed or normalized in seen:
            continue
        seen.add(normalized)
        orphans.append(name)
    return orphans
