from __future__ import annotations

import asyncio

from strife.engine.context import GameContext, ReplayFrame
from strife.engine.game import Game
from strife.engine.players import GameOutcome, Move
from strife.persistence.repositories import MoveRecord
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
from strife.presentation.game_frame import add_game_header
from strife.presentation.roster import player_mention


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

    def _name(self, seat: int) -> str:
        return self.players[seat].display_name

    def _turn_status(self, ctx: GameContext) -> str:
        if self.accused_player is not None:
            accused_name = self._name(self.accused_player)
            accuser_name = self._name(self.accuser) if self.accuser is not None else "System"
            return f"⚠️ {accuser_name} accused {accused_name}! Voting in progress..."
        return "Ask questions. Accuse a player or guess the location at any time."

    async def play(self, ctx: GameContext) -> GameOutcome:
        # 1. Setup secret PMs
        await ctx.record_action("setup", {
            "location": self.location,
            "spy": self.spy,
        })

        for p in self.players:
            priv_view = LayoutView()
            priv_container = Container()
            if p.seat == self.spy:
                add_game_header(
                    priv_container,
                    ctx.emoji,
                    game_key=self.metadata.key,
                    game_name=self.metadata.name,
                    title="Your Role",
                    status="🕵️ You are the SPY! Try to blend in and guess the location.",
                    is_replay=ctx.is_replay,
                )
            else:
                add_game_header(
                    priv_container,
                    ctx.emoji,
                    game_key=self.metadata.key,
                    game_name=self.metadata.name,
                    title="Your Role",
                    status=f"📍 Secret Location: **{self.location}**",
                    is_replay=ctx.is_replay,
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
                sources={"accuse", "guess_location", "pass"},
                until="any",
            )

            # Get the first move that occurred
            actor_seat, move = next(iter(moves.items()))

            if move.source == "pass":
                # A simple pass move from a bot or timer.
                # If all bots pass, we increment turn counter.
                self.turn += 1
                continue

            elif move.source == "guess_location":
                # Only the spy can guess location
                if actor_seat != self.spy:
                    # Non-spy trying to guess - ignore or penalize
                    continue

                guess = move.args.get("location")
                if guess == self.location:
                    winner_faction = "spy"
                    self.history.append(f"Spy guessed the location correctly: {guess}!")
                else:
                    winner_faction = "villagers"
                    self.history.append(f"Spy guessed the wrong location: {guess}! The location was {self.location}.")
                
                await ctx.record_action("spy_guess", {
                    "spy": actor_seat,
                    "location": guess,
                    "correct": (guess == self.location),
                    "history": list(self.history),
                })
                break

            elif move.source == "accuse":
                target = int(move.args.get("target", -1))
                if target not in self.alive or target == actor_seat:
                    continue

                self.accused_player = target
                self.accuser = actor_seat
                self.votes = {}

                await ctx.record_action("accusation_start", {
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
                    sources={"vote"},
                    until="all",
                )

                guilty_count = 0
                for v_seat, v_move in vote_moves.items():
                    val = v_move.args.get("value", "innocent")
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
                    
                    await ctx.record_action("accusation_resolve", {
                        "accused": target,
                        "unanimous": True,
                        "votes": dict(self.votes),
                        "history": list(self.history),
                    })
                    break
                else:
                    self.history.append(f"Accusation of {self._name(target)} failed ({guilty_count}/{len(voters)} guilty votes).")
                    await ctx.record_action("accusation_resolve", {
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
            await ctx.record_action("limit_reached", {
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
        add_game_header(
            container,
            ctx.emoji,
            game_key=self.metadata.key,
            game_name=self.metadata.name,
            title=f"Turn {self.turn}/{self.max_turns} - Discussion Phase",
            status=self._turn_status(ctx),
            status_emoji="timer",
            is_replay=ctx.is_replay,
        )

        # Show players list
        roster_text = "**Active Players:**\n"
        for p in self.players:
            roster_text += f"• {p.display_name}\n"

        # Show possible locations list
        loc_text = "\n**Possible Locations:**\n"
        for loc in self.LOCATIONS:
            loc_text += f"• {loc}\n"

        container.add_text(TextDisplay(roster_text + loc_text))

        # Inputs
        # 1. Accuse Player Select Menu
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

        # 2. Guess Location Select Menu (used by spy)
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

        # 3. Action Buttons
        row3 = ActionRow()
        row3.add_button(
            Button(
                source="accuse",
                label="Submit Accusation",
                style=ButtonStyle.DANGER,
                disabled=ctx.is_replay,
            )
        )
        row3.add_button(
            Button(
                source="guess_location",
                label="Guess Location",
                style=ButtonStyle.SUCCESS,
                disabled=ctx.is_replay,
            )
        )
        row3.add_button(
            Button(
                source="pass",
                label="Pass / End Turn",
                style=ButtonStyle.SECONDARY,
                disabled=ctx.is_replay,
            )
        )
        row3.add_button(
            Button(
                source="peek",
                label="Peek Info",
                emoji="peek",
                style=ButtonStyle.SECONDARY,
                disabled=ctx.is_replay,
            )
        )
        container.add_action_row(row3)

        view.add_container(container)
        return view

    def _voting_view(self, ctx: GameContext) -> LayoutView:
        view = LayoutView()
        container = Container()
        accused_name = self._name(self.accused_player)
        add_game_header(
            container,
            ctx.emoji,
            game_key=self.metadata.key,
            game_name=self.metadata.name,
            title="Accusation Voting",
            status=f"Is **{accused_name}** the Spy? (Unanimous vote required)",
            status_emoji="ban",
            is_replay=ctx.is_replay,
        )

        votes_text = "**Votes Cast:**\n"
        for p in self.players:
            if p.seat == self.accused_player:
                continue
            v_status = self.votes.get(p.seat, "Pending...")
            votes_text += f"• {p.display_name}: **{v_status.upper()}**\n"

        container.add_text(TextDisplay(votes_text))

        row = ActionRow()
        row.add_button(
            Button(
                source="vote_guilty",
                label="Guilty",
                style=ButtonStyle.DANGER,
                disabled=ctx.is_replay,
            )
        )
        row.add_button(
            Button(
                source="vote_innocent",
                label="Innocent",
                style=ButtonStyle.SUCCESS,
                disabled=ctx.is_replay,
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
                disabled=ctx.is_replay,
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
        add_game_header(
            container,
            ctx.emoji,
            game_key=self.metadata.key,
            game_name=self.metadata.name,
            title="Game Over!",
            status=f"The {winner.upper()} won! The Spy was **{spy_name}**.",
            status_emoji="success",
            is_replay=ctx.is_replay,
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
            takeover_info = None
            if move.arguments.get("replaced_by_bot"):
                for p in self.players:
                    if p.seat == move.actor_seat:
                        p.is_bot = True
                        p.bot_difficulty = "hard"
                        takeover_info = {
                            "user_id": p.user_id,
                            "display_name": p.display_name,
                            "is_bot": p.is_bot,
                            "type": "bot_takeover",
                            "reason": move.arguments.get("replace_reason", "timeout"),
                        }

            if move.source == "setup":
                self.location = move.arguments["location"]
                self.spy = move.arguments["spy"]
                view = self._discussion_view(ctx)
                frames.append(ReplayFrame(
                    index=len(frames),
                    turn_label=f"Setup (Turn {len(frames)+1})",
                    actor_seat=None,
                    view=clone_and_disable(view),
                    takeover_info=takeover_info,
                    timestamp=move.created_at,
                ))

            elif move.source == "accusation_start":
                self.accuser = move.actor_seat
                self.accused_player = move.arguments["accused"]
                self.votes = {}
                view = self._voting_view(ctx)
                frames.append(ReplayFrame(
                    index=len(frames),
                    turn_label=f"Accusation (Turn {len(frames)+1})",
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
                    turn_label=f"Accusation Resolved (Turn {len(frames)+1})",
                    actor_seat=None,
                    view=clone_and_disable(view),
                    takeover_info=takeover_info,
                    timestamp=move.created_at,
                ))

            elif move.source == "spy_guess":
                self.history = move.arguments.get("history", [])
                view = LayoutView()
                container = Container()
                add_game_header(
                    container,
                    ctx.emoji,
                    game_key=self.metadata.key,
                    game_name=self.metadata.name,
                    title="Spy Guessed Location",
                    status=f"Guess: {move.arguments['location']}",
                    is_replay=ctx.is_replay,
                )
                view.add_container(container)
                frames.append(ReplayFrame(
                    index=len(frames),
                    turn_label=f"Spy Guess (Turn {len(frames)+1})",
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

    def peek_info(self, seat: int, ctx: GameContext) -> str:
        if seat == self.spy:
            return "🕵️ **You are the SPY!** Try to blend in and guess the location."
        return f"📍 **Secret Location: {self.location}**"
