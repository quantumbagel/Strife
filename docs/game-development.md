# Game Development Guide

This guide walks through building a Strife game. For the method reference table, see [game-api.md](game-api.md). For how games are discovered, sandboxed, and exposed on Discord, see [game-architecture.md](game-architecture.md).

## Quick start

1. Scaffold a game: `python scripts/scaffold_game.py my_game "My Game"`
2. Implement `play()` in `strife/games/my_game/game.py`
3. Run locally: `python scripts/run_game.py my_game`
4. Games under `strife/games/` are auto-discovered at bot startup — no `bot.py` edits needed.

## Minimal turn-based game

The simplest path is `TurnBasedGame` ([`strife/engine/turn_based.py`](../strife/engine/turn_based.py)). See [`strife/games/tictactoe/`](../strife/games/tictactoe/) for a complete example.

```python
@game_metadata_from(
    key="my_game",
    name="My Game",
    summary="Short tagline",
    description="Longer description for the catalog.",
    tags=("2p",),
    author="You",
    version="1.0.0",
    author_link=None,
    source_link=None,
    time_estimate="5m",
    difficulty=2,
    player_count=PlayerCount(fixed=2),
    player_order=PlayerOrder.RANDOM,
)
class MyGame(TurnBasedGame):
    def reset(self) -> None: ...
    def apply_move(self, move: MoveRecord) -> None: ...
    def render(self, ctx, *, title, status=None, status_emoji=None) -> LayoutView: ...
    async def play(self, ctx) -> GameOutcome: ...
    async def bot_move(self, difficulty, seat) -> Move: ...
```

Implement `render_final()` when the finished board looks different from a normal turn.

## Metadata and lobby settings

Attach metadata with `@game_metadata_from(...)` or `@game_metadata(META)`.

Lobby settings use `SettingOption` entries. Read them at runtime with `self.setting("key")`, which falls back to the metadata default.

For `OptionType.CHOICE` settings, map each choice value to a custom emoji key with `choice_emojis`:

```python
SettingOption(
    key="first_move",
    title="First Move",
    description="Who plays X (moves first)",
    type=OptionType.CHOICE,
    default="random",
    choices=("random", "creator"),
    emoji="first_move",  # section header emoji
    choice_emojis=(("random", "restart"), ("creator", "creator")),
)
```

Unset choices fall back to the option-level `emoji`, then `"pointing"`.

Declare capabilities explicitly:

- `supports_replay=True` (default) — requires `parse_replay` or `TurnBasedGame`
- `bots=(BotSpec(...),)` — requires `bot_move()`
- `supports_player_removal=True` — requires `remove_player()`

## The game loop

Use `GameContext` inside `play()`:

| Call                                                         | When                              |
|--------------------------------------------------------------|-----------------------------------|
| `ctx.request_input(view, actor=seat, sources={...})`         | One player acts                   |
| `ctx.request_inputs(view, actors={...}, until="all"\|"any")` | Multiple players act              |
| `ctx.request_inputs(..., record=False)`                      | Collect inputs without logging each one |
| `ctx.update(view)`                                           | Refresh the board without waiting |
| `ctx.send_private(seat, view)`                               | DM hidden information             |
| `ctx.record_event(name, args)`                               | Log a non-input event for replays |
| `ctx.respond_query(view)`                                    | Reply to a peek / query button    |

Player inputs are recorded automatically. Use `record_event` for phase transitions and hidden reveals.

