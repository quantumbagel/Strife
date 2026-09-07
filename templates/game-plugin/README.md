# Strife game plugin

This repository is a **GitHub template**. Create a new game from it, then install that repo on a running bot.

## 1. Use the template

On GitHub: **Use this template** → **Create a new repository**. Do not fork.

Then replace `my_game` everywhere:

| File | What to change |
|------|----------------|
| `plugin.toml` | `key`, `version`, `platform_version`, `dependencies` |
| `game.py` | `@game_metadata_from(...)` (`key` must match `plugin.toml`) and the class name |
| `__init__.py` | Export that class |
| `changelog.toml` | First `[[release]]` — keep `version` in sync with `plugin.toml` |

`key` is a lowercase identifier (`letter`, then letters/digits/underscore, max 32). It is the Discord slash-group name if you add `slash_moves`.

## 2. Implement the game

Import from `strife.engine` and `strife.presentation` only. Do not import `strife.session`, `strife.bot`, matchmaking, routing, or persistence.

Put art in `emoji/` (`game.webp` plus pieces/roles). The host uploads them as `{key}_{stem}` (`emoji/token.webp` → `my_game_token`). In `play()`, `ctx.emoji.get("token")` resolves your file. Platform chrome (`loading`, `error`, `success`, `peek`, …) uses `ctx.emoji.get("loading", base=True)`.

List third-party libraries in `plugin.toml` `dependencies` (PEP 508). The host pip-installs them at `strife/install`. Do not import those extras at module top until they are listed here. Do not call `pip` from game code.

When you ship a release, bump `version` in **both** `plugin.toml` and `GameMetadata`, add a `[[release]]` at the top of `changelog.toml`, and keep `platform_version` on the API you actually use.

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
plugin.toml       # required: key, version, platform_version, dependencies
changelog.toml    # required for in-bot Changes; newest [[release]] first
__init__.py       # export the Game subclass
game.py           # rules + metadata
emoji/            # optional .webp / .png
```

`plugin.toml` is the install-time source of truth. The host reads it **before** importing your package.

## Contract

See the main Strife repo:

- `docs/game-api.md` — method contract
- `docs/game-development.md` — tutorial
- `docs/game-architecture.md` — host/plugin boundary
- `docs/plugins.md` — install / update / uninstall
- `docs/interfaces.md` — player / operator / author contract

## Local check

From a Strife checkout, copy this package to `plugins/<key>/` (folder name does not have to match `key`, but `plugin.toml` `key` must):

```
python scripts/run_game.py <key>
```
