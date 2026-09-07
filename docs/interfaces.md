# Interface Contract

This is the canonical description of how **players**, **operators**, and **game authors** talk to Strife. Other docs (`README.md`, [game-api.md](game-api.md), [game-architecture.md](game-architecture.md), [game-development.md](game-development.md), [plugins.md](plugins.md)) must match this file. When they disagree, this file wins until it is deliberately changed.

The host already has a coherent model: **slash to enter, a channel message to marshal, a public thread to play, ephemeral chrome for everything else.** Games never see Discord. The rest of this document freezes that model, then records where the current code and docs drift from it.

---

## 1. Four surfaces

| Surface | Where | What it is for |
|---------|--------|----------------|
| **Slash** | Guild text channels | Start a lobby, look things up, forfeit a live game, game-specific moves that cannot be buttons (Chess) |
| **Lobby message** | Public channel (or the guild default lobby channel) | Join, leave, ready, creator tools. This is the marshal UI |
| **Game thread** | Public thread `{Game} (#{code})` | The match: header + board. Clicks are moves or queries |
| **Ephemeral chrome** | Only visible to the clicker | Catalog, settings, profile, replay, about, server settings, errors, success toasts |

Rules that follow from that split:

1. An ephemeral interaction **must not** become the public lobby message. Catalog / About / Profile never post a lobby by responding on themselves; they post to the channel (or the guild default channel).
2. Lobby actions have **one** implementation used by buttons and by any slash fallback.
3. `/strife forfeit` is **in-game only**. Leaving a lobby is Leave.
4. Games build `LayoutView` trees. The host compiles, routes, times out, persists, and talks to Discord.

---

## 2. Roles

| Role | How they are recognized | What they may do |
|------|-------------------------|------------------|
| Anyone in a guild | Present in a server the bot is in | `/play`, catalog, profile, replay, about; join **public** lobbies |
| Lobby member | Seated human in `SessionRegistries.user_location` kind `lobby` | Leave, ready, **view** settings (read-only) |
| Lobby creator | `lobby.creator_id` (transfers if the creator leaves) | Privacy, rules, bots, kick, blacklist, approve/deny, pre-approve, clear ready, end lobby |
| Match player | Human seat in a live `GameSession`, not bot-taken-over | Board clicks, peek, game slash moves, `/strife forfeit` |
| Guild administrator | Discord Administrator | `/strife server` and its components |
| Bot owner | `STRIFE_OWNER_IDS` | `strife/…` message commands (no mention required) |

Occupancy is **one place only**: `SessionRegistries.user_location`. A user is in at most one lobby or one game. Rematch, forfeit, timeout, and start must update that map in the same transaction as the session/lobby change.

---

## 3. Player contract

### 3.1 Canonical slash commands

| Command | Parameters | Who | Does |
|---------|------------|-----|------|
| `/play` | `game` (required, autocomplete = enabled games), `private` (optional) | Anyone | Create a **public channel** lobby message. Creator is seated |
| `/strife catalog` | `page` optional | Anyone | Ephemeral browse. **Play** posts a lobby to the channel (same helper as `/play`), always public |
| `/strife profile` | `user`, `game` (autocomplete of enabled keys), `page` | Anyone | Stats + recent matches; replay picker |
| `/strife replay` | `match` (6-character code, case-insensitive) | Anyone | Ephemeral replay. Nav is owner-gated on that message |
| `/strife forfeit` | — | Match player | Forfeit the live game. **Error if the caller is only in a lobby** |
| `/strife settings` | — | Lobby member | Open settings. Members see read-only; creator can edit. **No `private` shortcut** |
| `/strife server` | — | Administrator | Default lobby channel |
| `/strife about` | — | Anyone | About / catalog / attributions / changes (bot, platform, and game notes) |
| `/strife lobby join` | `creator` | Anyone | Join that creator’s lobby (the one marshal action a scrolled-away message cannot do) |
| `/strife lobby leave` | — | Lobby member | Leave |
| `/strife lobby ready` | — | Lobby member | Toggle ready |
| `/<game> …` | From `metadata.slash_moves` | Match player in the **live thread** | Same `Move` as a board control. Registered only for **enabled** games |

