from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import discord
    from strife.presentation.message import ViewSurface

from strife.engine.context import GameContext, ReplayFrame
from strife.engine.game import Game
from strife.engine.players import GameOutcome, Move
from strife.persistence.repositories import MoveRecord
from strife.engine.replay import system_replay_info
from strife.games.spyfall.bot import choose_move
from strife.presentation.components import (
    ActionRow,
    Button,
    ButtonStyle,
    Container,
    LayoutView,
    Select,
    SelectChoice,
    TextDisplay,
)
from strife.presentation.game_ui import message_lead


class Spyfall(Game):
    LOCATIONS = [
        "Space Station",
        "Submarine",
        "Casino",
        "Supermarket",
        "Pirate Ship",
        "Movie Studio",
        "Polar Station",
        "Military Base",
        "Airplane",
        "Carnival",
    ]

    def __init__(self, players, settings, rng):
        super().__init__(players, settings, rng)
        self.location = self.rng.choice(self.LOCATIONS)
        self.spy = self.rng.randint(0, len(players) - 1)
        self.alive = {p.seat for p in players}
        
        # State tracking
        self.accused_player: int | None = None
        self.accuser: int | None = None
        self.votes: dict[int, str] = {}
        self.history: list[str] = []
        self.turn = 1
        self.max_turns = 5
        self.pending_accuse: dict[int, int] = {}
        self.pending_guess: dict[int, str] = {}

    def _name(self, seat: int) -> str:
        return self.players[seat].mention

    def _phase_status(self, ctx: GameContext) -> str:
        if self.accused_player is not None:
            accused_name = self._name(self.accused_player)
            accuser_name = self._name(self.accuser) if self.accuser is not None else "System"
            return f"⚠️ {accuser_name} accused {accused_name}! Voting in progress..."
        return "Ask questions. Accuse a player or guess the location at any time."

    async def play(self, ctx: GameContext) -> GameOutcome:
        # 1. Setup secret PMs
        await ctx.record_event("setup", {
            "location": self.location,
            "spy": self.spy,
        })

        for p in self.players:
            priv_view = LayoutView()
            priv_container = Container()
            if p.seat == self.spy:
                message_lead(
                    priv_container,
                    "🕵️ You are the SPY! Try to blend in and guess the location.",
                    emoji=ctx.emoji,
                )
            else:
                message_lead(
                    priv_container,
                    f"📍 Secret Location: **{self.location}**",
                    emoji=ctx.emoji,
                )
            priv_view.add_container(priv_container)
            await ctx.send_private(p.seat, priv_view)

        # 2. Main Gameplay Loop
        winner_faction = None
        while winner_faction is None and self.turn <= self.max_turns:
            # We display the public discussion screen and wait for input
            view = self._discussion_view(ctx)
            
            # Request input from ANY active player
            moves = await ctx.request_inputs(
                view,
                actors=set(self.alive),
                sources={"accuse_select", "location_select", "accuse", "guess_location", "pass"},
                until="any",
                record=False,
            )

            if not moves:
                continue

            actor_seat, move = next(iter(moves.items()))

            if move.source == "accuse_select":
                val = move.args.get("value")
                if val is not None:
                    target = int(val)
                    if target in self.alive and target != actor_seat:
                        self.pending_accuse[actor_seat] = target
                continue

            if move.source == "location_select":
                if actor_seat != self.spy:
                    continue
                val = move.args.get("value")
                if val is not None:
                    self.pending_guess[actor_seat] = str(val)
                continue

            if move.source == "pass":
                # A simple pass move from a bot or timer.
                # If all bots pass, we increment turn counter.
                self.turn += 1
                continue

            elif move.source == "guess_location":
                if actor_seat != self.spy:
                    self.turn += 1
                    continue

                guess = self.pending_guess.get(actor_seat)
                if not guess:
                    continue
                if guess == self.location:
                    winner_faction = "spy"
                    self.history.append(f"Spy guessed the location correctly: {guess}!")
                else:
                    winner_faction = "villagers"
                    self.history.append(f"Spy guessed the wrong location: {guess}! The location was {self.location}.")
                
                await ctx.record_event("spy_guess", {
                    "spy": actor_seat,
                    "location": guess,
                    "correct": (guess == self.location),
                    "history": list(self.history),
                })
                break

            elif move.source == "accuse":
                target = self.pending_accuse.get(actor_seat)
                if target is None:
                    continue
                if target not in self.alive or target == actor_seat:
                    continue

                self.accused_player = target
                self.accuser = actor_seat
                self.votes = {}
                self.pending_accuse.pop(actor_seat, None)

                await ctx.record_event("accusation_start", {
                    "accuser": actor_seat,
                    "accused": target,
                })

                # Enter voting phase
                voting_view = self._voting_view(ctx)
                # Wait for all other alive players to vote
                voters = set(self.alive) - {target}
                
                vote_moves = await ctx.request_inputs(
                    voting_view,
                    actors=voters,
                    sources={"vote_guilty", "vote_innocent"},
                    until="all",
                    record=False,
                )

                guilty_count = 0
                for v_seat, v_move in vote_moves.items():
                    val = "guilty" if v_move.source == "vote_guilty" else "innocent"
                    self.votes[v_seat] = val
                    if val == "guilty":
                        guilty_count += 1

                # If unanimous guilty (except the accused themselves)
                unanimous = (guilty_count == len(voters))
                if unanimous:
                    if target == self.spy:
                        winner_faction = "villagers"
                        self.history.append(f"{self._name(target)} was unanimously accused and was the Spy!")
                    else:
                        winner_faction = "spy"
                        self.history.append(f"{self._name(target)} was unanimously accused but was innocent! The real spy was {self._name(self.spy)}.")
                    
                    await ctx.record_event("accusation_resolve", {
                        "accused": target,
                        "unanimous": True,
                        "votes": dict(self.votes),
                        "history": list(self.history),
                    })
                    break
                else:
                    self.history.append(f"Accusation of {self._name(target)} failed ({guilty_count}/{len(voters)} guilty votes).")
                    await ctx.record_event("accusation_resolve", {
                        "accused": target,
                        "unanimous": False,
                        "votes": dict(self.votes),
                        "history": list(self.history),
                    })
                    self.accused_player = None
                    self.accuser = None
                    self.turn += 1

        # If turn limit reached without resolution, Spy wins by default
        if winner_faction is None:
            winner_faction = "spy"
            self.history.append(f"Timer/turn limit reached! The villagers failed to find the Spy. The Spy was {self._name(self.spy)}.")
            await ctx.record_event("limit_reached", {
                "history": list(self.history),
            })

        # Compile final outcome
        results = {}
        player_descriptions = {}
        for p in self.players:
            is_spy_player = (p.seat == self.spy)
            if winner_faction == "spy":
                results[p.seat] = "win" if is_spy_player else "loss"
                player_descriptions[p.seat] = "Won as Spy!" if is_spy_player else "Lost to the Spy!"
            else:
                results[p.seat] = "loss" if is_spy_player else "win"
                player_descriptions[p.seat] = "Lost as Spy!" if is_spy_player else "Found the Spy!"

        return GameOutcome(
            results=results,
            summary={"winner_faction": winner_faction, "spy": self.spy, "location": self.location, "history": list(self.history)},
            description=f"The {winner_faction} won!",
            player_descriptions=player_descriptions,
        )

    def _discussion_view(self, ctx: GameContext) -> LayoutView:
        view = LayoutView()
        container = Container()
        message_lead(container, self._phase_status(ctx), emoji=ctx.emoji, prefix_emoji="timer")

        # Show players list
        roster_text = "**Active Players:**\n"
        for p in self.players:
            roster_text += f"• {p.mention}\n"

        # Show possible locations list
        loc_text = "\n**Possible Locations:**\n"
        for loc in self.LOCATIONS:
            loc_text += f"• {loc}\n"

        container.add_text(TextDisplay(roster_text + loc_text))

        if not ctx.is_replay:
            other_choices = [
                SelectChoice(label=p.display_name, value=str(p.seat))
                for p in self.players
                if p.seat in self.alive
            ]
            row1 = ActionRow()
            row1.add_select(
                Select(
                    source="accuse_select",
                    placeholder="Accuse a player of being the spy",
                    choices=other_choices,
                )
            )
            container.add_action_row(row1)

            loc_choices = [
                SelectChoice(label=loc, value=loc)
                for loc in self.LOCATIONS
            ]
            row2 = ActionRow()
            row2.add_select(
                Select(
                    source="location_select",
                    placeholder="Guess location (Spy only)",
                    choices=loc_choices,
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
                )
            )
            container.add_action_row(row3)

        view.add_container(container)
        return view

    def _voting_view(self, ctx: GameContext) -> LayoutView:
        view = LayoutView()
        container = Container()
        accused_name = self._name(self.accused_player)
        message_lead(
            container,
            f"Is **{accused_name}** the Spy? (Unanimous vote required)",
            emoji=ctx.emoji,
            prefix_emoji="ban",
        )

        votes_text = "**Votes Cast:**\n"
        for p in self.players:
            if p.seat == self.accused_player:
                continue
            v_status = self.votes.get(p.seat, "Pending...")
            votes_text += f"• {p.mention}: **{v_status.upper()}**\n"

        container.add_text(TextDisplay(votes_text))

        if not ctx.is_replay:
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
                )
            )
            container.add_action_row(row2)

        view.add_container(container)
        return view

    async def final_view(self, ctx: GameContext, outcome: GameOutcome) -> LayoutView | None:
        summary = outcome.summary or {}
        winner = summary.get("winner_faction", "villagers")
        spy_name = self._name(summary.get("spy", 0))
        view = LayoutView()
        container = Container()
        message_lead(
            container,
            f"The {winner.upper()} won! The Spy was **{spy_name}**.",
            emoji=ctx.emoji,
            prefix_emoji="success",
        )

        history_text = "**Log of Events:**\n" + "\n".join(f"• {item}" for item in self.history)
        container.add_text(TextDisplay(history_text))
        view.add_container(container)
        return view

    async def parse_replay(self, moves: list[MoveRecord], ctx: GameContext) -> list[ReplayFrame]:
        self.alive = {p.seat for p in self.players}
        self.history = []
        self.accused_player = None
        self.accuser = None
        self.votes = {}
        self.turn = 1

        frames: list[ReplayFrame] = []
        from strife.presentation.compiler import clone_and_disable

        for move in moves:
            takeover_info = system_replay_info(self.players, move)

            if move.source == "setup":
                self.location = move.arguments["location"]
                self.spy = move.arguments["spy"]
                view = self._discussion_view(ctx)
                frames.append(ReplayFrame(
                    index=len(frames),
                    turn_label="Setup",
                    actor_seat=None,
                    view=clone_and_disable(view),
                    takeover_info=takeover_info,
                    timestamp=move.created_at,
                ))

            elif move.source == "accusation_start":
                self.accuser = move.arguments.get("accuser", move.actor_seat)
                self.accused_player = move.arguments["accused"]
                self.votes = {}
                view = self._voting_view(ctx)
                frames.append(ReplayFrame(
                    index=len(frames),
                    turn_label="Accusation",
                    actor_seat=move.actor_seat,
                    view=clone_and_disable(view),
                    takeover_info=takeover_info,
                    timestamp=move.created_at,
                ))

            elif move.source == "accusation_resolve":
                self.votes = {int(k): v for k, v in move.arguments["votes"].items()}
                self.history = move.arguments.get("history", [])
                self.accused_player = None
                self.accuser = None
                self.turn += 1
                view = self._discussion_view(ctx)
                frames.append(ReplayFrame(
                    index=len(frames),
                    turn_label="Accusation Resolved",
                    actor_seat=None,
                    view=clone_and_disable(view),
                    takeover_info=takeover_info,
                    timestamp=move.created_at,
                ))

            elif move.source == "spy_guess":
                self.history = move.arguments.get("history", [])
                view = LayoutView()
                container = Container()
                message_lead(
                    container,
                    f"Guess: {move.arguments['location']}",
                    emoji=ctx.emoji,
                )
                view.add_container(container)
                frames.append(ReplayFrame(
                    index=len(frames),
                    turn_label="Spy Guess",
                    actor_seat=move.actor_seat,
                    view=clone_and_disable(view),
                    takeover_info=takeover_info,
                    timestamp=move.created_at,
                ))

        return frames

    async def bot_move(self, difficulty: str, seat: int) -> Move:
        # A mock turn choose method:
        # Converts buttons (vote_guilty, vote_innocent) to the args used by choose_move
        move = await asyncio.to_thread(choose_move, self, difficulty, seat)
        if move.source == "vote":
            # Translate to the source button click name
            val = move.args.get("value")
            source = "vote_guilty" if val == "guilty" else "vote_innocent"
            return Move(actor_seat=seat, source=source, args=move.args)
        return move

    async def handle_query(
        self,
        seat: int,
        source: str,
        interaction: discord.Interaction,
        ctx: GameContext,
        surface: ViewSurface,
    ) -> bool:
        if source == "peek":
            peek_text = (
                "🕵️ **You are the SPY!** Try to blend in and guess the location."
                if seat == self.spy
                else f"📍 **Secret Location: {self.location}**"
            )
            await interaction.response.send_message(peek_text, ephemeral=True)
            return True
        return await super().handle_query(seat, source, interaction, ctx, surface)
