# 13 - Testing

Test strategy using `pytest` + `pytest-asyncio` with hand-written Discord fakes (Decision F1; no `dpytest`). The goal is to verify the platform's logic - codec, routing, sessions, engine determinism, and game rules - without a live gateway. CI/containers are out of scope (F4); tests run with `pytest` locally.

---

## 1. Layout

```
tests/
├── conftest.py
├── fakes/
│   ├── discord.py        # FakeInteraction, FakeResponse, FakeMessage, FakeThread, FakeUser, FakeChannel
│   ├── surface.py        # FakeSurface capturing the latest rendered LayoutView
│   └── repos.py          # in-memory fake repositories
├── unit/
│   ├── test_custom_id.py
│   ├── test_compiler.py
│   ├── test_router.py
│   ├── test_session.py
│   ├── test_roles.py
│   ├── test_registries.py
│   ├── test_config.py
│   ├── test_emoji.py
│   ├── test_tictactoe.py
│   └── test_mafia.py
└── integration/
    ├── test_match_flow.py
    ├── test_rematch.py
    ├── test_afk.py
    ├── test_replay_determinism.py
    └── test_persistence.py     # real Postgres; skipped without TEST_DATABASE_URL
```

`pyproject.toml` sets `asyncio_mode = "auto"` ([01](01-foundation.md)) so async tests need no decorator.

---

## 2. Fakes - `tests/fakes/`

Just enough of the discord.py surface to drive the code under test:

```python
class FakeResponse:
    def __init__(self): self.deferred = False; self.edited = None; self.messages = []
    async def defer(self): self.deferred = True
    async def edit_message(self, *, view=None, **k): self.edited = view
    async def send_message(self, *, view=None, ephemeral=False, **k):
        self.messages.append((view, ephemeral))

class FakeInteraction:
    def __init__(self, user, custom_id, values=None):
        self.user = user
        self.data = {"custom_id": custom_id, "values": values or []}
        self.response = FakeResponse()
        self.message = FakeMessage()
        self.type = InteractionType.component
```

- `FakeMessage.edit(view=...)` stores the last compiled view for assertions.
- `FakeSurface` replaces `ViewSurface` in unit/integration tests and records every Strife `LayoutView` passed to `update/replace`, so tests assert on board/lobby state directly.
- `FakeUser`/`FakeThread`/`FakeChannel` expose only the attributes the code reads (`id`, `display_name`, `mention`, thread creation returning a `FakeThread`).

### Driving matches without Discord

To simulate a human move, call `session.submit(InteractionInput(actor=fake_user, source="tile_00", args={}, interaction=FakeInteraction(...)))` directly - this bypasses the gateway but exercises the real session/turn-loop path. Bot seats auto-resolve. This makes whole matches scriptable and deterministic. Wrap awaits in `asyncio.wait_for(..., timeout=2)` so a logic bug surfaces as a failure, not a hang.

---

## 3. Unit test targets

- `test_custom_id.py`: encode/decode round-trip for representative routes; the 100-char boundary forces the overflow path and a `~token` pointer; `decode` of a malformed or expired id raises `PayloadExpired`/error ([05](05-interaction-routing.md)).
- `test_compiler.py`: Strife tree maps to the expected `discord.ui` structure; size-style prefixes (`## `/`### `); emoji resolution to unicode vs `<:name:id>`; `LayoutError` on >40 components, mixed button+select in one ActionRow, >4000-char text; `disable_all` disables every nested interactive item ([04](04-presentation-components-v2.md)).
- `test_router.py`: each prefix branch - `g_move` to a present session calls `submit` + defers; to a missing session sends ephemeral `game_ended`; `r_nav` renders a frame; `rematch` records a vote; unknown prefix logs + ephemeral; not-your-turn maps to the right text ([05](05-interaction-routing.md)).
- `test_session.py`: `request_input` registers pending and resolves on `submit`; rejects wrong actor / wrong source; bot seat auto-resolves via `bot_move`; `force_move` injects an AFK move; completion finalizes (persist called, board disabled) ([06](06-game-engine-api.md)).
- `test_roles.py`: every `RoleMode`/`RoleFlow` assigns correctly; same seed -> same assignment ([06](06-game-engine-api.md)).
- `test_registries.py`: global user lock - reserve blocks a second placement across guilds; release frees it; `promote` swaps lobby->game keeping the thread id ([07](07-matchmaking-and-lobby.md)).
- `test_config.py`: `games.yaml` defaults merge per game; `text.toml` `get` with placeholders and missing-key behavior ([02](02-configuration-and-emoji.md)).
- `test_emoji.py`: resolver returns custom string when id present, unicode fallback otherwise; non-destructive sync sets ids from a fake application-emoji list ([02](02-configuration-and-emoji.md)).
- `test_tictactoe.py`: win/draw detection across all 8 lines; the **hard bot never loses** (play it against an exhaustive/random opponent over many seeds and assert no loss); determinism for a fixed seed ([11](11-reference-game-tictactoe.md)).
- `test_mafia.py`: role-multiset counts honor `mafia_count`/toggles; night elimination respects doctor protection; majority lynch + tie = no lynch; win conditions (town clear, mafia parity); `remove_player` mid-match ([12](12-reference-game-mafia.md)).