**Important:** only **action** inputs (sources listed in `request_input`) and **explicit** `record_event` calls end up in the move log. Query buttons, link buttons, and other auxiliary UI do not — and should not be replayed. See [What gets recorded vs replayed](#what-gets-recorded-vs-replayed) below.

### Interaction argument shapes

| Component | `Move.args` |
|-----------|-------------|
| Button | `{}` |
| Single select | `{"value": "id"}` |
| Multi select | `{"values": ["a", "b"]}` |

## Action buttons vs query buttons vs link buttons

**Doing something is not the same as making a move, and making a move is not the same as logging a replay step.** A board can show many clickable controls. Solo actions (tile clicks) log one step each. Group actions (7 players voting) should log **one** resolution step — see [Group actions](#group-actions-collect-many-record-one) below.

There are three kinds of in-game controls:

### Action buttons (game moves)

Action buttons **must** appear in the `sources` set passed to `request_input` or `request_inputs`. When clicked, they resolve the pending input and are recorded as moves.

```python
row.add_button(Button(source="vote_guilty", label="Guilty", style=ButtonStyle.DANGER))
move = await ctx.request_input(view, actor=seat, sources={"vote_guilty", "vote_innocent"})
```

### Query buttons (peek, help, open ephemeral UI)

Query buttons have a `source` string but are **omitted from `sources`**. When clicked, the router calls `handle_query()` instead of submitting a move. Use them for:

- Peeking at hidden information (role, hand, secret location)
- Opening a private ephemeral panel without submitting an action
- Showing "cannot act" or other validation messages

```python
# On the board — peek sits next to action buttons
row.add_button(Button(source="pass", label="Pass", style=ButtonStyle.SECONDARY))
row.add_button(Button(source="peek", label="Peek Info", emoji="peek", style=ButtonStyle.SECONDARY))

# Waiting for a move — only list action sources
move = await ctx.request_input(view, actor=seat, sources={"pass"})
```

Implement the handler on your game class:

```python
async def handle_query(self, seat: int, source: str, ctx: GameContext) -> bool:
    if source == "peek":
        role = self.role.get(seat, "unknown")
        view = query_panel(ctx, title=f"Your role: {role.title()}", prefix_emoji="user")
        await ctx.respond_query(view)
        return True
    return False
```

Return `True` when handled. Return `False` to fall through to normal move submission (which will fail if the source was not in `sources`).

**Replay:** omit action rows during replay (`if not ctx.is_replay:`) so replays show game state only.

**Ephemeral notices:** use `query_panel` + `ctx.respond_query` so peeks and query errors match platform command styling. Do not send Discord messages from game code.

```python
view = query_panel(ctx, title="Your cards", prefix_emoji="peek", body=hand_text)
await ctx.respond_query(view)
```

### Ephemeral sub-views (query opens, action completes)

Coup uses a two-step pattern for actions that are easier in a private panel:

1. Public board shows a **query** button (e.g. `exchange_open`) — not in `sources`.
2. `handle_query` sends an ephemeral view via `ctx.respond_query` containing the real control (e.g. a `Select` with `source="exchange_select"`).
3. `play()` waits with `sources={"exchange_select"}` — clicks on the ephemeral control submit the move.

The launcher is a query; the control inside the ephemeral message is an action. See [`strife/games/coup/`](../strife/games/coup/) (`exchange_open`, `lose_influence_open`).

### Link buttons (external URLs)

Link buttons open a URL and never touch the game loop. No `source` is needed:

```python
Button(
    label="How to Play",
    style=ButtonStyle.LINK,
    url="https://en.wikipedia.org/wiki/Example",
)
```

See [`strife/games/test/`](../strife/games/test/) for link buttons mixed with action and select components.

## What gets recorded vs replayed

Replays rebuild public game state from the **move log**, not from every button click. A control can be a real game action live without deserving its own replay step.

Think in four buckets:

| Bucket | Examples | Kind | Appears in replay? |
|--------|----------|------|-------------------|
| **Solo moves** | Tile click, pass, single bid | `game` | Yes — one frame per entry |
| **Group resolutions** | 7-player vote tally | `game` (`record_event` once) | Yes — one frame |
| **System events** | Forfeit, cancel, bot takeover | `system` | Metadata only (banner, early stop) |
| **Queries** | Peek role, peek hand | *(not logged)* | No |
| **Links / static UI** | Rules URL | *(not logged)* | No |

### Group actions: collect many, record one

When several players act at once (votes, simultaneous bids, night actions resolved together), each click matters live but replay should show **one action** with the combined result.

1. Collect with `record=False` so individual clicks stay out of the log.
2. Apply results to game state in `play()`.
3. Emit a single `record_event` with everything `parse_replay` needs — this is a **`game`** entry.
4. In `parse_replay`, handle only `game` entries for state — check `move.is_game` before applying.

System entries (`forfeit`, `game_end`, `bot_takeover`) are recorded by the engine. Do not emit these from game code. On timeout with bots enabled, the lifecycle writes a **`bot_takeover`** system entry first, then the bot's move as a separate **`game`** entry — never flags on the move itself.

```python
# 7 players vote — one replay step, not seven
votes = await ctx.request_inputs(
    day_view,
    actors=set(self.alive),
    sources={"vote"},
    until="all",
    record=False,  # do not log each vote click
)
tally = Counter()
for seat, move in votes.items():
    target = parse_target(move)
    if target is not None:
        tally[target] += 1
lynched = resolve_lynch(tally)

await ctx.record_event("day_outcome", {
    "lynched": lynched,
    "votes": {seat: m.args.get("target") for seat, m in votes.items()},
    "history": list(self.history),
})
```

`parse_replay` then handles `"day_outcome"` once and renders a single "Lynch Vote" frame. See [`strife/games/mafia/`](../strife/games/mafia/) and [`strife/games/spyfall/`](../strife/games/spyfall/) (`accusation_resolve`).

Use the default `record=True` when each input **is** its own replay step (tic-tac-toe tile clicks, Coup's main action).

### Rules of thumb

1. **Solo actions** — leave `record=True` (default); one input → one log entry → one replay frame.
2. **Simultaneous group actions** — `record=False` while collecting, then one `record_event` for the resolution.
3. **Peek / help** — `handle_query`; never recorded.
4. **In replay views**, omit action rows (`if not ctx.is_replay:`) so only game state is shown.

### Common mistakes

```python
# Wrong — 7 vote clicks create 7 replay frames
votes = await ctx.request_inputs(view, actors=voters, sources={"vote"}, until="all")
for seat, move in votes.items():
    await ctx.record_event("cast_vote", {"seat": seat, ...})

# Wrong — parse_replay handles per-vote sources when you wanted one frame
if move.source == "vote":
    ...

# Right — collect silently, record resolution once
votes = await ctx.request_inputs(..., record=False)
await ctx.record_event("day_outcome", {"votes": {...}, "lynched": seat})
# parse_replay: only handle "day_outcome"
```

```python
# Wrong — peek is not a move; do not record it
await ctx.record_event("peek", {"seat": seat, "role": role})

# Wrong — listing peek in sources makes it a move
move = await ctx.request_input(view, actor=seat, sources={"pass", "peek"})
```

## Bots

Implement `async def bot_move(self, difficulty: str, seat: int) -> Move`. Return a `Move` with the same `source` string your buttons/selects use. The session time-boxes every call to 10s, including `self.bot_move(...)` from `play()`.

Put CPU-heavy search in `asyncio.to_thread` (see tic-tac-toe / mafia). A tight loop on the event loop will freeze the bot; the timeout cannot interrupt it.

## Replays

### TurnBasedGame (recommended for turn-based games)

Implement `reset`, `apply_move`, and `render`. Replay frame building is handled for you.

### Custom replays

Use helpers from [`strife/engine/replay.py`](../strife/engine/replay.py):

```python
from strife.engine.replay import ReplayBuilder, system_replay_info, is_terminal_replay_move

builder = ReplayBuilder(ctx)
builder.initial_frame(self.render(ctx, title="Action 1", ...))
for index, move in enumerate(moves):
    takeover = system_replay_info(self.players, move)
    if move.is_game:
        self.apply_move(move)
    if is_terminal_replay_move(move, index, len(moves)):
        builder.after_move(move, self.render_final(ctx), label="Final", takeover_info=takeover)
        break
    builder.after_move(move, self.render(...), label=f"Action {index + 2}", takeover_info=takeover)
return builder.build()
```

## Roles, private messages, and simultaneous input

- Roles: declare `role_mode`, `role_flow`, and `roles` in metadata. Override `validate_roles()` if needed. Assigned roles appear on `player.role_key`.
- Private messages: `await ctx.send_private(seat, view)` sends a DM; record the event for replay.
- Simultaneous input: `ctx.request_inputs(..., until="all")` waits for every actor; `until="any"` returns on the first response.

See [`strife/games/test/`](../strife/games/test/) for a guided tour of every API feature.

## Ephemeral queries (`handle_query`)

See [Action buttons vs query buttons vs link buttons](#action-buttons-vs-query-buttons-vs-link-buttons) above for the full pattern. In short: put peek/help buttons on the board, leave them out of `sources`, and handle them in `handle_query()`. Used by Coup, Mafia, Spyfall, and Liars Dice.

## Presentation style

Platform commands (catalog, about, settings, profile, errors) set the visual language. Games should match it. The contract lives in [`strife/presentation/style.py`](../strife/presentation/style.py).

- One `Container` per message
- Header: `### {custom emoji} Title` — breadcrumbs use the `forward` emoji
- One `-#` subtitle for status, counts, or a hint
- Visible separators between sections; custom application emoji only (no ⚠️ 🕵️ 📍 ⏱️ decoration)
- `**Section Title**` headings, calm sentence-case copy, Title Case button labels
- Buttons: `SECONDARY` default, `PRIMARY` for the main CTA, `SUCCESS` / `DANGER` only when the action itself confirms or destroys
- Peeks, query errors, and slash-command feedback use the same header/body panels — never bare text

[`strife/presentation/game_ui.py`](../strife/presentation/game_ui.py):

- `action_status(ctx, player, prefix_emoji=...)` — standard "who can act" line
- `message_lead(container, text, emoji=..., prefix_emoji=...)` — optional contextual lead above game content
- `game_container(ctx, lead=..., prefix_emoji=...)` — container with optional lead
- `query_panel(ctx, title=..., prefix_emoji=..., body=...)` — ephemeral peek / notice
- `ctx.respond_query(view)` — send that panel (host compiles and replies)

Use `message_lead` for phase-specific context (e.g. "Waiting for votes..."). Omit the lead for self-explanatory boards.

## Forfeits and player removal

The engine injects `forfeit` and `game_end` moves into the log. Use `forfeit_outcome()` from [`strife/engine/outcomes.py`](../strife/engine/outcomes.py) to build a standard outcome when a forfeit ends the game.

If `supports_player_removal` is set, implement `remove_player(seat)` to update alive-player tracking.

## Checklist 

- [ ] Metadata complete (name, summary, player count, tags)
- [ ] `play()` returns `GameOutcome` with per-seat `results` and `player_descriptions`
- [ ] Bots work for every declared difficulty
- [ ] Replays render correctly (including forfeits and bot takeovers)
- [ ] `final_view()` shows a sensible end state (optional but recommended)
- [ ] Game emoji configured in `config/emoji.yaml`
- [ ] Run locally with `python scripts/run_game.py <key>`
- [ ] Query buttons (peek, etc.) omitted from `sources` and handled in `handle_query()`
- [ ] Replay views show game state only — omit action rows when `ctx.is_replay`
- [ ] Group actions use `request_inputs(..., record=False)` plus one `record_event` for replay
- [ ] `parse_replay` handles resolution events, not every per-player click source

## Examples by complexity

| Game                                        | Why read it                                       |
|---------------------------------------------|---------------------------------------------------|
| [tictactoe](../strife/games/tictactoe/)     | Minimal `TurnBasedGame`, bots, decorator metadata |
| [connectfour](../strife/games/connectfour/) | Turn-based replay, emoji grid UI                    |
| [test](../strife/games/test/)               | Every API feature in one place                    |
| [coup](../strife/games/coup/)               | Multi-phase flow, `handle_query`, semantic events |
| [mafia](../strife/games/mafia/)             | Roles, private messages, player removal           |
