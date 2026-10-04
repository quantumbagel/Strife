# Plugins

Every game is a plugin: builtins in `strife/games/`, third-party clones in `plugins/`. Builtins can be uninstalled too.

`config/games.yaml` `enabled: false` **hides** a game and keeps history. Uninstall removes the plugin and wipes its data. Owner command shape: [interfaces.md](interfaces.md#41-owner-message-commands).

## Manifest

`plugin.toml` at the package root, read **before** import:

```toml
key = "chess"
version = "1.2.1"
platform_version = "3.0.0"
dependencies = [
  "chess>=1.11.2",
  "resvg-py>=0.3.3",
]
```

`dependencies` are PEP 508 extras for that game only — not the Strife `pyproject.toml`. Boot / `python -m strife.plugins sync-deps` installs them. Plugins with missing extras are skipped at load (`missing_dependencies`).

`key` must match `GameMetadata.key`. `version` and `platform_version` are required, must be versions (`major.minor.patch`, minor/patch optional), and are stamped from this file. `platform_version` must match this host (same major, host ≥ target). Duplicate keys are rejected.

Requirements cannot name host packages (`discord.py` / `discord-py`, `asyncpg`, `msgpack`, `PyYAML`, `pydantic`, `pydantic-settings`, `packaging`, `pip`, `setuptools`, `wheel`, `strife`, plus their recursive requires — see `host_protected_distributions` in [`strife/plugins/deps.py`](../strife/plugins/deps.py)) or be URL, VCS, or path specs.

`changelog.toml` is optional to load. Newest `[[release]]` first; latest `version` should match `plugin.toml`. Shown in `/strife about` → Changes.

```toml
[[release]]
version = "1.0.0"
date = "2026-09-07"
summary = "Initial release."
added = ["A thing"]
```

Empty arrays may be omitted.

## Owner commands

| Command | What it does |
|---------|----------------|
| `strife/plugins` | List builtins and git plugins, with loaded / NOT loaded / hidden status |
| `strife/install <git-url> [ref]` | Clone, register, create a `games.yaml` row if the key is new (`enabled: true`), updates the slash tree (published right away only with `STRIFE_SYNC_ON_START=true`; otherwise run `strife/sync`). `github.com/you/repo` (no scheme) gets `https://`. If the plugin fails to load, the folder and both rows are rolled back and the error is shown. Missing extras load on next boot |
| `strife/install <key>` | Restore an uninstalled builtin. Does not un-hide `enabled: false` |
| `strife/update <key> [ref]` | Replace git files; keep history. Refused while that game has a live match (in memory or stored) or lobby. If the new code fails to load for any reason (including missing extras), the old files and ref are restored. With no `ref`, reuses the ref recorded at install/last update (the reply says which). Then updates the slash tree (published right away only with `STRIFE_SYNC_ON_START=true`; otherwise run `strife/sync`) |
| `strife/uninstall <key> confirm` | Stop live games, remove files, **then** delete matches/stats, updates the slash tree (published right away only with `STRIFE_SYNC_ON_START=true`; otherwise run `strife/sync`). If the plugin is already gone, wipes leftover history |

`ref` may be a branch, tag, or commit SHA (7–40 hex). A short SHA clones full history to resolve it and is recorded as the full SHA. A pinned commit stays pinned: `strife/update <key> main` moves it onto a branch.

You can’t install a git plugin whose key collides with a shipped game — restore the builtin instead. To refresh an installed git plugin, `strife/update`, not uninstall+install.

After a git install/update, run `strife/sync` unless `STRIFE_SYNC_ON_START=true` (then the host syncs for you). Run `strife/emoji` if it had `emoji/`. Plugin emoji stay in the package (`emoji/duke.webp` in Coup → `coup_duke`). They never copy into `assets/emoji/`.

A live match stores a plugin **build** fingerprint (content hash of `*.py` and `plugin.toml`). After an update, the next boot resumes a live match only if that hash still matches.

## Overlay

`config/plugins.yaml`:

```yaml
removed: []          # uninstalled builtins
installed: {}        # git plugins: key → {source, ref}
```

Uninstalling a builtin adds it to `removed`; the files stay in the image. `games.yaml` is hide-vs-show, not installed-vs-not. `strife/install chess` clears the removed mark. A missing `games.yaml` row is created as `enabled: true`; an existing row (including `enabled: false`) is left alone. A loaded game with no row at all (e.g. a folder copied into `plugins/`) is enabled with default tuning.

Git plugins live in `plugins/<key>/` (compose mounts `./plugins`). During `strife/update` the old copy sits in `plugins/.<key>.previous/` until the new code loads; dot-folders are never loaded. Uninstall deletes that directory and **fails** (history untouched) if it can’t. Host installs need `git` on PATH.

`strife/emoji` replaces emoji one name at a time and deletes stale names; a failed upload leaves that name on its Unicode `fallback` and is listed in the reply. It uploads plugin files as `{key}_{stem}` and platform files from `assets/emoji/` using names in `strife/presentation/base_emojis.py`.

## Dependencies

If `STRIFE_SYNC_PLUGIN_DEPS` is true (default), boot pip-installs **missing** extras for active plugins (off the event loop). Missing = distribution absent or version doesn’t match (`chess>=1.11.2`). The Docker image runs `python -m strife.plugins sync-deps --builtins-only` at build so Chess works on first start.

Live install/update/uninstall do **not** call pip. If extras are missing after install, files stay and the owner is told to restart. A failed update still restores the previous files. Extras are never pip-uninstalled from a running bot.

## Trust

Plugins run in-process. Review a third-party repo before installing it.

## Authoring

- Builtin: `python scripts/scaffold_game.py my_game "My Game"`
- Third-party: GitHub **Use this template** on `templates/game-plugin/`, then `strife/install https://github.com/you/your-game`