Chess is `/chess move move:<SAN or UCI>`. The board has no piece buttons; the slash command **is** the move. Illegal input is an **error**, never a success toast.

Slash commands that exist today but are **not** part of this contract (button/settings duplicates): `/strife lobby` `kick`, `end`, `clear-ready`, `privacy`, `reset-privacy`, `reset-rules`, `option`, `approve`, `deny`, `preapprove`, `revoke-approval`, `blacklist-add`, `blacklist-remove`; `/strife bot add/remove`; `/strife settings private:`. They must either call the **same** inner lobby actions as the buttons (and stay documented) or be removed.

### 3.2 Canonical components

| Prefix | Message | Meaning |
|--------|---------|---------|
| `lobby_*` | Lobby message + settings | Marshal. Creator-only prefixes are enforced on **every** path |
| `g_move:` / `g_select:` | Board | Game input. `resource_id` = thread id |
| `replay_noop:` | Thread header | Roster / clock. Clicks are no-ops |
| `cat_nav:` | Catalog | Page / jump / play |
| `r_nav:` | Replay | Frame nav (owner of that ephemeral message) |
| `prof_nav:` | Profile | Page / filter / open replay |
| `rematch:` | Results (old lobby message) | Vote to rematch |
| `forfeit:` | Error cards | Same as `/strife forfeit` (live game only) |
| `about_nav:` | About | Tabs (main, background, attributions, **changes**); opening catalog **replaces** the about message. Changes payload: `scope` (`hub` \| `bot` \| `platform` \| `games` \| `game`), optional `game` key, `page` |
| `server_*` | Server settings | Re-check Administrator on every click |

`PROF_OPEN` is unused and must not be part of the contract.

Oversized `custom_id` payloads may go through a cache, but **lobby and board controls that only carry a source + resource id must not expire while that lobby/game is alive.** Invalidate cache entries when the lobby or session ends.

### 3.3 Journeys

**Start.** `/play game:tictactoe` (optionally `private:true`) or Catalog **Play**. Bot posts a lobby card in this channel, or in the guild default channel if that is set and different. Forum channels are rejected (`errors.need_text_channel`). Not valid in DMs.

**Marshal.** Public Join (or Request to Join). Leave / Ready on the same message. Creator opens Settings (General / Access / Rules). When `can_start` is true and every human is ready, the host opens a public thread, replaces the lobby card with a pointer, and starts `game.play()`.

**Play.** Header (no-op clicks) + board. Timeouts warn in-thread, then apply `games.yaml` `turn_timeout_consequence`. The thread locks when the match ends. Results + Rematch + View Replay land on the **old lobby message**.

**Hidden information.** Mafia, Spyfall, and Liar’s Dice DM role/hand when DMs are open; **Peek** always works from the board. Coup is **peek-only** (no DM). Failed DMs post a thread notice that Peek still works — they must not dump the private content.

**Rematch.** Eligible humans (seated in the finished match, not bot-taken-over) vote for 120s. Unanimous → new lobby with the **same privacy, creator, settings, and bots**. If a voter is already in another session, the vote **fails** with `errors.already_in_session`; it does not silently drop them.

**Replay / profile.** Ephemeral. Jump modals edit the **parent** message. Abandoned matches may appear on the profile list; `/strife replay` autocomplete is completed matches only.

### 3.4 Feedback

All player-visible errors and successes go through `UserErrorPresenter` / `UserSuccessPresenter` and `config/text.toml`.

