# Game Exposure and Sandbox Architecture

This document describes how a Strife game is **plugged into** the platform and **isolated from** Discord, persistence, and matchmaking. It is about the host/plugin boundary, not how to implement a specific game. The canonical player / operator / author interface is [interfaces.md](interfaces.md). For the author-facing method reference see [game-api.md](game-api.md); for a tutorial see [game-development.md](game-development.md).

Strife does **not** run games in a separate process, container, or VM. Isolation is an **API sandbox**: game code is an in-process plugin that talks to the host only through `GameContext` and Discord-free layout objects. Three hosts implement that API (live Discord session, replay, local CLI), so the same `play()` can run without knowing which host is behind it.

**Trust model:** plugin code is trusted to *be a game*, not to be a perfect game. Authors will try to implement rules and a board; they will not steal the bot token, fork-bomb the host, or reach through into matchmaking. The sandbox exists so a buggy `play()` (uncaught exception, bad bot, hung query) ruins **that match**, and so games stay portable across live / replay / CLI. It is not a security boundary against a hostile plugin. `strife/install` is owner-only; review a third-party repo before installing it.

## Mental model

A game is a **rules engine plus UI trees**. It does not send Discord messages, encode button `custom_id`s, open threads, or write to Postgres. The host does those things.

```
 Discord / CLI / replay UI
            │
            ▼
   Routing, slash commands, catalog
            │
            ▼
   Session / ReplayService / MockContext   ← host (owns Discord, DB, timeouts)
            │
            ▼
   GameContext protocol                    ← sandbox wall
            │
            ▼
   Game subclass (builtin or plugins/<key>/)  ← plugin
```

Everything above the wall can change (Discord library, thread model, storage) without games changing. Everything below the wall can change (new game, new bot, new board) without the host knowing the rules.

## Layers

| Layer | Package | Role |
|-------|---------|------|
| Plugin | `strife/games/<key>/` or `plugins/<key>/` | Rules, `plugin.toml`, metadata, bots, board `LayoutView`s |
| Contract | `strife/engine/game.py`, `context.py`, `metadata.py`, `platform.py` | `Game`, `GameContext`, `GameMetadata`, `PLATFORM_VERSION` |
| Registry | `strife/engine/registry.py` | Validate, instantiate |
| Plugin lifecycle | `strife/plugins/` | Manifest, install, update, uninstall, extras |
| Operator config | `config/games.yaml`, `config/plugins.yaml` | Enable/disable, uninstall overlay, timeouts |
| Presentation | `strife/presentation/` | Discord-free components → compiled Discord views |
| Routing | `strife/routing/` | Decode `custom_id` → lobby / game / catalog / replay |
| Matchmaking | `strife/matchmaking/` | Lobbies, start match, promote to session |
| Live host | `strife/session/` | `LiveContext` + pending inputs + move log |
| Replay host | `strife/replay/` | `ReplayContext` + `parse_replay` |
| CLI host | `scripts/run_game.py` | `MockContext` (no Discord) |
| Exposure | `strife/commands/` | `/play`, `/strife catalog`, per-game slash groups |

## 1. What a game plugin is

A game is a Python package with a `plugin.toml` at its root. Shipped builtins live under `strife/games/<folder>/`. Third-party plugins live under `plugins/<key>/` after `strife/install <git-url>`. Both are the same kind of plugin; builtins can be uninstalled too.

1. Declares `key`, `version`, `platform_version`, and `dependencies` in `plugin.toml` (read **before** import). Optional `changelog.toml` is shown in `/strife about` → Changes.
2. Exports the `Game` subclass as `GAME` from the package `__init__.py`.
3. Attaches `GameMetadata` via `@game_metadata_from(...)`, `@game_metadata(META)`, or `Cls.metadata = META`. `metadata.key` must match `plugin.toml`. The host stamps `version` / `platform_version` from the manifest.
4. Implements `play(ctx) -> GameOutcome`. Other methods are required only when metadata declares the matching capability.

