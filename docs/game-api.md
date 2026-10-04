# Game API

What a game class must implement. Surfaces players actually see: [interfaces.md](interfaces.md). Tutorial: [game-development.md](game-development.md).

## Setup

1. `plugin.toml` (`key`, `version`, `platform_version`, `dependencies`) and `changelog.toml` next to the package
2. Subclass `Game` in `game.py`, export it as `GAME` from `__init__.py`
3. Attach `GameMetadata` (`@game_metadata` / `@game_metadata_from`). `metadata.key` must match `plugin.toml`. The host stamps `version` / `platform_version` from the manifest
4. Builtins live in `strife/games/`; git installs in `plugins/`

Import from `strife.engine` and `strife.presentation` only — not persistence, the compiler, or `strife.session`. Games never see Discord types.

Live clicks and replay log rows are the same `Move` (`args`, `source`, `kind`).

Talk to the host through `GameContext`: input, board updates, DMs, `record_event`, query replies. `ctx.turn_timeout_seconds` is the host’s per-turn budget when the match has a clock (`None` on CLI and replay). `async with ctx.turn_deadline(seconds=None)` spans that budget (or an explicit `seconds`) across several `request_input` / `request_inputs` calls — re-asking after an invalid submit does not reset the clock. Replay and CLI are no-ops.

`GameOutcome.results` is `dict[int, Result]` (`WIN` / `LOSS` / `DRAW`). `Result` is a `StrEnum`, so string comparisons still work.

`player.mention` / `str(player)` is the display name (bots append ` (difficulty)`). The Discord host installs mention markup at startup.

`request_inputs(..., per_seat={seat: SeatPrompt(...)})` overrides `sources` / `description` per seat. A `None` field falls back to the request-wide value.

## Randomness

| RNG | Use |
|-----|-----|
| `self.rng` | Rules: shuffles, dice, setup, tie-breaks. Seeded from the match seed |
| `self.bot_rng` | Bot decision heuristics only (not seeded) |

The host never consumes `self.rng` for bot scheduling; `until="any"` bot picks use a separate host RNG.

All rules randomness must come from `self.rng`. `play()` must not read wall-clock time for rules; use `Move.created_at` when you need timestamps in game logic.

## Versions

This host is **platform 3.0.0**.

| Field | Meaning |
|-------|---------|
| `version` | This plugin’s semver (Tic-Tac-Toe 1.0.0 vs Coup 1.0.0 are unrelated) |
| `platform_version` | The game API this plugin was written for |

The game loads if major versions match and the host is ≥ the target. A 3.0.0 game runs on 3.2.0; a 3.2.0 game does not run on 3.0.0.

`changelog.toml` is not a load gate. Players see it in `/strife about` → Changes. Keep the latest `[[release]].version` in sync with `plugin.toml`. Host notes: `changelog/bot.toml`, `changelog/platform.toml`.

## Required methods

| Method | When | Does |
|--------|------|------|
| `play(ctx)` | Always | Game loop; return `GameOutcome` (`results: dict[int, Result]`) when finished |
| `bot_move(request)` | `metadata.bots` | Pick a move for a bot (`BotRequest`: `seat`, `difficulty`, `sources`, `description`, `form`) |
| `remove_player(seat)` | Inferred when you override this method | Engine calls this **once** per mid-match removal. Do not call it from `play()` |

Missing a required method **skips the game** (error log).

## Optional hooks

| Method | Does |
|--------|------|
| `render_replay(ctx, live_view)` | Replay frame for the current state. Default: last board `play()` showed. Hidden-info games override to reveal secrets. Return `None` to skip a frame |
| `replay_label()` | Optional per-frame label in the replay UI |
| `final_view(ctx, outcome)` | End-state UI |
| `handle_query(seat, source, ctx)` | Peek / extra UI. Return `True` if handled |
| `forfeit_end_outcome(seat, reason)` | Results when the host ends the match on a forfeit |
| `on_timeout(move)` | `TurnBasedGame`: called by `take_turn` on `Interrupt.TIMEOUT` instead of `apply_move`. Default: no-op |

## Replay

Replays **re-run `play()`** against a `GameContext` that answers `request_input`, `request_inputs`, and `record_event` from the stored log (same seed and players). The engine snapshots frames at `update` / `request_*` boundaries via `render_replay`. Divergence between what `play()` asks for and the log raises `ReplayDivergence`. Live matches resume the same way after a restart (catch-up, then continue); they are not replayable until they end. Plugin updates while a match is live abandon it at the next boot.

Matches persisted before log format 3 / platform 3.0 cannot be replayed.

## Moves vs queries vs links

| Kind | How | In the log? | Replay? |
|------|-----|-------------|---------|
| Solo move | `request_input(..., sources={...})` | Yes (`game`) | Yes |
| Group input | `request_inputs(...)` then one `record_event` | One event per input + one event row | Yes |
| Form select | `Select(..., form=True)` | No (value rides on the next move) | No |
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
| `game` | Every `request_input` / `request_inputs` answer, plus `record_event` | tile click, `day_outcome` | Consumed in order by replay |
| `system` | Host | `forfeit`, `game_end`, `bot_takeover`, `timeout` | Metadata, banners, early end |

