# Game development

Build a game, run it locally, ship it. Method tables: [game-api.md](game-api.md). Install/uninstall: [plugins.md](plugins.md). Player/operator surfaces: [interfaces.md](interfaces.md).

## Quick start

1. Builtin: `python scripts/scaffold_game.py my_game "My Game"`
2. Or **Use this template** on `templates/game-plugin/`, then `strife/install <git-url>`
3. Implement `play()` in `game.py`. Third-party libs go in `plugin.toml`, not the platform `pyproject.toml`
4. `python scripts/run_game.py my_game`

No `bot.py` or `strife.session` edits. The host loads `plugin.toml` at startup.

## Turn-based games

Subclass `TurnBasedGame`. See [`strife/games/tictactoe/`](../strife/games/tictactoe/).

```python
from strife.engine import TurnBasedGame, game_metadata_from, PlayerCount, PlayerOrder, Move

@game_metadata_from(
    key="my_game",
    name="My Game",
    summary="Short tagline",
    player_count=PlayerCount(fixed=2),
    player_order=PlayerOrder.RANDOM,
)
class MyGame(TurnBasedGame):
    def reset(self) -> None: ...
    def apply_move(self, move: Move) -> None: ...
    def render(self, ctx, *, lead=None, prefix_emoji=None) -> LayoutView: ...
    async def play(self, ctx) -> GameOutcome:
        move = await self.take_turn(ctx, {"tile_00"})
        ...
    async def bot_move(self, difficulty, seat) -> Move: ...
```

`take_turn` renders, waits, and calls `apply_move` — live play and replay share that path. Wrap live buttons in `add_controls(container, ctx, row)` so they disappear in replay.

Override `render_final()` if the finished board looks different.

## Metadata and settings

Attach metadata with `@game_metadata_from(...)` or `@game_metadata(META)`.

