# Game API

What a game class must implement. Surfaces players actually see: [interfaces.md](interfaces.md). Tutorial: [game-development.md](game-development.md).

## Setup

1. `plugin.toml` (`key`, `version`, `platform_version`, `dependencies`) and `changelog.toml` next to the package
2. Subclass `Game` in `game.py`, export it as `GAME` from `__init__.py`
3. Attach `GameMetadata` (`@game_metadata` / `@game_metadata_from`). `metadata.key` must match `plugin.toml`. The host stamps `version` / `platform_version` from the manifest
4. Builtins live in `strife/games/`; git installs in `plugins/`

Import from `strife.engine` and `strife.presentation` only — not persistence, the compiler, or `strife.session`. Games never see Discord types.

Live clicks and replay log rows are the same `Move` (`args`, `source`, `kind`). `arguments` is an alias of `args`.

Talk to the host through `GameContext`: input, board updates, DMs, `record_event`, query replies.

## Versions

This host is **platform 1.0.0**.

| Field | Meaning |
|-------|---------|
| `version` | This plugin’s semver (Tic-Tac-Toe 1.0.0 vs Coup 1.0.0 are unrelated) |
| `platform_version` | The game API this plugin was written for |

The game loads if major versions match and the host is ≥ the target. A 1.0.0 game runs on 1.2.0; a 1.2.0 game does not run on 1.0.0.

`changelog.toml` is not a load gate. Players see it in `/strife about` → Changes. Keep the latest `[[release]].version` in sync with `plugin.toml`. Host notes: `changelog/bot.toml`, `changelog/platform.toml`.

## Required methods

| Method | When | Does |
|--------|------|------|
| `play(ctx)` | Always | Game loop; return `GameOutcome` when finished |
| `parse_replay(moves, ctx)` | `supports_replay` | Build replay frames |
| `bot_move(difficulty, seat)` | `metadata.bots` | Pick a move for a bot |
| `remove_player(seat)` | `supports_player_removal` | Update state when someone leaves |

Missing a required method **skips the game** (error log). Default `supports_replay=True`, so a raw `Game` without `parse_replay` (and not `TurnBasedGame`) won’t show up in `/play`.

## Optional hooks

| Method | Does |
|--------|------|
| `final_view(ctx, outcome)` | End-state UI |
| `handle_query(seat, source, ctx)` | Peek / extra UI. Return `True` if handled |

## Moves vs queries vs links

| Kind | How | In the log? | Replay? |
|------|-----|-------------|---------|
| Solo move | `request_input(..., sources={...})` | Yes (default) | One frame |
| Group input | `request_inputs(..., record=False)` + `record_event` | One event | One frame |
| Query | `Button(..., query=True)` + `handle_query` | No | No |
| Link | `Button(style=ButtonStyle.LINK, url=...)` | No | No |

`query=True` is enough — not a move even if listed in `sources`. Hide live rows in replay with `add_controls(container, ctx, row)`.

```python
async def handle_query(self, seat, source, ctx) -> bool:
    if source == "peek":
        view = query_panel(ctx, title=f"Role: {self.role[seat]}", prefix_emoji="peek")
        await ctx.respond_query(view)
        return True
    return False

row.add_button(Button(source="peek", label="Peek", query=True))
move = await ctx.request_input(view, actor=seat, sources={"pass"})
```

Link buttons need no `source`:

```python
Button(label="Rules", style=ButtonStyle.LINK, url="https://example.com/rules")
```

## Move log

| Kind | Who writes it | Examples | Replay |
|------|---------------|----------|--------|
| `game` | `request_input` (default), `record_event` | tile click, `day_outcome` | Apply state / render |
| `system` | Host | `forfeit`, `game_end`, `bot_takeover`, `timeout` | Banners, early end — not rules |

Games only emit **`game`** via `record_event`. `record_event` rejects the four system names.

`parse_replay` walks the full log (order + system banners via `system_replay_info`) but only applies `move.is_game`. `TurnBasedGame` does this for you. `total_turns` counts game entries only.

| Pattern | Recording |
|---------|-----------|
| Solo `request_input` | Auto, `game` |
| Group `request_inputs(..., record=False)` | You emit one `record_event` |
| Phase change | `record_event("day_outcome", {...})` |
| Peek | Not recorded |
| Forfeit / timeout / cancel | Host → `system` |

Don’t `record_event` a player click that was already recorded. For votes, `record=False` then one combined event.

```python
await ctx.record_event("night_outcome", {"day": self.day, "victim": seat})
```

Stable names and keys so `parse_replay` can read them. Ignore sources that were never logged (`"peek"`).

## Args

| Component | `Move.args` |
|-----------|-------------|
| Button | `{}` |
| Single select | `{"value": "choice_id"}` |
| Multi select | `{"values": ["a", "b"]}` |

## Settings

```python
mode = self.setting("first_move", "random")
```

Falls back to the metadata default, then the argument.

## Roles

The lobby does not deal roles. Declare `RoleSpec` for catalog copy and DMs. Assign in `__init__` / `play()` and set `player.role_key`. If players pick, `request_inputs` after the match starts. Game-wide knobs (mafia count) stay on `SettingOption`.

## Host-injected sources

- `forfeit` — also a live `Move` when `supports_player_removal`
- `timeout` — live move when the consequence is skip/strike
- `game_end` — cancelled or timed out
- `bot_takeover` — **system** row, then the bot’s **game** move. Use `iter_replay()` so the banner sticks to the next frame

Handle these with `system_replay_info()` from `strife.engine.replay`.

## Also

- [game-development.md](game-development.md) — tutorial
- [game-architecture.md](game-architecture.md) — load path and isolation
- `strife/games/tictactoe/` — small turn-based example
- `strife/games/test/` — API showcase
