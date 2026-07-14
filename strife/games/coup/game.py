from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, Mapping

if TYPE_CHECKING:
    import discord
    from strife.presentation.message import ViewSurface

from strife.engine.context import GameContext, ReplayFrame
from strife.engine.game import Game
from strife.engine.players import GameOutcome, Move, Player
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

    def __init__(self, players: list[Player], settings: Mapping[str, Any], rng):
        super().__init__(players, settings, rng)
        # Create deck: 3 of each role
        self.deck = list(self.ROLES * 3)
        self.rng.shuffle(self.deck)

        # Hands: list of active cards for each player
        self.hands: dict[int, list[str]] = {p.seat: [self.deck.pop(), self.deck.pop()] for p in players}
        # Revealed cards: public knowledge
        self.revealed: dict[int, list[str]] = {p.seat: [] for p in players}
        
        # Coins: start at 2 (but starting player gets 1 if 2-player match)
        self.coins: dict[int, int] = {p.seat: 2 for p in players}
        self.current = self.rng.randint(0, len(players) - 1)
        if len(players) == 2:
            self.coins[self.current] = 1

        self.alive: set[int] = {p.seat for p in players}
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

        # Interactive form selection state
        self.selected_action: str | None = None
        self.selected_target: str | None = None

    def _next_player(self, current: int) -> int:
        n = len(self.players)
        seat = (current + 1) % n
        while seat not in self.alive:
            seat = (seat + 1) % n
        return seat

    def _turn_wait_description(self, *, forced_coup: bool) -> str:
        if forced_coup:
            return "Must Coup — choose a target"
        return "Select action & target, then Submit"

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

    def _pick_challenge(self, moves: dict[int, Move]) -> tuple[int | None, Move | None]:
        for seat, move in moves.items():
            if move.source == "challenge":
                return seat, move
        return None, None

    def _pick_block(self, moves: dict[int, Move], action_type: str) -> tuple[int | None, Move | None]:
        valid = self._valid_block_sources(action_type)
        for seat, move in moves.items():
            if move.source in valid:
                return seat, move
        return None, None

    def _valid_block_sources(self, action_type: str) -> set[str]:
        return {
            "foreign_aid": {"block_duke"},
            "assassinate": {"block_contessa"},
            "steal": {"block_captain", "block_ambassador"},
        }.get(action_type, set())

    async def _resolve_action_challenge(
        self,
        ctx: GameContext,
        actor: int,
        challenger_seat: int,
        challenge_card: str,
    ) -> bool:
        """Resolve a challenge to an action claim. Returns True if the action failed."""
        await ctx.record_event("challenge_declare", {
            "challenger": challenger_seat,
            "challenged": actor,
            "card": challenge_card,
        })

        if challenge_card in self.hands[actor]:
            self.history.append(
                f"🛡️ {self.players[actor].mention} successfully proved they have the **{challenge_card.title()}**!"
            )
            self.hands[actor].remove(challenge_card)
            self.deck.append(challenge_card)
            self.rng.shuffle(self.deck)
            self.hands[actor].append(self.deck.pop())
            await self._lose_influence(
                ctx,
                challenger_seat,
                f"{self.players[challenger_seat].mention} failed challenge and must lose influence.",
            )
            return False

        self.history.append(
            f"🕵️ {self.players[actor].mention} lied about having the **{challenge_card.title()}**!"
        )
        await self._lose_influence(
            ctx,
            actor,
            f"{self.players[actor].mention} failed challenge and must lose influence.",
        )
        return True

    async def _resolve_block_challenge(
        self,
        ctx: GameContext,
        actor: int,
        blocker_seat: int,
        claim: str,
        challenger_seat: int,
    ) -> tuple[bool, bool]:
        """Resolve a block challenge. Returns (block_succeeded, challenger_lost_influence)."""
        await ctx.record_event("block_challenge", {
            "challenger": challenger_seat,
            "blocker": blocker_seat,
            "claim": claim,
        })

        if claim in self.hands[blocker_seat]:
            self.history.append(
                f"🛡️ {self.players[blocker_seat].mention} proved they have the **{claim.title()}**!"
            )
            self.hands[blocker_seat].remove(claim)
            self.deck.append(claim)
            self.rng.shuffle(self.deck)
            self.hands[blocker_seat].append(self.deck.pop())
            await self._lose_influence(
                ctx,
                challenger_seat,
                f"{self.players[challenger_seat].mention} failed block challenge and must lose influence.",
            )
            return True, True

        self.history.append(
            f"🕵️ {self.players[blocker_seat].mention} lied about having the **{claim.title()}**!"
        )
        await self._lose_influence(
            ctx,
            blocker_seat,
            f"{self.players[blocker_seat].mention} failed block challenge.",
        )
        return False, False

    def _role_emoji(self, ctx: GameContext, role: str) -> str:
        fallback = {"duke": "👑", "assassin": "🗡️", "captain": "⚓", "ambassador": "💼", "contessa": "🛡️"}.get(role, "🎴")
        return ctx.emoji.get(f"coup_{role}") or fallback

    def _format_hand(self, ctx: GameContext, seat: int, private: bool = False) -> str:
        if private:
            return " ".join(f"{self._role_emoji(ctx, r)} **{r.title()}**" for r in self.hands[seat])
        if ctx.is_replay:
            cards = [f"{self._role_emoji(ctx, r)} **{r.title()}**" for r in self.hands[seat]]
        else:
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
                self.selected_action = None
                self.selected_target = None
                last_actor = actor

            action_type = None
            target = None

            if ctx.is_bot(actor):
                difficulty = self.players[actor].bot_difficulty or "medium"
                move = await self.bot_move(difficulty, actor)
                action_type = move.args.get("action", "income")
                target_val = move.args.get("target")
                target = int(target_val) if target_val is not None else None
            else:
                # Interactive dropdown input loop for humans
                while True:
                    forced_coup = (self.coins[actor] >= 10)
                    if forced_coup:
                        self.selected_action = "coup"

                    view = self._public_board_view(ctx)

                    # Allowed inputs: selects and the submit button (if valid configuration selected)
                    sources = {"action_select", "target_select"}
                    is_valid = False
                    if self.selected_action is not None:
                        if self.selected_action in ("income", "foreign_aid", "tax", "exchange"):
                            is_valid = True
                        elif self.selected_action in ("coup", "assassinate", "steal"):
                            if self.selected_target is not None and self.selected_target != "none":
                                is_valid = True

                    if is_valid:
                        sources.add("submit_action")

                    move = await ctx.request_input(
                        view,
                        actor=actor,
                        sources=sources,
                        description=self._turn_wait_description(forced_coup=forced_coup),
                        timeout_seconds=30.0,
                        timeout_consequence="skip"
                    )

                    if move.source == "timeout":
                        # 30-sec Turn Timeout: Apply the "Skip Action" coin penalty
                        penalty = False
                        if self.coins[actor] > 0:
                            self.coins[actor] -= 1
                            penalty = True
                        msg = f"⏱️ {self.players[actor].mention} timed out!"
                        if penalty:
                            msg += " Action skipped and lost 1 coin as a penalty."
                        else:
                            msg += " Action skipped."
                        self.history.append(msg)
                        action_type = "skipped"
                        break

                    if move.source == "action_select":
                        self.selected_action = move.args.get("value")
                        if self.selected_action in ("income", "foreign_aid", "tax", "exchange"):
                            self.selected_target = "none"
                        elif self.selected_target == "none":
                            self.selected_target = None
                        continue
                    elif move.source == "target_select":
                        self.selected_target = move.args.get("value")
                        continue
                    elif move.source == "submit_action":
                        action_type = self.selected_action
                        target = None if self.selected_target in (None, "none") else int(self.selected_target)
                        break

            if action_type == "skipped":
                self.current = self._next_player(actor)
                continue

            if action_type in ("coup", "assassinate", "steal"):
                if target is None or target not in self.alive or target == actor:
                    continue  # Invalid target choice, force retry

            if action_type == "coup" and self.coins[actor] < 7:
                continue
            if action_type == "assassinate" and self.coins[actor] < 3:
                continue

            self.current_action = action_type
            self.current_target = target

            await ctx.record_event("action_declare", {
                "player": actor,
                "type": action_type,
                "target": target,
            })

            # Spend coins
            if action_type == "coup":
                self.coins[actor] -= 7
            elif action_type == "assassinate":
                self.coins[actor] -= 3

            # Challenge Phase
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

                if action_type in ("assassinate", "steal") and target is not None:
                    block_claims = {
                        "assassinate": {"block_contessa"},
                        "steal": {"block_captain", "block_ambassador"},
                    }[action_type]
                    per_seat_sources = {
                        seat: (
                            {"challenge", "pass"} | block_claims
                            if seat == target
                            else {"challenge", "pass"}
                        )
                        for seat in opponents
                    }
                    react_moves = await ctx.request_inputs(
                        self._public_board_view(
                            ctx,
                            status=(
                                f"React to {self.players[actor].display_name}'s Assassinate (Assassin claim)..."
                                if action_type == "assassinate"
                                else f"React to {self.players[actor].display_name}'s Steal (Captain claim)..."
                            )
                        ),
                        actors=opponents,
                        sources={"challenge", "pass"},
                        per_seat_sources=per_seat_sources,
                        record=False,
                        description=self._challenge_wait_description(actor, challenge_card, action_type),
                        timeout_seconds=10.0,
                        timeout_consequence="skip"
                    )

                    challenger_seat, react_move = self._pick_challenge(react_moves)
                    if react_move is not None and challenger_seat is not None:
                        action_failed = await self._resolve_action_challenge(
                            ctx, actor, challenger_seat, challenge_card
                        )

                    if not action_failed:
                        target_move = react_moves.get(target)
                        if target_move is not None and target_move.source in block_claims:
                            blocker_seat = target
                            claim = target_move.source.split("block_")[1]
                            self.current_blocker = blocker_seat
                            self.current_block_claim = claim
                            await ctx.record_event("block_declare", {
                                "blocker": blocker_seat,
                                "action": action_type,
                                "claim": claim,
                            })

                            self.state_phase = "block_challenge_window"
                            block_challengers = set(self.alive) - {blocker_seat}
                            challenge_moves = await ctx.request_inputs(
                                self._public_board_view(
                                    ctx,
                                    status=f"{self.players[blocker_seat].display_name} blocks with {claim.title()}. Challenge?"
                                ),
                                actors=block_challengers,
                                sources={"challenge", "pass"},
                                record=False,
                                description=self._block_challenge_wait_description(blocker_seat, claim),
                                timeout_seconds=10.0,
                                timeout_consequence="skip"
                            )
                            block_challenger, block_challenge_move = self._pick_challenge(challenge_moves)
                            if block_challenge_move is not None and block_challenger is not None:
                                blocked, _ = await self._resolve_block_challenge(
                                    ctx, actor, blocker_seat, claim, block_challenger
                                )
                                action_blocked = blocked
                            else:
                                action_blocked = True
                else:
                    react_moves = await ctx.request_inputs(
                        self._public_board_view(
                            ctx,
                            status=f"Challenge {self.players[actor].display_name}'s {challenge_card.title()} claim?"
                        ),
                        actors=opponents,
                        sources={"challenge", "pass"},
                        record=False,
                        description=self._challenge_wait_description(actor, challenge_card, action_type),
                        timeout_seconds=10.0,
                        timeout_consequence="skip"
                    )

                    challenger_seat, react_move = self._pick_challenge(react_moves)
                    if react_move is not None and challenger_seat is not None:
                        action_failed = await self._resolve_action_challenge(
                            ctx, actor, challenger_seat, challenge_card
                        )

            if action_failed and action_type == "assassinate":
                self.coins[actor] += 3  # Refund assassination fee on failed challenge

            # Block Phase
            if not action_failed and action_type == "foreign_aid":
                self.state_phase = "block_window"
                blockers = (set(self.alive) - {actor}) & self.alive

                block_sources = self._valid_block_sources(action_type) | {"pass"}
                block_moves = await ctx.request_inputs(
                    self._public_board_view(ctx, status="Waiting for blocks..."),
                    actors=blockers,
                    sources=block_sources,
                    record=False,
                    description=self._block_wait_description(action_type),
                    timeout_seconds=10.0,
                    timeout_consequence="skip"
                )

                blocker_seat, block_move = self._pick_block(block_moves, action_type)

                if block_move is not None and blocker_seat is not None:
                    claim = block_move.source.split("block_")[1]
                    self.current_blocker = blocker_seat
                    self.current_block_claim = claim
                    await ctx.record_event("block_declare", {
                        "blocker": blocker_seat,
                        "action": action_type,
                        "claim": claim,
                    })

                    self.state_phase = "block_challenge_window"
                    block_challengers = set(self.alive) - {blocker_seat}
                    challenge_moves = await ctx.request_inputs(
                        self._public_board_view(
                            ctx,
                            status=f"{self.players[blocker_seat].display_name} blocks with {claim.title()}. Challenge?"
                        ),
                        actors=block_challengers,
                        sources={"challenge", "pass"},
                        record=False,
                        description=self._block_challenge_wait_description(blocker_seat, claim),
                        timeout_seconds=10.0,
                        timeout_consequence="skip"
                    )
                    block_challenger, block_challenge_move = self._pick_challenge(challenge_moves)
                    if block_challenge_move is not None and block_challenger is not None:
                        blocked, _ = await self._resolve_block_challenge(
                            ctx, actor, blocker_seat, claim, block_challenger
                        )
                        action_blocked = blocked
                    else:
                        action_blocked = True

            # Resolve Action Effects
            if not action_failed and not action_blocked:
                if action_type == "income":
                    self.coins[actor] += 1
                    self.history.append(f"🪙 {self.players[actor].mention} took Income.")
                elif action_type == "foreign_aid":
                    self.coins[actor] += 2
                    self.history.append(f"💰 {self.players[actor].mention} took Foreign Aid.")
                elif action_type == "tax":
                    self.coins[actor] += 3
                    self.history.append(f"👑 {self.players[actor].mention} taxed the Treasury (+3 coins).")
                elif action_type == "coup":
                    self.history.append(f"💥 {self.players[actor].mention} staged a Coup on {self.players[target].mention}!")
                    await self._lose_influence(ctx, target, f"💥 Coup target {self.players[target].mention} must lose influence.")
                elif action_type == "assassinate":
                    self.history.append(f"🗡️ {self.players[actor].mention} assassinated {self.players[target].mention}!")
                    await self._lose_influence(ctx, target, f"🗡️ Assassination target {self.players[target].mention} must lose influence.")
                elif action_type == "steal":
                    stolen = min(2, self.coins[target])
                    self.coins[actor] += stolen
                    self.coins[target] -= stolen
                    self.history.append(f"⚓ {self.players[actor].mention} stole {stolen} coins from {self.players[target].mention}.")
                elif action_type == "exchange":
                    self.state_phase = "exchange"
                    drawn = [self.deck.pop(), self.deck.pop()]
                    prior_count = len(self.hands[actor])
                    self.exchange_options[actor] = list(self.hands[actor] + drawn)

                    public_view = self._public_board_view(
                        ctx,
                        status=f"Waiting for {self.players[actor].display_name} to exchange cards...",
                    )
                    keep_move = await ctx.request_input(
                        public_view,
                        actor=actor,
                        sources={"exchange_select"},
                        description="Exchange — choose cards to keep",
                        timeout_seconds=30.0,
                        timeout_consequence="skip"
                    )

                    if keep_move.source == "timeout":
                        # Exchange Timeout: auto-keep original cards
                        keep_cards = list(self.hands[actor])
                        self.history.append(f"⏱️ {self.players[actor].mention} timed out exchanging cards! Original cards kept.")
                    else:
                        raw_keep = keep_move.args.get("values", [])
                        if not raw_keep and keep_move.args.get("value") is not None:
                            raw_keep = [keep_move.args.get("value")]
                        if not raw_keep and keep_move.args.get("keep"):
                            raw_keep = keep_move.args.get("keep")

                        options = self.exchange_options[actor]
                        keep_cards = []
                        for item in raw_keep:
                            if isinstance(item, str) and item.isdigit():
                                idx = int(item)
                                if 0 <= idx < len(options):
                                    keep_cards.append(options[idx])
                            elif item in options:
                                keep_cards.append(item)

                        if len(keep_cards) != prior_count:
                            keep_cards = list(self.hands[actor])

                    returned = [card for card in self.exchange_options[actor] if card not in keep_cards]
                    if len(keep_cards) == prior_count:
                        self.hands[actor] = keep_cards
                        self.deck.extend(returned)
                        self.rng.shuffle(self.deck)
                    self.history.append(f"💼 {self.players[actor].mention} exchanged cards with the Deck.")
                    
                    await ctx.record_event("exchange_resolve", {
                        "player": actor,
                        "keep": self.hands[actor],
                    })

            self.current = self._next_player(actor)

        # Match over
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
            description=f"🏆 {winner_mention} won!",
            player_descriptions=player_descriptions,
        )

    async def _lose_influence(self, ctx: GameContext, seat: int, status_message: str) -> None:
        self.state_phase = "lose_influence"
        self.current_loser = seat
        cards = self.hands[seat]
        if not cards:
            return

        if len(cards) == 1:
            # Force reveal last card
            lost_card = cards.pop()
            self.revealed[seat].append(lost_card)
            self.history.append(f"💀 {self.players[seat].mention} revealed their last card: **{lost_card.title()}**.")
            self.alive.discard(seat)
            self.coins[seat] = 0
            await ctx.record_event("lose_influence_resolve", {
                "player": seat,
                "card": lost_card,
            })
            return

        # Choose a card to reveal
        public_view = self._public_board_view(ctx, status=status_message)
        move = await ctx.request_input(
            public_view,
            actor=seat,
            sources={"lose_influence_select"},
            description="Choose a card to reveal",
            timeout_seconds=30.0,
            timeout_consequence="skip"
        )

        if move.source == "timeout":
            # Timeout: auto-reveal the first card
            lost_card = cards[0]
            self.history.append(f"⏱️ {self.players[seat].mention} timed out choosing a card to reveal! Auto-revealed: **{lost_card.title()}**.")
        else:
            lost_card = move.args.get("value") or (move.args.get("values")[0] if move.args.get("values") else (move.args.get("card")))
            if lost_card is not None and str(lost_card).isdigit():
                idx = int(lost_card)
                if 0 <= idx < len(cards):
                    lost_card = cards[idx]
        
        if lost_card in cards:
            self.hands[seat].remove(lost_card)
            self.revealed[seat].append(lost_card)
        else:
            lost_card = self.hands[seat].pop()
            self.revealed[seat].append(lost_card)

        self.history.append(f"💀 {self.players[seat].mention} revealed: **{lost_card.title()}**.")
        
        if not self.hands[seat]:
            self.alive.discard(seat)
            self.coins[seat] = 0

        await ctx.record_event("lose_influence_resolve", {
            "player": seat,
            "card": lost_card,
        })

    def get_lose_influence_view(self, seat: int, ctx: GameContext) -> LayoutView:
        cards = self.hands[seat]
        choices = [
            SelectChoice(
                label=f"{role.title()} #{idx + 1}",
                value=str(idx),
                emoji=f"coup_{role}",
            )
            for idx, role in enumerate(cards)
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
        keep_count = len(self.hands[seat])
        choices = [
            SelectChoice(
                label=f"{role.title()} #{idx + 1}",
                value=str(idx),
                emoji=f"coup_{role}",
            )
            for idx, role in enumerate(self.exchange_options[seat])
        ]
        
        view = LayoutView()
        container = Container()
        message_lead(
            container,
            f"Select {keep_count} card(s) to keep",
            emoji=ctx.emoji,
        )
        row = ActionRow()
        row.add_select(
            Select(
                source="exchange_select",
                placeholder="Select cards to keep",
                choices=choices,
                min_values=keep_count,
                max_values=keep_count,
            )
        )
        container.add_action_row(row)
        view.add_container(container)
        return view

    def _public_board_view(self, ctx: GameContext, status: str | None = None) -> LayoutView:
        view = LayoutView()
        container = Container()

        # Build clean header status
        current_status = status or f"👉 {self.players[self.current].display_name}'s Turn"
        message_lead(container, current_status, emoji=ctx.emoji)

        # 🏰 Premium Board Layout Text
        table_text = (
            "🏰 **C O U P**\n"
            "▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬\n"
        )
        
        action_desc = ""
        if self.current_action is not None and self.current_actor is not None:
            actor_name = self.players[self.current_actor].mention
            target_name = self.players[self.current_target].mention if self.current_target is not None else ""
            
            if self.current_action == "tax":
                action_desc = f"{self._role_emoji(ctx, 'duke')} {actor_name} is claiming **Duke** to Tax (+3 coins)"
            elif self.current_action == "assassinate":
                action_desc = f"{self._role_emoji(ctx, 'assassin')} {actor_name} is claiming **Assassin** to assassinate {target_name}"
            elif self.current_action == "steal":
                action_desc = f"{self._role_emoji(ctx, 'captain')} {actor_name} is claiming **Captain** to steal 2 coins from {target_name}"
            elif self.current_action == "exchange":
                action_desc = f"{self._role_emoji(ctx, 'ambassador')} {actor_name} is claiming **Ambassador** to Exchange cards"
            elif self.current_action == "foreign_aid":
                action_desc = f"🪙 {actor_name} is taking **Foreign Aid** (+2 coins)"
            elif self.current_action == "coup":
                action_desc = f"💥 {actor_name} is staging a **Coup** on {target_name}"
            elif self.current_action == "income":
                action_desc = f"💵 {actor_name} is taking **Income** (+1 coin)"
                
        block_desc = ""
        if self.current_blocker is not None and self.current_block_claim is not None:
            blocker_name = self.players[self.current_blocker].mention
            block_desc = f"{self._role_emoji(ctx, self.current_block_claim)} {blocker_name} is claiming **{self.current_block_claim.title()}** to block"

        if action_desc:
            table_text += "⚡ **Current Action:**\n"
            table_text += f"> {action_desc}\n"
            if block_desc:
                table_text += f"> {block_desc}\n"
            table_text += "\n"

        table_text += "👤 **Roster & Influence:**\n"
        for p in self.players:
            is_active = (p.seat == self.current and self.state_phase == "turn")
            marker = "👉" if is_active else "•"
            hand_str = self._format_hand(ctx, p.seat)
            coins_str = f"💰 **{self.coins[p.seat]}**" if p.seat in self.alive else "💀 *Exiled*"
            bot_tag = " [BOT]" if p.is_bot else ""
            table_text += f"{marker} {p.mention}{bot_tag}: {hand_str} ({coins_str})\n"

        treasury_coins = max(0, 50 - sum(self.coins.values()))
        deck_count = len(self.deck)
        table_text += f"\n🏦 **Treasury:** 💰 {treasury_coins} | 🎴 **Court Deck:** {deck_count} cards\n"

        if self.history:
            table_text += "\n📋 **Recent Events:**\n" + "\n".join(f"• {item}" for item in self.history[-5:])

        table_text += "\n▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬"
        container.add_text(TextDisplay(table_text))

        # Build Interactive Controls (Only if not a Replay)
        if not ctx.is_replay:
            actor = self.current

            if self.state_phase == "turn":
                forced_coup = self.coins[actor] >= 10

                # Action options
                action_choices = []
                if forced_coup:
                    action_choices.append(SelectChoice(
                        label="Coup (Forced) -7 coins",
                        value="coup",
                        description="Must launch a Coup when starting with 10+ coins",
                        emoji="💥",
                        default=True
                    ))
                else:
                    action_choices.append(SelectChoice(
                        label="Income (+1 coin)",
                        value="income",
                        description="Take 1 coin from the Treasury",
                        emoji="💵",
                        default=(self.selected_action == "income")
                    ))
                    action_choices.append(SelectChoice(
                        label="Foreign Aid (+2 coins)",
                        value="foreign_aid",
                        description="Take 2 coins (Can be blocked by Duke)",
                        emoji="🪙",
                        default=(self.selected_action == "foreign_aid")
                    ))
                    action_choices.append(SelectChoice(
                        label="Tax (Duke) (+3 coins)",
                        value="tax",
                        description="Take 3 coins claiming Duke",
                        emoji=self._role_emoji(ctx, "duke"),
                        default=(self.selected_action == "tax")
                    ))
                    if self.coins[actor] >= 7:
                        action_choices.append(SelectChoice(
                            label="Coup (-7 coins)",
                            value="coup",
                            description="Force another player to lose influence",
                            emoji="💥",
                            default=(self.selected_action == "coup")
                        ))
                    if self.coins[actor] >= 3:
                        action_choices.append(SelectChoice(
                            label="Assassinate (-3 coins)",
                            value="assassinate",
                            description="Assassinate another player (Can be blocked by Contessa)",
                            emoji=self._role_emoji(ctx, "assassin"),
                            default=(self.selected_action == "assassinate")
                        ))
                    action_choices.append(SelectChoice(
                        label="Steal (Captain)",
                        value="steal",
                        description="Take 2 coins from another player",
                        emoji=self._role_emoji(ctx, "captain"),
                        default=(self.selected_action == "steal")
                    ))
                    action_choices.append(SelectChoice(
                        label="Exchange (Ambassador)",
                        value="exchange",
                        description="Exchange cards with the Court Deck",
                        emoji=self._role_emoji(ctx, "ambassador"),
                        default=(self.selected_action == "exchange")
                    ))

                # Target options
                target_choices = [
                    SelectChoice(
                        label="No Target Required",
                        value="none",
                        description="For Income, Foreign Aid, Tax, or Exchange",
                        emoji="🚫",
                        default=(self.selected_target == "none")
                    )
                ]
                for p in self.players:
                    if p.seat in self.alive and p.seat != actor:
                        target_choices.append(SelectChoice(
                            label=f"Target: {p.display_name} (💰 {self.coins[p.seat]} coins)",
                            value=str(p.seat),
                            emoji="👤",
                            default=(self.selected_target == str(p.seat))
                        ))

                # Dropdowns row
                row_actions = ActionRow()
                row_actions.add_select(
                    Select(
                        source="action_select",
                        placeholder="Select your action...",
                        choices=action_choices,
                    )
                )
                container.add_action_row(row_actions)

                row_targets = ActionRow()
                row_targets.add_select(
                    Select(
                        source="target_select",
                        placeholder="Select a target player (if needed)...",
                        choices=target_choices,
                    )
                )
                container.add_action_row(row_targets)

                # Submit Button Validation
                is_valid = False
                submit_label = "Submit Action"
                if self.selected_action is not None:
                    action_title = self.selected_action.title()
                    if self.selected_action in ("income", "foreign_aid", "tax", "exchange"):
                        is_valid = True
                        submit_label = f"Confirm {action_title}"
                    elif self.selected_action in ("coup", "assassinate", "steal"):
                        if self.selected_target is not None and self.selected_target != "none":
                            target_player = self.players[int(self.selected_target)]
                            is_valid = True
                            submit_label = f"Confirm {action_title} ➔ {target_player.display_name}"
                        else:
                            submit_label = "Select a Target..."
                else:
                    submit_label = "Select an Action..."

                row_submit = ActionRow()
                row_submit.add_button(
                    Button(
                        source="submit_action",
                        label=submit_label,
                        style=ButtonStyle.SUCCESS if is_valid else ButtonStyle.SECONDARY,
                        disabled=not is_valid
                    )
                )
                container.add_action_row(row_submit)

            elif self.state_phase in ("challenge_window", "block_challenge_window"):
                row = ActionRow()
                row.add_button(Button(source="challenge", label="Challenge Claim", style=ButtonStyle.DANGER))
                if self.state_phase == "challenge_window":
                    if self.current_action == "assassinate":
                        row.add_button(
                            Button(
                                source="block_contessa",
                                label="Block: Contessa",
                                style=ButtonStyle.PRIMARY,
                            )
                        )
                    elif self.current_action == "steal":
                        row.add_button(
                            Button(
                                source="block_captain",
                                label="Block: Captain",
                                style=ButtonStyle.PRIMARY,
                            )
                        )
                        row.add_button(
                            Button(
                                source="block_ambassador",
                                label="Block: Ambassador",
                                style=ButtonStyle.PRIMARY,
                            )
                        )
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
                        label=f"Choose card to reveal ({self.players[self.current_loser].display_name})",
                        style=ButtonStyle.DANGER,
                    )
                )
                container.add_action_row(row)

            elif self.state_phase == "exchange":
                row = ActionRow()
                row.add_button(
                    Button(
                        source="exchange_open",
                        label=f"Select cards to keep ({self.players[self.current_actor].display_name})",
                        style=ButtonStyle.PRIMARY,
                    )
                )
                container.add_action_row(row)

            # Ephemeral Card Peek Button
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

        history_text = "🏆 **Final Match Logs:**\n" + "\n".join(f"• {item}" for item in self.history)
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
        if len(self.players) == 2:
            self.coins[0] = 1
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
            self.coins[seat] = 0

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

            if move.source == "timeout":
                _resolve_pending_action()
                actor = move.actor_seat
                if actor is not None:
                    if self.coins[actor] > 0:
                        self.coins[actor] -= 1
                    self.current = self._next_player(actor)
                view = self._public_board_view(ctx, status="Turn timed out")
                frames.append(
                    ReplayFrame(
                        index=len(frames),
                        turn_label="Timeout",
                        actor_seat=actor,
                        view=clone_and_disable(view),
                        takeover_info=takeover_info,
                        timestamp=move.created_at,
                    )
                )
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
        if move.source == "action":
            action_type = move.args.get("type", "income")
            target_val = move.args.get("target")
            return Move(
                actor_seat=seat,
                source="submit_action",
                args={"action": action_type, "target": target_val}
            )
        elif move.source == "block":
            claim = move.args.get("claim", "captain")
            source = f"block_{claim}"
            return Move(actor_seat=seat, source=source, args=move.args)
        elif move.source == "lose_card":
            cards = self.hands.get(seat, [])
            card = move.args.get("card", cards[0] if cards else "0")
            idx = cards.index(card) if card in cards else 0
            return Move(actor_seat=seat, source="lose_influence_select", args={"value": str(idx)})
        elif move.source == "exchange_keep":
            keep = move.args.get("keep", [])
            indices = []
            options = self.exchange_options.get(seat, [])
            for card in keep:
                if card in options:
                    indices.append(str(options.index(card)))
            return Move(actor_seat=seat, source="exchange_select", args={"values": indices})
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
