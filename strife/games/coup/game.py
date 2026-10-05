from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from typing import Any, ClassVar

from strife.engine import BotRequest, Interrupt, TimeoutConsequence
from strife.engine.context import GameContext
from strife.engine.game import Game
from strife.engine.players import GameOutcome, Move, Player, Result
from strife.engine.workers import run_cpu
from strife.games.coup.bot import choose_move
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
from strife.presentation.style import (
    add_body,
    add_divider,
    add_meta,
    add_section,
    history_block,
)


class Coup(Game):
    ROLES: ClassVar[list[str]] = ["duke", "assassin", "captain", "ambassador", "contessa"]

    def __init__(self, players: list[Player], settings: Mapping[str, Any], rng):
        super().__init__(players, settings, rng)
        # Create deck: 3 of each role
        self.deck = list(self.ROLES * 3)
        self.rng.shuffle(self.deck)

        # Hands: list of active cards for each player
        self.hands: dict[int, list[str]] = {
            p.seat: [self.deck.pop(), self.deck.pop()] for p in players
        }
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
        self._notice: str | None = None

    def active_seats(self) -> set[int]:
        return set(self.alive)

    def remove_player(self, seat: int) -> None:
        if seat not in self.alive and not self.hands.get(seat):
            return
        for card in list(self.hands.get(seat, [])):
            self.revealed[seat].append(card)
        self.hands[seat] = []
        self.alive.discard(seat)
        self.coins[seat] = 0

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

    def _challenge_wait_description(
        self, actor: int, challenge_card: str, action_type: str
    ) -> str:
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

    def _reaction_hint(self) -> str:
        if self.state_phase == "block_challenge_window":
            return "Anyone except the blocker can Challenge or Pass."
        if self.state_phase == "block_window":
            if self.current_action in ("assassinate", "steal"):
                return "The target can Block or Pass."
            return "Anyone except the actor can Block or Pass."
        return "Anyone except the actor can Challenge or Pass."

    def _pick_challenge(self, moves: dict[int, Move]) -> tuple[int | None, Move | None]:
        human = None
        bot = None
        # Lowest seat wins a tie, independent of the order the answers arrived in.
        for seat, move in sorted(moves.items()):
            if move.interrupt is not None:
                continue
            if move.source != "challenge":
                continue
            if self.players[seat].is_bot:
                if bot is None:
                    bot = (seat, move)
            elif human is None:
                human = (seat, move)
        return human or bot or (None, None)

    def _pick_block(
        self, moves: dict[int, Move], action_type: str
    ) -> tuple[int | None, Move | None]:
        valid = self._valid_block_sources(action_type)
        human = None
        bot = None
        for seat, move in sorted(moves.items()):
            if move.interrupt is not None:
                continue
            if move.source not in valid:
                continue
            if self.players[seat].is_bot:
                if bot is None:
                    bot = (seat, move)
            elif human is None:
                human = (seat, move)
        return human or bot or (None, None)

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
        await ctx.record_event(
            "challenge_declare",
            {
                "challenger": challenger_seat,
                "challenged": actor,
                "card": challenge_card,
            },
        )

        if challenge_card in self.hands[actor]:
            self.history.append(
                f"{ctx.emoji.get('success', base=True)} {self.players[actor].mention} proved **{challenge_card.title()}**."
            )
            self.hands[actor].remove(challenge_card)
            self.deck.append(challenge_card)
            self.rng.shuffle(self.deck)
            replacement = self.deck.pop()
            self.hands[actor].append(replacement)
            await ctx.record_event(
                "challenge_resolve",
                {
                    "challenged": actor,
                    "proved": True,
                    "shown": challenge_card,
                    "replacement": replacement,
                },
            )
            await self._lose_influence(
                ctx,
                challenger_seat,
                f"{self.players[challenger_seat].mention} failed challenge and must lose influence.",
            )
            return False

        self.history.append(
            f"{ctx.emoji.get('error', base=True)} {self.players[actor].mention} lied about **{challenge_card.title()}**."
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
        await ctx.record_event(
            "block_challenge",
            {
                "challenger": challenger_seat,
                "blocker": blocker_seat,
                "claim": claim,
            },
        )

        if claim in self.hands[blocker_seat]:
            self.history.append(
                f"{ctx.emoji.get('success', base=True)} {self.players[blocker_seat].mention} proved **{claim.title()}**."
            )
            self.hands[blocker_seat].remove(claim)
            self.deck.append(claim)
            self.rng.shuffle(self.deck)
            replacement = self.deck.pop()
            self.hands[blocker_seat].append(replacement)
            await ctx.record_event(
                "challenge_resolve",
                {
                    "challenged": blocker_seat,
                    "proved": True,
                    "shown": claim,
                    "replacement": replacement,
                },
            )
            await self._lose_influence(
                ctx,
                challenger_seat,
                f"{self.players[challenger_seat].mention} failed block challenge and must lose influence.",
            )
            return True, True

        self.history.append(
            f"{ctx.emoji.get('error', base=True)} {self.players[blocker_seat].mention} lied about **{claim.title()}**."
        )
        await self._lose_influence(
            ctx,
            blocker_seat,
            f"{self.players[blocker_seat].mention} failed block challenge.",
        )
        return False, False

    def _timeout_coup_target(self, actor: int) -> int | None:
        others = [seat for seat in self.alive if seat != actor]
        if not others:
            return None
        return self.rng.choice(others)

    async def _run_challenge_window(
        self,
        ctx: GameContext,
        actor: int,
        challenge_card: str,
        action_type: str,
    ) -> bool:
        """Anyone except the actor may challenge. Returns True if the action failed."""
        self.state_phase = "challenge_window"
        opponents = set(self.alive) - {actor}
        if not opponents:
            return False
        react_moves = await ctx.request_inputs(
            self._public_board_view(
                ctx,
                status=f"Challenge {self.players[actor].display_name}'s {challenge_card.title()} claim?",
            ),
            actors=opponents,
            sources={"challenge", "pass"},
            description=self._challenge_wait_description(
                actor, challenge_card, action_type
            ),
            timeout_seconds=10.0,
            timeout_consequence=TimeoutConsequence.SKIP,
        )
        challenger_seat, react_move = self._pick_challenge(react_moves)
        if react_move is not None and challenger_seat is not None:
            return await self._resolve_action_challenge(
                ctx, actor, challenger_seat, challenge_card
            )
        return False

    async def _run_block_window(
        self,
        ctx: GameContext,
        actor: int,
        action_type: str,
        blockers: set[int],
    ) -> bool:
        """Optional block, then an optional challenge of that block.

        Returns True if the action is blocked.
        """
        blockers = {seat for seat in blockers if seat in self.alive}
        if not blockers:
            return False
        self.state_phase = "block_window"
        block_sources = self._valid_block_sources(action_type) | {"pass"}
        if len(blockers) == 1:
            blocker_name = self.players[next(iter(blockers))].display_name
            status = f"Waiting for {blocker_name} to block..."
        else:
            status = "Waiting for blocks..."
        block_moves = await ctx.request_inputs(
            self._public_board_view(ctx, status=status),
            actors=blockers,
            sources=block_sources,
            description=self._block_wait_description(action_type),
            timeout_seconds=10.0,
            timeout_consequence=TimeoutConsequence.SKIP,
        )
        blocker_seat, block_move = self._pick_block(block_moves, action_type)
        if block_move is None or blocker_seat is None:
            return False

        claim = block_move.source.split("block_")[1]
        self.current_blocker = blocker_seat
        self.current_block_claim = claim
        await ctx.record_event(
            "block_declare",
            {
                "blocker": blocker_seat,
                "action": action_type,
                "claim": claim,
            },
        )

        self.state_phase = "block_challenge_window"
        block_challengers = set(self.alive) - {blocker_seat}
        if not block_challengers:
            return True
        challenge_moves = await ctx.request_inputs(
            self._public_board_view(
                ctx,
                status=f"{self.players[blocker_seat].display_name} blocks with {claim.title()}. Challenge?",
            ),
            actors=block_challengers,
            sources={"challenge", "pass"},
            description=self._block_challenge_wait_description(blocker_seat, claim),
            timeout_seconds=10.0,
            timeout_consequence=TimeoutConsequence.SKIP,
        )
        block_challenger, block_challenge_move = self._pick_challenge(challenge_moves)
        if block_challenge_move is not None and block_challenger is not None:
            blocked, _ = await self._resolve_block_challenge(
                ctx, actor, blocker_seat, claim, block_challenger
            )
            return blocked
        return True

    def _role_emoji(self, ctx: GameContext, role: str) -> str:
        fallback = {
            "duke": "👑",
            "assassin": "🗡️",
            "captain": "⚓",
            "ambassador": "💼",
            "contessa": "🛡️",
        }.get(role, "🎴")
        return ctx.emoji.get(role) or fallback

    def _format_hand(self, ctx: GameContext, seat: int, private: bool = False) -> str:
        if private:
            return " ".join(
                f"{self._role_emoji(ctx, r)} {r.title()}" for r in self.hands[seat]
            )
        cards = ["`[Card]`" for _ in self.hands[seat]]
        rev = [
            f"{self._role_emoji(ctx, r)} ~~{r.title()}~~" for r in self.revealed[seat]
        ]
        return "  ".join(cards + rev)

    def _format_hand_replay(self, ctx: GameContext, seat: int) -> str:
        cards = [f"{self._role_emoji(ctx, r)} {r.title()}" for r in self.hands[seat]]
        rev = [
            f"{self._role_emoji(ctx, r)} ~~{r.title()}~~" for r in self.revealed[seat]
        ]
        return "  ".join(cards + rev)

    async def play(self, ctx: GameContext) -> GameOutcome:
        await ctx.record_event(
            "deal",
            {
                "hands": {seat: list(cards) for seat, cards in self.hands.items()},
                "deck": list(self.deck),
                "current": self.current,
                "coins": dict(self.coins),
            },
        )
        last_actor = None
        while len(self.alive) > 1:
            actor = self.current
            if actor not in self.alive:
                self.current = self._next_player(actor)
                continue
            if actor != last_actor:
                self.state_phase = "turn"
                self.current_actor = actor
                self.current_action = None
                self.current_target = None
                self.current_blocker = None
                self.current_block_claim = None
                self.current_loser = None
                last_actor = actor

            action_type = None
            target = None

            async with ctx.turn_deadline(30.0):
                while True:
                    forced_coup = self.coins[actor] >= 10

                    view = self._public_board_view(ctx)
                    move = await ctx.request_input(
                        view,
                        actor=actor,
                        sources={"submit_action"},
                        description=self._turn_wait_description(
                            forced_coup=forced_coup
                        ),
                        timeout_consequence=TimeoutConsequence.SKIP,
                    )

                    if move.interrupt is Interrupt.FORFEIT:
                        action_type = "forfeited"
                        break

                    if move.interrupt is Interrupt.TIMEOUT:
                        if forced_coup:
                            coup_target = self._timeout_coup_target(actor)
                            if coup_target is not None:
                                self.history.append(
                                    f"{ctx.emoji.get('timer', base=True)} {self.players[actor].mention} "
                                    "timed out and was forced to coup."
                                )
                                action_type = "coup"
                                target = coup_target
                                break
                        penalty = False
                        if self.coins[actor] > 0:
                            self.coins[actor] -= 1
                            penalty = True
                        msg = f"{ctx.emoji.get('timer', base=True)} {self.players[actor].mention} timed out."
                        if penalty:
                            msg += " Action skipped and 1 coin lost."
                        else:
                            msg += " Action skipped."
                        self.history.append(msg)
                        action_type = "skipped"
                        await ctx.record_event(
                            "turn_skip",
                            {
                                "player": actor,
                                "penalty": penalty,
                                "coins": dict(self.coins),
                            },
                        )
                        break

                    action_type = move.args.get("action")
                    if forced_coup:
                        action_type = "coup"
                    target_val = move.args.get("target")
                    try:
                        target = (
                            int(target_val)
                            if target_val not in (None, "none", "")
                            else None
                        )
                    except TypeError, ValueError:
                        target = None

                    if action_type is None:
                        self._notice = "Select an action."
                        continue
                    if action_type in ("coup", "assassinate", "steal") and (
                        target is None
                        or target not in self.alive
                        or target == actor
                    ):
                        self._notice = "Choose a target."
                        continue
                    if action_type == "coup" and self.coins[actor] < 7:
                        self._notice = "Coup costs 7 coins."
                        continue
                    if action_type == "assassinate" and self.coins[actor] < 3:
                        self._notice = "Assassinate costs 3 coins."
                        continue
                    break

            if action_type == "forfeited":
                if len(self.alive) <= 1:
                    break
                self.current = self._next_player(actor)
                continue

            if action_type == "skipped":
                self.current = self._next_player(actor)
                continue

            if actor not in self.alive:
                continue

            self.current_action = action_type
            self.current_target = target

            steal_snapshot = None
            if action_type == "steal" and target is not None:
                steal_snapshot = self.coins[target]

            await ctx.record_event(
                "action_declare",
                {
                    "player": actor,
                    "type": action_type,
                    "target": target,
                    "steal_snapshot": steal_snapshot,
                },
            )

            # Spend coins
            if action_type == "coup":
                self.coins[actor] -= 7
            elif action_type == "assassinate":
                self.coins[actor] -= 3

            # Challenge, then (if the action is still live) a separate block window.
            challenge_card = {
                "tax": "duke",
                "assassinate": "assassin",
                "steal": "captain",
                "exchange": "ambassador",
            }.get(action_type)

            action_blocked = False
            action_failed = False

            if challenge_card is not None:
                action_failed = await self._run_challenge_window(
                    ctx, actor, challenge_card, action_type
                )

            if action_failed and action_type == "assassinate" and actor in self.alive:
                self.coins[actor] += 3
                await ctx.record_event("assassinate_refund", {"player": actor})

            if (
                not action_failed
                and actor in self.alive
                and action_type in ("foreign_aid", "assassinate", "steal")
            ):
                if action_type == "foreign_aid":
                    blockers = set(self.alive) - {actor}
                elif target is not None and target in self.alive:
                    blockers = {target}
                else:
                    blockers = set()
                action_blocked = await self._run_block_window(
                    ctx, actor, action_type, blockers
                )

            if actor not in self.alive:
                await self._record_action_resolve(ctx)
                if len(self.alive) <= 1:
                    break
                self.current = self._next_player(actor)
                continue

            # Resolve Action Effects
            if not action_failed and not action_blocked:
                if action_type == "income":
                    self.coins[actor] += 1
                    self.history.append(
                        f"{ctx.emoji.get('success', base=True)} {self.players[actor].mention} took income."
                    )
                elif action_type == "foreign_aid":
                    self.coins[actor] += 2
                    self.history.append(
                        f"{ctx.emoji.get('public', base=True)} {self.players[actor].mention} took foreign aid."
                    )
                elif action_type == "tax":
                    self.coins[actor] += 3
                    self.history.append(
                        f"{self._role_emoji(ctx, 'duke')} {self.players[actor].mention} taxed the treasury (+3 coins)."
                    )
                elif (
                    action_type == "coup"
                    and target is not None
                    and target in self.alive
                ):
                    self.history.append(
                        f"{ctx.emoji.get('explosion', base=True)} {self.players[actor].mention} staged a coup on {self.players[target].mention}."
                    )
                    await self._lose_influence(
                        ctx,
                        target,
                        f"{self.players[target].mention} must lose influence.",
                    )
                elif (
                    action_type == "assassinate"
                    and target is not None
                    and target in self.alive
                ):
                    self.history.append(
                        f"{self._role_emoji(ctx, 'assassin')} {self.players[actor].mention} assassinated {self.players[target].mention}."
                    )
                    await self._lose_influence(
                        ctx,
                        target,
                        f"{self.players[target].mention} must lose influence.",
                    )
                elif action_type == "steal" and target is not None:
                    if target in self.alive:
                        stolen = min(2, self.coins[target])
                        self.coins[target] -= stolen
                    else:
                        stolen = min(2, steal_snapshot or 0)
                    self.coins[actor] += stolen
                    target_ref = self.players[target].mention
                    self.history.append(
                        f"{self._role_emoji(ctx, 'captain')} {self.players[actor].mention} stole {stolen} coins from {target_ref}."
                    )
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
                        timeout_consequence=TimeoutConsequence.SKIP,
                    )

                    if keep_move.interrupt is Interrupt.FORFEIT:
                        # remove_player already revealed their hand; only the drawn cards go back.
                        self.deck.extend(drawn)
                        self.rng.shuffle(self.deck)
                        self.exchange_options.pop(actor, None)
                    elif keep_move.interrupt is Interrupt.TIMEOUT:
                        # Exchange Timeout: auto-keep original cards
                        keep_cards = list(self.hands[actor])
                        leftover = Counter(self.exchange_options[actor]) - Counter(
                            keep_cards
                        )
                        self.history.append(
                            f"{ctx.emoji.get('timer', base=True)} {self.players[actor].mention} timed out exchanging cards. Original cards kept."
                        )
                    else:
                        raw_keep = keep_move.args.get("keep") or []
                        if isinstance(raw_keep, str):
                            raw_keep = [raw_keep]

                        options = self.exchange_options[actor]
                        keep_cards = []
                        used_idx: set[int] = set()
                        leftover = Counter(options)
                        for item in raw_keep:
                            if isinstance(item, str) and item.isdigit():
                                idx = int(item)
                                if 0 <= idx < len(options) and idx not in used_idx:
                                    keep_cards.append(options[idx])
                                    used_idx.add(idx)
                                    leftover[options[idx]] -= 1
                            elif leftover[item] > 0:
                                keep_cards.append(item)
                                leftover[item] -= 1

                        if len(keep_cards) != prior_count:
                            keep_cards = list(self.hands[actor])
                            leftover = Counter(options) - Counter(keep_cards)

                    if keep_move.interrupt is not Interrupt.FORFEIT:
                        returned = list(leftover.elements())
                        if len(keep_cards) == prior_count:
                            self.hands[actor] = keep_cards
                            self.deck.extend(returned)
                            self.rng.shuffle(self.deck)
                        self.history.append(
                            f"{self._role_emoji(ctx, 'ambassador')} {self.players[actor].mention} exchanged cards with the deck."
                        )

                        await ctx.record_event(
                            "exchange_resolve",
                            {
                                "player": actor,
                                "keep": self.hands[actor],
                            },
                        )

            await self._record_action_resolve(ctx)

            if len(self.alive) <= 1:
                break
            self.current = self._next_player(actor)

        # Match over
        if not self.alive:
            return GameOutcome(
                results={p.seat: Result.LOSS for p in self.players},
                summary={"history": list(self.history)},
                description="Match ended by forfeit.",
                player_descriptions={
                    p.seat: "Removed from play." for p in self.players
                },
            )
        winner = next(iter(self.alive))
        winner_mention = self.players[winner].mention
        results = {
            p.seat: Result.WIN if p.seat == winner else Result.LOSS
            for p in self.players
        }
        player_descriptions = {
            p.seat: "Won the coup!" if p.seat == winner else "Influence eliminated."
            for p in self.players
        }

        return GameOutcome(
            results=results,
            summary={"winner": winner, "history": list(self.history)},
            description=f"{winner_mention} won.",
            player_descriptions=player_descriptions,
        )

    async def _record_action_resolve(self, ctx: GameContext) -> None:
        await ctx.record_event(
            "action_resolve",
            {
                "coins": dict(self.coins),
                "hands": {seat: list(cards) for seat, cards in self.hands.items()},
                "revealed": {
                    seat: list(cards) for seat, cards in self.revealed.items()
                },
                "alive": sorted(self.alive),
                "deck": list(self.deck),
                "history": list(self.history),
            },
        )

    async def _lose_influence(
        self, ctx: GameContext, seat: int, status_message: str
    ) -> None:
        if seat not in self.alive and not self.hands.get(seat):
            return
        self.state_phase = "lose_influence"
        self.current_loser = seat
        cards = self.hands[seat]
        if not cards:
            return

        if len(cards) == 1:
            # Force reveal last card
            lost_card = cards.pop()
            self.revealed[seat].append(lost_card)
            self.history.append(
                f"{ctx.emoji.get('error', base=True)} {self.players[seat].mention} revealed their last card: **{lost_card.title()}**."
            )
            self.alive.discard(seat)
            self.coins[seat] = 0
            await ctx.record_event(
                "lose_influence_resolve",
                {
                    "player": seat,
                    "card": lost_card,
                },
            )
            return

        # Choose a card to reveal
        public_view = self._public_board_view(ctx, status=status_message)
        move = await ctx.request_input(
            public_view,
            actor=seat,
            sources={"lose_influence_select"},
            description="Choose a card to reveal",
            timeout_seconds=30.0,
            timeout_consequence=TimeoutConsequence.SKIP,
        )

        if move.interrupt is Interrupt.FORFEIT:
            return

        if move.interrupt is Interrupt.TIMEOUT:
            # Timeout: auto-reveal the first card
            lost_card = cards[0]
            self.history.append(
                f"{ctx.emoji.get('timer', base=True)} {self.players[seat].mention} timed out choosing a card. Auto-revealed **{lost_card.title()}**."
            )
        else:
            lost_card = move.args.get("value") or (
                move.args.get("values")[0]
                if move.args.get("values")
                else (move.args.get("card"))
            )
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

        self.history.append(
            f"{ctx.emoji.get('error', base=True)} {self.players[seat].mention} revealed **{lost_card.title()}**."
        )

        if not self.hands[seat]:
            self.alive.discard(seat)
            self.coins[seat] = 0

        await ctx.record_event(
            "lose_influence_resolve",
            {
                "player": seat,
                "card": lost_card,
            },
        )

    def get_lose_influence_view(self, seat: int, ctx: GameContext) -> LayoutView:
        cards = self.hands[seat]
        choices = [
            SelectChoice(
                label=f"{role.title()} #{idx + 1}",
                value=str(idx),
                emoji=role,
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
                emoji=role,
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
                source="keep",
                placeholder="Select cards to keep",
                choices=choices,
                min_values=keep_count,
                max_values=keep_count,
                form=True,
            )
        )
        container.add_action_row(row)
        submit_row = ActionRow()
        submit_row.add_button(
            Button(
                source="exchange_select", label="Keep Cards", style=ButtonStyle.PRIMARY
            )
        )
        container.add_action_row(submit_row)
        view.add_container(container)
        return view

    def _build_base_board(
        self, ctx: GameContext, status: str | None = None, is_replay: bool = False
    ) -> tuple[LayoutView, Container]:
        view = LayoutView()
        container = Container()

        current_status = status or f"{self.players[self.current].mention} to act"
        if self._notice and not is_replay:
            current_status = self._notice
            self._notice = None
        message_lead(container, current_status, emoji=ctx.emoji)

        action_desc = ""
        if self.current_action is not None and self.current_actor is not None:
            actor_name = self.players[self.current_actor].mention
            target_name = (
                self.players[self.current_target].mention
                if self.current_target is not None
                else ""
            )

            if self.current_action == "tax":
                action_desc = f"{self._role_emoji(ctx, 'duke')} {actor_name} claims **Duke** to tax (+3 coins)"
            elif self.current_action == "assassinate":
                action_desc = f"{self._role_emoji(ctx, 'assassin')} {actor_name} claims **Assassin** to assassinate {target_name}"
            elif self.current_action == "steal":
                action_desc = f"{self._role_emoji(ctx, 'captain')} {actor_name} claims **Captain** to steal from {target_name}"
            elif self.current_action == "exchange":
                action_desc = f"{self._role_emoji(ctx, 'ambassador')} {actor_name} claims **Ambassador** to exchange cards"
            elif self.current_action == "foreign_aid":
                action_desc = f"{actor_name} takes **Foreign Aid** (+2 coins)"
            elif self.current_action == "coup":
                action_desc = f"{actor_name} stages a **Coup** on {target_name}"
            elif self.current_action == "income":
                action_desc = f"{actor_name} takes **Income** (+1 coin)"

        block_desc = ""
        if self.current_blocker is not None and self.current_block_claim is not None:
            blocker_name = self.players[self.current_blocker].mention
            block_desc = f"{self._role_emoji(ctx, self.current_block_claim)} {blocker_name} claims **{self.current_block_claim.title()}** to block"

        if action_desc:
            action_body = action_desc
            if block_desc:
                action_body = f"{action_body}\n{block_desc}"
            add_section(container, "Current Action", action_body)
            add_divider(container)

        roster_lines = []
        for p in self.players:
            is_active = p.seat == self.current and self.state_phase == "turn"
            marker = (
                ctx.emoji.get("pointing", base=True)
                if is_active
                else ctx.emoji.get("bullet", base=True)
            )
            name = member_line(
                ctx.emoji,
                user_id=p.user_id,
                display_name=p.display_name,
                is_bot=p.is_bot,
                bot_difficulty=p.bot_difficulty,
            )
            hand_str = (
                self._format_hand_replay(ctx, p.seat)
                if is_replay
                else self._format_hand(ctx, p.seat)
            )
            coins_str = (
                f"{self.coins[p.seat]} coins" if p.seat in self.alive else "Exiled"
            )
            roster_lines.append(f"-# {marker} {name} · {hand_str} · **{coins_str}**")
        add_section(container, "Players", "\n".join(roster_lines))

        treasury_coins = max(0, 50 - sum(self.coins.values()))
        add_meta(
            container,
            f"**Treasury:** {treasury_coins} coins · **Court deck:** {len(self.deck)} cards",
        )
        add_divider(container)

        block = history_block(self.history, ctx.emoji)
        if block:
            add_body(container, block)
            add_divider(container)

        return view, container

    def _public_board_view_replay(
        self, ctx: GameContext, status: str | None = None
    ) -> LayoutView:
        view, container = self._build_base_board(ctx, status=status, is_replay=True)
        view.add_container(container)
        return view

    def _public_board_view(
        self, ctx: GameContext, status: str | None = None
    ) -> LayoutView:
        view, container = self._build_base_board(ctx, status=status, is_replay=False)
        actor = self.current

        if self.state_phase == "turn":
            forced_coup = self.coins[actor] >= 10

            # Action options
            action_choices = []
            if forced_coup:
                action_choices.append(
                    SelectChoice(
                        label="Coup (Forced) -7 coins",
                        value="coup",
                        description="Must launch a Coup when starting with 10+ coins",
                        emoji="explosion",
                        default=True,
                    )
                )
            else:
                action_choices.append(
                    SelectChoice(
                        label="Income (+1 coin)",
                        value="income",
                        description="Take 1 coin from the Treasury",
                        emoji="success",
                    )
                )
                action_choices.append(
                    SelectChoice(
                        label="Foreign Aid (+2 coins)",
                        value="foreign_aid",
                        description="Take 2 coins (Can be blocked by Duke)",
                        emoji="public",
                    )
                )
                action_choices.append(
                    SelectChoice(
                        label="Tax (Duke) (+3 coins)",
                        value="tax",
                        description="Take 3 coins claiming Duke",
                        emoji="duke",
                    )
                )
                if self.coins[actor] >= 7:
                    action_choices.append(
                        SelectChoice(
                            label="Coup (-7 coins)",
                            value="coup",
                            description="Force another player to lose influence",
                            emoji="explosion",
                        )
                    )
                if self.coins[actor] >= 3:
                    action_choices.append(
                        SelectChoice(
                            label="Assassinate (-3 coins)",
                            value="assassinate",
                            description="Assassinate another player (Can be blocked by Contessa)",
                            emoji="assassin",
                        )
                    )
                action_choices.append(
                    SelectChoice(
                        label="Steal (Captain)",
                        value="steal",
                        description="Steal 2 coins from another player (Can be blocked by Captain/Ambassador)",
                        emoji="captain",
                    )
                )
                action_choices.append(
                    SelectChoice(
                        label="Exchange (Ambassador)",
                        value="exchange",
                        description="Draw 2 cards and choose which to keep",
                        emoji="ambassador",
                    )
                )

            row_actions = ActionRow()
            row_actions.add_select(
                Select(
                    source="action",
                    placeholder="Select action to perform",
                    choices=action_choices,
                    form=True,
                )
            )
            container.add_action_row(row_actions)

            others = [
                p for p in self.players if p.seat != actor and p.seat in self.alive
            ]
            target_choices = [
                SelectChoice(
                    label=f"{p.display_name} · {self.coins[p.seat]} coins · {len(self.hands.get(p.seat, []))} influence",
                    value=str(p.seat),
                    default=(len(others) == 1),
                )
                for p in others
            ]
            if target_choices:
                row_targets = ActionRow()
                row_targets.add_select(
                    Select(
                        source="target",
                        placeholder="Select target player",
                        choices=target_choices,
                        form=True,
                    )
                )
                container.add_action_row(row_targets)

            row_submit = ActionRow()
            row_submit.add_button(
                Button(
                    source="submit_action",
                    label="Submit Move",
                    style=ButtonStyle.PRIMARY,
                )
            )
            container.add_action_row(row_submit)

        elif self.state_phase in ("challenge_window", "block_challenge_window"):
            add_body(container, f"-# {self._reaction_hint()}")
            row = ActionRow()
            row.add_button(
                Button(
                    source="challenge",
                    label="Challenge Claim",
                    style=ButtonStyle.DANGER,
                )
            )
            row.add_button(
                Button(source="pass", label="Pass", style=ButtonStyle.SECONDARY)
            )
            container.add_action_row(row)

        elif self.state_phase == "block_window":
            add_body(container, f"-# {self._reaction_hint()}")
            row = ActionRow()
            if self.current_action == "steal":
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
            elif self.current_action == "assassinate":
                row.add_button(
                    Button(
                        source="block_contessa",
                        label="Block: Contessa",
                        style=ButtonStyle.PRIMARY,
                    )
                )
            elif self.current_action == "foreign_aid":
                row.add_button(
                    Button(
                        source="block_duke",
                        label="Block: Duke",
                        style=ButtonStyle.PRIMARY,
                    )
                )

            row.add_button(
                Button(source="pass", label="Pass", style=ButtonStyle.SECONDARY)
            )
            container.add_action_row(row)

        elif self.state_phase == "lose_influence":
            row = ActionRow()
            row.add_button(
                Button(
                    source="lose_influence_open",
                    label=f"Choose card to reveal ({self.players[self.current_loser].display_name})",
                    style=ButtonStyle.DANGER,
                    query=True,
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
                    query=True,
                )
            )
            container.add_action_row(row)

        # Ephemeral Card Peek Button
        row_peek = ActionRow()
        row_peek.add_button(
            Button(
                source="peek",
                label="Peek Cards",
                emoji="peek",
                style=ButtonStyle.SECONDARY,
                query=True,
            )
        )
        container.add_action_row(row_peek)

        view.add_container(container)
        return view

    async def final_view(
        self, ctx: GameContext, outcome: GameOutcome
    ) -> LayoutView | None:
        summary = outcome.summary or {}
        winner_seat = summary.get("winner")
        view = LayoutView()
        container = Container()
        if winner_seat is None:
            message_lead(
                container,
                "Match ended by forfeit.",
                emoji=ctx.emoji,
                prefix_emoji="error",
            )
        else:
            winner_name = self.players[winner_seat].mention
            message_lead(
                container,
                f"{winner_name} wins the match!",
                emoji=ctx.emoji,
                prefix_emoji="success",
            )

        block = history_block(self.history, ctx.emoji, limit=10)
        if block:
            add_body(container, block)
        view.add_container(container)
        return view

    def render_replay(
        self, ctx: GameContext, live_view: LayoutView | None
    ) -> LayoutView | None:
        return self._public_board_view_replay(ctx)

    def replay_label(self) -> str | None:
        if self.state_phase != "turn":
            return self.state_phase.replace("_", " ").title()
        return f"{self.players[self.current].display_name}'s turn"

    async def bot_move(self, request: BotRequest) -> Move:
        move = await run_cpu(choose_move, self, request.difficulty, request.seat)
        seat = request.seat
        if move.source == "action":
            action_type = move.args.get("type", "income")
            target_val = move.args.get("target")
            return Move(
                actor_seat=seat,
                source="submit_action",
                args={
                    "action": action_type,
                    **({"target": str(target_val)} if target_val is not None else {}),
                },
            )
        elif move.source == "block":
            claim = move.args.get("claim", "captain")
            source = f"block_{claim}"
            return Move(actor_seat=seat, source=source, args=move.args)
        elif move.source == "lose_card":
            cards = self.hands.get(seat, [])
            card = move.args.get("card", cards[0] if cards else "0")
            idx = cards.index(card) if card in cards else 0
            return Move(
                actor_seat=seat,
                source="lose_influence_select",
                args={"value": str(idx)},
            )
        elif move.source == "exchange_keep":
            keep = move.args.get("keep", [])
            indices = []
            options = self.exchange_options.get(seat, [])
            for card in keep:
                if card in options:
                    indices.append(str(options.index(card)))
            return Move(
                actor_seat=seat, source="exchange_select", args={"keep": indices}
            )
        return move

    async def handle_query(self, seat: int, source: str, ctx: GameContext) -> bool:
        if source == "peek":
            cards = self.hands.get(seat, [])
            if not cards:
                view = query_panel(
                    ctx,
                    title="Your cards",
                    prefix_emoji="peek",
                    body="You have no active cards left.",
                )
            else:
                view = query_panel(
                    ctx,
                    title="Your cards",
                    prefix_emoji="peek",
                    body=f"{self._format_hand(ctx, seat, private=True)}\n-# {self.coins.get(seat, 0)} coins",
                )
            await ctx.respond_query(view)
            return True

        if source == "lose_influence_open":
            if (
                self.state_phase != "lose_influence"
                or getattr(self, "current_loser", None) != seat
            ):
                notice = query_panel(
                    ctx,
                    title="You cannot act right now",
                    prefix_emoji="error",
                    body="Influence loss is not pending for you.",
                )
                await ctx.respond_query(notice)
                return True
            await ctx.respond_query(self.get_lose_influence_view(seat, ctx))
            return True

        if source == "exchange_open":
            if (
                self.state_phase != "exchange"
                or getattr(self, "current_actor", None) != seat
            ):
                notice = query_panel(
                    ctx,
                    title="You cannot act right now",
                    prefix_emoji="error",
                    body="Card exchange is not available to you.",
                )
                await ctx.respond_query(notice)
                return True
            await ctx.respond_query(self.get_exchange_view(seat, ctx))
            return True

        return await super().handle_query(seat, source, ctx)
