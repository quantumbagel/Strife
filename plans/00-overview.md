# 00 - Strife Build Overview

This is the index and ground-truth document for building **Strife**, a Discord multi-game platform driven by the Components V2 Layouts API. It distills [Architecture.md](../Architecture.md) into an actionable, subsystem-oriented build plan. Read this first, then follow the files in numeric order.

---

## 1. Vision

Strife hosts turn-based and social games inside Discord threads. Each game is a self-registering plugin that builds its UI with a Components V2 abstraction, receives player input through interaction callbacks, and runs as an async coroutine. The platform handles matchmaking lobbies, the component rendering pipeline, stateless interaction routing, persistence of finished matches, deterministic replays, AI bots, and AFK resolution.

The two reference games are **Tic-Tac-Toe** (deterministic, perfect-information, optimal AI) and **Mafia** (hidden roles, phased social deduction, player removal), chosen to exercise every platform subsystem.

---

## 2. Scope

### In scope

- Game plugin API + registry metadata contract (Architecture section 1).
- Components V2 game-facing abstraction compiled to discord.py (section 2).
- Stateless `custom_id` interaction routing + payload codec + overflow cache (section 3).
- Single-process concurrency model with async DB driver (section 4, single-process variant).
- Matchmaking lobbies, settings panel, role selection, bot queueing (sections 5, 6A, 6B).
- User-facing slash command tree and owner admin message-commands (section 5).
- Interactive replay via deterministic re-simulation (section 6C).
- Merged `/strife profile` (win/loss + recent matches) - replaces separate history/profile.
- AFK / turn-timeout handling and unanimous rematch.
- Emoji resolution DB + dynamic synchronization (section 7).
- Reference games: Tic-Tac-Toe and Mafia.

### Out of scope (explicitly removed)

- ELO / ratings / leaderboards and any leaderboard channel posts.
- Cross-server matchmaking (lobbies are created and played within a single guild).
- Spectator registration and Peek snapshots (the `spectate:` / `peek:` prefixes and the game-view Peek/Spectate buttons are removed).
- `/strife feedback` and the feedback table.
- Live game-state persistence / lazy rehydration / horizontal scaling (see Decision D4).

---

## 3. Locked Decisions

These were confirmed during planning and are authoritative across all plan files.

