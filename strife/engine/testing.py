from __future__ import annotations

import asyncio
import json
import random
from collections.abc import Mapping
from typing import Literal

from strife.engine.context import noop_turn_deadline
from strife.engine.game import Game
from strife.engine.inputs import (
    apply_bot_form,
    check_timeout_consequence,
    make_bot_request,
    resolve_description,
    resolve_sources,
    validate_bot_move,
    validate_form_args,
)
from strife.engine.log import SYSTEM_SOURCES, LogEntryKind
from strife.engine.log_cursor import (
    LogEnded,
    ReplayDivergence,
    args_equal,
    is_match_ending,
)
from strife.engine.match_log import MatchLog
from strife.engine.players import Move
from strife.logging import get_logger
from strife.engine.requests import SeatPrompt, TimeoutConsequence
from strife.engine.seat_events import apply_seat_event, is_seat_event
from strife.presentation.components import LayoutView, default_form_values, form_fields
from strife.presentation.emoji import EmojiResolver


log = get_logger("engine.testing")


class ScriptExhausted(RuntimeError):
    """No scripted or interactive input is available for this request."""


class MockContext:
    """Local ``GameContext`` for CLI runs and plugin tests."""

    def __init__(
        self,
        game: Game,
        *,
        emoji: EmojiResolver,
        script: list[tuple[int | None, str, dict]] | None = None,
        interactive: bool = False,
        verbose: bool = False,
    ) -> None:
        self._game = game
        self.emoji = emoji
        self._script = list(script or [])
        self._script_index = 0
        self._interactive = interactive
        self._verbose = verbose
        self._rng = random.Random()
        self.log = MatchLog()
        self.updates: list[LayoutView] = []
        self.private: list[tuple[int, LayoutView]] = []
        self.queries: list[LayoutView] = []

    @property
    def recorded(self) -> list[Move]:
        return self.log.entries

    @property
    def started_at(self) -> None:
        return None

    @property
    def is_replay(self) -> bool:
        return False

    @property
    def turn_timeout_seconds(self) -> float | None:
        return None

    def turn_deadline(self, seconds: float | None = None):
        return noop_turn_deadline(seconds)

    def is_bot(self, seat: int) -> bool:
        return self._game.players[seat].is_bot

    async def update(self, view: LayoutView) -> None:
        self.updates.append(view)
        if self._verbose:
            print(
                f"[update #{len(self.updates)}] view with "
                f"{len(view.children)} top-level node(s)"
            )

    async def send_private(self, seat: int, view: LayoutView) -> None:
        self.private.append((seat, view))
        if self._verbose:
            print(
                f"[private -> seat {seat}] DM with "
                f"{len(view.children)} top-level node(s)"
            )

    async def respond_query(self, view: LayoutView) -> None:
        self.queries.append(view)
        if self._verbose:
            print(f"[query] view with {len(view.children)} top-level node(s)")

    def _form_map(self, view: LayoutView) -> dict[str, tuple[str, ...]]:
        return {name: field.choices for name, field in form_fields(view).items()}

    def _check_form(self, view: LayoutView, move: Move) -> None:
        form = self._form_map(view)
        if form:
            validate_form_args(form, move.args)

    def _script_row(
        self, index: int
    ) -> tuple[int | None, str, dict, LogEntryKind] | None:
        if index >= len(self._script):
            return None
        row = self._script[index]
        raw_seat = row[0]
        seat = None if raw_seat is None else int(raw_seat)
        source = str(row[1])
        args = dict(row[2])
        if len(row) >= 4:
            kind = row[3]
        elif source in SYSTEM_SOURCES:
            kind = LogEntryKind.SYSTEM
        else:
            kind = LogEntryKind.GAME
        return seat, source, args, kind

    def _script_move(self, row: tuple[int | None, str, dict, LogEntryKind]) -> Move:
        seat, source, args, kind = row
        return Move(
            actor_seat=seat,
            source=source,
            args=dict(args),
            kind=kind,
        )

    def _log_scripted_system(
        self, source: str, args: dict, actor_seat: int | None
    ) -> Move:
        logged = self.log.system(source, dict(args), actor_seat=actor_seat)
        if self._verbose:
            print(f"[system] {logged.source} {logged.args} seat={actor_seat}")
        self._script_index += 1
        return logged

    def _drain_metadata(self, waiting: set[int]) -> None:
        while True:
            row = self._script_row(self._script_index)
            if row is None:
                return
            seat, source, args, kind = row
            move = self._script_move(row)
            if is_match_ending(move):
                answers_waiting = (
                    move.source == "forfeit"
                    and move.actor_seat is not None
                    and int(move.actor_seat) in waiting
                )
                if answers_waiting:
                    return
                self._log_scripted_system(source, args, seat)
                raise LogEnded(match_ended=True)
            if kind != LogEntryKind.SYSTEM or not is_seat_event(move):
                return
            if source == "forfeit" and seat is not None and seat in waiting:
                return
            apply_seat_event(self._game, move)
            self._log_scripted_system(source, args, seat)

    def _take_scripted(
        self, seat: int, allowed: set[str] | None, view: LayoutView
    ) -> Move | None:
        row = self._script_row(self._script_index)
        if row is None:
            return None
        script_seat, source, args, kind = row
        if script_seat != seat:
            return None
        self._script_index += 1
        if kind == LogEntryKind.GAME:
            args = {**default_form_values(form_fields(view)), **args}
            if allowed is not None and source not in allowed:
                raise ValueError(
                    f"Scripted source {source!r} not in allowed sources {allowed!r}"
                )
        move = Move(
            actor_seat=seat,
            source=source,
            args=dict(args),
            kind=kind,
        )
        if kind == LogEntryKind.GAME:
            self._check_form(view, move)
        if is_seat_event(move):
            apply_seat_event(self._game, move)
        if self._verbose:
            print(f"[input] seat {seat} -> {source} {args}")
        return move

    def _prompt_interactive(
        self, actor: int, allowed: set[str] | None, view: LayoutView
    ) -> Move:
        label = self._game.players[actor].display_name
        allowed_list = sorted(allowed) if allowed else ["(any)"]
        print(f"\nAction: {label} (seat {actor})")
        print(f"Allowed sources: {', '.join(allowed_list)}")
        fields = form_fields(view)
        if fields:
            print("Form fields (attached to the next move):")
            for name, field in fields.items():
                kind = "multi" if field.multi else "single"
                print(f"  {name} ({kind}): {', '.join(field.choices)}")
        while True:
            source = input("Enter move source (or 'quit'): ").strip()
            if source == "quit":
                raise SystemExit(0)
            if allowed is not None and source not in allowed:
                print(f"Invalid source. Choose from: {', '.join(sorted(allowed))}")
                continue
            args: dict = {}
            raw_args = input("Enter args JSON (optional): ").strip()
            if raw_args:
                args = json.loads(raw_args)
            move = Move(actor_seat=actor, source=source, args=args)
            try:
                self._check_form(view, move)
            except Exception as exc:
                print(f"Invalid form args: {exc}")
                continue
            return move

    def _prompt_choose_seat(self, prompt: str, seats: set[int]) -> int:
        while True:
            raw = input(prompt).strip()
            try:
                seat = int(raw)
            except ValueError:
                continue
            if seat in seats:
                return seat

    async def _bot_move(
        self,
        view: LayoutView,
        seat: int,
        *,
        allowed: set[str] | None,
        description: str | None,
    ) -> Move | None:
        request = make_bot_request(
            self._game.players,
            view,
            seat,
            sources=allowed,
            description=description,
        )
        try:
            move = await asyncio.wait_for(self._game.bot_move(request), timeout=10.0)
            move = apply_bot_form(request, move)
            validate_bot_move(request, move)
        except asyncio.CancelledError:
            raise
        except Exception:
            # Same as the live host: the turn becomes a system timeout, but say why.
            log.exception("Bot move failed for seat %s; logging a timeout", seat)
            return None
        return move

    async def _act(
        self,
        view: LayoutView,
        seat: int,
        *,
        allowed: set[str] | None,
        description: str | None,
    ) -> Move:
        move = self._take_scripted(seat, allowed, view)
        if move is None and self.is_bot(seat):
            move = await self._bot_move(
                view,
                seat,
                allowed=allowed,
                description=description,
            )
            if move is None:
                move = Move(
                    actor_seat=seat,
                    source="timeout",
                    args={},
                    kind=LogEntryKind.SYSTEM,
                )
        elif move is None:
            if self._interactive:
                move = self._prompt_interactive(seat, allowed, view)
            else:
                raise ScriptExhausted(
                    f"No scripted input for seat {seat} ({description or 'move'})"
                )

        self.log.record(move)
        return move

    async def request_input(
        self,
        view: LayoutView,
        *,
        actor: int,
        sources: set[str] | None = None,
        description: str | None = None,
        timeout_seconds: float | None = None,
        timeout_consequence: TimeoutConsequence | None = None,
    ) -> Move:
        await self.update(view)
        allowed = resolve_sources(view, sources, seat=actor)
        check_timeout_consequence(timeout_consequence, allowed)
        self._drain_metadata({actor})
        return await self._act(
            view,
            actor,
            allowed=allowed,
            description=description,
        )

    async def request_inputs(
        self,
        view: LayoutView,
        *,
        actors: set[int],
        sources: set[str] | None = None,
        until: Literal["all", "any"] = "all",
        per_seat: Mapping[int, SeatPrompt] | None = None,
        description: str | None = None,
        timeout_seconds: float | None = None,
        timeout_consequence: TimeoutConsequence | None = None,
    ) -> dict[int, Move]:
        await self.update(view)
        bots = {seat for seat in actors if self.is_bot(seat)}

        def _description(seat: int) -> str | None:
            return resolve_description(description, per_seat=per_seat, seat=seat)

        def _allowed(seat: int) -> set[str] | None:
            return resolve_sources(view, sources, per_seat=per_seat, seat=seat)

        allowed_sets = [_allowed(seat) for seat in actors]
        if allowed_sets:
            check_timeout_consequence(
                timeout_consequence, allowed_sets[0], *allowed_sets[1:]
            )
        else:
            check_timeout_consequence(timeout_consequence, set())

        self._drain_metadata(actors)

        if until == "any":
            row = self._script_row(self._script_index)
            if row is not None:
                script_seat, source, args, kind = row
                if (
                    kind == LogEntryKind.SYSTEM
                    and source == "timeout"
                    and script_seat is None
                    and args.get("until") == "any"
                ):
                    self._log_scripted_system(source, args, script_seat)
                    return {}
                if script_seat in actors:
                    move = await self._act(
                        view,
                        script_seat,
                        allowed=_allowed(script_seat),
                        description=_description(script_seat),
                    )
                    return {script_seat: move}
                raise ValueError(
                    f"Script entry expected one of {sorted(actors)}, got {script_seat}"
                )

            if bots:
                bot_seat = self._rng.choice(sorted(bots))
                move = await self._bot_move(
                    view,
                    bot_seat,
                    allowed=_allowed(bot_seat),
                    description=_description(bot_seat),
                )
                if move is None:
                    logged = self.log.system(
                        "timeout",
                        {
                            "reason": "timeout",
                            "until": "any",
                            "seats": sorted(actors),
                        },
                    )
                    if self._verbose:
                        print(
                            f"[system] {logged.source} {logged.args} "
                            f"seat={logged.actor_seat}"
                        )
                    return {}
                self.log.record(move)
                return {bot_seat: move}

            if self._interactive:
                seat = self._prompt_choose_seat(
                    f"Choose an actor: {sorted(actors)}; enter seat: ",
                    actors,
                )
                move = await self._act(
                    view,
                    seat,
                    allowed=_allowed(seat),
                    description=_description(seat),
                )
                return {seat: move}
            raise ScriptExhausted("No input available for until='any' request")

        results: dict[int, Move] = {}
        remaining = set(actors)
        while remaining:
            self._drain_metadata(remaining)
            row = self._script_row(self._script_index)
            if row is not None and row[0] in remaining:
                script_seat = row[0]
                move = await self._act(
                    view,
                    script_seat,
                    allowed=_allowed(script_seat),
                    description=_description(script_seat),
                )
                results[script_seat] = move
                remaining.discard(script_seat)
                continue

            leftover_bots = sorted(seat for seat in remaining if self.is_bot(seat))
            if leftover_bots:
                for seat in leftover_bots:
                    move = await self._act(
                        view,
                        seat,
                        allowed=_allowed(seat),
                        description=_description(seat),
                    )
                    results[seat] = move
                    remaining.discard(seat)
                continue

            if not remaining:
                break

            if self._interactive:
                seat = self._prompt_choose_seat(
                    f"Choose a human to act: {sorted(remaining)}; enter seat: ",
                    remaining,
                )
                move = await self._act(
                    view,
                    seat,
                    allowed=_allowed(seat),
                    description=_description(seat),
                )
                results[seat] = move
                remaining.discard(seat)
                continue

            raise ScriptExhausted(
                f"No scripted input for remaining human seats {sorted(remaining)}"
            )

        return results

    async def record_event(self, source: str, arguments: dict) -> None:
        self._drain_metadata(set())
        row = self._script_row(self._script_index)
        if row is not None:
            script_seat, script_source, script_args, kind = row
            if kind == LogEntryKind.GAME and script_seat is None:
                self._script_index += 1
                if script_source != source or not args_equal(script_args, arguments):
                    raise ReplayDivergence(
                        f"record_event {source!r} args mismatch: "
                        f"log={script_args!r} live={arguments!r}"
                    )
        if self._verbose:
            print(f"[event] {source} {arguments}")
        self.log.event(source, arguments)
