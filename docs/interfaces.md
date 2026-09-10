# Interfaces

How players, operators, and game authors talk to Strife. If other docs disagree with this file, this file wins.

Slash starts things. A channel message seats the table. A public thread is the match. Everything else is ephemeral. Games never see Discord.

## 1. Four surfaces

| Surface | Where | For |
|---------|--------|-----|
| **Slash** | Guild text channels | Start a lobby, look things up, forfeit, text moves (Chess) |
| **Lobby message** | Public channel (or the guild default) | Join, leave, ready, creator tools |
| **Game thread** | `{Game} (#{code})` | Header + board. Clicks are moves or peeks |
| **Ephemeral** | Only the clicker | Catalog, settings, profile, replay, about, errors, toasts |

- Catalog / About / Profile must not become the public lobby by responding on themselves. They post to the channel (or the guild default).
- Lobby buttons and slash fallbacks call the same code.
- `/strife forfeit` is in-game only. Leaving a lobby is Leave.
- Games build `LayoutView` trees. The host compiles, routes, times out, persists, and talks to Discord.

## 2. Roles

| Role | Who | May |
|------|-----|-----|
| Anyone in a guild | In a server the bot is in | `/play`, catalog, profile, replay, about; join **public** lobbies |
| Lobby member | Seated human, location `lobby` | Leave, ready, **view** settings |
| Lobby creator | `lobby.creator_id` (transfers if they leave) | Privacy, rules, bots, kick, blacklist, approve, end lobby |
| Match player | Human seat in a live game, not bot-taken-over | Board clicks, peek, game slash, `/strife forfeit` |
| Guild administrator | Discord Administrator | `/strife server` |
| Bot owner | `STRIFE_OWNER_IDS` | `strife/…` (no mention) |

A user is in at most one lobby or one game (`SessionRegistries.user_location`). Rematch, forfeit, timeout, and start must update that map with the session/lobby change.

## 3. Players

### 3.1 Slash

| Command | Parameters | Who | Does |
|---------|------------|-----|------|
| `/play` | `game` required, `private` optional | Anyone | Public-channel lobby. Creator is seated |
| `/strife catalog` | `page` optional | Anyone | Ephemeral browse. **Play** posts a lobby (always public) |
| `/strife profile` | `user`, `game`, `page` | Anyone | Stats + recent matches |
| `/strife replay` | `match` (6 chars, case-insensitive) | Anyone | Ephemeral replay. Nav is owner-gated on that message |
| `/strife forfeit` | — | Match player | Forfeit. **Error if the caller is only in a lobby** |
| `/strife settings` | — | Lobby member | Settings. Members read-only; creator edits. No `private` shortcut |
| `/strife server` | — | Administrator | Default lobby channel |
| `/strife about` | — | Anyone | About / attributions / Changes |
| `/strife lobby join` | `creator` | Anyone | Join that creator’s lobby |
| `/strife lobby leave` | — | Lobby member | Leave |
| `/strife lobby ready` | — | Lobby member | Toggle ready |
| `/<game> …` | From `slash_moves` | Match player in the **live thread** | Same `Move` as a board control. Enabled games only |

Chess is `/chess move move:<SAN or UCI>`. No piece buttons. Illegal input is an error, never a success toast.

Slash that duplicates buttons (`/strife lobby kick`, `end`, `option`, `/strife bot add`, …) must call the same inner actions or be removed.

### 3.2 Components

| Prefix | Message | Meaning |
|--------|---------|---------|
| `lobby_*` | Lobby + settings | Marshal. Creator-only prefixes enforced on every path |
| `g_move:` / `g_select:` | Board | Game input. `resource_id` = thread id |
| `replay_noop:` | Thread header | Roster / clock. Clicks do nothing |
| `cat_nav:` | Catalog | Page / jump / play |
| `r_nav:` | Replay | Frame nav (owner of that ephemeral message) |
| `prof_nav:` | Profile | Page / filter / open replay |
| `rematch:` | Results (old lobby message) | Vote to rematch |
| `forfeit:` | Error cards | Same as `/strife forfeit` |
| `about_nav:` | About | Tabs; opening catalog **replaces** the about message |
| `server_*` | Server settings | Re-check Administrator on every click |

