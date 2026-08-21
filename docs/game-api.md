# Game API Reference

This document describes the contract for implementing a Strife game.

## Overview

1. Subclass `Game` in `strife/games/<key>/game.py`.
2. Attach `GameMetadata` via `@game_metadata(...)` or `GameClass.metadata = META`.
3. Games under `strife/games/` are auto-discovered at startup.

Runtime interaction uses `GameContext`: request player input, update the board, send private messages, record non-input events, and reply to query buttons. Games never see Discord types.

Import the plugin surface from `strife.engine` and `strife.presentation` — not persistence, the view compiler, or the live host (`strife.session`). Live inputs and replay log rows are the same `Move` type (`args`, `source`, `kind`). `arguments` is a compatibility alias of `args`.

## Versions

This host is **platform 1.0.0** (`PLATFORM_VERSION` in `strife.engine`).

Each game declares two independent semver strings on `GameMetadata`:

| Field | Meaning |
|-------|---------|
| `version` | The plugin's own version (Tic-Tac-Toe 1.0.0 vs Coup 1.0.0 are unrelated) |
| `platform_version` | The game API this plugin was written for |

Registration skips a game when `platform_version` is not compatible with the host: same major, and the host is greater than or equal to the target (minor, then patch). A 1.0.0 game runs on 1.2.0; a 1.2.0 game does not run on 1.0.0; a 2.0.0 game does not run on 1.x.

```python
@game_metadata_from(
    key="my_game",
    name="My Game",
    version="1.0.0",
    platform_version="1.0.0",
    ...
)
```

## Required methods

| Method                       | Required when                      | Purpose                                            |
|------------------------------|------------------------------------|----------------------------------------------------|
| `play(ctx)`                  | Always                             | Main game loop; return `GameOutcome` when finished |
| `parse_replay(moves, ctx)`   | `metadata.supports_replay`         | Build a list of `ReplayFrame` for match replays    |
| `bot_move(difficulty, seat)` | `metadata.bots` is non-empty       | Choose a move for bot players                      |
| `remove_player(seat)`        | `metadata.supports_player_removal` | Update game state when a player leaves mid-game    |

Registration validates these capabilities and logs warnings for missing implementations.

## Optional hooks

| Method                                                  | Purpose                                                  |
|---------------------------------------------------------|----------------------------------------------------------|
| `final_view(ctx, outcome)`                              | Custom end-state UI shown after the game ends            |
| `handle_query(seat, source, ctx)` | Ephemeral peek / auxiliary UI (return `True` if handled) |

## Action buttons vs query buttons vs link buttons

**Not every clickable control is a move.** Players may peek, open help, or launch ephemeral UI without advancing the game. Those interactions are live-only: they are not written to the move log and should not produce replay frames.

Not every control on the board is a game move. Strife supports three kinds of interactive elements:

| Kind | How to build | In move log? | In replay? |
|------|--------------|--------------|------------|
| **Solo move** | `request_input(..., sources={...})` | Yes (auto, default) | Yes — one frame |
| **Group input** | `request_inputs(..., record=False)` + `record_event` | One resolution event | Yes — one frame |
| **Query** | `Button(..., query=True)` + `handle_query` | No | No |
| **Link** | `Button(style=ButtonStyle.LINK, url=...)` | No | No |

### Query buttons (`handle_query`)

Use query buttons for read-only or auxiliary UI that should **not** advance the game: peek at hidden info, open an ephemeral sub-view, or show validation errors.

Mark them `query=True`. That is enough — they are not moves even if listed in `sources`. When a player clicks, the router calls `handle_query`. If it returns `True`, the interaction is done.

```python
async def handle_query(self, seat, source, ctx) -> bool:
    if source == "peek":
        view = query_panel(ctx, title=f"Role: {self.role[seat]}", prefix_emoji="peek")
        await ctx.respond_query(view)
        return True
    return False
```

