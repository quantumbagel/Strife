from __future__ import annotations

import json
import random
from collections.abc import Mapping
from typing import Literal

from strife.engine.context import noop_turn_deadline
from strife.engine.game import Game
from strife.engine.inputs import (
    check_timeout_consequence,
    make_bot_request,
    resolve_description,
    resolve_sources,
    validate_bot_move,
    validate_form_args,
)
from strife.engine.match_log import MatchLog
from strife.engine.players import Move
from strife.engine.requests import SeatPrompt, TimeoutConsequence
from strife.presentation.components import LayoutView, form_fields
from strife.presentation.emoji import EmojiResolver


class ScriptExhausted(RuntimeError):
    """No scripted or interactive input is available for this request."""


class MockContext:
    """Local ``GameContext`` for CLI runs and plugin tests."""

    def __init__(
        self,
        game: Game,
        *,
        emoji: EmojiResolver,
        script: list[tuple[int, str, dict]] | None = None,
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

    def _take_scripted(
        self, seat: int, allowed: set[str] | None, view: LayoutView
    ) -> Move | None:
        if self._script_index >= len(self._script):
            return None
        script_seat, source, args = self._script[self._script_index]
        self._script_index += 1
        if script_seat != seat:
            raise ValueError(
                f"Script entry expected seat {seat}, got {script_seat}"
            )
        if allowed is not None and source not in allowed:
            raise ValueError(
                f"Scripted source {source!r} not in allowed sources {allowed!r}"
            )
        move = Move(actor_seat=seat, source=source, args=dict(args))
        self._check_form(view, move)
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

    async def _act(
        self,
        view: LayoutView,
        seat: int,
        *,
        allowed: set[str] | None,
        description: str | None,
    ) -> Move:
        if self.is_bot(seat):
            request = make_bot_request(
                self._game.players,
                view,
                seat,
                sources=allowed,
                description=description,
            )
            move = await self._game.bot_move(request)
            validate_bot_move(request, move)
        else:
            move = self._take_scripted(seat, allowed, view)
            if move is None:
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
        humans = {seat for seat in actors if not self.is_bot(seat)}
        bots = actors - humans

        def _description(seat: int) -> str | None:
            return resolve_description(description, per_seat=per_seat, seat=seat)

        def _allowed(seat: int) -> set[str] | None:
            return resolve_sources(
                view, sources, per_seat=per_seat, seat=seat
            )

        for seat in actors:
            check_timeout_consequence(timeout_consequence, _allowed(seat))

        if until == "any":
            if not humans:
                bot_seat = self._rng.choice(sorted(bots))
                move = await self._act(
                    view,
                    bot_seat,
                    allowed=_allowed(bot_seat),
                    description=_description(bot_seat),
                )
                return {bot_seat: move}

            if self._script_index < len(self._script):
                script_seat, _, _ = self._script[self._script_index]
                if script_seat in actors:
                    move = await self._act(
                        view,
                        script_seat,
                        allowed=_allowed(script_seat),
                        description=_description(script_seat),
                    )
                    return {script_seat: move}

            if bots:
                bot_seat = self._rng.choice(sorted(bots))
                move = await self._act(
                    view,
                    bot_seat,
                    allowed=_allowed(bot_seat),
                    description=_description(bot_seat),
                )
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
        for seat in sorted(bots):
            move = await self._act(
                view,
                seat,
                allowed=_allowed(seat),
                description=_description(seat),
            )
            results[seat] = move

        remaining_humans = set(humans)
        while remaining_humans:
            if self._script_index < len(self._script):
                script_seat, _, _ = self._script[self._script_index]
                if script_seat in remaining_humans:
                    move = await self._act(
                        view,
                        script_seat,
                        allowed=_allowed(script_seat),
                        description=_description(script_seat),
                    )
                    results[script_seat] = move
                    remaining_humans.remove(script_seat)
                    continue

            if self._interactive:
                seat = self._prompt_choose_seat(
                    f"Choose a human to act: {sorted(remaining_humans)}; enter seat: ",
                    remaining_humans,
                )
                move = await self._act(
                    view,
                    seat,
                    allowed=_allowed(seat),
                    description=_description(seat),
                )
                results[seat] = move
                remaining_humans.remove(seat)
                continue

            raise ScriptExhausted(
                f"No scripted input for remaining human seats {sorted(remaining_humans)}"
            )

        return results

    async def record_event(self, source: str, arguments: dict) -> None:
        if self._verbose:
            print(f"[event] {source} {arguments}")
        self.log.event(source, arguments)
