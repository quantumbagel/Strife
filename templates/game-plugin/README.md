# Strife game plugin

This repository is a **GitHub template**. Create a new game from it, then install that repo on a running bot.

The package is **not standalone**. You need [Strife](https://github.com/quantumbagel/Strife) (Python **3.14**) — a checkout to try locally, or a running bot to `strife/install`. `strife/install` clones this **repository root**; keep `plugin.toml` at the root.

## 1. Use the template

On GitHub: **Use this template** → **Create a new repository**. Do not fork.

Replace `TODO` / `my_game` everywhere, and put your name in `LICENSE` before you publish:

| File | What to change |
|------|----------------|
| `plugin.toml` | `key`, `version`, `platform_version`, `dependencies` |
| `game.py` | `@game_metadata_from(...)` (`key` must match `plugin.toml`) and the class name |
| `__init__.py` | Export that class |
| `changelog.toml` | First `[[release]]` — `version` matches `plugin.toml`; set `date` |
| `LICENSE` | Copyright holder |

`key` is a lowercase identifier (`letter`, then letters/digits/underscore, max 32). It is the Discord slash-group name if you add `slash_moves`.

## 2. Implement the game

Import from `strife.engine` and `strife.presentation` only. Do not import `strife.session`, `strife.bot`, matchmaking, routing, or persistence.

Put art in `emoji/`. Catalog uses **`emoji/game.webp`** (uploaded as `{key}_game`). Without it, catalog falls back to the platform game icon. Extra stems (`token.webp`) become `{key}_{stem}`. In `play()`, `ctx.emoji.get("token")` resolves your file. Platform chrome (`loading`, `error`, `success`, `peek`, …) uses `ctx.emoji.get("loading", base=True)`. After install, run `strife/emoji` if you shipped an `emoji/` folder.

List third-party libraries in `plugin.toml` `dependencies` (PEP 508). The host pip-installs them at `strife/install`. Do not import those extras at module top until they are listed here. Do not call `pip` from game code. Do not add a `pyproject.toml` / `requirements.txt` for extras.

When you ship a release, bump `version` in **both** `plugin.toml` and `GameMetadata`, add a `[[release]]` at the top of `changelog.toml`, and keep `platform_version` on the API you actually use.

Optional patterns (see shipped games in the Strife repo, not required here):

- Lobby knobs: `SettingOption` on metadata, read with `self.setting("key")`
- Peek / help: `Button(..., query=True)` and `handle_query` → `ctx.respond_query`
- Text moves: `slash_moves=` on metadata, then `strife/sync`
- Heavier bots: a `bot.py` module used from `bot_move`

## 3. Install on the bot

An owner runs:

```
strife/install https://github.com/you/your-game
```

Optional ref (branch, tag, or SHA): `strife/install https://github.com/you/your-game v1.2.0`

Then:

- `strife/emoji` if you shipped `emoji/`
- `strife/sync` if the game declares `slash_moves`
- `/play` — the game appears once `games.yaml` has `enabled: true` (install sets that)

Update later with `strife/update <key> [ref]`. That keeps match history.

## Layout

```
plugin.toml       # required at repo root: key, version, platform_version, dependencies
changelog.toml    # shown in /strife about → Changes; newest [[release]] first
LICENSE           # replace the TODO copyright holder
__init__.py       # export the Game subclass
game.py           # rules + metadata
emoji/            # optional; catalog icon is game.webp
```

`plugin.toml` is the install-time source of truth. The host reads it **before** importing your package.

## Contract

From the [Strife](https://github.com/quantumbagel/Strife) repo:

- [Game API](https://github.com/quantumbagel/Strife/blob/main/docs/game-api.md) — method contract
- [Game development](https://github.com/quantumbagel/Strife/blob/main/docs/game-development.md) — tutorial
- [Game architecture](https://github.com/quantumbagel/Strife/blob/main/docs/game-architecture.md) — host/plugin boundary
- [Plugins](https://github.com/quantumbagel/Strife/blob/main/docs/plugins.md) — install / update / uninstall
- [Interfaces](https://github.com/quantumbagel/Strife/blob/main/docs/interfaces.md) — player / operator / author contract

## Local check

From a **Strife** checkout (not this repo), copy this package to `plugins/<key>/` (folder name does not have to match `key`, but `plugin.toml` `key` must):

```
python scripts/run_game.py <key>
python scripts/run_game.py <key> --replay
```
