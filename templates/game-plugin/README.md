# Strife game plugin

Use this repository as a **GitHub template**, implement a game against the Strife plugin API, then install it on a running bot:

```
strife/install https://github.com/you/your-game
```

## Layout

```
plugin.toml     # required: key, version, platform_version, dependencies
__init__.py     # export the Game subclass
game.py         # rules + metadata
emoji/          # optional .webp / .png; uploaded as {key}_{stem} (token.webp → my_game_token)
```

`plugin.toml` is the install-time source of truth. The host reads it **before** importing your package so it can `pip install` extras. Do not import those extras at module top until they are listed here. Do not call `pip` from game code.

`key` in `plugin.toml` must match `GameMetadata.key` and the Discord slash-group name if you add `slash_moves`.

## Contract

Import from `strife.engine` and `strife.presentation` only. Do not import `strife.session`, `strife.bot`, matchmaking, routing, or persistence.

See the main Strife repo:

- `docs/game-api.md` — method contract
- `docs/game-development.md` — tutorial
- `docs/game-architecture.md` — host/plugin boundary
- `docs/plugins.md` — install / update / uninstall

## Local check

From a Strife checkout (with this plugin copied to `plugins/<key>/`):

```
python scripts/run_game.py <key>
```

On the bot, after install: `/play`, and `strife/emoji` if you shipped an `emoji/` folder. If the game has slash commands, `strife/sync`.

In `play()`, `ctx.emoji.get("token")` resolves your plugin file. Platform chrome (`loading`, `error`, `success`, …) is the set in Strife's `strife/presentation/base_emojis.py`; pass `base=True` to use those and never collide with a plugin stem of the same name.
