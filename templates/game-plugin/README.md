# Strife game plugin

GitHub template for a Strife game. Create a repo from it, then install that repo on a running bot.

This package is not standalone. You need [Strife](https://github.com/quantumbagel/Strife) (Python **3.14**) — a checkout to try locally, or a running bot for `strife/install`. Keep `plugin.toml` at the repository root; install clones this root.

## 1. Use the template

GitHub: **Use this template** → **Create a new repository**. Don’t fork.

Replace `TODO` / `my_game` everywhere, and put your name in `LICENSE`:

| File | Change |
|------|--------|
| `plugin.toml` | `key`, `version`, `platform_version`, `dependencies` |
| `game.py` | `@game_metadata_from(...)` (`key` matches `plugin.toml`) and the class name |
| `__init__.py` | `GAME =` that class |
| `changelog.toml` | First `[[release]]` — `version` matches `plugin.toml`; set `date` |
| `LICENSE` | Copyright holder |

`key` is lowercase (`letter`, then letters/digits/underscore, max 32). It’s the Discord slash-group name if you add `slash_moves`.

## 2. Implement

Import from `strife.engine` and `strife.presentation` only.

Art goes in `emoji/`. Catalog uses **`emoji/game.webp`** (`{key}_game`). Extra stems become `{key}_{stem}`. In `play()`, `ctx.emoji.get("token")` is your file. Platform chrome uses `ctx.emoji.get("loading", base=True)`. After install, `strife/emoji` if you shipped `emoji/`.

Third-party libs: `plugin.toml` `dependencies` (PEP 508). The host pip-installs them at boot, not during live `strife/install`. Don’t add a `pyproject.toml` / `requirements.txt` for extras.

Ship a release: bump `version` in `plugin.toml`, add a `[[release]]` at the top of `changelog.toml`. Don’t put semver on `GameMetadata`.

Optional:

- Lobby knobs: `SettingOption`, read with `self.setting("key")`
- Peek: `Button(..., query=True)` and `handle_query` → `ctx.respond_query`
- Text moves: `slash_moves=` on metadata, then `strife/sync`
- Heavier bots: a `bot.py` used from `bot_move`

## 3. Install

```
strife/install https://github.com/you/your-game
```

Optional ref: `strife/install https://github.com/you/your-game v1.2.0`

Then `strife/emoji` if you shipped `emoji/`, `strife/sync` if you declared `slash_moves`. The game appears in `/play` once `games.yaml` has `enabled: true` (install creates that row if the key is new).

Later: `strife/update <key> [ref]` (keeps match history).

## Layout

```
plugin.toml       # key, version, platform_version, dependencies
changelog.toml    # /strife about → Changes
LICENSE
__init__.py       # GAME = the class
game.py
emoji/            # optional; catalog icon is game.webp
```

## Docs

In the [Strife](https://github.com/quantumbagel/Strife) repo:

- [Game development](https://github.com/quantumbagel/Strife/blob/main/docs/game-development.md)
- [Game API](https://github.com/quantumbagel/Strife/blob/main/docs/game-api.md)
- [Plugins](https://github.com/quantumbagel/Strife/blob/main/docs/plugins.md)
- [Interfaces](https://github.com/quantumbagel/Strife/blob/main/docs/interfaces.md)

## Local check

From a **Strife** checkout, copy this package to `plugins/<key>/`:

```
python scripts/run_game.py <key>
python scripts/run_game.py <key> --replay
```
