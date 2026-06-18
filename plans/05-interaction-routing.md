# 05 - Stateless Interaction Routing

The `custom_id` codec, the overflow payload cache, and the gateway/router that turns raw component interactions into game/replay/lobby actions (Architecture section 3). Per Decision D4 there is **no** lazy rehydration: a click on a game that is not in memory returns an ephemeral "Game Ended". Depends on [04-presentation-components-v2.md](04-presentation-components-v2.md).

---

## 1. `custom_id` grammar

```
<prefix><resource_id>/<body>
```

- `<prefix>` - a constant token ending in `:` identifying the route family.
- `<resource_id>` - an integer locating the in-memory or persisted target.
- `<body>` - the encoded source + payload (Section 2), or `~<token>` for an overflow pointer (Section 3).

### Prefix registry (`strife/routing/prefixes.py`)

Game / replay / rematch families (Architecture section 3; `spectate:` and `peek:` are **removed**):

- `g_move:` - game move button. `resource_id = thread_id`.
- `g_select:` - game select-menu input. `resource_id = thread_id`.
- `r_nav:` - replay viewer navigation. `resource_id = match_id`.
- `rematch:` - rematch vote. `resource_id = thread_id`.

Lobby families (Architecture sections 6A/6B; `resource_id = lobby thread id`):

- `lobby_join:`, `lobby_leave:`, `lobby_ready:`, `lobby_assign:` (assign roles), `lobby_settings:` (open ephemeral panel), `lobby_start:`.
- `lobby_role:` (per-player role select), `lobby_priv:` (privacy select), `lobby_reset_priv:`, `lobby_opt:` (match-option select), `lobby_reset_rules:`, `lobby_end:`.

`ResourceID` unification: live lobby and live game both key on their **thread id** (the lobby thread becomes the match thread); replay keys on the persisted **match id**.

---

## 2. Codec - `strife/routing/custom_id.py`