Seat events (system `bot_takeover`, and system `forfeit` with `args.removed`) are applied by the engine at the position of their log row — when `play()` next calls `request_input` / `request_inputs` / `record_event`, or before the move that follows them is returned. The game owns its own `Player` copies (`role_key`, `is_bot`); the session keeps a separate list.

Games only emit **`game`** via `record_event`. `record_event` rejects the four system names.

Every player input is logged exactly once by the host. `record_event` rows are checkpoints your `play()` must emit identically on replay (`source` + `args`). `total_turns` counts **game** rows with `actor_seat is not None` (player inputs only).

| Pattern | Recording |
|---------|-----------|
| Solo `request_input` | Auto, `game` |
| Group `request_inputs` | One `game` row per seat answer, then your `record_event` |
| Phase change | `record_event("day_outcome", {...})` |
| Peek | Not recorded |
| Forfeit / timeout / cancel | Host → `system` |

Don’t `record_event` the same click that `request_input` already logged. For votes, log each ballot via `request_inputs`, then one combined `record_event`.

```python
await ctx.record_event("night_outcome", {"day": self.day, "victim": seat})
```

Stable names and keys so replay can validate them. Ignore sources that were never logged (`"peek"`).

## Args

| Component | `Move.args` |
|-----------|-------------|
| Button | `{}` |
| Single select | `{"value": "choice_id"}` |
| Multi select | `{"values": ["a", "b"]}` |
| Form select (`form=True`) | Not a move. Next move gets `args[select.source]`: `str` if `max_values == 1`, else `list[str]` |

## Forms

`Select(..., form=True)` only stores a pick for that seat. The host attaches it to that seat’s next real move as `args[select.source]`. Form clicks are not logged. `move_sources` omits them; `resolve_sources` subtracts them even if listed in `sources`.

`form_fields(view)` → `{source: FormField}`. `FormField`: `source`, `choices` (tuple of values), `multi`, `default` (from choices with `default=True`).

Invalid submit (missing target, etc.): set a notice and `request_input` again. Wrap the loop in `ctx.turn_deadline` so the clock does not reset. Bots skip the clicks and return one move whose args use the form source names.

## BotRequest

| Field | Meaning |
|-------|---------|
| `seat` | Seat to move |
| `difficulty` | Declared bot difficulty |
| `sources` | Resolved allowed move sources (`None` = any) |
| `description` | Text from `request_input(s)` |
| `form` | Field source → legal choice values (empty if the view has no form selects) |

`validate_bot_move` rejects a form-field arg whose value is not in `form[source]`.

## GameContext

`async with ctx.turn_deadline(seconds=None):` — every inner `request_input` / `request_inputs` that omits `timeout_seconds` uses time remaining until the deadline set on entry (`seconds=None` → host turn timeout). An explicit `timeout_seconds` uses the smaller of the two. Floor 1 second. Replay and CLI: no-op.

## Settings

```python
mode = self.setting("first_move", "random")
```

Falls back to the metadata default, then the argument.

## Roles

The lobby does not deal roles. Declare `RoleSpec` for catalog copy and DMs. Assign in `__init__` / `play()` and set `player.role_key`. If players pick, `request_inputs` after the match starts. Game-wide knobs (mafia count) stay on `SettingOption`.

## Host-injected sources

Use `move.interrupt` (`Interrupt.FORFEIT` / `Interrupt.TIMEOUT`), not `move.source` string checks.

Seat events are applied by the engine (see Move log). `remove_player` is called by the engine exactly once; games must not call it.

- `forfeit` — mid-match removal (override `remove_player`). A pending input for that seat resolves with this `Move` (`interrupt` is `Interrupt.FORFEIT`). System row: `actor_seat` = the seat, `args` = `{"reason": "forfeit" | "timeout", "removed": true}`
- `timeout` — skip/strike, or AUTO_PASS when `pass` is not in allowed sources (`Interrupt.TIMEOUT`)
- `pass` — AUTO_PASS (requires `"pass"` in `sources`) and strike when `pass` is allowed
- `game_end` — cancelled or timed out
- `bot_takeover` — **system** row, then the bot’s **game** move

`TurnBasedGame.take_turn` calls `on_timeout(move)` on `Interrupt.TIMEOUT` instead of `apply_move`. Override it to advance the turn.

Banner metadata for removals and bot takeover uses `system_replay_info()` from `strife.engine.replay` (read-only; does not mutate players).

## First-to-act timeouts

`request_inputs(..., until="any")` is one shared window, not any one seat’s turn. If its deadline passes with nobody acting, no seat is blamed: no AFK consequence (bot takeover, removal, strike, `timeout_consequence`) applies and no turn warning is sent. `request_inputs` returns **`{}`** and the host logs one **system** `timeout` row with `actor_seat=None` and `args={"reason": "timeout", "until": "any", "seats": [...]}`. Games must treat an empty result as “nobody acted” (e.g. advance the round, as Spyfall does) — re-asking forever never ends the match.

`until="all"` keeps per-seat timeouts: each seat that runs out gets its own consequence.

## Also

- [game-development.md](game-development.md) — tutorial
- [game-architecture.md](game-architecture.md) — load path and isolation
- `strife/games/tictactoe/` — small turn-based example
- `strife/games/test/` — API showcase