`PROF_OPEN` is unused.

Lobby and board controls that only carry a source + resource id must not expire while that lobby/game is alive. Invalidate cache entries when it ends.

### 3.3 Journeys

**Start.** `/play game:tictactoe` (optional `private:true`) or Catalog **Play**. Lobby posts here, or in the guild default channel if that’s set. No forums, no DMs.

**Marshal.** Join (or Request to Join). Leave / Ready on the same message. Creator opens Settings. When the table can start and every human is ready, a public thread opens, the lobby card becomes a pointer, and `game.play()` runs.

**Play.** Header (no-op) + board. Timeouts warn in-thread, then apply `games.yaml` `turn_timeout_consequence`. Thread locks when the match ends. Results + Rematch + View Replay land on the **old lobby message**.

**Hidden info.** Mafia, Spyfall, and Liar’s Dice DM when DMs work; **Peek** always works. Coup is peek-only. Failed DMs post a thread notice — they must not dump the private content.

**Rematch.** Eligible humans vote for 120s. Unanimous → new lobby with the same privacy, creator, settings, and bots. If a voter is already in another session, the vote fails (`errors.already_in_session`).

**Replay / profile.** Ephemeral. Jump modals edit the parent message. Abandoned matches may show on the profile list; `/strife replay` autocomplete is completed matches only.

### 3.4 Feedback

Player-visible errors and successes go through `UserErrorPresenter` / `UserSuccessPresenter` and `config/text.toml`.

- Join / leave / ready / privacy: one policy for button and slash (always toast, or never)
- Board clicks `defer()`; no “move submitted” toast
- Game slash toasts success only after a legal move is accepted
- Query `not_a_player` uses the same presenter (Forfeit / Leave when relevant)
- Owner commands may `message.reply`

### 3.5 Permissions

| Action | Allowed |
|--------|---------|
| Privacy, rules, kick, bots, end, approve, blacklist | Creator only, including `/strife settings` |
| View settings | Any lobby member |
| Forfeit | Live match player |
| `/strife server` and `server_*` | Administrator, re-checked on components |
| `strife/…` | `owner_ids`, message **starts with** `strife/` |

## 4. Operators

### 4.1 Owner message commands

Type `strife/<cmd>` at the **start** of the message. Mentions are ignored. Message Content intent is required.

| Command | Does |
|---------|------|
| `strife/sync [local \| <guild id>]` | Push the slash tree (default: global) |
| `strife/emoji` | Upload `assets/emoji/` plus each plugin `emoji/` as `{key}_{stem}` |
| `strife/plugins` | List builtins and git plugins |
| `strife/install <git-url> [ref]` | Clone, register. Creates `games.yaml` row if the key is new |
| `strife/install <key>` | Restore an uninstalled builtin (does not un-hide `enabled: false`) |
| `strife/update <key> [ref]` | Replace git files; keep history; refuse while that game is live |
| `strife/uninstall <key> confirm` | Stop live games, remove plugin, **then** wipe matches/stats |
| `strife/dbreset confirm` | Wipe the database and re-run migrations |

`strife/clear` and `strife/treediff` are internals. Leave them out of README. `clear` wipes the slash tree and is easy to run by mistake.

Hide a game without deleting history: `config/games.yaml` `enabled: false`.

### 4.2 Config