```python
row.add_button(Button(source="peek", label="Peek", query=True, style=ButtonStyle.SECONDARY))
move = await ctx.request_input(view, actor=seat, sources={"pass"})
```

Hide action rows during replay with `add_controls(container, ctx, row)` (no-op when `ctx.is_replay`).

**Examples:** Mafia (peek role), Spyfall (peek location), Coup (peek cards, `exchange_open` ephemeral launcher).

### Link buttons

External links need no `source`:

```python
Button(label="Rules", style=ButtonStyle.LINK, url="https://example.com/rules")
```

## Move logging

The move log drives replay. Each entry has a **kind**:

| Kind | Recorded by | Examples | Replay role |
|------|-------------|----------|-------------|
| `game` | `request_input` (default), `record_event` | tile click, `day_outcome`, `accusation_resolve` | Apply state / render frames |
| `system` | Engine only (`_record_system`) | `forfeit`, `game_end`, `bot_takeover` | Metadata (takeover banners, early end) — not game rules |

Games should only emit **`game`** entries via `record_event`. System events are injected by the session and lifecycle (forfeits, cancellations, bot takeover).

`parse_replay` should iterate the full log (to preserve order and handle system events via `system_replay_info`) but only apply **`game`** entries to state. Check `move.is_game` before updating game state. `TurnBasedGame` does this automatically.

`total_turns` on saved matches counts **game** log entries (actions) only.

### Recording patterns

| Pattern | Recording |
|---------|-----------|
| Solo move (`request_input`, default) | Auto-recorded as `game` |
| Group input (`request_inputs`, `record=False`) | Not auto-recorded — emit one `game` `record_event` after |
| Resolution / phase change | `record_event("day_outcome", {...})` → `game` |
| Query / peek | Not recorded |
| Forfeit / timeout / cancel | Engine → `system` |

Player inputs with `record=True` (default) are stored as `game` entries automatically. Do not call `record_event` for them unless you need custom argument shapes.

For simultaneous actions (votes, group bids), pass `record=False` to `request_inputs`, then one `record_event` with the combined result.

Semantic events should use:

```python
await ctx.record_event("night_outcome", {"day": self.day, "victim": seat})
```

Use stable event names and argument keys so `parse_replay()` can replay them. `parse_replay` should ignore sources that were never logged (e.g. `"peek"`).

## Interaction args shape

| Component | `Move.args` |
|-----------|-------------|
| Button | `{}` |
| Single select | `{"value": "choice_id"}` |
| Multi select | `{"values": ["a", "b"]}` |

## Settings

Read lobby settings with `self.setting("key")`, which falls back to the metadata default:

```python
mode = self.setting("first_move", "random")
```

## Roles

Named identities belong to the game class. The lobby does not collect or assign them.

Declare `roles` (`RoleSpec`) for catalog copy and DMs. Assign in `__init__` / `play()` (random deal, composition from settings, or `request_inputs` if players pick) and stamp `player.role_key` for results. Secret per-player data uses `request_inputs` after the match starts, not a lobby form.

Game-wide knobs (mafia count, enable doctor) stay on `SettingOption`.

## Special move sources

The engine may inject these sources into the move log:

- `forfeit` — player forfeited (may also be injected as a live `Move` when `supports_player_removal`)
- `timeout` — injected live move when the timeout consequence is skip/strike
- `game_end` — session cancelled or timed out
- Bot takeover is a **`system`** `bot_takeover` entry, separate from the bot's subsequent **`game`** move. Use `iter_replay()` so takeover banners attach to the next real frame.

Replay implementations should handle these via `system_replay_info()` from `strife.engine.replay`.

## Further reading

- [Game Development Guide](game-development.md) — tutorial and checklist
- [Game Exposure and Sandbox Architecture](game-architecture.md) — discovery, GameContext isolation, catalog/slash exposure
- `strife/games/tictactoe/` — minimal turn-based example
- `strife/games/test/` — full API integration showcase