Structured payloads are packed with `msgpack` then base64url-encoded (Architecture's "binary bit-packing into MessagePack ... encoded via Base64"). This avoids verbose query strings and maximizes payload room within the 100-char limit.

```python
@dataclass(frozen=True)
class Route:
    prefix: str          # includes trailing ':'
    resource_id: int
    source: str          # the component's logical source (e.g. "tile_00")
    payload: dict        # decoded arguments

class CustomIdEncoder:
    def __init__(self, cache: PayloadCache, *, limit: int = 100): ...

    def encode(self, prefix: str, resource_id: int, source: str, payload: dict | None) -> str:
        body = {"s": source, "p": payload or {}}
        blob = base64.urlsafe_b64encode(msgpack.packb(body)).rstrip(b"=").decode()
        cid = f"{prefix}{resource_id}/{blob}"
        if len(cid) <= self.limit:
            return cid
        token = self.cache.put(msgpack.packb(body))           # overflow (Section 3)
        return f"{prefix}{resource_id}/~{token}"

    def decode(self, custom_id: str) -> Route:
        prefix, rest = custom_id.split(":", 1); prefix += ":"
        rid_str, _, body = rest.partition("/")
        if body.startswith("~"):
            raw = self.cache.get(body[1:])
            if raw is None:
                raise PayloadExpired()
        else:
            pad = "=" * (-len(body) % 4)
            raw = msgpack.unpackb(base64.urlsafe_b64decode(body + pad))
        # raw here is the msgpack bytes for overflow; unpack consistently:
        data = msgpack.unpackb(raw) if isinstance(raw, (bytes, bytearray)) else raw
        return Route(prefix, int(rid_str), data["s"], data.get("p", {}))
```

- Compact keys (`s`, `p`) shave bytes.
- `encode` is synchronous to keep the [04](04-presentation-components-v2.md) compiler synchronous; the v1 in-memory cache is synchronous. Adopting Redis later means making the cache (and this path) async - noted as the only refactor needed for that swap.
- `PayloadExpired` and any malformed id surface as a user-facing "Game Ended / expired" message (Section 5).

---

## 3. Overflow payload cache - `strife/routing/cache.py`

For exceptionally large state (rare), offload the payload and embed a short pointer (`~<token>`), per Architecture section 3.

```python
class PayloadCache(Protocol):
    def put(self, value: bytes, *, ttl: float = 3600) -> str   # returns short token
    def get(self, token: str) -> bytes | None

class InMemoryPayloadCache(PayloadCache):
    # dict[token] -> (value, expires_at); short base32 tokens; lazy sweep on access
    ...
```

- v1: in-memory, single-process (consistent with D4). Tokens are short (e.g. 8 base32 chars).
- Redis-ready: a `RedisPayloadCache` implementing the same protocol can replace it later (the only consumer is the encoder).
- Because the cache is volatile, an overflow pointer that outlives a restart simply yields `PayloadExpired` -> "Game Ended", which is acceptable under D4.

---

## 4. Gateway + Router

### Gateway hook

`StrifeBot` listens for interactions and forwards **component** interactions to the router; application-command interactions remain with the command tree.

```python
async def on_interaction(self, interaction: discord.Interaction) -> None:
    if interaction.type is discord.InteractionType.component:
        await self.router.dispatch(interaction)
```

### Router - `strife/routing/router.py`

```python
class InteractionRouter:
    def __init__(self, sessions: SessionRegistries, replay: ReplayService,
                 lobby: LobbyService, lifecycle: LifecycleService,
                 encoder: CustomIdEncoder, text: TextConfig): ...
    async def dispatch(self, interaction: discord.Interaction) -> None: ...
```

Dispatch cycle (Architecture section 3):

```mermaid
sequenceDiagram
    participant Discord
    participant Gateway as on_interaction
    participant Router
    participant Reg as SessionRegistries
    participant Session as GameSession
    Discord->>Gateway: component interaction
    Gateway->>Router: dispatch(interaction)
    Router->>Router: decode custom_id -> Route
    alt g_move / g_select
        Router->>Reg: lookup active game by resource_id (thread id)
        alt found
            Router->>Session: submit(actor, source, payload)
            Session->>Session: resolve pending input future
            Router->>Discord: defer (ack); session edits the message
        else missing
            Router->>Discord: ephemeral "Game Ended"
        end
    else r_nav
        Router->>Router: parse frame/seek from payload
        Router->>Discord: render target frame (edit message)
    else rematch
        Router->>Router: record vote; if unanimous reset thread to lobby
    else lobby_*
        Router->>Router: delegate to LobbyService handler
    end
```

Branch responsibilities:

- `g_move` / `g_select`: `sessions.active_games.get(resource_id)`. If present, build an `InteractionInput(actor=interaction.user, source=route.source, args=route.payload, interaction=interaction)` and call `session.submit(input)`. The session validates whose turn it is (and ownership for hidden views), applies/queues the input, resolves the pending input future, and updates the shared message via its `ViewSurface`. The router defers the interaction (`interaction.response.defer()`) before/while the session edits, so Discord receives an ack within 3 s. If the session is absent -> ephemeral `common.game_ended`.
- `r_nav`: stateless. Parse `frame`/`mode=seek`/`owner` from payload; delegate to `ReplayService.render_frame(match_id, frame, interaction)` ([10](10-replay-and-profile.md)). Optional owner guard: only the user who opened the replay (`owner` in payload) may navigate; others get an ephemeral notice. No session lookup, no DB write.
- `rematch`: delegate to `LifecycleService.register_rematch_vote(thread_id, interaction.user)` ([08](08-session-lifecycle.md)).
- `lobby_*`: delegate to the matching `LobbyService` handler ([07](07-matchmaking-and-lobby.md)), passing the parsed `Route` and interaction.

### Acknowledgement strategy

- Default for game/lobby mutations: `await interaction.response.defer()` then the owning service edits the message via `ViewSurface.update` (a separate `message.edit`). This cleanly separates "ack the click" from "re-render the board".
- Where a single atomic swap is preferable (e.g. replay frame change), use `interaction.response.edit_message(view=compiled)` directly.
- Never leave an interaction unacknowledged; the router wraps each branch in try/except and, on unexpected error, sends an ephemeral generic error and logs with the decoded `Route`.

### Concurrency

The router holds no global lock. Per-session serialization is the `GameSession`'s `asyncio.Lock` ([06](06-game-engine-api.md)); concurrent clicks on the same game queue behind it. Lobby mutations are serialized by a per-lobby lock in the `LobbyService`.

---

## 5. Error handling matrix

- Active game missing (`g_move`/`g_select`) -> ephemeral `common.game_ended`.
- `PayloadExpired` (overflow token gone) -> ephemeral `common.game_ended`.
- Replay match not found (`r_nav`) -> ephemeral "match not found".
- Not-your-turn / not-allowed (raised by session) -> ephemeral `common.not_your_turn` / `common.forbidden`.
- Unknown prefix or malformed id -> log at WARNING + ephemeral generic error.

---

## 6. Deliverables checklist

- [ ] `strife/routing/prefixes.py` (prefix constants + family helpers).
- [ ] `strife/routing/custom_id.py` (`CustomIdEncoder.encode/decode`, `Route`, `PayloadExpired`).
- [ ] `strife/routing/cache.py` (`PayloadCache` protocol + `InMemoryPayloadCache`).
- [ ] `strife/routing/router.py` (`InteractionRouter.dispatch` with all prefix branches + ack strategy + error matrix).
- [ ] `on_interaction` gateway hook in `StrifeBot`.
- [ ] Unit tests: encode/decode round-trip, overflow path, 100-char boundary, decode of malformed ids ([13](13-testing.md)).