| File / env | Role |
|------------|------|
| `config/games.yaml` | Exposure and timeouts. Unknown key → **`enabled: false`**. Fields: `enabled`, `turn_timeout_seconds`, `turn_timeout_warning_seconds`, `turn_timeout_max_strikes`, `turn_timeout_consequence` (`abandon` \| `skip` \| `auto_pass` \| `game_ends` \| `strike`), `play_hang_seconds`, `settings_overrides` |
| `config/plugins.yaml` | `removed` builtins, `installed` git plugins |
| `config/emoji.yaml` | Emoji **id cache**, written by `strife/emoji` |
| `config/text.toml` | Player-facing copy |
| `changelog/bot.toml`, `changelog/platform.toml` | Host changelogs in `/strife about` → Changes |
| `STRIFE_DISCORD_TOKEN`, `STRIFE_OWNER_IDS` | Required |
| Other env | Optional; see `.env.example` |

Connect Four’s key is `connect_four` (folder `connectfour/`). Yaml, metadata, and `/play` all use the key.

Disabled games stay in the registry for old replays. They are not in `/play` or the catalog, and they don’t get slash groups until re-enabled + `strife/sync`.

### 4.3 Trust

Plugins run in-process. Review a third-party repo before installing it.

## 5. Game authors

### 5.1 Imports

```
strife.engine
strife.presentation.components
strife.presentation.game_ui
strife.presentation.style
strife.presentation.roster     # shipped social games
plugin.toml extras
```

Do not import `discord`, `strife.session`, `strife.bot`, matchmaking, routing, persistence, or the compiler. That’s a convention, not an import firewall.

### 5.2 Package

1. `plugin.toml`: `key`, `version`, `platform_version`, `dependencies`
2. `changelog.toml` (optional to load, expected for Changes). Newest first; latest `version` matches `plugin.toml`
3. Export `GAME` from `__init__.py`
4. `GameMetadata.key` equals `plugin.toml` key. Host stamps versions from the manifest
5. `play(ctx) -> GameOutcome` always
6. Missing `parse_replay` / `bot_move` / `remove_player` when declared **skips registration**. Default `supports_replay=True` — use `TurnBasedGame` or set `supports_replay=False`

Scaffold: `python scripts/scaffold_game.py <key> "Title"`. Third-party: **Use this template** on `templates/game-plugin/`, then `strife/install <git-url>`. Local: `python scripts/run_game.py <key>`.

### 5.3 `GameContext`

Games type against the protocol. They never construct a host.

| Member | Purpose |
|--------|---------|
| `ctx.rng` / `self.rng` | Same seeded RNG on every host |
| `ctx.players`, `ctx.settings`, `ctx.emoji` | Seats, settings, emoji keys |
| `ctx.started_at`, `ctx.is_replay`, `ctx.is_bot(seat)` | Clock, hide controls, skip DMs |
| `self.setting(key)` | On **`Game`**, not `ctx`. Metadata default fallback |
| `await ctx.update(view)` | Refresh the board |
| `await ctx.request_input(...)` | One actor |
| `await ctx.request_inputs(...)` | Simultaneous / first-to-act |
| `await ctx.send_private(seat, view)` | DM; thread notice if DMs fail |
| `await ctx.record_event(source, arguments)` | One `game` log row. Rejects system names |
| `await ctx.respond_query(view)` | Only inside `handle_query` |

`timeout_seconds` / `timeout_consequence` / `per_seat_sources` / `descriptions` are real API. Chess clocks and Coup skip-on-timeout use them. Replay never runs `request_*`.

Optional: `handle_query`, `final_view`, `active_seats()` (timeout/forfeit — override if seats can leave).

Don’t call `bot_move` from `play()`. Call `request_input` so bots, humans, CLI, and the log share one path. Caps: `bot_move` 10s, `handle_query` 5s, `final_view` 5s, `parse_replay` 15s. `play()` with no context progress for `play_hang_seconds` (default 45s) is cancelled. CPU-bound work: `run_cpu`.

### 5.4 Buttons and recording

| Kind | How | In the log? |
|------|-----|-------------|
| Solo move | `request_input(..., sources={...})` | Yes (`game`), unless `record=False` |
| Group input | `request_inputs(..., record=False)` then one `record_event` | One `game` row |
| Query | `query=True` + `handle_query` | No |
| Link | `ButtonStyle.LINK` | No |
| System | Host only | `forfeit`, `game_end`, `bot_takeover`, `timeout` |

