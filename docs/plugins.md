# Plugins

Every game is a plugin: builtins in `strife/games/`, third-party clones in `plugins/`. Builtins can be uninstalled too.

`config/games.yaml` `enabled: false` **hides** a game and keeps history. Uninstall removes the plugin and wipes its data. Owner command shape: [interfaces.md](interfaces.md#41-owner-message-commands).

## Manifest

`plugin.toml` at the package root, read **before** import:

```toml
key = "chess"
version = "1.1.0"
platform_version = "1.0.0"
dependencies = [
  "chess>=1.11.2",
  "resvg-py>=0.3.3",
]
```

`dependencies` are PEP 508 extras for that game only — not the Strife `pyproject.toml`. Boot / `python -m strife.plugins sync-deps` installs them; `check_dependencies` only skips the plugin if they’re missing.

`key` must match `GameMetadata.key`. `version` and `platform_version` are stamped from this file. `platform_version` must match this host (same major, host ≥ target).

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
| `strife/plugins` | List builtins and git plugins |
| `strife/install <git-url> [ref]` | Clone, register, create a `games.yaml` row if the key is new (`enabled: true`). Missing extras load on next boot |
| `strife/install <key>` | Restore an uninstalled builtin. Does not un-hide `enabled: false` |
| `strife/update <key> [ref]` | Replace git files; keep history. Refused while that game has a live match or lobby |
| `strife/uninstall <key> confirm` | Stop live games, remove files, **then** delete matches/stats. If the plugin is already gone, wipes leftover history |

You can’t install a git plugin whose key collides with a shipped game — restore the builtin instead. To refresh an installed git plugin, `strife/update`, not uninstall+install.

After a git install/update: `strife/emoji` if it had `emoji/`, `strife/sync` if it declared `slash_moves`. Plugin emoji stay in the package (`emoji/duke.webp` in Coup → `coup_duke`). They never copy into `assets/emoji/`.

## Overlay

`config/plugins.yaml`:

```yaml
removed: []          # uninstalled builtins
installed: {}        # git plugins: key → {source, ref}
```

Uninstalling a builtin adds it to `removed`; the files stay in the image. `games.yaml` is hide-vs-show, not installed-vs-not. `strife/install chess` clears the removed mark. A missing `games.yaml` row is created as `enabled: true`; an existing row (including `enabled: false`) is left alone.

Git plugins live in `plugins/<key>/` (compose mounts `./plugins`). Uninstall deletes that directory and **fails** (history untouched) if it can’t. Host installs need `git` on PATH.

`strife/emoji` uploads plugin files as `{key}_{stem}` and platform files from `assets/emoji/` using names in `strife/presentation/base_emojis.py`.

## Dependencies

If `STRIFE_SYNC_PLUGIN_DEPS` is true (default), boot pip-installs **missing** extras for active plugins (off the event loop). Missing = distribution absent or version doesn’t match (`chess>=1.11.2`). The Docker image runs `python -m strife.plugins sync-deps --builtins-only` at build so Chess works on first start.

Live install/update/uninstall do **not** call pip. If extras are missing after install, files stay and the owner is told to restart. Extras are never pip-uninstalled from a running bot.

## Trust

Plugins run in-process. Review a third-party repo before installing it.

## Authoring

- Builtin: `python scripts/scaffold_game.py my_game "My Game"`
- Third-party: GitHub **Use this template** on `templates/game-plugin/`, then `strife/install https://github.com/you/your-game`