Typical layout:

```
strife/games/tictactoe/          # or plugins/my_game/
  plugin.toml     # install-time manifest
  changelog.toml  # player-facing history (/strife about → Changes)
  __init__.py     # GAME = the Game subclass
  game.py         # Game subclass + metadata
  bot.py          # optional bot policy
  emoji/          # optional; uploaded as {key}_{stem} (e.g. duke.webp → coup_duke)
```

Optional extras:

- `dependencies` in `plugin.toml` (Chess: `chess`, `resvg-py`). The host pip-installs them at boot / image build (`sync-deps`). `check_dependencies` only **skips** the plugin if they are missing. Live install does not call pip.
- `slash_moves` on metadata (Chess `/chess move`): extra Discord commands that submit the same `Move` objects as buttons.
- `bot.py` used by `bot_move()`.
- Game-specific emoji in `emoji/` (`game.webp` plus piece/role art). Uploaded as `{key}_{stem}`. Platform chrome uses `ctx.emoji.get("loading", base=True)` from the list in `strife/presentation/base_emojis.py`.

Games may import engine types, presentation components, and third-party libraries listed in `plugin.toml`. They should not import matchmaking, routing, persistence, `strife.session`, or `strife.bot`.

Operator install/uninstall is documented in [plugins.md](plugins.md). The GitHub template is `templates/game-plugin/` (Use this template → `strife/install <url>`).

## 2. Discovery and registration

On startup, `StrifeBot.setup_hook` constructs a `PluginManager` and loads into a `GameRegistry`:

```
PluginManager.load(registry)
  → read strife/games/*/plugin.toml (skip config/plugins.yaml `removed`)
  → read plugins/*/plugin.toml
  → pip-install missing extras if STRIFE_SYNC_PLUGIN_DEPS (boot only)
  → skip if platform_version incompatible or extras missing
  → import package, stamp version from plugin.toml, register GAME
```

`register()` is fail-soft:

- Missing `GAME` export, or `GAME` is not a `Game` subclass → log and skip.
- Missing `metadata` or `metadata.key` → log and skip.
- Invalid `version` / `platform_version` on the manifest, or a `platform_version` the host cannot satisfy → log error and skip.
- Capability mismatch (e.g. `supports_replay` but no `parse_replay`) → log error and skip (not a warning).
- Duplicate key → log warning and skip the second class.
- Import error (missing extra, syntax error) → log exception and skip that package.

Registered games live in `registry._games: dict[str, type[Game]]`. `get(key)` / `metadata(key)` raise `KeyError` if the game never registered.

`registry.create(key, players, settings, seed)` is the factory the lobby uses at match start:

1. Look up the class.
2. Build `random.Random(seed)` (replays reconstruct the same RNG).
3. `game_cls(players, settings, rng)`. The game stamps `player.role_key` if it has named identities.

