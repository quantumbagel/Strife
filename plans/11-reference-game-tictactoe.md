# 11 - Reference Game: Tic-Tac-Toe

A minimal, perfect-information, two-player game that exercises the full pipeline: registry metadata, the turn loop, button moves, win detection, an optimal bot, and replay. Architecture section 6D (Peek/Spectate row removed). Depends on [06](06-game-engine-api.md).

Location: `strife/games/tictactoe/` (`game.py`, `bot.py`).

---

## 1. Registry metadata

```python
META = GameMetadata(
    key="tictactoe", name="Tic-Tac-Toe",
    summary="Classic 3x3. Get three in a row.",
    description="Two players alternate placing X and O on a 3x3 grid; first to align three wins.",
    tags=("classic", "strategy", "2p"),
    author="Strife", version="1.0.0", author_link=None, source_link=None,
    time_estimate="2m", difficulty="easy",
    player_count=PlayerCount(fixed=2),
    player_order=PlayerOrder.RANDOM,            # seat 0 = X (moves first), seat 1 = O
    bots=(BotSpec("easy", "Random legal move"),
          BotSpec("medium", "Win/block heuristic"),
          BotSpec("hard", "Optimal minimax (never loses)")),
    settings=(SettingOption(key="first_move", title="First Move",
                            description="Who plays X (moves first)",
                            type=OptionType.CHOICE, default="random",
                            choices=("random", "creator")),),
    slash_moves=(),                              # moves are board buttons (tile_XY)
    role_mode=RoleMode.NONE, role_flow=RoleFlow.NONE, roles=(),
    supports_player_removal=False,               # 2p: a removal ends the match
    accent_color=0x57F287,
)
```

`supports_bots` is True (bots present), so AFK timeout hot-swaps to a bot ([08](08-session-lifecycle.md) D10) rather than ending the game.

---

## 2. State

```python
class TicTacToe(Game):
    metadata = META
    def __init__(self, players, settings, rng):
        super().__init__(players, settings, rng)
        self.board: list[Optional[int]] = [None] * 9   # index = row*3+col, value = seat
        self.current: int = self._starting_seat()       # honors first_move setting (uses rng for "random")
        self.marks = {0: "tictactoe_x", 1: "tictactoe_o"}   # seat -> emoji name

    def _idx(self, col, row) -> int: return row * 3 + col
```

Seat-to-mark: seat 0 = X, seat 1 = O. `_starting_seat` uses `self.rng` when `first_move == "random"` (so replay reproduces it from the seed).

---

## 3. Turn loop - `play(ctx)`

```python
async def play(self, ctx) -> GameOutcome:
    while True:
        seat = self.current
        view = self._board_view(prompt=f"{self.marks[seat]} turn")
        empties = {f"tile_{c}{r}" for r in range(3) for c in range(3)
                   if self.board[self._idx(c, r)] is None}
        move = await ctx.request_input(view, actor=seat, sources=empties)
        col, row = int(move.source[5]), int(move.source[6])     # "tile_<col><row>"
        self.board[self._idx(col, row)] = seat
        if (line := self._winning_line(seat)) is not None:
            await ctx.update(self._board_view(prompt="Winner!", highlight=line))
            return GameOutcome(results={seat: "win", 1 - seat: "loss"},
                               summary={"winner": seat, "line": line})
        if all(v is not None for v in self.board):
            await ctx.update(self._board_view(prompt="Draw"))
            return GameOutcome(results={0: "draw", 1: "draw"}, summary={"winner": None})
        self.current = 1 - seat
```

- Bot seats resolve transparently via `Game.bot_move` ([06](06-game-engine-api.md)); `play` never branches on human/bot.
- The move `source` (`tile_<col><row>`) is the recorded move ([06](06-game-engine-api.md)); replay reapplies it. Source parsing is positional and validated against `empties` by the session before resolve.

---

## 4. Board view (Architecture section 6D, no Peek/Spectate)

`_board_view(prompt, highlight=None) -> LayoutView`:

```
## [tictactoe_x] Tic-Tac-Toe
Container(accent 0x57F287):
  TextDisplay: "{X emoji} <@playerX>  vs  {O emoji} <@playerO>"  (subheader)
  TextDisplay: "{turn indicator / prompt / draw / winner}"        (body)
ActionRow 1: [ (0,0) ][ (1,0) ][ (2,0) ]
ActionRow 2: [ (0,1) ][ (1,1) ][ (2,1) ]
ActionRow 3: [ (0,2) ][ (1,2) ][ (2,2) ]
```

Tile button rules:

- Empty tile: `Button(source=f"tile_{c}{r}", label="\u200b", style=SECONDARY)` (enabled).
- Occupied tile: `Button(source=..., emoji=<mark emoji>, style=PRIMARY/SUCCESS, disabled=True)`; winning-line tiles use SUCCESS when `highlight` is set.
- 3 action rows x 3 tiles = 9 buttons, within the ActionRow (<=5/row) and LayoutView (<=40) limits ([04](04-presentation-components-v2.md)).
- Engine sends with prefix `g_move`, `resource_id = thread_id` ([05](05-interaction-routing.md)).

---

## 5. Win detection

`_winning_line(seat) -> list[int] | None` checks the 8 lines (3 rows, 3 cols, 2 diagonals) for all-`seat`; returns the winning indices (for highlight) or None.

---

## 6. Bots - `strife/games/tictactoe/bot.py` (Decision D13)

`Game.bot_move(difficulty, seat)` dispatches by difficulty; all randomness/tie-breaking uses `self.rng` so behavior is seed-reproducible:

- `easy`: choose a uniform-random empty cell.
- `medium`: take a winning move if available; else block the opponent's winning move; else prefer center > corners > edges.
- `hard`: **optimal minimax** over the (tiny) game tree with memoization (or alpha-beta); guaranteed to never lose. Among equally optimal moves, pick via `self.rng` for variety.

```python
async def bot_move(self, difficulty, seat) -> Move:
    fn = {"easy": self._random_move, "medium": self._heuristic_move,
          "hard": self._minimax_move}[difficulty]
    col, row = await asyncio.to_thread(fn, seat)   # to_thread keeps the loop free
    return Move(actor_seat=seat, source=f"tile_{col}{row}", args={})
```

Minimax sketch: score terminal states (+1 win / 0 draw / -1 loss from the mover's perspective), recurse over empty cells alternating players, return the move maximizing the bot's outcome. For 3x3 the full tree is trivially fast; `to_thread` is precautionary.

---

## 7. Determinism & replay

- Only `self.rng` (seeded by the match seed) introduces randomness (starting seat, bot tie-breaks).
- All moves (human and bot) are recorded; replay re-runs `play()` under `ReplayContext` and reproduces identical boards/frames ([10](10-replay-and-profile.md)).

---

## 8. Deliverables checklist

- [ ] `strife/games/tictactoe/game.py` (`TicTacToe`: metadata, state, `play`, board view, win detection, `_starting_seat`).
- [ ] `strife/games/tictactoe/bot.py` (`bot_move` with easy/medium/hard, minimax).
- [ ] Registered in `GameRegistry` at startup ([01](01-foundation.md) step 6).
- [ ] Tests: win/draw detection, hard bot never loses, full-game determinism/replay ([13](13-testing.md)).
