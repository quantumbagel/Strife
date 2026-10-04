from __future__ import annotations

import asyncio
import random
import time

import chess
import chess.svg
import chess.variant
import resvg_py

from strife.engine import (
    BotRequest,
    BotSpec,
    TimeoutConsequence,
    GameContext,
    GameOutcome,
    Move,
    MoveParam,
    OptionType,
    ParamType,
    Player,
    PlayerCount,
    PlayerOrder,
    SettingOption,
    SlashMove,
    TurnBasedGame,
    game_metadata_from,
    run_cpu,
)
from datetime import datetime, timezone
from strife.presentation.components import (
    Container,
    LayoutView,
    MediaGallery,
    MediaGalleryItem,
    ViewFile,
)
from strife.presentation.game_ui import game_container
from strife.presentation.style import add_meta


VARIANT_STANDARD = "standard"
VARIANT_CHESS960 = "chess960"
VARIANT_CHOICES = (
    VARIANT_STANDARD,
    VARIANT_CHESS960,
    "crazyhouse",
    "king of the hill",
    "three-check",
    "atomic",
    "antichess",
    "horde",
    "racing kings",
)

_VARIANT_BOARDS: dict[str, type[chess.Board]] = {
    VARIANT_STANDARD: chess.Board,
    "crazyhouse": chess.variant.CrazyhouseBoard,
    "king of the hill": chess.variant.KingOfTheHillBoard,
    "three-check": chess.variant.ThreeCheckBoard,
    "atomic": chess.variant.AtomicBoard,
    "antichess": chess.variant.AntichessBoard,
    "horde": chess.variant.HordeBoard,
    "racing kings": chess.variant.RacingKingsBoard,
}

_TERMINATION_LABELS = {
    chess.Termination.CHECKMATE: "checkmate",
    chess.Termination.STALEMATE: "stalemate",
    chess.Termination.INSUFFICIENT_MATERIAL: "insufficient material",
    chess.Termination.SEVENTYFIVE_MOVES: "seventy-five-move rule",
    chess.Termination.FIVEFOLD_REPETITION: "fivefold repetition",
    chess.Termination.FIFTY_MOVES: "fifty-move rule",
    chess.Termination.THREEFOLD_REPETITION: "threefold repetition",
    chess.Termination.VARIANT_DRAW: "variant draw",
}

_VARIANT_WIN_LABELS = {
    "king of the hill": "king of the hill",
    "three-check": "three checks",
    "atomic": "explosion",
    "antichess": "losing all pieces",
    "horde": "destroying the horde",
    "racing kings": "reaching the eighth rank",
}


def parse_user_move(board: chess.Board, text: str) -> chess.Move | None:
    text = text.strip()
    if not text:
        return None
    # 1. Try UCI directly (case insensitive)
    try:
        m = board.parse_uci(text.lower())
        if m in board.legal_moves:
            return m
    except ValueError:
        pass

    # 2. Try SAN directly as is
    try:
        m = board.parse_san(text)
        if m in board.legal_moves:
            return m
    except ValueError:
        pass

    # 3. Try SAN with capitalized first letter (e.g. nf3 -> Nf3, qe2 -> Qe2)
    if len(text) > 1 and text[0].lower() in ("n", "b", "r", "q", "k"):
        capitalized = text[0].upper() + text[1:]
        try:
            m = board.parse_san(capitalized)
            if m in board.legal_moves:
                return m
        except ValueError:
            pass

    # 4. Try lowercase SAN (e.g., e4, d5)
    try:
        m = board.parse_san(text.lower())
        if m in board.legal_moves:
            return m
    except ValueError:
        pass

    return None


def _termination_label(termination: chess.Termination, variant: str) -> str:
    if termination in (chess.Termination.VARIANT_WIN, chess.Termination.VARIANT_LOSS):
        return _VARIANT_WIN_LABELS.get(variant, "variant rule")
    return _TERMINATION_LABELS.get(
        termination, termination.name.replace("_", " ").lower()
    )