- **D1 - Discord library:** `discord.py` >= 2.6 (native Components V2 via `ui.LayoutView`). The library's component classes map almost 1:1 onto the architecture's component spec.
- **D2 - Runtime:** Python 3.14 (matches the project `.venv`, version 3.14.5).
- **D3 - Database:** PostgreSQL accessed through `asyncpg` with an async connection pool. Schema and migrations are plain `.sql` files applied by a lightweight runner (no ORM).
- **D4 - Concurrency / persistence model:** Single process, single event loop. Active games live **only** in memory; there is **no** live-state rehydration. A bot restart loses in-flight games, and stale component clicks return an ephemeral "Game Ended" message. Only **finished** matches (plus their recorded moves and per-user win/loss totals) are persisted.
- **D5 - Component model:** Strife exposes its **own** component classes (mirroring section 2). A compiler converts the Strife tree into discord.py `ui.*` objects. State changes are applied by re-rendering the tree and editing the message (the architecture's "delta sync" is realized as a full re-render + `message.edit`; Discord reconciles the visual delta).
- **D6 - Custom ID codec:** `[Prefix][ResourceID]/[Payload]`. Structured payloads are packed with `msgpack` and base64url-encoded. If the encoded `custom_id` would exceed Discord's 100-char limit, the payload is offloaded to a TTL key-value cache and only a short pointer is embedded. The cache is an in-memory implementation behind a swappable interface (Redis-ready).
- **D7 - Config:** Secrets in `.env` (loaded via `pydantic-settings`); game configuration in a YAML file; user-facing text strings in a TOML file; emoji mapping cache in a YAML file (rewritten by the `strife/emoji` admin command); the privileged owners list in static config.
- **D8 - Rating:** None. No ELO, no leaderboards.
- **D9 - Matchmaking lock:** A user may be in **one** active lobby or match at a time, enforced **globally** across all guilds the process serves.
- **D10 - AFK resolution order:** On turn timeout (after a warning), resolve in this order: (1) hot-swap the idle human to a bot if the game declares `supports_bots`; else (2) forfeit/remove the idle player if the game declares `supports_player_removal`; else (3) end the game.
- **D11 - Rematch:** A rematch requires **all** current players to click the rematch button; once unanimous, the existing thread is reset to a fresh matchmaking lobby with the same lineup (chat history preserved).
- **D12 - Replay:** Stored as the initial seed/settings plus the ordered list of **concrete logical moves** (including bot and AFK-resolved moves). The replay viewer re-runs the engine headlessly to reproduce each frame. This requires deterministic game logic (all randomness flows through a seeded RNG).
- **D13 - Bots:** Strong AI. Tic-Tac-Toe uses optimal minimax; Mafia uses role-aware heuristic agents whose strength scales with difficulty (a full social-deduction AI is out of reach, so "strong" means a strong **bounded heuristic**).

### Defaulted decisions (reasonable, overridable)

- **F1:** Test stack is `pytest` + `pytest-asyncio` with hand-written Discord interaction/message fakes (not `dpytest`, whose Components V2 / 3.14 support is uncertain).
- **F2:** Top-level Python package is `strife`.
- **F3:** Whitelist/blacklist access lists are retained as part of the private-lobby settings panel (section 6B).
- **F4:** Operational artifacts are limited to a testing strategy (no Docker, CI, or lint/type-check tooling), per scope selection.

---

## 4. Technology Stack

- Language / runtime: Python 3.14 (CPython).
- Discord: `discord.py` >= 2.6 (`discord.ui.LayoutView`, `Container`, `Section`, `TextDisplay`, `MediaGallery`, `Separator`, `ActionRow`, `Button`, `Select`).
- Database: PostgreSQL 14+ via `asyncpg`.
- Serialization: `msgpack` for payload packing; base64url (stdlib `base64`) for transport.
- Config: `pydantic` / `pydantic-settings`, `PyYAML`, stdlib `tomllib` (read) for TOML text.
- Testing: `pytest`, `pytest-asyncio`.

### Dependency list (for `pyproject.toml`)

Pin to the latest compatible release at implementation time; do not invent exact patch versions.

- Runtime: `discord.py>=2.6`, `asyncpg>=0.30`, `msgpack>=1.1`, `PyYAML>=6.0`, `pydantic>=2`, `pydantic-settings>=2`.
- Dev: `pytest>=8`, `pytest-asyncio>=0.24`.

> Python 3.14 compatibility risk: `discord.py` 2.6 and its dependency `aiohttp` must publish wheels/support for 3.14. Verify at install time. If a transitive dependency lags, the fallback is to pin Python 3.13 (this is the single most likely setup snag; it is isolated to [01-foundation.md](01-foundation.md)).

---

## 5. Repository / Package Layout

```
Strife/
├── Architecture.md
├── pyproject.toml
├── .env.example
├── config/
│   ├── games.yaml            # per-game static config (D7)
│   ├── text.toml             # user-facing strings (D7)
│   └── emoji.yaml            # resolved emoji cache, rewritten by strife/emoji
├── assets/emoji/             # source icon images uploaded as app emojis
├── migrations/               # 0001_init.sql, 0002_*.sql ... (D3)
├── strife/
│   ├── __init__.py
│   ├── __main__.py           # process entrypoint
│   ├── bot.py                # discord.py client subclass + setup_hook
│   ├── settings.py           # pydantic-settings (.env)
│   ├── logging.py
│   ├── config/               # loaders: games.py, text.py, owners
│   ├── presentation/         # components.py, compiler.py, message.py, emoji.py
│   ├── routing/              # custom_id.py, cache.py, router.py, gateway.py
│   ├── persistence/          # pool.py, migrator.py, repositories/
│   ├── engine/               # metadata.py, registry.py, session.py, context.py, roles.py, bots.py
│   ├── matchmaking/          # registries.py, lobby.py, lobby_view.py, settings_view.py
│   ├── lifecycle/            # timeout.py, rematch.py
│   ├── commands/             # play.py, strife_group.py, admin.py
│   ├── replay/               # simulator.py, view.py
│   └── games/
│       ├── tictactoe/        # game.py, bot.py
│       └── mafia/            # game.py, roles.py, bot.py
└── tests/
    ├── conftest.py
    ├── fakes/                # fake Discord interaction/message/thread objects
    ├── unit/
    └── integration/
```

---

## 6. Implementation Order

Build in this dependency order. Each links to its detailed plan.

1. [01-foundation.md](01-foundation.md) - project skeleton, settings, bot client, logging, entrypoint.
2. [02-configuration-and-emoji.md](02-configuration-and-emoji.md) - config loaders + emoji resolution/sync.
3. [03-persistence.md](03-persistence.md) - asyncpg pool, migrations, schema, repositories.
4. [04-presentation-components-v2.md](04-presentation-components-v2.md) - Strife component abstraction + discord.py compiler + message manager.
5. [05-interaction-routing.md](05-interaction-routing.md) - custom_id codec, overflow cache, gateway + router.
6. [06-game-engine-api.md](06-game-engine-api.md) - registry metadata, GameSession, turn loop, game-facing context API.
7. [07-matchmaking-and-lobby.md](07-matchmaking-and-lobby.md) - registries, lobby model, lobby + settings views.
8. [08-session-lifecycle.md](08-session-lifecycle.md) - AFK timeout scheduler, rematch, forfeit.
9. [09-commands.md](09-commands.md) - slash command tree + owner admin commands.
10. [10-replay-and-profile.md](10-replay-and-profile.md) - re-simulation engine, replay view, merged profile.
11. [11-reference-game-tictactoe.md](11-reference-game-tictactoe.md) - Tic-Tac-Toe plugin + optimal bot.
12. [12-reference-game-mafia.md](12-reference-game-mafia.md) - Mafia plugin + role-aware bots.
13. [13-testing.md](13-testing.md) - test strategy, fakes, determinism tests.

A practical milestone path: after (1)-(6) plus Tic-Tac-Toe (11) you have an end-to-end playable vertical slice; matchmaking (7), lifecycle (8), commands (9), replay (10), and Mafia (12) layer on top.

---

## 7. Cross-Cutting Conventions

- **Async everywhere:** all I/O (Discord, DB) is awaited on the single event loop; no blocking calls. CPU-heavy bot search (minimax) runs via `asyncio.to_thread` if it could stall the loop.
- **Per-session serialization:** each `GameSession` holds an `asyncio.Lock`; interaction inputs for a session are processed one at a time to avoid state races.
- **No secrets in code or config-in-repo:** only `.env` holds tokens; `.env.example` documents required keys.
- **Stateless UI identity:** never rely on in-memory view objects surviving a restart; everything needed to route a click lives in the `custom_id` (or the TTL cache pointer).
- **Headers:** every top-level view renders a header text display styled `## [emoji] Name of Tab` (Architecture section 6).
- **IDs:** Discord snowflakes and internal match IDs are integers; match "codes" are short human-shareable strings.

---

## 8. Glossary

- **Lobby:** pre-game matchmaking state in a thread; tracks joined players, queued bots, settings, and role selections.
- **Session (GameSession):** a running match; owns the game coroutine, the live component view, the per-session lock, and pending-input futures.
- **Move:** a single recorded logical action `(actor, source, arguments)` applied to a game; the unit of replay.
- **Frame:** the rendered view state at a given turn index, reproduced during replay.
- **Registry metadata:** the static contract a game advertises (identity, players, roles, settings, slash moves, bots, capabilities).
- **Resolver (emoji):** the layer translating semantic icon names into platform emoji strings with unicode fallback.
- **Codec / custom_id:** the serialization scheme that makes interactions self-describing and routable.
