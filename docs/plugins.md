# Plugins

Every game is a plugin: shipped builtins under `strife/games/` and third-party packages cloned into `plugins/`. The host does not treat builtins as uninstallable-protected. `config/games.yaml` still **hides** a game (`enabled: false`) without deleting history. Uninstall removes the plugin and wipes its data. Owner command shape and the enable-vs-uninstall split are also in [interfaces.md](interfaces.md#41-owner-message-commands).

## Manifest

Each plugin package has `plugin.toml` at its root:

```toml
key = "chess"
version = "1.0.0"
platform_version = "1.0.0"
dependencies = [
  "chess>=1.11.2",
  "resvg-py>=0.3.3",
]
```

The host reads this file **before** importing the package. `dependencies` are PEP 508 extras for that game only. They do not belong in the Strife `pyproject.toml`. `check_dependencies` never installs packages; install/sync does.

`key` must match `GameMetadata.key`. `platform_version` must be compatible with this host (same major, host ≥ target).

Player-facing history is a sibling `changelog.toml` (same `[[release]]` shape as `changelog/bot.toml` and `changelog/platform.toml`). Missing files are fine; the plugin still loads. `/strife about` → **Changes** shows bot, platform, and game notes.

```toml
[[release]]
version = "1.0.0"
date = "2026-09-07"
summary = "Initial release."
added = ["A thing"]
changed = []
fixed = []
removed = []
```

Newest first. `version` on the latest release should match `plugin.toml`. Empty arrays may be omitted.

## Owner commands

| Command | What it does |
|---------|----------------|
| `strife/plugins` | List builtins (active / uninstalled) and git-installed plugins |
| `strife/install <git-url> [ref]` | Clone a template-based repo, pip-install its extras, register it, set `games.yaml` `enabled: true` |
| `strife/install <key>` | Restore a shipped builtin that was uninstalled and re-enable it |
| `strife/update <key> [ref]` | Replace a git plugin's files from its recorded source without deleting match history. Refused while that game has a live match or lobby. `ref` may be a branch, tag, or commit SHA. |
| `strife/uninstall <key> confirm` | Abandon live matches/lobbies, remove files or mark the builtin removed, **then** delete matches/replays/stats for that `game_key`, unregister, pip-uninstall extras no other plugin needs. If the plugin is already gone, the same command wipes leftover history. |

Builtin keys are reserved. You cannot install a git plugin whose `plugin.toml` key collides with a shipped game; restore the builtin instead. To refresh an already-installed git plugin, use `strife/update`, not uninstall+install.

After a git install or update, run `strife/emoji` if the repo had an `emoji/` folder, and `strife/sync` if it declared `slash_moves`. Plugin emoji stay in `emoji/` inside the plugin package and are uploaded as `{key}_{stem}` (so `emoji/duke.webp` in Coup becomes `coup_duke`). They never copy into `assets/emoji/`.

## Overlay

`config/plugins.yaml` (mounted in Docker with the rest of `config/`):

```yaml
removed: []          # builtin keys the operator uninstalled
installed: {}        # git plugins: key → {source, ref}
```

Uninstalling a builtin does not delete it from the image. It adds the key to `removed` so discovery skips it, and sets `games.<key>.enabled: false`. `strife/install chess` clears that mark, pip-installs Chess extras again, and sets `enabled: true` (preserving other fields such as timeouts).

Git plugins live in `plugins/<key>/` (compose mounts `./plugins`). Uninstall deletes that directory (including its `emoji/` folder). It will **fail** (and leave history alone) if the directory cannot be deleted. If the plugin is already removed but matches or stats remain, re-run `strife/uninstall <key> confirm` to wipe that leftover data. `strife/emoji` uploads plugin files as `{key}_{stem}` and platform files from `assets/emoji/` using the names in `strife/presentation/base_emojis.py`. Host installs of git plugins require `git` on PATH.

`config/games.yaml` is parsed as YAML. Install/restore/update set `enabled: true`; uninstall sets `enabled: false` without dropping the rest of the entry.

## Dependencies

On boot, if `STRIFE_SYNC_PLUGIN_DEPS` is true (default), the host pip-installs **missing** extras for currently active plugins (off the event loop). A requirement is missing when the distribution is absent **or** its installed version does not satisfy the specifier (`chess>=1.11.2`). The Docker image also runs `python -m strife.plugins sync-deps --builtins-only` at build so shipped games with extras (Chess) work on first start.

Uninstall pip-uninstalls an extra only when no remaining active plugin lists it, and never uninstalls the host package, its recursive requires, or a seed set of platform libraries (discord.py, asyncpg, Pydantic, pip, …).

## Trust

Plugins still run **in-process**. `strife/install` is owner-only. Review a third-party repo before installing it; this is not a security sandbox.

## Authoring

- In-tree builtin: `python scripts/scaffold_game.py my_game "My Game"` (writes `plugin.toml` and `changelog.toml`)
- Third-party: publish `templates/game-plugin/` as a **GitHub template repository** (Settings → General → Template repository). Authors click **Use this template**, implement the game, then an owner runs `strife/install https://github.com/you/your-game`
