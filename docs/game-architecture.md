# How games plug in

A game is an in-process plugin. It talks to the host only through `GameContext` and Discord-free layout objects. Same `play()` runs live, in replay, and on the CLI.

This is not a security sandbox. `strife/install` is owner-only; review third-party repos. A buggy `play()` should ruin **that match**, not the bot.

Player/operator surfaces: [interfaces.md](interfaces.md). Methods: [game-api.md](game-api.md). Tutorial: [game-development.md](game-development.md).

```
Discord / CLI / replay
        │
   commands, catalog, routing
        │
   LiveContext / ReplayContext / MockContext
        │
   GameContext          ← games stop here
        │
   Game subclass
```

## Plugin layout

```
strife/games/tictactoe/     # or plugins/my_game/
  plugin.toml
  changelog.toml            # /strife about → Changes
  __init__.py               # GAME = the class
  game.py
  bot.py                    # optional
  emoji/                    # optional; {key}_{stem}
```

`plugin.toml` is read **before** import (`key`, `version`, `platform_version`, `dependencies`). The host stamps versions onto metadata. `metadata.key` must match.

Games may import `strife.engine`, `strife.presentation`, and extras listed in `plugin.toml`. Not matchmaking, routing, persistence, `strife.session`, or `strife.bot`.

## Load

On boot, `PluginManager` reads `strife/games/*/plugin.toml` (skip `plugins.yaml` `removed`) and `plugins/*/plugin.toml`, optionally pip-installs missing extras, then imports and registers `GAME`.

Skipped (logged, not fatal): missing `GAME`, bad versions, missing extras, capability mismatch (`supports_replay` but no `parse_replay`), duplicate key, import error.

`registry.create(key, players, settings, seed)` builds `random.Random(seed)` and constructs the class. Replays use the same seed.

A registered game can still be hidden. `config/games.yaml` `enabled: false` (or a missing key) keeps it out of `/play` and the catalog so old replays still load. Uninstall drops the plugin and deletes that game’s history. See [plugins.md](plugins.md).

```yaml
games:
  tictactoe:
    enabled: true
    turn_timeout_seconds: 60
  test:
    enabled: false
```

Unknown keys are `enabled: false`. A newly scaffolded game registers but won’t appear until you add a row.

| Field | Effect |
|-------|--------|
| `enabled` | Catalog, `/play`, new lobbies |
| `turn_timeout_*` | Copied onto the session |
| `play_hang_seconds` | Cancel `play()` if it stops talking to the host (default 45) |
| `settings_overrides` | Operator defaults over metadata |

## What games can call

| Surface | For |
|---------|-----|
| `ctx.rng`, `ctx.players`, `ctx.settings`, `ctx.emoji` | Seeded RNG, seats, lobby settings, emoji keys |
| `ctx.started_at`, `ctx.is_replay`, `ctx.is_bot(seat)` | Clock, hide controls, skip DMs |
| `self.setting(key)` | Setting with metadata default (on `Game`, not `ctx`) |
| `ctx.update(view)` | Edit the board |
| `ctx.request_input` / `request_inputs` | Wait for a move (or `bot_move`) |
| `ctx.send_private(seat, view)` | DM (thread notice if DMs fail) |
| `ctx.record_event` | One `game` log row |
| `ctx.respond_query(view)` | Peek panel, only inside `handle_query` |

A click becomes `Move(actor_seat, source, args)` after the host checks seat and allowed sources. Attachments are `ViewFile` bytes.

Don’t import `discord`, encode `custom_id`s, set `route_prefix` on board controls, touch the database, or emit system log names (`forfeit`, `game_end`, `bot_takeover`, `timeout`).

Layout is dataclasses (`LayoutView`, `Button`, …), not `discord.ui`. Emoji is a string key. The host compiles, signs `custom_id`s, and sends. Limits (40 components, 4000 chars, 100-char ids) fail in the host.

A live match has two messages: header (`replay_noop:`, clicks do nothing) and board (`g_move:`, `resource_id` = thread id).

## Three hosts

| Host | Context | Input | Used by |
|------|---------|-------|---------|
| Live | `LiveContext` | Wait on pending futures; bots via `bot_move` (10s) | `game.play(ctx)` |
| Replay | `ReplayContext` | `request_*` raise | `parse_replay` |
| CLI | `MockContext` | stdin / `--move` | `python scripts/run_game.py <key>` |

Replay never runs `play()`. New instance, stored seed, `parse_replay`. Prefer `TurnBasedGame` or `iter_replay` + `ReplayBuilder`.

Timeouts, forfeits, persist, rematch, and thread lock stay in the session.

## Discord exposure

Catalog and `/play` autocomplete = registered games with `enabled: true`. Catalog Play is not a game move (`cat_nav:` + `{"play": key}`).

Cards come from `GameMetadata` only. The class is constructed when a lobby starts. Settings UI comes from `metadata.settings`.

`slash_moves` become `/<key> …` (Chess: `/chess move`). The callback completes the same pending input a button would. Disabled games don’t get slash groups until re-enabled + `strife/sync`.

## Click path

`custom_id` → `Route(prefix, resource_id, source, payload)`. Game prefixes:

1. `session.handle_query` — if it returns True, done (not logged)
2. else `session.submit` → pending `request_input` future

Stale sessions disable the components and report `common.game_ended`.

Lobby start: `registry.create` → public thread `{Game} (#{code})` → header + board → `game.play(ctx)`. Exceptions in `play` abandon that match. Caps: `bot_move` 10s, `handle_query` 5s, `final_view` 5s, `parse_replay` 15s. If `play()` yields but never touches `GameContext` for `play_hang_seconds`, the session is cancelled.

A tight CPU loop (no await) still freezes the process. Use `run_cpu` for heavy work. A native crash kills the process; Docker restarts it with no live sessions.

## Adding a game (host side)

1. `plugin.toml` + `GAME` export. `changelog.toml` for Changes
2. Key matches, versions valid, extras installed, not in `plugins.yaml` `removed`
3. `config/games.yaml` `games.<key>.enabled: true`
4. Optional `strife/emoji` and `strife/sync` for `slash_moves`

No edits to `bot.py`, the router, or `strife.session`.

## Also

- [game-api.md](game-api.md) — methods and move log
- [game-development.md](game-development.md) — tutorial
- [plugins.md](plugins.md) — install / uninstall
- `scripts/scaffold_game.py`, `templates/game-plugin/`, `scripts/run_game.py`
- `strife/games/tictactoe/` — smallest complete game
- `strife/games/chess/` — slash moves, extras, file attachments