def _pocket_text(pocket: chess.variant.CrazyhousePocket) -> str:
    letters: list[str] = []
    for piece_type, letter in (
        (chess.QUEEN, "Q"),
        (chess.ROOK, "R"),
        (chess.BISHOP, "B"),
        (chess.KNIGHT, "N"),
        (chess.PAWN, "P"),
    ):
        letters.extend([letter] * pocket.count(piece_type))
    return " ".join(letters) if letters else "—"


@game_metadata_from(
    key="chess",
    name="Chess",
    summary="Chess with variants and optional clocks.",
    description="Two players. Use /chess move: e4, Nf3, or e2e4. Variants and clocks are lobby settings.",
    tags=("classic", "strategy", "2p"),
    author="Strife",
    author_link=None,
    source_link=None,
    how_to_play_link="https://en.wikipedia.org/wiki/Rules_of_chess",
    time_estimate="15m",
    difficulty=4,
    player_count=PlayerCount(fixed=2),
    player_order=PlayerOrder.RANDOM,
    bots=(
        BotSpec("random", "Random legal move"),
        BotSpec("capture-priority", "Prioritizes captures, then random"),
    ),
    bot_takeover_difficulty="capture-priority",
    settings=(
        SettingOption(
            key="variant",
            title="Ruleset",
            description="Standard, Chess960, Crazyhouse, and other variants",
            type=OptionType.CHOICE,
            default=VARIANT_STANDARD,
            choices=VARIANT_CHOICES,
            emoji="game",
        ),
        SettingOption(
            key="clock_minutes",
            title="Clock (minutes)",
            description="Minutes on each clock. 0 means no clock.",
            type=OptionType.INT,
            default=0,
            minimum=0,
            maximum=180,
            emoji="timer",
        ),
        SettingOption(
            key="increment_seconds",
            title="Increment (seconds)",
            description="Seconds added after each move.",
            type=OptionType.INT,
            default=0,
            minimum=0,
            maximum=60,
            emoji="time",
        ),
        SettingOption(
            key="color",
            title="Creator Color",
            description="White, black, or random.",
            type=OptionType.CHOICE,
            default="random",
            choices=("random", "white", "black"),
            emoji="first_move",
            choice_emojis=(
                ("random", "restart"),
                ("white", "user"),
                ("black", "user"),
            ),
        ),
    ),
    slash_moves=(
        SlashMove(
            name="move",
            description="Make a chess move (e.g. e4, Nf3, e2e4)",
            params=(
                MoveParam(
                    name="move",
                    description="The move to play in SAN or UCI notation",
                    type=ParamType.STRING,
                    required=True,
                ),
            ),
        ),
    ),
)
class Chess(TurnBasedGame):
    def __init__(self, players: list[Player], settings, rng: random.Random):
        super().__init__(players, settings, rng)
        self._chess960_pos: int | None = None
        self._apply_creator_color()
        self.reset()

    def _variant(self) -> str:
        value = str(self.setting("variant", VARIANT_STANDARD)).lower()
        if value == VARIANT_CHESS960 or value in _VARIANT_BOARDS:
            return value
        return VARIANT_STANDARD

    def _apply_creator_color(self) -> None:
        mode = str(self.setting("color", "random")).lower()
        if mode not in ("white", "black") or len(self.players) != 2:
            return
        creator_id = self.settings.get("creator_id")
        if creator_id is None:
            return
        creator = next((p for p in self.players if p.user_id == creator_id), None)
        if creator is None:
            return
        want_white = mode == "white"
        if (creator.seat == 0) == want_white:
            return
        self.players[0], self.players[1] = self.players[1], self.players[0]
        for idx, player in enumerate(self.players):
            player.seat = idx

    def _new_board(self) -> chess.Board:
        variant = self._variant()
        if variant == VARIANT_CHESS960:
            if self._chess960_pos is None:
                self._chess960_pos = self.rng.randint(0, 959)
            return chess.Board.from_chess960_pos(self._chess960_pos)
        return _VARIANT_BOARDS.get(variant, chess.Board)()

    def _clock_config(self) -> tuple[bool, list[float], float]:
        if "time_control" in self.settings and "clock_minutes" not in self.settings:
            time_control = str(self.settings.get("time_control") or "none")
            if time_control == "none":
                return False, [0.0, 0.0], 0.0
            try:
                base_str, inc_str = time_control.split("+", 1)
                base = float(base_str) * 60.0
                return True, [base, base], float(inc_str)
            except ValueError:
                return False, [0.0, 0.0], 0.0
        minutes = int(self.setting("clock_minutes", 0))
        increment = int(self.setting("increment_seconds", 0))
        if minutes <= 0:
            return False, [0.0, 0.0], 0.0
        base = float(minutes) * 60.0
        return True, [base, base], float(max(0, increment))

    def reset(self) -> None:
        self.board = self._new_board()
        self.current = 0
        active, clocks, increment = self._clock_config()
        self.time_control_active = active
        self.clocks = clocks
        self.increment = increment
        self.last_move_time = None

    def _format_time(self, seconds: float) -> str:
        if seconds < 0:
            seconds = 0
        mins = int(seconds // 60)
        secs = int(seconds % 60)
        return f"{mins:02d}:{secs:02d}"

    def _format_clocks(self) -> str:
        clocks = (
            f"White: `{self._format_time(self.clocks[0])}` | "
            f"Black: `{self._format_time(self.clocks[1])}`"
        )
        if self.increment:
            clocks += f" (+{int(self.increment)}s)"
        return clocks

    def _ruleset_label(self) -> str:
        variant = self._variant()
        if variant == VARIANT_CHESS960:
            pos = f" · #{self._chess960_pos}" if self._chess960_pos is not None else ""
            return f"Chess960{pos}"
        return variant.capitalize()

    def _consume_thinking_time(self, move: Move) -> None:
        """Subtract elapsed clock time without applying increment."""
        if not self.time_control_active or move.actor_seat is None:
            return
        if self.last_move_time is not None and move.created_at is not None:
            t1 = self.last_move_time
            t2 = move.created_at
            if t1.tzinfo is not None:
                t1 = t1.astimezone(timezone.utc).replace(tzinfo=None)
            if t2.tzinfo is not None:
                t2 = t2.astimezone(timezone.utc).replace(tzinfo=None)
            elapsed = (t2 - t1).total_seconds()
            self.clocks[move.actor_seat] = max(0.0, self.clocks[move.actor_seat] - elapsed)
        if move.created_at is not None:
            stamped = move.created_at
            if stamped.tzinfo is not None:
                stamped = stamped.astimezone(timezone.utc).replace(tzinfo=None)
            self.last_move_time = stamped

    def _tick_clock(self, move: Move) -> None:
        self._consume_thinking_time(move)
        if self.time_control_active and move.actor_seat is not None:
            self.clocks[move.actor_seat] += self.increment

    def apply_move(self, move: Move) -> None:
        if move.source == "move":
            move_text = move.args.get("move") or ""
        else:
            move_text = move.source
        m = parse_user_move(self.board, move_text)
        if m is None:
            return
        self._tick_clock(move)
        self.board.push(m)
        self.current = 1 - self.current

    def _apply_system_timeout(self, move: Move) -> None:
        if self.time_control_active and move.source == "game_end" and move.args.get("reason") == "timeout":
            if self.last_move_time is not None and move.created_at is not None:
                t1 = self.last_move_time
                t2 = move.created_at
                if t1.tzinfo is not None:
                    t1 = t1.astimezone(timezone.utc).replace(tzinfo=None)
                if t2.tzinfo is not None:
                    t2 = t2.astimezone(timezone.utc).replace(tzinfo=None)
                elapsed = (t2 - t1).total_seconds()

                actor = self.current
                self.clocks[actor] = max(0.0, self.clocks[actor] - elapsed)

    def replay_label(self) -> str | None:
        n = len(self.board.move_stack)
        return "Start" if n == 0 else f"Move {n}"

    def _action_status(self, ctx: GameContext, seat: int) -> str:
        player = self.players[seat]
        color = "White" if seat == 0 else "Black"
        check_str = " (in check)" if self.board.is_check() else ""
        return f"{color} ({player.mention}) to act{check_str}"

    def _clock_loss_outcome(self, loser_seat: int) -> GameOutcome:
        winner = 1 - loser_seat
        winner_mention = str(self.players[winner])
        return GameOutcome(
            results={winner: "win", loser_seat: "loss"},
            summary={"winner": winner, "reason": "timeout"},
            description=f"{winner_mention} won on time",
            player_descriptions={winner: "Won on time", loser_seat: "Lost on time"},
        )

    def forfeit_end_outcome(self, forfeiter_seat: int, reason: str = "forfeit") -> GameOutcome:
        if reason == "timeout" and self.time_control_active:
            return self._clock_loss_outcome(forfeiter_seat)
        return super().forfeit_end_outcome(forfeiter_seat, reason=reason)

    def _outcome(self) -> GameOutcome | None:
        result = self.board.outcome(claim_draw=True)
        if result is None:
            return None

        reason = _termination_label(result.termination, self._variant())
        if result.winner is None:
            return GameOutcome(
                results={0: "draw", 1: "draw"},
                summary={"winner": None, "reason": reason},
                description=f"Draw by {reason}",
                player_descriptions={0: f"Draw ({reason})", 1: f"Draw ({reason})"},
            )

        winner = 0 if result.winner == chess.WHITE else 1
        loser = 1 - winner
        winner_mention = str(self.players[winner])
        return GameOutcome(
            results={winner: "win", loser: "loss"},
            summary={"winner": winner, "reason": reason},
            description=f"{winner_mention} won by {reason}",
            player_descriptions={winner: f"Won by {reason}", loser: "Lost"},
        )

    async def play(self, ctx: GameContext) -> GameOutcome:
        error_msg = None
        if self.time_control_active and self.last_move_time is None:
            if ctx.started_at is not None:
                stamped = ctx.started_at
                if stamped.tzinfo is not None:
                    stamped = stamped.astimezone(timezone.utc).replace(tzinfo=None)
                self.last_move_time = stamped
            else:
                self.last_move_time = datetime.now(timezone.utc).replace(tzinfo=None)
        turn_deadline: float | None = None
        turn_seat: int | None = None

        while True:
            outcome = self._outcome()
            if outcome is not None:
                return outcome

            seat = self.current
            if self.time_control_active and self.clocks[seat] <= 0:
                return self._clock_loss_outcome(seat)

            if turn_seat != seat:
                turn_seat = seat
                if self.time_control_active:
                    turn_deadline = time.monotonic() + self.clocks[seat]
                else:
                    host_timeout = ctx.turn_timeout_seconds
                    budget = float(host_timeout) if host_timeout else 90.0
                    turn_deadline = time.monotonic() + budget

            lead = self._action_status(ctx, seat)
            if error_msg:
                lead = f"**{error_msg}**\n{lead}"

            view = await run_cpu(
                self.render,
                ctx,
                lead=lead,
                status_emoji="loading",
            )

            # Accept both UCI and SAN notation, and the slash command "move"
            sources = {m.uci() for m in self.board.legal_moves} | {
                self.board.san(m) for m in self.board.legal_moves
            }
            sources.add("move")

            remaining = max(0.01, turn_deadline - time.monotonic()) if turn_deadline is not None else None
            if self.time_control_active:
                remaining = max(0.01, min(remaining or 0.01, self.clocks[seat]))
            timeout_seconds = remaining
            timeout_consequence = (
                TimeoutConsequence.GAME_ENDS if self.time_control_active else None
            )

            move = await ctx.request_input(
                view,
                actor=seat,
                sources=sources,
                timeout_seconds=timeout_seconds,
                timeout_consequence=timeout_consequence,
                            )

            if move.is_system:
                continue

            if move.source == "move":
                move_text = move.args.get("move", "")
            else:
                move_text = move.source

            m = parse_user_move(self.board, move_text)
            if m is not None:
                self.apply_move(move)
                await ctx.record_event(move.source, dict(move.args))
                error_msg = None
                turn_seat = None
                turn_deadline = None
            else:
                error_msg = f"Invalid or illegal move: '{move_text}'. Try again."
                if self.time_control_active:
                    self._consume_thinking_time(move)

    def _render_base(
        self,
        ctx: GameContext,
        *,
        title: str | None = None,
        status: str | None = None,
        status_emoji: str | None = None,
        lead: str | None = None,
    ) -> tuple[LayoutView, Container]:
        view = LayoutView()

        lastmove = self.board.peek() if self.board.move_stack else None
        check = self.board.king(self.board.turn) if self.board.is_check() else None
        svg_data = chess.svg.board(self.board, lastmove=lastmove, check=check)
        png_data = resvg_py.svg_to_bytes(svg_string=svg_data, width=450, height=450)
        filename = f"board_{len(self.board.move_stack)}.png"
        view.files = [ViewFile(data=png_data, filename=filename, description="Chess board")]

        container = game_container(ctx, lead=lead or status or title, prefix_emoji=status_emoji or "loading")

        if self._variant() != VARIANT_STANDARD:
            add_meta(container, f"{ctx.emoji.get('game', base=True)} {self._ruleset_label()}")
        if self.time_control_active:
            add_meta(container, f"{ctx.emoji.get('timer', base=True)} Clocks: {self._format_clocks()}")
        if isinstance(self.board, chess.variant.CrazyhouseBoard):
            white_pocket = _pocket_text(self.board.pockets[chess.WHITE])
            black_pocket = _pocket_text(self.board.pockets[chess.BLACK])
            add_meta(
                container,
                f"{ctx.emoji.get('game', base=True)} Pockets: White `{white_pocket}` · Black `{black_pocket}`",
            )
        if isinstance(self.board, chess.variant.ThreeCheckBoard):
            add_meta(
                container,
                (
                    f"{ctx.emoji.get('explosion', base=True)} Checks left: "
                    f"White {self.board.remaining_checks[chess.WHITE]} · "
                    f"Black {self.board.remaining_checks[chess.BLACK]}"
                ),
            )

        gallery = MediaGallery()
        gallery.add_item(MediaGalleryItem(media_url=f"attachment://{filename}", description="Chess board"))
        container.set_gallery(gallery)

        return view, container

    def render(
        self,
        ctx: GameContext,
        *,
        lead: str | None = None,
        prefix_emoji: str | None = None,
        title: str | None = None,
        status: str | None = None,
        status_emoji: str | None = None,
    ) -> LayoutView:
        view, container = self._render_base(
            ctx,
            title=title,
            status=status,
            status_emoji=status_emoji or prefix_emoji,
            lead=lead,
        )
        if isinstance(self.board, chess.variant.CrazyhouseBoard):
            add_meta(
                container,
                "Use `/chess move` with SAN or UCI — `e4`, `Nf3`, `e2e4`, or drops like `N@f3`.",
            )
        else:
            add_meta(container, "Use `/chess move` with SAN or UCI — `e4`, `Nf3`, or `e2e4`.")
        view.add_container(container)
        return view

    async def bot_move(self, request: BotRequest) -> Move:
        await asyncio.sleep(0.5)
        legal = list(self.board.legal_moves)
        if not legal:
            return Move(actor_seat=request.seat, source="resign", args={})

        if request.difficulty == "capture-priority":
            captures = [m for m in legal if self.board.is_capture(m)]
            if captures:
                chosen = self.bot_rng.choice(captures)
            else:
                chosen = self.bot_rng.choice(legal)
        else:
            chosen = self.bot_rng.choice(legal)

        return Move(actor_seat=request.seat, source=chosen.uci(), args={})