- Join / leave / ready / privacy: **one** policy, applied to button and slash (either always toast, or never — do not mix).
- Board clicks `defer()`; they do not toast “move submitted.”
- Game slash moves toast success **only after** the pending input accepted a legal move.
- Query `not_a_player` uses the same presenter as other errors (Forfeit / Leave actions when relevant).
- Owner commands may use plain `message.reply`; they are not a player surface.

### 3.5 Permissions that must not drift

| Action | Allowed |
|--------|---------|
| Toggle privacy, change rules, kick, bots, end, approve, blacklist | Creator only, **including** `/strife settings` |
| View settings | Any lobby member |
| Forfeit | Live match player |
| `/strife server` and `server_*` clicks | Administrator, re-checked on components |
| `strife/…` | `owner_ids` and message **starts with** `strife/` |

---

## 4. Operator contract

### 4.1 Owner message commands

Type `strife/<cmd>` as the **start of the message** in any channel or DM. A mention prefix is **not** accepted (`@bot strife/sync` is ignored). Message Content intent is required.

| Command | Does |
|---------|------|
| `strife/sync [local \| <guild id>]` | Push the slash tree (default: global) |
| `strife/emoji` | Upload `assets/emoji/` stems listed in `base_emojis.py`, plus each plugin `emoji/` as `{key}_{stem}` |
| `strife/plugins` | List builtins and git plugins |
| `strife/install <git-url> [ref]` | Clone, register. Creates `games.yaml` row if the key is new. Extras install on next boot if missing. |
| `strife/install <key>` | Restore an uninstalled builtin (does not un-hide `enabled: false`) |
| `strife/update <key> [ref]` | Replace git files; keep history; refuse while that game has a live match or lobby |
| `strife/uninstall <key> confirm` | Stop live games, remove plugin, **then** wipe matches/stats for that `game_key`. Does not pip-uninstall extras. |
| `strife/dbreset confirm` | Wipe the database and re-run migrations |

`strife/clear` and `strife/treediff` are **developer internals**. They stay out of README. `clear` wipes the slash tree and is easy to run by mistake.

Hiding a game without deleting history is `config/games.yaml` `enabled: false`, not uninstall.

### 4.2 Config

| File / env | Role |
|------------|------|
| `config/games.yaml` | Exposure and timeouts. Unknown key → **`enabled: false`**. Fields: `enabled`, `turn_timeout_seconds`, `turn_timeout_warning_seconds`, `turn_timeout_max_strikes`, `turn_timeout_consequence` (`abandon` \| `skip` \| `auto_pass` \| `game_ends` \| `strike`), `play_hang_seconds`, `settings_overrides` |
| `config/plugins.yaml` | `removed` builtins, `installed` git plugins |
| `config/emoji.yaml` | Runtime emoji **id cache**, not a catalog. Written by `strife/emoji` |
| `config/text.toml` | Player-facing copy |
| `changelog/bot.toml`, `changelog/platform.toml` | Host changelogs shown in `/strife about` → Changes. Same `[[release]]` shape as plugin `changelog.toml` |
| `STRIFE_DISCORD_TOKEN`, `STRIFE_OWNER_IDS` | Required |
| `STRIFE_DATABASE_URL`, `STRIFE_LOG_LEVEL`, `STRIFE_CONFIG_DIR`, `STRIFE_PLUGINS_DIR`, `STRIFE_CHANGELOG_DIR`, `STRIFE_MIGRATIONS_DIR`, `STRIFE_DATABASE_MIN_SIZE`, `STRIFE_DATABASE_MAX_SIZE`, `STRIFE_CPU_POOL_SIZE`, `STRIFE_SYNC_ON_START`, `STRIFE_SYNC_PLUGIN_DEPS` | Optional; see `.env.example` |

Connect Four’s **key** is `connect_four` (folder `strife/games/connectfour/`). Yaml, metadata, and `/play` autocomplete all use the key.

Disabled games stay in the registry so old replays load. They are not in `/play` or the catalog, and they do **not** get slash groups until re-enabled + `strife/sync`.