`query=True` is stripped from allowed sources. The router calls `handle_query` only for query controls. `add_controls` is a no-op when `ctx.is_replay`.

`Move.args` is the field. `arguments` is a read-only alias. Slash **name** is the `source`.

`parse_replay` iterates the full log, applies only `move.is_game`, and uses the same rules path as live. Don’t write a second engine.

### 5.5 Three hosts

| Host | Class | `record=False` | Bots | Timeouts | Queries |
|------|--------|----------------|------|----------|---------|
| Live | `LiveContext` | Honor | via `request_input` | Honor | `handle_query` |
| CLI | `MockContext` | Honor | Scripted / first source | Passed through | Optional |
| Replay | `ReplayContext` | n/a | n/a | n/a | n/a |

CLI `--replay` and live `recorded_moves` should be the same kind of stream.

`LiveContext` must not expose `_session` or Discord objects to game code.

### 5.6 Presentation

Games build dataclasses in `strife/presentation/components.py`. Emoji is a string key. Files are `ViewFile` bytes. Don’t set `route_prefix` / `resource_id` on board controls.

`ChannelSelect` / `UserSelect` are host widgets. `MoveParam.autocomplete` is `(current: str) -> list[str]` — no Discord `Interaction`. Shipped games must not need Discord types.

Compiler limits fail in the host. CLI should surface the same `LayoutError`.

## 6. Host contributors

1. Occupancy is transactional. Lobby start, promote, finalize, timeout, rematch, and cancel each have a rollback that can’t leave a live session with released users, or a lobby with no reserved creator.
2. Buttons and slash call the same lobby methods. Settings views are pure; they receive `owner_ids`.
3. Persist the match (and moves) before rematch is offered. Persist failure notifies the thread, skips rematch, then still releases occupancy.
4. `request_inputs` must not share one `last_move_at` across waiters. AFK must not fire the same seat twice.
5. Don’t hold `lobby.lock` across Discord HTTP. Set `starting`, drop the lock, re-check.
6. Presentation that games import stays Discord-free. Thread headers, compiler, `ViewSurface`, HMAC, and owner badges live outside plugin import paths.
7. Tests pin `Move` shapes, `query=True`, `record=False` on all three hosts, plugin.toml version stamps, occupancy rollback, and at least one social game’s CLI log vs `parse_replay`.

## 7. Still open

- `/strife lobby *` still exists as a parallel surface. Deleting duplicates is optional (`/strife lobby join` stays).
- Live matches persist only at finalize (kill -9 loses the in-progress game).
- Profile `list_recent` still uses a per-row count subquery.
- `config/emoji.yaml` may keep leftover cache keys until the next `strife/emoji`.
- `errors.not_on_whitelist` is unused copy.
- Spyfall still has a `bot_move` helper during discussion.

## 8. Host internals that still hurt

Dual lobby UI (`lobby_flow`, `settings_view`, `lobby_commands`) still implements some actions twice. Intended: one `LobbyActions` used by router and slash.

Member cache is off, so join/ready may fetch while holding the lobby lock. Match start no longer holds that lock across thread create.

Repos are one module. A match row exists only at finalize. Intended: insert on start, append moves, mark status on end.

`strife/emoji` still deletes every application emoji before upload.

Plugins run in-process. Live install does not pip-install; missing extras load on the next boot.

Coup / Mafia / Spyfall / Liar’s Dice each reimplement phase windows, peek, and group `record=False`. There is no host helper for “phase + group resolution + peek.”

Chess `bot_move` still `asyncio.sleep(0.5)` on the event loop; Liar’s Dice `sleep(4)` in `play()`. Replay Chess render is not offloaded.

Don’t add `GameContext` methods until they exist on live **and** CLI and appear in this file.
