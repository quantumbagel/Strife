from __future__ import annotations

from typing import ClassVar

from strife.engine import BotRequest, Interrupt
from strife.engine.context import GameContext
from strife.engine.game import Game
from strife.engine.outcomes import forfeit_outcome
from strife.engine.players import GameOutcome, Move, Result
from strife.engine.workers import run_cpu
from strife.games.spyfall.bot import choose_move
from strife.presentation.components import (
    ActionRow,
    Button,
    ButtonStyle,
    Container,
    LayoutView,
    Select,
    SelectChoice,
)
from strife.presentation.game_ui import message_lead, query_panel
from strife.presentation.roster import member_line
from strife.presentation.style import add_body, add_section, history_block


class Spyfall(Game):
    LOCATIONS: ClassVar[list[str]] = [
        "Airplane",
        "Bank",
        "Beach",
        "Casino",
        "Cathedral",
        "Circus Tent",
        "Embassy",
        "Hospital",
        "Hotel",
        "Military Base",
        "Movie Studio",
        "Ocean Liner",
        "Passenger Train",
        "Pirate Ship",
        "Polar Station",
        "Police Station",
        "Restaurant",
        "School",
        "Service Station",
        "Space Station",
        "Submarine",
        "Supermarket",
        "Theater",
        "University",
        "Zoo",
    ]

    def __init__(self, players, settings, rng):
        super().__init__(players, settings, rng)
        self.location = self.rng.choice(self.LOCATIONS)
        self.spy = self.rng.randint(0, len(players) - 1)
        self.alive = {p.seat for p in players}
        self.forfeited: set[int] = set()

        # State tracking
        self.accused_player: int | None = None
        self.accuser: int | None = None
        self.votes: dict[int, str] = {}
        self.history: list[str] = []
        self.turn = 1
        self.max_turns = 5
        self._passes: set[int] = set()
        self._accused_seats: set[int] = set()
        self._notice: str | None = None

    def active_seats(self) -> set[int]:
        return set(self.alive)

    def _winner_after_removal(self) -> str | None:
        if self.spy not in self.alive:
            return "villagers"
        if not (self.alive - {self.spy}):
            return "spy"
        return None

    def remove_player(self, seat: int) -> None:
        self._drop_seat(seat)

    def _drop_seat(self, seat: int) -> None:
        if seat < 0 or seat >= len(self.players):
            return
        if seat not in self.alive:
            return
        self.alive.discard(seat)
        self.forfeited.add(seat)
        self._passes.discard(seat)
        if self.accused_player == seat or self.accuser == seat:
            self.accused_player = None
            self.accuser = None
            self.votes = {}
        self.history.append(f"{self._name(seat)} left the game.")

    def _name(self, seat: int) -> str:
        return self.players[seat].mention

    def _discussion_round_done(self) -> bool:
        return self.alive <= self._passes

    def _phase_status(self, ctx: GameContext) -> str:
        if self.accused_player is not None:
            accused_name = self._name(self.accused_player)
            accuser_name = (
                self._name(self.accuser) if self.accuser is not None else "Someone"
            )
            return f"{accuser_name} accused {accused_name}. Voting in progress."
        return "Ask questions. Accuse a player or guess the location at any time."

    async def play(self, ctx: GameContext) -> GameOutcome:
        # 1. Setup secret PMs
        await ctx.record_event(
            "setup",
            {
                "location": self.location,
                "spy": self.spy,
            },
        )

        for p in self.players:
            priv_view = LayoutView()
            priv_container = Container()
            if p.seat == self.spy:
                message_lead(
                    priv_container,
                    "You are the spy. Blend in and guess the location.",
                    emoji=ctx.emoji,
                    prefix_emoji="game",
                )
            else:
                message_lead(
                    priv_container,
                    f"Secret location: **{self.location}**",
                    emoji=ctx.emoji,
                    prefix_emoji="learn",
                )
            priv_view.add_container(priv_container)
            await ctx.send_private(p.seat, priv_view)

        # 2. Main Gameplay Loop
        winner_faction = None
        while winner_faction is None and self.turn <= self.max_turns:
            pending_accusation: tuple[int, int] | None = None
            async with ctx.turn_deadline():
                while True:
                    view = self._discussion_view(ctx)
                    actors = set(self.alive)
                    if not actors:
                        break
                    moves = await ctx.request_inputs(
                        view,
                        actors=actors,
                        sources={"accuse", "guess_location", "pass"},
                        until="any",
                    )
                    if not moves:
                        self.turn += 1
                        self._passes.clear()
                        break
                    actor_seat, move = next(iter(moves.items()))

                    if move.interrupt is Interrupt.FORFEIT:
                        winner_faction = self._winner_after_removal()
                        break

                    if move.source == "pass":
                        self._passes.add(actor_seat)
                        if self._discussion_round_done():
                            self.turn += 1
                            self._passes.clear()
                        break

                    elif move.source == "guess_location":
                        if actor_seat != self.spy:
                            self._notice = "Only the spy can guess the location."
                            continue

                        guess = move.args.get("location")
                        if not guess:
                            self._notice = "Choose a location first."
                            continue
                        guess = str(guess)
                        if guess == self.location:
                            winner_faction = "spy"
                            self.history.append(
                                f"Spy guessed the location correctly: {guess}!"
                            )
                        else:
                            winner_faction = "villagers"
                            self.history.append(
                                f"Spy guessed the wrong location: {guess}! The location was {self.location}."
                            )

                        await ctx.record_event(
                            "spy_guess",
                            {
                                "spy": actor_seat,
                                "location": guess,
                                "correct": (guess == self.location),
                                "history": list(self.history),
                            },
                        )
                        break

                    elif move.source == "accuse":
                        if actor_seat in self._accused_seats:
                            self._notice = (
                                "You can only accuse one player per game. "
                                "You have already made an accusation."
                            )
                            continue
                        raw_target = move.args.get("target")
                        try:
                            target = int(raw_target) if raw_target is not None else None
                        except TypeError, ValueError:
                            target = None
                        if target is None:
                            self._notice = "Choose a player to accuse first."
                            continue
                        if target == actor_seat:
                            self._notice = "You cannot accuse yourself."
                            continue
                        if target not in self.alive:
                            self._notice = "That player is no longer in the game."
                            continue

                        self.accused_player = target
                        self.accuser = actor_seat
                        self.votes = {}
                        self._accused_seats.add(actor_seat)

                        await ctx.record_event(
                            "accusation_start",
                            {
                                "accuser": actor_seat,
                                "accused": target,
                            },
                        )
                        pending_accusation = (actor_seat, target)
                        break

            if not self.alive or winner_faction is not None:
                break
            if pending_accusation is None:
                continue

            actor_seat, target = pending_accusation
            # Enter voting phase
            voting_view = self._voting_view(ctx)
            # Wait for all other alive players to vote
            voters = set(self.alive) - {target}

            vote_moves = await ctx.request_inputs(
                voting_view,
                actors=voters,
                sources={"vote_guilty", "vote_innocent"},
                until="all",
            )

            winner_faction = self._winner_after_removal()
            if winner_faction is not None:
                break
            if self.accused_player is None:
                # The accused left mid-vote; the accuser keeps their accusation.
                self._accused_seats.discard(actor_seat)
                await ctx.record_event(
                    "accusation_resolve",
                    {
                        "accused": target,
                        "unanimous": False,
                        "cancelled": True,
                        "votes": {},
                        "history": list(self.history),
                    },
                )
                continue

            guilty_count = 0
            for v_seat, v_move in vote_moves.items():
                if v_move.interrupt is Interrupt.FORFEIT:
                    continue
                val = "guilty" if v_move.source == "vote_guilty" else "innocent"
                self.votes[v_seat] = val
                if val == "guilty":
                    guilty_count += 1

            voters = set(self.alive) - {target}
            unanimous = guilty_count == len(voters) and len(self.votes) == len(voters)
            if unanimous:
                if target == self.spy:
                    winner_faction = "villagers"
                    self.history.append(
                        f"{self._name(target)} was unanimously accused and was the Spy!"
                    )
                else:
                    winner_faction = "spy"
                    self.history.append(
                        f"{self._name(target)} was unanimously accused but was innocent! The real spy was {self._name(self.spy)}."
                    )

                await ctx.record_event(
                    "accusation_resolve",
                    {
                        "accused": target,
                        "unanimous": True,
                        "votes": dict(self.votes),
                        "history": list(self.history),
                    },
                )
                break
            else:
                self.history.append(
                    f"Accusation of {self._name(target)} failed ({guilty_count}/{len(voters)} guilty votes)."
                )
                await ctx.record_event(
                    "accusation_resolve",
                    {
                        "accused": target,
                        "unanimous": False,
                        "votes": dict(self.votes),
                        "history": list(self.history),
                    },
                )
                self.accused_player = None
                self.accuser = None

        # If turn limit reached without resolution, Spy wins by default
        if winner_faction is None:
            winner_faction = "spy"
            self.history.append(
                f"Timer/turn limit reached! The villagers failed to find the Spy. The Spy was {self._name(self.spy)}."
            )
            await ctx.record_event(
                "limit_reached",
                {
                    "history": list(self.history),
                },
            )

        return self._faction_outcome(winner_faction)

    def _faction_outcome(self, winner_faction: str) -> GameOutcome:
        results = {}
        player_descriptions = {}
        for p in self.players:
            is_spy_player = p.seat == self.spy
            if winner_faction == "spy":
                results[p.seat] = Result.WIN if is_spy_player else Result.LOSS
                player_descriptions[p.seat] = (
                    "Won as Spy!" if is_spy_player else "Lost to the Spy!"
                )
            else:
                results[p.seat] = Result.LOSS if is_spy_player else Result.WIN
                player_descriptions[p.seat] = (
                    "Lost as Spy!" if is_spy_player else "Found the Spy!"
                )

        for seat in self.forfeited:
            results[seat] = Result.LOSS
            player_descriptions[seat] = "Forfeited"

        return GameOutcome(
            results=results,
            summary={
                "winner_faction": winner_faction,
                "spy": self.spy,
                "location": self.location,
                "history": list(self.history),
            },
            description=f"The {winner_faction} won!",
            player_descriptions=player_descriptions,
        )

    def forfeit_end_outcome(
        self, forfeiter_seat: int, reason: str = "forfeit"
    ) -> GameOutcome:
        self._drop_seat(forfeiter_seat)
        if forfeiter_seat == self.spy:
            return self._faction_outcome("villagers")
        remaining_villagers = self.alive - {self.spy}
        if not remaining_villagers:
            return self._faction_outcome("spy")
        outcome = forfeit_outcome(
            self.players,
            forfeiter_seat,
            alive_seats=self.alive,
            reason=reason,
            must_end=True,
        )
        assert outcome is not None
        return outcome

    def render_replay(
        self, ctx: GameContext, live_view: LayoutView | None
    ) -> LayoutView | None:
        if self.accused_player is not None:
            view = self._voting_view_replay(ctx)
        else:
            view = self._discussion_view_replay(ctx)
        container = view.containers[0]
        add_section(
            container,
            "Revealed",
            (
                f"{ctx.emoji.get('learn', base=True)} **Location:** {self.location}\n"
                f"{ctx.emoji.get('game', base=True)} **Spy:** {self._name(self.spy)}"
            ),
        )
        return view

    def replay_label(self) -> str | None:
        if self.accused_player is not None:
            return "Accusation vote"
        return f"Round {self.turn}"

    def _discussion_view_replay(self, ctx: GameContext) -> LayoutView:
        view = LayoutView()
        container = Container()
        message_lead(
            container, self._phase_status(ctx), emoji=ctx.emoji, prefix_emoji="timer"
        )

        roster_lines = [
            member_line(
                ctx.emoji,
                user_id=p.user_id,
                display_name=p.display_name,
                is_bot=p.is_bot,
                bot_difficulty=p.bot_difficulty,
            )
            for p in self.players
        ]
        add_section(
            container,
            "Players",
            "\n".join(roster_lines) if roster_lines else "_None_",
        )
        add_section(
            container,
            "Locations",
            "\n".join(
                f"{ctx.emoji.get('bullet', base=True)} {loc}" for loc in self.LOCATIONS
            ),
        )
        block = history_block(self.history, ctx.emoji, limit=5)
        if block:
            add_body(container, block)
        view.add_container(container)
        return view

    def _discussion_view(self, ctx: GameContext) -> LayoutView:
        view = self._discussion_view_replay(ctx)
        container = view.containers[0]
        add_body(
            container,
            "-# Accuse someone else. Only the spy can guess the location.",
        )
        if self._notice:
            add_body(container, self._notice)
            self._notice = None
        other_choices = [
            SelectChoice(label=p.display_name, value=str(p.seat))
            for p in self.players
            if p.seat in self.alive
        ]
        if not other_choices:
            other_choices = [SelectChoice(label="No one to accuse", value="_")]
        row1 = ActionRow()
        row1.add_select(
            Select(
                source="target",
                placeholder="Accuse a player of being the spy",
                choices=other_choices,
                form=True,
            )
        )
        container.add_action_row(row1)

        loc_choices = [SelectChoice(label=loc, value=loc) for loc in self.LOCATIONS]
        row2 = ActionRow()
        row2.add_select(
            Select(
                source="location",
                placeholder="Guess location (Spy only)",
                choices=loc_choices,
                form=True,
            )
        )
        container.add_action_row(row2)

        row3 = ActionRow()
        row3.add_button(
            Button(
                source="accuse",
                label="Submit Accusation",
                style=ButtonStyle.DANGER,
            )
        )
        row3.add_button(
            Button(
                source="guess_location",
                label="Guess Location (Spy only)",
                style=ButtonStyle.SUCCESS,
            )
        )
        row3.add_button(
            Button(
                source="pass",
                label="Pass",
                style=ButtonStyle.SECONDARY,
            )
        )
        row3.add_button(
            Button(
                source="peek",
                label="Peek Info",
                emoji="peek",
                style=ButtonStyle.SECONDARY,
                query=True,
            )
        )
        container.add_action_row(row3)
        return view

    def _voting_view_replay(self, ctx: GameContext) -> LayoutView:
        view = LayoutView()
        container = Container()
        accused_name = self._name(self.accused_player)
        message_lead(
            container,
            f"Is **{accused_name}** the Spy? (Unanimous vote required)",
            emoji=ctx.emoji,
            prefix_emoji="ban",
        )

        vote_lines = []
        for p in self.players:
            if p.seat == self.accused_player:
                continue
            v_status = self.votes.get(p.seat, "Pending")
            vote_lines.append(f"{p.mention}: **{v_status.title()}**")
        add_section(
            container, "Votes", "\n".join(vote_lines) if vote_lines else "_None_"
        )
        view.add_container(container)
        return view

    def _voting_view(self, ctx: GameContext) -> LayoutView:
        view = self._voting_view_replay(ctx)
        container = view.containers[0]
        row = ActionRow()
        row.add_button(
            Button(
                source="vote_guilty",
                label="Guilty",
                style=ButtonStyle.DANGER,
            )
        )
        row.add_button(
            Button(
                source="vote_innocent",
                label="Innocent",
                style=ButtonStyle.SUCCESS,
            )
        )
        container.add_action_row(row)

        row2 = ActionRow()
        row2.add_button(
            Button(
                source="peek",
                label="Peek Info",
                emoji="peek",
                style=ButtonStyle.SECONDARY,
                query=True,
            )
        )
        container.add_action_row(row2)
        return view

    async def final_view(
        self, ctx: GameContext, outcome: GameOutcome
    ) -> LayoutView | None:
        summary = outcome.summary or {}
        winner = summary.get("winner_faction", "villagers")
        spy_name = self._name(summary.get("spy", 0))
        view = LayoutView()
        container = Container()
        message_lead(
            container,
            f"The {winner} won. The spy was **{spy_name}**. The location was **{self.location}**.",
            emoji=ctx.emoji,
            prefix_emoji="success",
        )
        block = history_block(self.history, ctx.emoji, limit=10)
        if block:
            add_body(container, block)
        view.add_container(container)
        return view

    async def bot_move(self, request: BotRequest) -> Move:
        return await run_cpu(choose_move, self, request)

    async def handle_query(self, seat: int, source: str, ctx: GameContext) -> bool:
        if source == "peek":
            if seat == self.spy:
                view = query_panel(
                    ctx,
                    title="You are the spy",
                    prefix_emoji="game",
                    body="Blend in and guess the location.",
                )
            else:
                view = query_panel(
                    ctx,
                    title="Secret location",
                    prefix_emoji="learn",
                    body=f"**{self.location}**",
                )
            await ctx.respond_query(view)
            return True
        return await super().handle_query(seat, source, ctx)