### 4.3 Trust

Plugins run **in-process**. `strife/install` is owner-only. Review a third-party repo before installing it. This is not a security sandbox.

---

## 5. Game-author contract

### 5.1 Allowed imports

```
strife.engine          # Game, TurnBasedGame, GameContext, Move, metadata, ReplayBuilder, run_cpu, …
strife.presentation.components
strife.presentation.game_ui    # add_controls, game_container, query_panel, action_status, message_lead
strife.presentation.style
strife.presentation.roster     # member_line (shipped social games)
plugin.toml extras             # e.g. chess, resvg-py
```

Do **not** import `discord`, `strife.session`, `strife.bot`, `strife.matchmaking`, `strife.routing`, `strife.persistence`, or the presentation compiler / `ViewSurface`. Isolation is an API convention, not an import firewall — first-party games still obey it.

### 5.2 Package shape

1. `plugin.toml`: `key`, `version`, `platform_version`, `dependencies`.
2. `changelog.toml` next to it (optional to load, expected for Changes). Newest `[[release]]` first; latest `version` should match `plugin.toml`.
3. Export the `Game` subclass as `GAME` from `__init__.py`.
4. `GameMetadata.key` **equals** `plugin.toml` key. The host **stamps** `metadata.version` / `platform_version` from the manifest at load. Do not duplicate them on the class.
5. `play(ctx) -> GameOutcome` is always required.
6. Capability flags are fail-closed: missing `parse_replay` / `bot_move` / `remove_player` **skips registration** (error log, not a warning). Default `supports_replay=True`, so a raw `Game` without `parse_replay` will not load — subclass `TurnBasedGame` or set `supports_replay=False`.

Scaffold an in-tree builtin: `python scripts/scaffold_game.py <key> "Title"`. Third-party: GitHub **Use this template** on `templates/game-plugin/`, then `strife/install <git-url>`. Local: `python scripts/run_game.py <key>`.

### 5.3 `GameContext` — the wall

Games type against the protocol. They never construct a host.

| Member | Purpose |
|--------|---------|
| `ctx.rng` / `self.rng` | Same seeded `random.Random` on every host |
| `ctx.players`, `ctx.settings`, `ctx.emoji` | Seats, lobby settings, emoji keys |
| `ctx.started_at`, `ctx.is_replay`, `ctx.is_bot(seat)` | Clock, hide controls, skip DMs |
| `self.setting(key)` | On **`Game`**, not on `ctx`. Metadata default fallback |
| `await ctx.update(view)` | Refresh the board |
| `await ctx.request_input(view, actor=, sources=, record=True, description=, timeout_seconds=, timeout_consequence=)` | One actor |
| `await ctx.request_inputs(..., until="all"\|"any", per_seat_sources=, record=, descriptions=, timeout_*)` | Simultaneous / first-to-act |
| `await ctx.send_private(seat, view)` | DM; host posts a thread notice if DMs fail |
| `await ctx.record_event(source, arguments)` | One `game` log row. Rejects `forfeit`, `game_end`, `bot_takeover`, `timeout` |
| `await ctx.respond_query(view)` | Only inside `handle_query` |

`timeout_seconds` / `timeout_consequence` / `per_seat_sources` / `descriptions` are **real API**. Chess clocks and Coup skip-on-timeout use them. Every host (live, CLI) must honor them. Replay never runs `request_*`.

Optional hooks: `handle_query`, `final_view`, `active_seats()` (timeout/forfeit uses this — override if seats can leave mid-game).

Do **not** call `bot_move` from `play()`. Call `request_input` so bots, humans, CLI, and the move log share one path. The host time-boxes `bot_move` (10s), `handle_query` (5s), `final_view` (5s), `parse_replay` (15s), and cancels `play()` if it makes no context progress for `play_hang_seconds` (default 45s). CPU-bound work goes through `run_cpu`.