**Registration is not the same as being playable.** A game can be in the registry and still hidden from players. See [Operator config](#3-operator-config-the-exposure-switch).

## 3. Operator config: the exposure switch

`config/games.yaml` is the operator overlay. It does not define games; it gates and tunes ones the registry already found.

```yaml
defaults:
  turn_timeout_seconds: 90
  turn_warning_seconds: 30

games:
  tictactoe:
    enabled: true
    turn_timeout_seconds: 60
  test:
    enabled: false
```

`GamesConfig.for_game(key)`:

- Known key → merge per-game fields over `defaults`.
- **Unknown key → `enabled: false`.** A newly scaffolded package will register but will not appear in `/play` or the catalog until it has a `games.yaml` entry with `enabled: true`.

Fields:

| Field | Effect |
|-------|--------|
| `enabled` | Catalog listing, `/play` autocomplete, `LobbyService.create_lobby` |
| `turn_timeout_seconds` / `turn_timeout_warning_seconds` | Copied onto `GameSession` at start |
| `turn_timeout_max_strikes` | Default 3 |
| `turn_timeout_consequence` | `abandon` \| `skip` \| `auto_pass` \| `game_ends` \| `strike` |
| `play_hang_seconds` | Cancel `play()` if it makes no context progress (default 45) |
| `settings_overrides` | Operator defaults that overlay metadata settings (e.g. Mafia `mafia_count_default`) |

Disabled games remain in the registry so replays of old matches can still load `parse_replay`. They are just not offered as new lobbies. **Uninstall** is stronger: it drops the plugin from the registry and deletes matches, moves, and per-game stats for that `game_key`. It does not write `games.yaml`. **Update** (`strife/update`) replaces a git plugin's files in place and keeps history. See [plugins.md](plugins.md).

## 4. The sandbox wall: `GameContext`

`GameContext` (`strife/engine/context.py`) is a `Protocol`. Games type against it; they never construct a host themselves.

### What the plugin can see

| Surface | Purpose |
|---------|---------|
| `ctx.rng` | Seeded RNG (same seed → same shuffle in replay) |
| `ctx.players` | `Player` seats, display names, bot flags, roles |
| `ctx.settings` | Lobby settings dict |
| `ctx.emoji` | Resolve application-emoji **keys** to markup |
| `ctx.started_at` | Match start time |
| `ctx.is_replay` | Hide action rows during replay renders (`add_controls`) |
| `ctx.is_bot(seat)` | Skip DMs / treat AI seats |
| `self.setting(key)` on **`Game`** (not on `ctx`) | Setting with metadata default fallback |

`Player` is a seat-centric DTO (`user_id`, `display_name`, `is_bot`, `role_key`). Games mention players; they do not fetch Discord members.

### What the plugin can ask the host to do

| Call | Host does |
|------|-----------|
| `await ctx.update(view)` | Compile `LayoutView` and edit the board message |
| `await ctx.request_input(view, actor=seat, sources={...}, record=, timeout_seconds=, timeout_consequence=)` | Show board, wait for one actor's move (or call `bot_move`) |
| `await ctx.request_inputs(..., until=, per_seat_sources=, record=, timeout_*)` | Simultaneous / first-to-act input |
| `await ctx.send_private(seat, view)` | DM (or thread notice if DMs fail) |
| `await ctx.record_event(source, arguments)` | Append a `game` log entry for replay |
| `await ctx.respond_query(view)` | Ephemeral peek / auxiliary panel (only inside `handle_query`) |

The plugin never receives a Discord `Interaction`. A button click becomes `Move(actor_seat, source, args)` only after the host has checked seat, allowed sources, and pending-input state. Attachments are `ViewFile` bytes; the host converts them.

### What the plugin must not do

Games should not:

- Import `discord` or call `interaction.response.*`.
- Encode `custom_id`s or set `route_prefix` / `resource_id` on board controls (the live surface already uses `g_move:` + thread id).
- Touch `GameSession` (`strife.session`), the database, or `SessionRegistries`.
- Emit system log sources (`forfeit`, `game_end`, `bot_takeover`, `timeout`) — `record_event` rejects those names.

## 5. Presentation sandbox

Games build **plain dataclasses** in `strife/presentation/components.py`:

`LayoutView` → `Container` / `ActionRow` / `Section` / `TextDisplay` / `Button` / `Select` / `MediaGallery` / `ViewFile`

Those types do not subclass `discord.ui`. Emoji on buttons is a **string key** (`"play"`, `"tictactoe_x"`), not a `PartialEmoji`. File attachments are `ViewFile(data=bytes, filename=...)`.

The host compiles later:

```
LayoutView
  → Compiler.compile(view, resource_id=thread_id, prefix="g_move:")
      → resolve emoji keys
      → CustomIdEncoder.encode(prefix, resource_id, source, payload)
      → discord.ui.LayoutView
  → ViewSurface.send / update  (Discord HTTP)
```

`Compiler` also enforces Discord limits (40 components, 4000 characters, 100-char `custom_id`). Oversized layouts fail in the host, not as a raw Discord 400 in game code.

`ViewSurface` binds one Discord message. A live match uses two surfaces:

- **Header** (`replay_noop:` prefix): roster, clock, whose turn — clicks are no-ops.
- **Board** (`g_move:` prefix, `resource_id = thread_id`): game controls.

Because compilation is host-side, `MockContext` can print “view with N nodes” and `ReplayContext` can stash `LayoutView`s in `ReplayFrame`s without Discord.

## 6. Three hosts, one plugin

| Host | Context class | `request_input` | `update` | Used by |
|------|---------------|-----------------|----------|---------|
| Live | `LiveContext` (`strife.session`) | Wait on `GameSession.pending` futures; bots call `bot_move` (10s cap) | Edit board message | `GameSession._run` → `game.play(ctx)` |
| Replay | `ReplayContext` | Raises `NotImplementedError` | Raises | `ReplayService` → `game.parse_replay(moves, ctx)` |
| CLI | `MockContext` | stdin or `--move seat:source:json` | stdout | `python scripts/run_game.py <key>` |

Live-only concerns stay in the session:

- Turn timeouts and strikes (`lifecycle/timeout.py`).
- Forfeit / cancel / bot takeover (system log entries).
- Persist `FinishedMatch` + move log.
- Lock the thread when done.
- Results / rematch UI on the old lobby message.

Replay never runs `play()`. It instantiates the same class with the stored seed, players, and settings, then asks `parse_replay` to rebuild frames. Log rows are `Move` values (same type as live input). `TurnBasedGame` supplies a default `parse_replay` from `reset` / `apply_move` / `render`. Custom games should use `iter_replay` + `ReplayBuilder` from `strife.engine` — not the presentation compiler.

## 7. How games are exposed to Discord

### Catalog and `/play`

`CatalogService._enabled_games()` is `registry.all()` filtered by `config.games.for_game(key).enabled`.

`/play game:` autocomplete walks the same list. `LobbyService.create_lobby` rejects unknown keys and disabled keys (`errors.unknown_game`, `errors.game_disabled`).

Catalog “Play” buttons are **not** game moves. They use prefix `cat_nav:` and payload `{"play": meta.key}`; the router calls `lobby.create_lobby`.

### Metadata as the public listing

Catalog cards are built only from `GameMetadata`: name, summary, player count, time estimate, difficulty, plugin `version`, `game_<key>` emoji. The game class is not instantiated until a lobby actually starts.

Lobby settings UI is generated from `metadata.settings` (`SettingOption`). Games read values later with `self.setting("key")`.

### Per-game slash commands

If `metadata.slash_moves` is non-empty, `register_game_slash_commands` adds a Discord command **group named after `metadata.key`**. Chess declares:

```python
slash_moves=(SlashMove(name="move", ..., params=(MoveParam(name="move", type=ParamType.STRING),)),)
```

which becomes `/chess move move:<san-or-uci>`.

The callback does **not** call into game code. It looks up `sessions.get_game(channel.id)` and `session.handle_slash_command(user_id, slash_move.name, args)`, which completes the same pending-input future a button would. The slash name is the move `source` (`"move"` for Chess).

Slash groups are registered for every **enabled** game with `slash_moves`. Disabled games stay in the registry for replay but do not get slash commands until re-enabled and `strife/sync`. They only succeed when invoked in a live game thread.

Platform commands (`/strife catalog`, `/strife profile`, `/strife forfeit`, …) are not part of the game plugin.

## 8. Routing clicks into the sandbox

`StrifeBot.on_interaction` forwards component interactions to `InteractionRouter.dispatch`.

1. Read `custom_id`.
2. `CustomIdEncoder.decode` → `Route(prefix, resource_id, source, payload)`.
   Payloads are msgpack + HMAC (key derived from the bot token). Oversized payloads are stored in `InMemoryPayloadCache` and referenced by token.
3. Branch on prefix.

Game prefixes (`g_move:`, `g_select:`):

```
resource_id = thread_id
session = sessions.get_game(thread_id)
if session.handle_query(source, interaction):   # peek / ephemeral UI
    return
defer
session.submit(InteractionInput(actor, source, args))
```

`submit` maps Discord user → seat, checks `pending[seat].allowed_sources`, and resolves the future `request_input` is awaiting.

If the session is gone, the router disables the stale components and reports `common.game_ended`.

**Query vs move** is decided by the game: `handle_query` runs **first**. Returning `True` consumes the click (not logged). Returning `False` falls through to `submit`, which fails unless that `source` was listed in `request_input(..., sources=...)`.

## 9. Live session: the host around `play()`

Lobby start (`LobbyService`, once everyone is ready):

1. `registry.create(...)` with a fresh seed.
2. Open a public thread named `{Game} (#{match_code})`.
3. Replace the lobby message with a “game started” pointer.
4. Send header + board surfaces into the thread.
5. `GameSession(..., turn_timeout_* from games.yaml)`.
6. `registries.promote(lobby_id, session)` — users move from location `lobby` to `game`.
7. `session.start()` → `asyncio.create_task(session._run())`.

`_run()`:

```
bind emoji context
try:
    outcome = await game.play(ctx)          # plugin loop
    finalize(completed)
except CancelledError:
    system game_end, finalize(abandoned)
except Exception:
    notify thread, finalize(abandoned)
```

`play()` is a single long-running coroutine. Timeouts, forfeits, and bot takeovers happen **around** it: the lifecycle task completes or cancels pending futures; `play()` just receives a `Move` (or is cancelled).

On finalize the host:

1. Persists match + **game** moves (system events stored too; `total_turns` counts game entries only).
2. Asks `game.final_view(ctx, outcome)` (5s cap).
3. Updates header to finished, posts results/rematch on the lobby surface.
4. Locks the thread.
5. Releases user locations.

Crashes in `play()`, `bot_move` (10s, wrapped on the instance so `play()` cannot bypass it), `handle_query` (5s), and `final_view` (5s) are contained by the session. A plugin exception abandons that match; it does not take down the bot. If `play()` yields but stops calling `GameContext` for `play_hang_seconds` (default 45s) with no pending input, the lifecycle cancels the session.

## 10. What is and is not isolated

Plugins are **trusted to try to be a game**. There is no import firewall, no separate UID, and no attempt to stop a plugin that *wants* to import `strife.bot` or read the token. The host assumes first-party (or reviewed) game packages that accidentally throw, hang a hook, or block the loop — not malware.

What the wall is for:

- **Accident containment.** An exception in `play()` abandons that match; other sessions keep running.
- **Host ownership.** Discord HTTP, `custom_id` HMAC, Postgres, clocks, forfeits, and catalog enablement stay in the host so games cannot accidentally couple to them.
- **Portability.** The same class runs under live, replay, and CLI contexts.

### Isolated in practice

- Discord HTTP (messages, threads, DMs, ephemeral query replies).
- `custom_id` encoding and HMAC.
- Match persistence and user stats.
- Who may click (seat, pending actors, allowed sources).
- Turn clocks, strikes, forfeit, rematch.
- Enabling a game for the public catalog.
- Python exceptions in `play` / `bot_move` / `handle_query` / `final_view` / `parse_replay` (that match or that replay fails; the process does not).
- `bot_move` timeout even when `play()` calls `self.bot_move` directly.
- `play()` that yields but never touches `GameContext` again (hung-session watchdog).

Queries stay behind the wall: `handle_query(seat, source, ctx)` builds a `LayoutView` and calls `ctx.respond_query(view)`. The host still has the Discord interaction; the plugin does not.

Attachments stay behind the wall: `LayoutView.files` is `list[ViewFile]`. The host converts to Discord files at send time.

`Player.user_id` is on the seat DTO so mentions work. Games should not call the Discord API with it.

### What a clumsy (not malicious) plugin can still do to the bot

Because everything shares one process and one event loop:

- A **tight CPU loop** or other non-awaiting work in `play()` / `bot_move` / `handle_query` / `render` freezes every match and every command. `asyncio.wait_for` only cancels if the code yields. First-party bots and Chess live `render` run on the dedicated CPU pool (`run_cpu` / `STRIFE_CPU_POOL_SIZE`).
- A **native crash** (segfault in an extension such as a plugin extra) or `os._exit()` kills the process. Docker `restart: unless-stopped` brings it back with no live sessions.
- Unbounded allocation can OOM the process.

Those are reliability problems, not an untrusted-code problem.

### Time / failure boxes

| Call | Timeout |
|------|---------|
| `play()` | Hung watchdog (`play_hang_seconds`, default 45s with no pending input and no context progress); cancelled on abandon / shutdown |
| `bot_move` | 10s (instance wrapper) |
| `handle_query` | 5s |
| `final_view` | 5s |
| `parse_replay` | 15s |

## 11. End-to-end: from package to first click

```
plugin.toml + package          config/games.yaml enabled: true
        │                              │
        ▼                              ▼
 PluginManager.load()           CatalogService / /play autocomplete
        │                              │
        └──────────► LobbyService.create_lobby(key)
                              │
                              ▼  (ready)
                    registry.create() → Game instance
                    public thread + GameSession
                              │
                    game.play(LiveContext)
                              │
                    ctx.request_input(view, sources={...})
                              │
                    Compiler → Discord board message
                              │
                    user clicks button
                              │
                    Router → session.handle_query? → session.submit
                              │
                    pending future resolves → Move back in play()
                              │
                    GameOutcome → persist → replay via parse_replay
```

The same `Game` class is constructed three more times without Discord:

- **CLI:** `scripts/run_game.py my_game`
- **Replay:** new instance + stored seed + `parse_replay`
- **Rematch:** new lobby from the previous metadata/settings, then the live path again

## 12. Adding a game (platform checklist)

Authoring steps live in [game-development.md](game-development.md). From the **host** side, a game is exposed when:

1. Package has `plugin.toml` (in-tree under `strife/games/` or installed into `plugins/`) and exports `GAME`. `changelog.toml` is shown in `/strife about` → Changes.
2. Manifest `key` matches metadata, `version` / `platform_version` on the manifest are valid, `platform_version` is compatible with this host, and capabilities match implementations.
3. Extras listed in `plugin.toml` are installed (image build / boot sync). Missing extras skip the game instead of failing startup. Live `strife/install` does not pip-install.
4. The plugin is not in `config/plugins.yaml` `removed`.
5. `config/games.yaml` has `games.<key>.enabled: true` (otherwise it stays registered-but-hidden). Git install creates this row when the key is new; it does not un-hide an existing `enabled: false`. Uninstall does not write `games.yaml`.
6. Optional `game_<key>` (and piece/role) entries; upload with `strife/emoji`.
7. Optional `slash_moves` — registered on the next command tree sync.

No edits to `bot.py`, the router, matchmaking, or `strife.session` are required for a standard button-driven game.

## Related documents

- [game-api.md](game-api.md) — method contract, move log kinds, query vs action
- [game-development.md](game-development.md) — tutorial, presentation style, checklist
- `scripts/scaffold_game.py` — in-tree builtin scaffold (writes `plugin.toml`)
- `templates/game-plugin/` — GitHub template for third-party plugins (`changelog.toml` included)
- `changelog/bot.toml`, `changelog/platform.toml` — host notes in `/strife about` → Changes
- `docs/plugins.md` — install, uninstall, overlay
- `scripts/run_game.py` — third host (CLI sandbox)
- `strife/games/test/` — API showcase (disabled in `games.yaml` by default)
- `strife/games/tictactoe/` — smallest complete `TurnBasedGame`
- `strife/games/chess/` — slash moves, `plugin.toml` extras, file attachments