In `plugin.toml`: `version` is this game’s semver; `platform_version` is the API it targets (now `1.0.0`). The host copies both onto metadata and skips the game if the platform doesn’t match. See [game-api.md](game-api.md#versions).

`changelog.toml` sits next to `plugin.toml`. Newest `[[release]]` first. Bump it when you bump `version`. Players see it in `/strife about` → Changes.

Lobby knobs are `SettingOption`. Read them with `self.setting("key")` (falls back to the default).

```python
SettingOption(
    key="first_move",
    title="First Move",
    description="Who plays X (moves first)",
    type=OptionType.CHOICE,
    default="random",
    choices=("random", "creator"),
    emoji="first_move",
    choice_emojis=(("random", "restart"), ("creator", "creator")),
)
```

Missing `choice_emojis` fall back to the option’s `emoji`, then `"pointing"`.

Capabilities:

- `supports_replay=True` (default) — implement `parse_replay`, or use `TurnBasedGame`
- `bots=(BotSpec(...),)` — implement `bot_move()`
- `supports_player_removal=True` — implement `remove_player()`

## Game loop

| Call | When |
|------|------|
| `ctx.request_input(view, actor=seat, sources={...})` | One player |
| `ctx.request_inputs(view, actors={...}, until="all"\|"any")` | Several players |
| `ctx.request_inputs(..., record=False)` | Collect clicks without logging each one |
| `ctx.update(view)` | Refresh the board |
| `ctx.send_private(seat, view)` | DM hidden info |
| `ctx.record_event(source, arguments)` | Log a non-input event |
| `ctx.respond_query(view)` | Reply to a peek / query button |

| Component | `Move.args` |
|-----------|-------------|
| Button | `{}` |
| Single select | `{"value": "id"}` |
| Multi select | `{"values": ["a", "b"]}` |

## Buttons

**Moves** must be in `sources`. They resolve `request_input` and go in the log.

```python
row.add_button(Button(source="vote_guilty", label="Guilty", style=ButtonStyle.DANGER))
move = await ctx.request_input(view, actor=seat, sources={"vote_guilty", "vote_innocent"})
```

**Queries** (`query=True`) are peeks, help, or a private panel. They are not moves, even if you list them in `sources`. Handle them in `handle_query`:

```python
row.add_button(Button(source="pass", label="Pass"))
row.add_button(Button(source="peek", label="Peek", emoji="peek", query=True))
move = await ctx.request_input(view, actor=seat, sources={"pass"})

async def handle_query(self, seat, source, ctx) -> bool:
    if source == "peek":
        view = query_panel(ctx, title=f"Your role: {self.role[seat]}", prefix_emoji="user")
        await ctx.respond_query(view)
        return True
    return False
```

Return `True` if you handled it. Use `query_panel` for peeks and query errors — don’t send Discord messages from game code.

**Links** open a URL. No `source`:

```python
Button(label="How to Play", style=ButtonStyle.LINK, url="https://en.wikipedia.org/wiki/Example")
```

**Ephemeral then act** (Coup): a query button opens a private panel; the control *inside* that panel is the real move (`sources={"exchange_select"}`). See [`strife/games/coup/`](../strife/games/coup/).

## Recording and replay

| What | Examples | In the log? | Replay frame? |
|------|----------|-------------|---------------|
| Solo move | Tile click, pass | `game` (auto) | Yes |
| Group resolution | Vote tally | one `game` `record_event` | Yes |
| System | Forfeit, cancel, bot takeover | `system` (host) | Banner / early stop |
| Peek / link | Peek role, rules URL | No | No |

Several players acting at once (votes, night actions): collect with `record=False`, then one `record_event` with the combined result. Don’t log seven vote clicks.

```python
votes = await ctx.request_inputs(
    day_view, actors=set(self.alive), sources={"vote"}, until="all", record=False,
)
await ctx.record_event("day_outcome", {"lynched": lynched, "votes": {...}})
```

`parse_replay` should apply `"day_outcome"`, not `"vote"`. See mafia and spyfall.

Leave `record=True` when each click *is* a replay step (tic-tac-toe tiles).

Don’t record peeks. Don’t emit `forfeit` / `game_end` / `bot_takeover` / `timeout` — the host does that.

### Custom `parse_replay`

`TurnBasedGame` builds frames from `reset` / `apply_move` / `render`. Otherwise:

```python
from strife.engine import ReplayBuilder, iter_replay

builder = ReplayBuilder(ctx)
builder.initial(self.render(ctx, lead="Start", prefix_emoji="loading"))
for step in iter_replay(moves, self.players):
    if step.move.is_game:
        self.apply_move(step.move)
    if not step.frame:
        continue
    view = self.render_final(ctx) if step.terminal else self.render(ctx)
    builder.add(step, view, label="Final" if step.terminal else "Turn")
    if step.terminal:
        break
return builder.build()
```

`iter_replay` skips metadata-only `bot_takeover` rows and hangs the banner on the next real frame. Replay views don’t have to match the live board. Test with `python scripts/run_game.py <key> --replay`.

## Bots

`async def bot_move(self, difficulty: str, seat: int) -> Move` — same `source` strings as your buttons. The host caps the call at 10s.

Heavy search goes through `run_cpu` (`strife.engine.workers`). A tight loop on the event loop freezes the whole bot; the timeout can’t interrupt it.

## Roles and DMs

Declare `roles` (`RoleSpec`) for catalog copy and DMs. Assign in `__init__` / `play()` and set `player.role_key`. If players pick, use `request_inputs`, not the lobby.

`ctx.send_private(seat, view)` DMs a player. `request_inputs(..., until="all")` waits for everyone; `until="any"` returns on the first answer.

## Look and feel

Match catalog / about / settings. Details in [`strife/presentation/style.py`](../strife/presentation/style.py).

- One `Container` per message
- Header `### {emoji} Title`; breadcrumbs use `forward`
- One `-#` subtitle
- Custom application emoji only (no ⚠️ 🕵️ 📍)
- Sentence-case copy, Title Case buttons
- `SECONDARY` default, `PRIMARY` for the main action, `SUCCESS` / `DANGER` only when the click itself confirms or destroys

Plugin art: `<package>/emoji/<stem>.webp` → `{key}_{stem}`. `ctx.emoji.get("duke")` is Coup’s `duke.webp`. Platform chrome (`loading`, `error`, `peek`, …) is in [`base_emojis.py`](../strife/presentation/base_emojis.py); pass `base=True`.

Helpers in [`game_ui.py`](../strife/presentation/game_ui.py): `action_status`, `message_lead`, `game_container`, `query_panel`.

## Forfeits

The host injects `forfeit` and `game_end`. Use `forfeit_outcome()` from [`outcomes.py`](../strife/engine/outcomes.py). If `supports_player_removal`, implement `remove_player(seat)`.

## Checklist

- [ ] Name, summary, player count, tags. Versions live in `plugin.toml`
- [ ] `__init__.py` exports `GAME`
- [ ] `changelog.toml` latest version matches `plugin.toml`
- [ ] `play()` returns `GameOutcome` with per-seat `results` and `player_descriptions`
- [ ] Bots for every declared difficulty
- [ ] Replays work, including forfeits and bot takeovers
- [ ] Query buttons have `query=True` and a `handle_query` handler
- [ ] Replay views hide action rows (`add_controls` or `if not ctx.is_replay`)
- [ ] Group actions: `record=False` + one `record_event`
- [ ] Art in `<package>/emoji/` (`game.webp`, pieces, roles)
- [ ] `python scripts/run_game.py <key>`

## Examples

| Game | Why |
|------|-----|
| [tictactoe](../strife/games/tictactoe/) | Smallest `TurnBasedGame` |
| [connectfour](../strife/games/connectfour/) | Emoji grid, turn-based replay |
| [test](../strife/games/test/) | Every API feature |
| [coup](../strife/games/coup/) | Phases, peek, semantic events |
| [mafia](../strife/games/mafia/) | Roles, DMs, player removal |