### 5.4 Buttons, queries, recording

| Kind | How | In the log? |
|------|-----|-------------|
| Solo move | `request_input(..., sources={...})` | Yes (`game`), unless `record=False` |
| Group input | `request_inputs(..., record=False)` then **one** `record_event` | One semantic `game` row |
| Query | `Button(..., query=True)` + `handle_query` → `respond_query` | No |
| Link | `ButtonStyle.LINK` | No |
| System | Host only | `forfeit`, `game_end`, `bot_takeover`, `timeout` |

`query=True` is stripped from allowed move sources even if listed in `sources`. The router must only call `handle_query` for query controls, not for every board click. `add_controls(container, ctx, row)` is a no-op when `ctx.is_replay`.

`Move.args` is the field (`{}` / `{"value"}` / `{"values"}`). `arguments` is a read-only alias. Slash moves produce the same `Move` type; the slash **name** is the `source`.

`parse_replay` iterates the full log, applies **only** `move.is_game`, and uses the same rules path as live (`TurnBasedGame`: `reset` / `apply_move` / `render`). Do not maintain a second engine in `parse_replay`.

### 5.5 Three hosts, one log

| Host | Class | `record=False` | Bots | Timeouts | Queries |
|------|--------|----------------|------|----------|---------|
| Live | `LiveContext` | Honor | `bot_move` via `request_input` | Honor | `handle_query` |
| CLI | `MockContext` | Honor | Scripted / first source | Passed through | Optional |
| Replay | `ReplayContext` | n/a — `request_*` raise | n/a | n/a | n/a |

CLI `--replay` and live `recorded_moves` must be the same kind of stream so `parse_replay` can be golden-tested with `scripts/run_game.py`.

`LiveContext` must not expose `_session` (or any Discord object) to game code. `GameContext` is a facade.

### 5.6 Presentation

Games build dataclasses in `strife/presentation/components.py`. Emoji is a **string key**. Files are `ViewFile` bytes. Do not set `route_prefix` / `resource_id` on board controls — the live surface already uses `g_move:` + thread id.

`ChannelSelect` / `UserSelect` are **host widgets**, not plugin API. `MoveParam.autocomplete` is `(current: str) -> list[str]` — no Discord `Interaction`. The test game may exercise channel/user selects; shipped games must not need Discord types.

Compiler limits (40 components, 4000 characters, 100-char `custom_id`) fail in the host. CLI should surface the same `LayoutError` rather than only printing node counts.

---

## 6. Host contributor contract

Everything above the wall can change without games changing. Contributors extending the host keep these invariants:

1. **Occupancy is transactional.** `create_lobby`, `_start` / `promote`, `_finalize`, timeout takeover, rematch, and cancel each have a rollback that cannot leave a live `GameSession` with released users, or a lobby in `lobbies` with no reserved creator.
2. **One lobby action module.** Buttons and slash call the same methods. Settings views are pure functions; they receive `owner_ids`, they do not call `get_settings()`.
3. **Persist then release.** Match row (+ moves) is durable before rematch is offered. Persist failure notifies the thread, skips rematch, then still releases occupancy so players are not trapped.
4. **Per-seat deadlines.** `request_inputs` must not share one `last_move_at` across waiters. AFK must not fire the same seat twice.
5. **Don’t hold `lobby.lock` across Discord HTTP** (thread create, `add_user`, message sends). Set `starting`, drop the lock, re-check.
6. **Presentation that games import must stay Discord-free.** Thread headers, compiler, `ViewSurface`, HMAC, and owner badges live outside `game_ui.py` / `components.py` plugin paths.
7. **Tests pin the contract:** `Move` shapes, `query=True` vs sources, `record=False` on all three hosts, plugin.toml stamps metadata version, occupancy rollback, and at least one social game’s CLI log vs `parse_replay`.

---

## 7. Remaining follow-ups

The critical and major holes listed when this contract was written are implemented. Left for later incremental work:

- Slash `/strife lobby *` still exists as a parallel surface. Buttons and slash now share start/occupancy rules; further deletion of duplicate slash is optional.
- Live matches are still persisted only at finalize (kill -9 loses the in-progress game).
- Profile `list_recent` still uses a per-row count subquery.
- `config/emoji.yaml` may still list leftover cache keys until the next `strife/emoji`.
- `errors.not_on_whitelist` is unused copy.
- Spyfall still has a `bot_move` helper during discussion; Coup now goes through `request_input`.

---

## 8. Badly implemented / inefficient systems

These are the host internals that make the contract in §1–§6 hard to keep. Incremental extraction, not a rewrite.

Occupancy, start rollback, per-seat timeouts, rematch snapshots, catalog Play, and the GameContext facade are implemented. What follows is still expensive or unfinished.

### Dual lobby UI

`lobby_flow.py` (~629), `settings_view.py` (~706), `lobby_commands.py` (~409), plus controls/moderation mixins, still implement some actions twice. Kick-from-settings refreshes the tab; `kick_member` does not. Privacy is creator-only on every path.

**Intended:** `LobbyActions` used by router and slash. Settings is a view. Delete or hide the duplicate slash once buttons cover the action (`/strife lobby join` stays).

### Discord chatter on the hot path

Intents include members, but `MemberCacheFlags.none()`, so join/ready may `fetch_guild` / `fetch_member` / `fetch_channel` while holding the lobby lock. Match start no longer holds that lock across thread create. Each new pending input still edits the board and the header.

### Persistence is end-shaped

Repos are one 542-line module. A match row exists only at finalize. Kill -9 leaves Discord threads and no replay. Profile `list_recent` runs a correlated `count(*)` per row. `apply_results` is per-player upsert.

**Intended:** insert match on start, append/batch moves, mark status on end; split repositories; join for player counts.

### Routing and presentation

`STRIFE_SIGNING_KEY` (else a hash of the bot token) signs `custom_id`s with a 16-byte HMAC. The payload cache is capped, TTL is 7 days, and teardown invalidates by `resource_id`. `emoji.reupload` still deletes every application emoji before upload.

### Plugins as live surgery

`PluginManager` clones into a temp dir, validates `plugin.toml`, then swaps onto `plugins/<key>/`. It does not pip-install or pip-uninstall in a live process; missing extras load on the next boot. Uninstall writes `plugins.yaml` and deletes git files, then the command wipes DB. `games.yaml` is the playability overlay only.

Wipe DB still happens after filesystem success; leftover history can be wiped by re-running uninstall.

### Game-author helpers that don’t exist

Tic-tac-toe / Connect Four share `TurnBasedGame` and stay small. Coup / Mafia / Spyfall / Liar’s Dice each reimplement phase windows, peek, group `record=False`, and a second `parse_replay`. There is no host helper for “phase + group resolution + peek,” so the largest plugins drift from §5.4.

`run_cpu` is used by bots (and Chess live render) but Chess `bot_move` still `asyncio.sleep(0.5)` on the event loop; Liar’s Dice `sleep(4)` in `play()`. Replay Chess render is **not** offloaded.

### Config

`plugins.yaml` is presence (`removed` builtins, git `installed` sources). `games.yaml` is playability (`enabled`, timeouts). Install creates a missing `games.yaml` row; it does not flip `enabled` on an existing row. Owner badges are passed in as `owner_ids`; presentation no longer calls `get_settings()`.

---

## 9. Alignment work

Items 1–4, 6–8, and 10 from the original list are done. Remaining:

1. Optionally delete duplicate `/strife lobby *` commands once Settings covers them (`/strife lobby join` stays).
2. Insert a match row at start and append moves (survives kill -9).
3. Diff/upsert application emoji instead of delete-all.

Do not add new `GameContext` methods until they exist on live **and** CLI and appear in this file.