---

## 4. Integration test targets

- `test_match_flow.py`: create lobby ([07](07-matchmaking-and-lobby.md)) -> join/ready -> start -> drive a full Tic-Tac-Toe match (two bots, or scripted humans via fakes) to completion -> assert `MatchRepository.create_finished` was called with the right seats/outcome ([03](03-persistence.md)).
- `test_rematch.py`: finish a match, register a vote from each eligible human, assert the thread resets to a fresh lobby with the same lineup and the same thread id ([08](08-session-lifecycle.md)).
- `test_afk.py`: advance the AFK scheduler's clock past `turn_timeout`; assert Tic-Tac-Toe hot-swaps the idle seat to a bot (move recorded), Mafia forfeit-removes when bots are disabled, and a bot-less/removal-less game ends ([08](08-session-lifecycle.md)).
- `test_replay_determinism.py` (the key D12 guarantee): run a full match capturing `recorded_moves`; `ReplaySimulator.simulate` twice and assert identical frames; assert the replayed final outcome equals the live outcome; for all-bot matches, assert two live runs with the same seed produce identical move lists ([10](10-replay-and-profile.md)).
- `test_persistence.py`: against a real Postgres (`TEST_DATABASE_URL`), run migrations into a temp schema, verify `create_finished` atomicity (match + players + moves + stat deltas commit together), `list_for_user` ordering/paging, and `user_game_stats` increments. Skipped via `pytest.mark.skipif` when `TEST_DATABASE_URL` is unset ([03](03-persistence.md)).

---

## 5. Fixtures - `conftest.py`

- `seed` -> a fixed int; `rng(seed)` -> `random.Random(seed)`.
- `emoji_resolver` -> resolver built from a static in-memory `EmojiConfig` (no Discord).
- `encoder` -> `CustomIdEncoder(InMemoryPayloadCache())`.
- `compiler` -> `Compiler(emoji_resolver, encoder)`.
- `registry` -> `GameRegistry` with Tic-Tac-Toe and Mafia registered.
- `registries` -> fresh `SessionRegistries`.
- `make_session` -> factory building a `GameSession` with a `FakeSurface` for a given game/players/seed.
- `pg_pool` -> (integration) real `asyncpg` pool to `TEST_DATABASE_URL`, migrated into a unique temp schema, torn down after.

---

## 6. Determinism discipline (cross-cutting)

- No test relies on wall-clock time, real network, or unseeded randomness; the AFK scheduler is driven by an injectable clock so timeouts are tested without sleeping.
- The single most important invariant - **a match re-simulated from `seed` + recorded moves reproduces exactly** - is asserted in `test_replay_determinism.py` and is a prerequisite for the replay feature ([10](10-replay-and-profile.md), D12).

---

## 7. Deliverables checklist

- [ ] `tests/conftest.py` + `tests/fakes/` (discord fakes, FakeSurface, fake repos, injectable clock).
- [ ] Unit suites for codec, compiler, router, session, roles, registries, config, emoji, and both games.
- [ ] Integration suites for match flow, rematch, AFK resolution, replay determinism, and (optional) persistence.
- [ ] All async tests bounded by `asyncio.wait_for` to prevent hangs.
- [ ] `pytest` green locally; persistence tests skip cleanly without `TEST_DATABASE_URL`.
