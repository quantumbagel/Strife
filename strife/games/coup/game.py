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
from strife.games.coup.bot import choose_move
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


class Coup(Game):
    ROLES = ["duke", "assassin", "captain", "ambassador", "contessa"]

    def __init__(self, players, settings, rng):
        super().__init__(players, settings, rng)
        # Create deck: 3 of each role
        self.deck = list(self.ROLES * 3)
        self.rng.shuffle(self.deck)

        # Hands: list of active cards for each player
        self.hands: dict[int, list[str]] = {p.seat: [self.deck.pop(), self.deck.pop()] for p in players}
        # Revealed cards: public knowledge
        self.revealed: dict[int, list[str]] = {p.seat: [] for p in players}
        
        # Coins: start at 2
        self.coins: dict[int, int] = {p.seat: 2 for p in players}
        self.alive: set[int] = {p.seat for p in players}
        self.current = self.rng.randint(0, len(players) - 1)
        self.history: list[str] = []

        # Flow phase tracking variables
        self.state_phase = "turn"  # turn, challenge_window, block_window, block_challenge_window, lose_influence, exchange
        self.current_actor: int | None = None
        self.current_action: str | None = None
        self.current_target: int | None = None
        self.current_blocker: int | None = None
        self.current_block_claim: str | None = None
        self.current_loser: int | None = None
        self.exchange_options: dict[int, list[str]] = {}

    def _next_player(self, current: int) -> int:
        n = len(self.players)
        seat = (current + 1) % n
        while seat not in self.alive:
            seat = (seat + 1) % n
        return seat

    def _turn_wait_description(self, *, forced_coup: bool) -> str:
        if forced_coup:
            return "Must Coup — choose a target"
        return "Take your turn"

    def _challenge_wait_description(self, actor: int, challenge_card: str, action_type: str) -> str:
        return (
            f"Challenge {self.players[actor].display_name}'s "
            f"{challenge_card.title()} claim ({action_type.title()}) or pass"
        )

    def _block_wait_description(self, action_type: str) -> str:
        labels = {
            "foreign_aid": "Block Foreign Aid (Duke) or pass",
            "assassinate": "Block Assassination (Contessa) or pass",
            "steal": "Block Steal (Captain/Ambassador) or pass",
        }
        return labels.get(action_type, "Block or pass")

    def _block_challenge_wait_description(self, blocker: int, claim: str) -> str:
        return (
            f"Challenge {self.players[blocker].display_name}'s "
            f"{claim.title()} block or pass"
        )

    def _role_emoji(self, ctx: GameContext, role: str) -> str:
        fallback = {"duke": "👑", "assassin": "🗡️", "captain": "⚓", "ambassador": "💼", "contessa": "🛡️"}.get(role, "🎴")
        return ctx.emoji.get(f"coup_{role}") or fallback

    def _format_hand(self, ctx: GameContext, seat: int, private: bool = False) -> str:
        if private:
            return " ".join(f"{self._role_emoji(ctx, r)} **{r.title()}**" for r in self.hands[seat])
        # Public view shows counts and revealed cards
        cards = ["🎴" for _ in self.hands[seat]]
        rev = [f"{self._role_emoji(ctx, r)} ~~{r.title()}~~" for r in self.revealed[seat]]
        return " ".join(cards + rev)

    async def play(self, ctx: GameContext) -> GameOutcome:
        last_actor = None
        while len(self.alive) > 1:
            actor = self.current
            if actor != last_actor:
                self.state_phase = "turn"
                self.current_actor = actor
                self.current_action = None
                self.current_target = None
                self.current_blocker = None
                self.current_block_claim = None
                self.current_loser = None
                last_actor = actor

            # 1. Update public view and request action
            view = self._public_board_view(ctx)
            


            # If actor has 10+ coins, they MUST Coup
            forced_coup = (self.coins[actor] >= 10)
            sources = {"action_coup", "action_target_select"} if forced_coup else {
                "action_income", "action_foreign_aid", "action_coup",
                "action_tax", "action_assassinate", "action_steal", "action_exchange",
                "action_target_select"
            }

            move = await ctx.request_input(
                view,
                actor=actor,
                sources=sources,
                description=self._turn_wait_description(forced_coup=forced_coup),
            )

            if move.source == "action_target_select":
                val = move.args.get("value") or (move.args.get("values")[0] if move.args.get("values") else None)
                if val is not None:
                    self.current_target = int(val)
                continue
            
            # Parse action details
            action_type = move.source.split("action_")[1]
            target = move.args.get("target")
            if target is None:
                target = self.current_target
            else:
                target = int(target)

            if action_type in ("coup", "assassinate", "steal"):
                if target is None or target not in self.alive or target == actor:
                    continue  # Invalid target

            self.current_action = action_type
            self.current_target = target

            await ctx.record_event("action_declare", {
                "player": actor,
                "type": action_type,
                "target": target,
            })

            # Check costs
            if action_type == "coup":
                self.coins[actor] -= 7
            elif action_type == "assassinate":
                self.coins[actor] -= 3

            # 2. Challenge Phase (Character actions: tax, assassinate, steal, exchange)
            challenge_card = {
                "tax": "duke",
                "assassinate": "assassin",
                "steal": "captain",
                "exchange": "ambassador",
            }.get(action_type)

            action_blocked = False
            action_failed = False

            if challenge_card is not None:
                self.state_phase = "challenge_window"
                opponents = set(self.alive) - {actor}
                
                # Check for challenges
                react_moves = await ctx.request_inputs(
                    self._public_board_view(ctx, status=f"Waiting for challenges to {self.players[actor].mention}'s claim of {challenge_card.title()} ({action_type.title()})..."),
                    actors=opponents,
                    sources={"challenge", "pass"},
                    until="any",
                    description=self._challenge_wait_description(actor, challenge_card, action_type),
                )

                challenger_seat = None
                react_move = None
                for seat, m in react_moves.items():
                    if m.source == "challenge":
                        challenger_seat = seat
                        react_move = m
                        break
                
                if react_move is None and react_moves:
                    challenger_seat, react_move = next(iter(react_moves.items()))

                if react_move and react_move.source == "challenge":
                    # A challenge occurred!
                    await ctx.record_event("challenge_declare", {
                        "challenger": challenger_seat,
                        "challenged": actor,
                        "card": challenge_card,
                    })

                    if challenge_card in self.hands[actor]:
                        # Actor has the card - they win the challenge!
                        self.history.append(f"{self.players[actor].mention} successfully proved they have {challenge_card}!")
                        
                        # Swap card
                        self.hands[actor].remove(challenge_card)
                        self.deck.append(challenge_card)
                        self.rng.shuffle(self.deck)
                        self.hands[actor].append(self.deck.pop())

                        # Challenger loses a card
                        await self._lose_influence(ctx, challenger_seat, f"{self.players[challenger_seat].mention} failed challenge and must lose influence.")
                    else:
                        # Actor lied - they lose the challenge!
                        self.history.append(f"{self.players[actor].mention} lied about having {challenge_card}!")
                        action_failed = True
                        await self._lose_influence(ctx, actor, f"{self.players[actor].mention} failed challenge and must lose influence.")

            # 3. Block Phase (if action is blockable and did not fail)
            if not action_failed and action_type in ("foreign_aid", "assassinate", "steal"):
                self.state_phase = "block_window"
                blockers = ({target} if action_type in ("assassinate", "steal") else set(self.alive) - {actor}) & self.alive
                
                block_moves = await ctx.request_inputs(
                    self._public_board_view(ctx, status=f"Waiting for blocks..."),
                    actors=blockers,
                    sources={"block_captain", "block_ambassador", "block_contessa", "block_duke", "pass"},
                    until="any",
                    description=self._block_wait_description(action_type),
                )

                blocker_seat = None
                block_move = None
                for seat, m in block_moves.items():
                    if m.source.startswith("block_"):
                        blocker_seat = seat
                        block_move = m
                        break
                
                if block_move is None and block_moves:
                    blocker_seat, block_move = next(iter(block_moves.items()))

                if block_move and block_move.source.startswith("block_"):
                    claim = block_move.source.split("block_")[1]
                    self.current_blocker = blocker_seat
                    self.current_block_claim = claim
                    await ctx.record_event("block_declare", {
                        "blocker": blocker_seat,
                        "action": action_type,
                        "claim": claim,
                    })

                    # Active player can challenge the block
                    self.state_phase = "block_challenge_window"
                    challenge_react = await ctx.request_input(
                        self._public_board_view(ctx, status=f"{self.players[blocker_seat].mention} blocks with {claim.title()}. Challenge?"),
                        actor=actor,
                        sources={"challenge", "pass"},
                        description=self._block_challenge_wait_description(blocker_seat, claim),
                    )

                    if challenge_react.source == "challenge":
                        await ctx.record_event("block_challenge", {
                            "challenger": actor,
                            "blocker": blocker_seat,
                            "claim": claim,
                        })

                        # Blocker must show claim card
                        if claim in self.hands[blocker_seat]:
                            # Blocker tells truth - block succeeds, challenger (actor) loses card
                            self.history.append(f"{self.players[blocker_seat].mention} proved they have {claim}!")
                            action_blocked = True
                            
                            self.hands[blocker_seat].remove(claim)
                            self.deck.append(claim)
                            self.rng.shuffle(self.deck)
                            self.hands[blocker_seat].append(self.deck.pop())

                            await self._lose_influence(ctx, actor, f"{self.players[actor].mention} failed block challenge and must lose influence.")
                        else:
                            # Blocker lied - block fails, blocker loses card
                            self.history.append(f"{self.players[blocker_seat].mention} lied about having {claim}!")
                            await self._lose_influence(ctx, blocker_seat, f"{self.players[blocker_seat].mention} failed block challenge.")
                    else:
                        action_blocked = True

            # 4. Resolve Action Effects
            if not action_failed and not action_blocked:
                if action_type == "income":
                    self.coins[actor] += 1
                    self.history.append(f"{self.players[actor].mention} took Income.")
                elif action_type == "foreign_aid":
                    self.coins[actor] += 2
                    self.history.append(f"{self.players[actor].mention} took Foreign Aid.")
                elif action_type == "tax":
                    self.coins[actor] += 3
                    self.history.append(f"{self.players[actor].mention} taxed the Treasury (+3 coins).")
                elif action_type == "coup":
                    self.history.append(f"{self.players[actor].mention} staged a Coup on {self.players[target].mention}!")
                    await self._lose_influence(ctx, target, f"Coup target {self.players[target].mention} must lose influence.")
                elif action_type == "assassinate":
                    self.history.append(f"{self.players[actor].mention} assassinated {self.players[target].mention}!")
                    await self._lose_influence(ctx, target, f"Assassination target {self.players[target].mention} must lose influence.")
                elif action_type == "steal":
                    stolen = min(2, self.coins[target])
                    self.coins[actor] += stolen
                    self.coins[target] -= stolen
                    self.history.append(f"{self.players[actor].mention} stole {stolen} coins from {self.players[target].mention}.")
                elif action_type == "exchange":
                    self.state_phase = "exchange"
                    # Ambassador exchange
                    drawn = [self.deck.pop(), self.deck.pop()]
                    self.exchange_options[actor] = list(self.hands[actor] + drawn)

                    # Prompt privately for keeping
                    public_view = self._public_board_view(ctx, status=f"Waiting for {self.players[actor].mention} to exchange cards...")
                    keep_move = await ctx.request_input(
                        public_view,
                        actor=actor,
                        sources={"exchange_select"},
                        description="Exchange — choose cards to keep",
                    )
                    keep_list = keep_move.args.get("values", [])
                    if not keep_list and keep_move.args.get("value"):
                        keep_list = [keep_move.args.get("value")]
                    if not keep_list and keep_move.args.get("keep"):
                        keep_list = keep_move.args.get("keep")

                    # Update hand
                    for card in keep_list:
                        if card in self.exchange_options[actor]:
                            self.exchange_options[actor].remove(card)
                    
                    # Return leftovers to deck
                    self.hands[actor] = keep_list
                    self.deck.extend(self.exchange_options[actor])
                    self.rng.shuffle(self.deck)
                    self.history.append(f"{self.players[actor].mention} exchanged cards with the Deck.")
                    
                    await ctx.record_event("exchange_resolve", {
                        "player": actor,
                        "keep": keep_list,
                    })

            self.current = self._next_player(actor)

        # Game over, compile outcome
        winner = next(iter(self.alive))
        winner_mention = self.players[winner].mention
        results = {p.seat: "win" if p.seat == winner else "loss" for p in self.players}
        player_descriptions = {
            p.seat: "Won the coup!" if p.seat == winner else "Influence eliminated."
            for p in self.players
        }

        return GameOutcome(
            results=results,
            summary={"winner": winner, "history": list(self.history)},
            description=f"{winner_mention} won!",
            player_descriptions=player_descriptions,
        )

    async def _lose_influence(self, ctx: GameContext, seat: int, status_message: str) -> None:
        self.state_phase = "lose_influence"
        self.current_loser = seat
        cards = self.hands[seat]
        if not cards:
            return

        if len(cards) == 1:
            # Forced choice
            lost_card = cards.pop()
            self.revealed[seat].append(lost_card)
            self.history.append(f"{self.players[seat].mention} revealed their last card: {lost_card.title()}.")
            self.alive.discard(seat)
            await ctx.record_event("lose_influence_resolve", {
                "player": seat,
                "card": lost_card,
            })
            return

        # Request selection
        public_view = self._public_board_view(ctx, status=status_message)
        move = await ctx.request_input(
            public_view,
            actor=seat,
            sources={"lose_influence_select"},
            description="Choose a card to reveal",
        )
        lost_card = move.args.get("value") or (move.args.get("values")[0] if move.args.get("values") else (move.args.get("card") or cards[0]))
        
        if lost_card in cards:
            self.hands[seat].remove(lost_card)
            self.revealed[seat].append(lost_card)
        else:
            lost_card = self.hands[seat].pop()
            self.revealed[seat].append(lost_card)

        self.history.append(f"{self.players[seat].mention} revealed a card: {lost_card.title()}.")
        
        await ctx.record_event("lose_influence_resolve", {
            "player": seat,
            "card": lost_card,
        })

    def get_lose_influence_view(self, seat: int, ctx: GameContext) -> LayoutView:
        cards = self.hands[seat]
        choices = [
            SelectChoice(label=role.title(), value=role, emoji=f"coup_{role}")
            for role in cards
        ]
        
        view = LayoutView()
        container = Container()
        message_lead(container, "Choose a card to reveal and discard", emoji=ctx.emoji)
        row = ActionRow()
        row.add_select(
            Select(
                source="lose_influence_select",
                placeholder="Choose a card to lose",
                choices=choices,
            )
        )
        container.add_action_row(row)
        view.add_container(container)
        return view

    def get_exchange_view(self, seat: int, ctx: GameContext) -> LayoutView:
        choices = [
            SelectChoice(label=role.title(), value=role, emoji=f"coup_{role}")
            for role in self.exchange_options[seat]
        ]
        
        view = LayoutView()
        container = Container()
        message_lead(
            container,
            f"Select {len(self.hands[seat])} card(s) to keep",
            emoji=ctx.emoji,
        )
        row = ActionRow()
        row.add_select(
            Select(
                source="exchange_select",
                placeholder="Select cards to keep",
                choices=choices,
                min_values=len(self.hands[seat]),
                max_values=len(self.hands[seat]),
            )
        )
        container.add_action_row(row)
        view.add_container(container)
        return view

    def _public_board_view(self, ctx: GameContext, status: str | None = None) -> LayoutView:
        view = LayoutView()
        container = Container()
        message_lead(
            container,
            status or f"Coins: {self.coins[self.current]}",
            emoji=ctx.emoji,
        )

        table_text = ""
        
        # Display current action / block details in a larger font and with detail
        action_desc = ""
        if self.current_action is not None and self.current_actor is not None:
            actor_name = self.players[self.current_actor].mention
            target_name = self.players[self.current_target].mention if self.current_target is not None else ""
            
            if self.current_action == "tax":
                action_desc = f"👑 {actor_name} is claiming **Duke** to Tax the Treasury (+3 coins)"
            elif self.current_action == "assassinate":
                action_desc = f"🗡️ {actor_name} is claiming **Assassin** to assassinate {target_name} (-3 coins)"
            elif self.current_action == "steal":
                action_desc = f"⚓ {actor_name} is claiming **Captain** to steal 2 coins from {target_name}"
            elif self.current_action == "exchange":
                action_desc = f"💼 {actor_name} is claiming **Ambassador** to Exchange cards with the Deck"
            elif self.current_action == "foreign_aid":
                action_desc = f"💰 {actor_name} is taking **Foreign Aid** (+2 coins)"
            elif self.current_action == "coup":
                action_desc = f"💥 {actor_name} is staging a **Coup** on {target_name} (-7 coins)"
            elif self.current_action == "income":
                action_desc = f"🪙 {actor_name} is taking **Income** (+1 coin)"
                
        block_desc = ""
        if self.current_blocker is not None and self.current_block_claim is not None:
            blocker_name = self.players[self.current_blocker].mention
            block_desc = f"🛡️ {blocker_name} is claiming **{self.current_block_claim.title()}** to block"

        if action_desc:
            table_text += f"## ⚡ Current Action\n> {action_desc}\n"
            if block_desc:
                table_text += f"> {block_desc}\n"
            table_text += "\n"

        table_text += "**Roster and Influence:**\n"
        for p in self.players:
            alive_status = self._format_hand(ctx, p.seat)
            table_text += f"• {p.mention}: {alive_status} (💰 {self.coins[p.seat]} coins)\n"

        if self.history:
            table_text += "\n**Recent Logs:**\n" + "\n".join(f"• {item}" for item in self.history[-5:])

        container.add_text(TextDisplay(table_text))

        if not ctx.is_replay:
            targets = [
                SelectChoice(label=p.display_name, value=str(p.seat), default=(p.seat == self.current_target))
                for p in self.players
                if p.seat in self.alive and p.seat != self.current
            ]

            if self.state_phase == "turn":
                if targets:
                    row_target = ActionRow()
                    row_target.add_select(
                        Select(
                            source="action_target_select",
                            placeholder="Choose a target (Coup/Assassinate/Steal)",
                            choices=targets,
                        )
                    )
                    container.add_action_row(row_target)

                row_actions1 = ActionRow()
                row_actions1.add_button(Button(source="action_income", label="Income (+1)", style=ButtonStyle.SECONDARY))
                row_actions1.add_button(Button(source="action_foreign_aid", label="Foreign Aid (+2)", style=ButtonStyle.SECONDARY))
                row_actions1.add_button(Button(source="action_coup", label="Coup (-7)", style=ButtonStyle.DANGER, disabled=(self.coins[self.current] < 7)))
                row_actions1.add_button(Button(source="action_tax", label="Tax (+3)", style=ButtonStyle.PRIMARY))
                container.add_action_row(row_actions1)

                row_actions2 = ActionRow()
                row_actions2.add_button(Button(source="action_assassinate", label="Assassinate (-3)", style=ButtonStyle.DANGER, disabled=(self.coins[self.current] < 3)))
                row_actions2.add_button(Button(source="action_steal", label="Steal (Captain)", style=ButtonStyle.PRIMARY))
                row_actions2.add_button(Button(source="action_exchange", label="Exchange (Ambassador)", style=ButtonStyle.PRIMARY))
                container.add_action_row(row_actions2)

            elif self.state_phase in ("challenge_window", "block_challenge_window"):
                row = ActionRow()
                row.add_button(Button(source="challenge", label="Challenge Claim", style=ButtonStyle.DANGER))
                row.add_button(Button(source="pass", label="Pass", style=ButtonStyle.SECONDARY))
                container.add_action_row(row)

            elif self.state_phase == "block_window":
                row = ActionRow()
                if self.current_action == "steal":
                    row.add_button(Button(source="block_captain", label="Block: Captain", style=ButtonStyle.PRIMARY))
                    row.add_button(Button(source="block_ambassador", label="Block: Ambassador", style=ButtonStyle.PRIMARY))
                elif self.current_action == "assassinate":
                    row.add_button(Button(source="block_contessa", label="Block: Contessa", style=ButtonStyle.PRIMARY))
                elif self.current_action == "foreign_aid":
                    row.add_button(Button(source="block_duke", label="Block: Duke", style=ButtonStyle.PRIMARY))

                row.add_button(Button(source="pass", label="Pass", style=ButtonStyle.SECONDARY))
                container.add_action_row(row)

            elif self.state_phase == "lose_influence":
                row = ActionRow()
                row.add_button(
                    Button(
                        source="lose_influence_open",
                        label=f"Lose Influence ({self.players[self.current_loser].mention})",
                        style=ButtonStyle.DANGER,
                    )
                )
                container.add_action_row(row)

            elif self.state_phase == "exchange":
                row = ActionRow()
                row.add_button(
                    Button(
                        source="exchange_open",
                        label=f"Exchange Cards ({self.players[self.current_actor].mention})",
                        style=ButtonStyle.PRIMARY,
                    )
                )
                container.add_action_row(row)

            row_peek = ActionRow()
            row_peek.add_button(Button(source="peek", label="Peek Cards", emoji="peek", style=ButtonStyle.SECONDARY))
            container.add_action_row(row_peek)

        view.add_container(container)
        return view

    async def final_view(self, ctx: GameContext, outcome: GameOutcome) -> LayoutView | None:
        summary = outcome.summary or {}
        winner_seat = summary.get("winner")
        view = LayoutView()
        container = Container()
        winner_name = self.players[winner_seat].mention if winner_seat is not None else "Unknown"
        message_lead(
            container,
            f"{winner_name} wins the match!",
            emoji=ctx.emoji,
            prefix_emoji="success",
        )

        history_text = "**Match History:**\n" + "\n".join(f"• {item}" for item in self.history)
        container.add_text(TextDisplay(history_text))
        view.add_container(container)
        return view

    def _replay_seat(self, move: MoveRecord, *keys: str) -> int | None:
        for key in keys:
            seat = move.arguments.get(key)
            if seat is not None:
                return int(seat)
        return move.actor_seat

    def _replay_reset(self) -> None:
        self.deck = list(self.ROLES * 3)
        self.rng.shuffle(self.deck)
        self.hands = {p.seat: [self.deck.pop(), self.deck.pop()] for p in self.players}
        self.revealed = {p.seat: [] for p in self.players}
        self.coins = {p.seat: 2 for p in self.players}
        self.alive = {p.seat for p in self.players}
        self.current = 0
        self.history = []
        self.state_phase = "turn"
        self.current_actor = None
        self.current_action = None
        self.current_target = None
        self.current_blocker = None
        self.current_block_claim = None
        self.current_loser = None
        self.exchange_options = {}

    def _replay_apply_action_costs(self, actor: int, action_type: str) -> None:
        if action_type == "coup":
            self.coins[actor] -= 7
        elif action_type == "assassinate":
            self.coins[actor] -= 3

    def _replay_apply_action_effects(
        self,
        actor: int,
        action_type: str,
        target: int | None,
    ) -> None:
        if action_type == "income":
            self.coins[actor] += 1
        elif action_type == "foreign_aid":
            self.coins[actor] += 2
        elif action_type == "tax":
            self.coins[actor] += 3
        elif action_type == "steal" and target is not None:
            stolen = min(2, self.coins[target])
            self.coins[actor] += stolen
            self.coins[target] -= stolen

    def _replay_lose_influence(self, seat: int, card: str) -> None:
        if card in self.hands[seat]:
            self.hands[seat].remove(card)
        elif self.hands[seat]:
            self.hands[seat].pop()
        self.revealed[seat].append(card)
        if not self.hands[seat]:
            self.alive.discard(seat)

    async def parse_replay(self, moves: list[MoveRecord], ctx: GameContext) -> list[ReplayFrame]:
        self._replay_reset()

        frames: list[ReplayFrame] = []
        from strife.presentation.compiler import clone_and_disable

        frames.append(
            ReplayFrame(
                index=0,
                turn_label="Start",
                actor_seat=None,
                view=clone_and_disable(self._public_board_view(ctx, status="Match start")),
                timestamp=ctx.started_at,
            )
        )

        pending_actor: int | None = None
        pending_action: str | None = None
        pending_target: int | None = None
        action_failed = False
        action_blocked = False
        block_pending = False

        def _resolve_pending_action() -> None:
            nonlocal pending_actor, pending_action, pending_target
            nonlocal action_failed, action_blocked, block_pending
            if pending_actor is None or pending_action is None:
                return
            if not action_failed and not action_blocked:
                self._replay_apply_action_effects(
                    pending_actor, pending_action, pending_target
                )
            pending_actor = None
            pending_action = None
            pending_target = None
            action_failed = False
            action_blocked = False
            block_pending = False
            self.current_blocker = None
            self.current_block_claim = None
            self.state_phase = "turn"

        for move in moves:
            takeover_info = system_replay_info(self.players, move)

            if move.source == "action_declare":
                _resolve_pending_action()
                actor = self._replay_seat(move, "player")
                if actor is None:
                    continue
                action_type = move.arguments.get("type")
                target = move.arguments.get("target")
                if target is not None:
                    target = int(target)

                self.current = actor
                self.current_actor = actor
                self.current_action = action_type
                self.current_target = target
                self.state_phase = "turn"
                if action_type:
                    self._replay_apply_action_costs(actor, action_type)

                pending_actor = actor
                pending_action = action_type
                pending_target = target
                action_failed = False
                action_blocked = False
                block_pending = False

                view = self._public_board_view(ctx)
                frames.append(
                    ReplayFrame(
                        index=len(frames),
                        turn_label="Action",
                        actor_seat=actor,
                        view=clone_and_disable(view),
                        takeover_info=takeover_info,
                        timestamp=move.created_at,
                    )
                )
                continue

            if move.source == "challenge_declare":
                self.state_phase = "challenge_window"
                challenged = self._replay_seat(move, "challenged")
                challenger = self._replay_seat(move, "challenger")
                view = self._public_board_view(
                    ctx,
                    status=(
                        f"{self.players[challenger].display_name} challenges "
                        f"{self.players[challenged].display_name}"
                        if challenger is not None and challenged is not None
                        else "Challenge declared"
                    ),
                )
                frames.append(
                    ReplayFrame(
                        index=len(frames),
                        turn_label="Challenge",
                        actor_seat=challenger,
                        view=clone_and_disable(view),
                        takeover_info=takeover_info,
                        timestamp=move.created_at,
                    )
                )
                continue

            if move.source == "block_declare":
                self.state_phase = "block_window"
                blocker = self._replay_seat(move, "blocker")
                claim = move.arguments.get("claim")
                self.current_blocker = blocker
                self.current_block_claim = claim
                block_pending = True
                view = self._public_board_view(ctx)
                frames.append(
                    ReplayFrame(
                        index=len(frames),
                        turn_label="Block",
                        actor_seat=blocker,
                        view=clone_and_disable(view),
                        takeover_info=takeover_info,
                        timestamp=move.created_at,
                    )
                )
                continue

            if move.source == "block_challenge":
                self.state_phase = "block_challenge_window"
                view = self._public_board_view(ctx, status="Block challenged")
                frames.append(
                    ReplayFrame(
                        index=len(frames),
                        turn_label="Block Challenge",
                        actor_seat=self._replay_seat(move, "challenger"),
                        view=clone_and_disable(view),
                        takeover_info=takeover_info,
                        timestamp=move.created_at,
                    )
                )
                continue

            if move.source == "exchange_resolve":
                actor = self._replay_seat(move, "player")
                if actor is not None:
                    keep = move.arguments.get("keep", [])
                    self.hands[actor] = list(keep)
                view = self._public_board_view(ctx, status="Cards exchanged")
                frames.append(
                    ReplayFrame(
                        index=len(frames),
                        turn_label="Exchange",
                        actor_seat=actor,
                        view=clone_and_disable(view),
                        takeover_info=takeover_info,
                        timestamp=move.created_at,
                    )
                )
                continue

            if move.source == "pass" and block_pending:
                action_blocked = True
                block_pending = False
                continue

            if move.source == "lose_influence_resolve":
                seat = self._replay_seat(move, "player")
                if seat is None:
                    continue
                card = move.arguments.get("card")
                if not card:
                    continue

                if block_pending:
                    if seat == self.current_blocker:
                        action_blocked = False
                        block_pending = False
                    elif seat == pending_actor:
                        action_blocked = True
                        block_pending = False
                elif seat == pending_actor:
                    action_failed = True

                self._replay_lose_influence(seat, card)
                self.state_phase = "lose_influence"
                self.current_loser = seat

                view = self._public_board_view(ctx, status="Influence lost")
                frames.append(
                    ReplayFrame(
                        index=len(frames),
                        turn_label="Influence Lost",
                        actor_seat=seat,
                        view=clone_and_disable(view),
                        takeover_info=takeover_info,
                        timestamp=move.created_at,
                    )
                )
                continue

        _resolve_pending_action()
        return frames

    async def bot_move(self, difficulty: str, seat: int) -> Move:
        move = await asyncio.to_thread(choose_move, self, difficulty, seat)
        # Adapt move inputs back to the button name clicks
        if move.source == "action":
            action_type = move.args.get("type", "income")
            source = f"action_{action_type}"
            return Move(actor_seat=seat, source=source, args=move.args)
        elif move.source == "block":
            claim = move.args.get("claim", "captain")
            source = f"block_{claim}"
            return Move(actor_seat=seat, source=source, args=move.args)
        elif move.source == "lose_card":
            # Maps to Select value selection in lose_influence
            return Move(actor_seat=seat, source="lose_influence_select", args=move.args)
        elif move.source == "exchange_keep":
            return Move(actor_seat=seat, source="exchange_select", args=move.args)
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
            cards = self.hands.get(seat, [])
            peek_text = (
                "You have no active cards left."
                if not cards
                else f"🎴 **Your Secret Cards:** {self._format_hand(ctx, seat, private=True)} | Coins: {self.coins.get(seat, 0)}"
            )
            await interaction.response.send_message(peek_text, ephemeral=True)
            return True

        if source == "lose_influence_open":
            if self.state_phase != "lose_influence" or getattr(self, "current_loser", None) != seat:
                await interaction.response.send_message("You cannot act right now — influence loss is not pending for you.", ephemeral=True)
                return True
            view = self.get_lose_influence_view(seat, ctx)
            compiled = surface.compiler.compile(view, resource_id=surface.resource_id, prefix=surface.prefix)
            await interaction.response.send_message(view=compiled, ephemeral=True)
            return True

        if source == "exchange_open":
            if self.state_phase != "exchange" or getattr(self, "current_actor", None) != seat:
                await interaction.response.send_message("You cannot act right now — card exchange is not available to you.", ephemeral=True)
                return True
            view = self.get_exchange_view(seat, ctx)
            compiled = surface.compiler.compile(view, resource_id=surface.resource_id, prefix=surface.prefix)
            await interaction.response.send_message(view=compiled, ephemeral=True)
            return True

        return await super().handle_query(seat, source, interaction, ctx, surface)
